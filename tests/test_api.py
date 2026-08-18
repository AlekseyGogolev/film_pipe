from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import time
from threading import Event
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

import anyio
import cv2
import numpy as np
import pytest

from filmpipe.api.app import create_app
from filmpipe.application.jobs import InMemoryJobRegistry, JobService
from filmpipe.domain.models import (
    ArtifactType,
    FinalProcessingMode,
    InputProcessingMode,
    ProcessingOptions,
    RestorationMode,
)
from filmpipe.domain.processor import ProcessingContext, ProcessorResult
from filmpipe.infrastructure.job_store import FileSystemJobRegistry
from filmpipe.infrastructure.logging import setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors import (
    DecodeBWImageProcessor,
    DecodePositiveImageProcessor,
    FailingProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)

from tests.image_fixtures import synthetic_bw_negative_16bit, write_image


TERMINAL_JOB_STATUSES = {"success", "partial_success", "failed"}


@dataclass
class ASGIResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class ASGITestClient:
    def __init__(self, app) -> None:
        self.app = app

    def get(self, path: str) -> ASGIResponse:
        return anyio.run(self._request, "GET", path, {}, b"")

    def post_multipart(
        self,
        path: str,
        *,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> ASGIResponse:
        body, content_type = _multipart_body(data, files)
        return anyio.run(
            self._request,
            "POST",
            path,
            {
                "content-type": content_type,
                "content-length": str(len(body)),
            },
            body,
        )

    async def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> ASGIResponse:
        url = urlsplit(path)
        request_messages = [{"type": "http.request", "body": body, "more_body": False}]
        response_status = 500
        response_headers: dict[str, str] = {}
        response_body: list[bytes] = []

        async def receive() -> dict[str, Any]:
            if request_messages:
                return request_messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                response_headers.update(
                    {
                        key.decode("latin-1").lower(): value.decode("latin-1")
                        for key, value in message.get("headers", [])
                    }
                )
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": url.path,
            "raw_path": url.path.encode("ascii"),
            "query_string": url.query.encode("ascii"),
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in {"host": "testserver", **headers}.items()
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await self.app(scope, receive, send)
        return ASGIResponse(
            status_code=response_status,
            headers=response_headers,
            content=b"".join(response_body),
        )


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)

    def shutdown(self, *, wait: bool = False) -> None:
        pass


def _client(tmp_path, *, pipeline_factory=None) -> ASGITestClient:
    setup_logging(tmp_path / "logs")
    service = JobService(
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline_factory=pipeline_factory or _default_test_pipeline,
    )
    app = create_app(
        job_service=service,
        job_registry=InMemoryJobRegistry(),
    )
    return ASGITestClient(app)


def _wait_for_job(
    client: ASGITestClient,
    job_id: str,
    *,
    statuses: set[str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    expected_statuses = statuses or TERMINAL_JOB_STATUSES
    deadline = time.monotonic() + timeout
    last_job: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        last_job = response.json()
        if last_job["status"] in expected_statuses:
            return last_job
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for job {job_id}: {last_job}")


def _default_test_pipeline(options: ProcessingOptions | None = None) -> ProcessingPipeline:
    options = options or ProcessingOptions()
    if options.input_processing == InputProcessingMode.ALREADY_POSITIVE:
        processors = [DecodePositiveImageProcessor()]
    else:
        processors = [
            DecodeBWImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
        ]
    if options.restoration != RestorationMode.OFF:
        processors.append(RestoredArtifactStubProcessor())
    if options.final_processing == FinalProcessingMode.CREATIVE:
        processors.append(CreativeArtifactStubProcessor())
    return ProcessingPipeline(processors)


def _pipeline_with_optional_failure() -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            DecodeBWImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
            FailingProcessor(name="restoration_stub", optional=True),
        ]
    )


def _already_positive_pipeline_with_optional_failure() -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            DecodePositiveImageProcessor(),
            FailingProcessor(name="restoration_stub", optional=True),
        ]
    )


@dataclass
class RestoredArtifactStubProcessor:
    name: str = "restoration_stub"
    optional: bool = True

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        if context.working_positive is None:
            raise RuntimeError("missing working positive")

        with TemporaryDirectory(prefix="filmpipe-test-restored-") as temporary_dir:
            temporary_path = Path(temporary_dir) / "restored.tiff"
            ok, encoded = cv2.imencode(".tiff", np.asarray(context.working_positive))
            assert ok
            temporary_path.write_bytes(encoded.tobytes())
            artifact = context.artifact_store.save_artifact(
                context.job_id,
                context.image_id,
                ArtifactType.RESTORED,
                temporary_path,
            )
        return ProcessorResult.success(image=image, artifacts=[artifact])


@dataclass
class CreativeArtifactStubProcessor:
    name: str = "generative_processing"
    optional: bool = True

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        if context.working_positive is None:
            raise RuntimeError("missing working positive")

        output = _to_png_stub_image(np.asarray(context.working_positive))
        with TemporaryDirectory(prefix="filmpipe-test-creative-") as temporary_dir:
            temporary_path = Path(temporary_dir) / "creative.png"
            ok, encoded = cv2.imencode(".png", output)
            assert ok
            temporary_path.write_bytes(encoded.tobytes())
            artifact = context.artifact_store.save_artifact(
                context.job_id,
                context.image_id,
                ArtifactType.CREATIVE,
                temporary_path,
            )
        return ProcessorResult.success(image=image, artifacts=[artifact])


@dataclass
class BlockingProcessor:
    started: Event
    release: Event
    name: str = "blocking_test_processor"
    optional: bool = False

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        self.started.set()
        assert self.release.wait(timeout=5)
        return ProcessorResult.success(image=image)


@dataclass
class SecondImageBlockingProcessor:
    first_completed: Event
    second_started: Event
    release_second: Event
    calls: int = 0
    name: str = "second_image_blocking_processor"
    optional: bool = False

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        self.calls += 1
        if self.calls == 1:
            self.first_completed.set()
            return ProcessorResult.success(image=image)

        self.second_started.set()
        assert self.release_second.wait(timeout=5)
        return ProcessorResult.success(image=image)


def _to_png_stub_image(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    if image.dtype == np.uint16:
        return np.rint(image.astype(np.float32) / np.iinfo(np.uint16).max * 255).astype(
            np.uint8
        )
    if np.issubdtype(image.dtype, np.floating):
        return np.rint(np.clip(image, 0.0, 1.0) * 255).astype(np.uint8)
    raise AssertionError(f"unsupported stub image dtype: {image.dtype}")


def _negative_upload(tmp_path, filename: str = "scan.tiff") -> tuple[str, bytes, str]:
    source = write_image(tmp_path / filename, synthetic_bw_negative_16bit())
    return (filename, source.read_bytes(), "image/tiff")


def _positive_upload(tmp_path, filename: str = "positive.tiff") -> tuple[str, bytes, str]:
    positive = np.tile(np.linspace(10000, 50000, 64, dtype=np.uint16), (32, 1))
    source = write_image(tmp_path / filename, positive)
    return (filename, source.read_bytes(), "image/tiff")


def _multipart_body(
    data: dict[str, str],
    files: list[tuple[str, tuple[str, bytes, str]]],
) -> tuple[bytes, str]:
    boundary = f"filmpipe-{uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in data.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for field_name, (filename, content, content_type) in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _decode_response_image(content: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert image is not None
    return image


def test_create_job_single_success_and_artifact_download(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    submitted_job = response.json()
    assert submitted_job["status"] == "pending"
    assert len(submitted_job["images"]) == 1
    assert submitted_job["images"][0]["status"] == "pending"

    job = _wait_for_job(client, submitted_job["id"])
    assert job["status"] == "success"
    assert job["input_processing"] == "bw_negative"
    assert job["restoration"] == "off"
    assert job["final_processing"] == "standard"
    assert len(job["images"]) == 1

    image = job["images"][0]
    assert image["status"] == "success"
    assert image["errors"] == []
    artifacts = {artifact["type"]: artifact for artifact in image["artifacts"]}
    assert set(artifacts) == {"original", "positive"}
    assert "path" not in artifacts["positive"]

    get_response = client.get(f"/jobs/{job['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == job["id"]

    preview = client.get(artifacts["positive"]["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    preview_output = _decode_response_image(preview.content)
    assert preview_output.dtype == np.uint8
    assert preview_output[:, 0].mean() < preview_output[:, -1].mean()

    download = client.get(artifacts["positive"]["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/tiff"
    assert "attachment" in download.headers["content-disposition"]
    master_output = _decode_response_image(download.content)
    assert master_output.dtype == np.uint16
    assert master_output[:, 0].mean() < master_output[:, -1].mean()

    original_preview = client.get(artifacts["original"]["preview_url"])
    assert original_preview.status_code == 200
    assert original_preview.headers["content-type"] == "image/png"


def test_create_job_returns_before_slow_processing_finishes(tmp_path):
    started = Event()
    release = Event()
    client = _client(
        tmp_path,
        pipeline_factory=lambda _options=None: ProcessingPipeline(
            [BlockingProcessor(started=started, release=release)]
        ),
    )

    started_at = time.monotonic()
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )
    elapsed = time.monotonic() - started_at

    assert response.status_code == 201
    assert elapsed < 0.5
    job = response.json()
    assert job["status"] == "pending"
    assert started.wait(timeout=1)

    running_job = _wait_for_job(
        client,
        job["id"],
        statuses={"running"},
        timeout=1,
    )
    assert running_job["images"][0]["status"] == "running"

    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["id"] == job["id"]

    release.set()
    completed_job = _wait_for_job(client, job["id"])
    assert completed_job["status"] == "success"


def test_create_job_persists_uploads_and_manifest_before_enqueue(tmp_path):
    setup_logging(tmp_path / "logs")
    jobs_root = tmp_path / "jobs"
    queue = RecordingQueue()
    service = JobService(
        storage=FileSystemArtifactStore(jobs_root),
        pipeline_factory=_default_test_pipeline,
    )
    client = ASGITestClient(
        create_app(
            job_service=service,
            job_registry=FileSystemJobRegistry(jobs_root),
            job_queue=queue,
        )
    )

    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job_id = response.json()["id"]
    manifest_path = jobs_root / job_id / "job.json"
    upload_path = jobs_root / job_id / "inputs" / "0" / "scan.tiff"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert upload_path.is_file()
    assert manifest["status"] == "pending"
    assert manifest["inputs"] == ["inputs/0/scan.tiff"]
    assert queue.enqueued == [job_id]


def test_async_job_manifest_tracks_running_and_terminal_states(tmp_path):
    setup_logging(tmp_path / "logs")
    started = Event()
    release = Event()
    jobs_root = tmp_path / "jobs"
    service = JobService(
        storage=FileSystemArtifactStore(jobs_root),
        pipeline_factory=lambda _options=None: ProcessingPipeline(
            [BlockingProcessor(started=started, release=release)]
        ),
    )
    client = ASGITestClient(
        create_app(
            job_service=service,
            job_registry=FileSystemJobRegistry(jobs_root),
        )
    )

    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job_id = response.json()["id"]
    manifest_path = jobs_root / job_id / "job.json"

    assert started.wait(timeout=1)
    running_job = _wait_for_job(
        client,
        job_id,
        statuses={"running"},
        timeout=1,
    )
    running_manifest = json.loads(manifest_path.read_text("utf-8"))
    assert running_job["images"][0]["status"] == "running"
    assert running_manifest["status"] == "running"
    assert running_manifest["images"][0]["status"] == "running"

    release.set()
    completed_job = _wait_for_job(client, job_id)
    completed_manifest = json.loads(manifest_path.read_text("utf-8"))
    assert completed_job["status"] == "success"
    assert completed_manifest["status"] == "success"
    assert completed_manifest["images"][0]["status"] == "success"


def test_polling_shows_image_progress_while_batch_job_runs(tmp_path):
    first_completed = Event()
    second_started = Event()
    release_second = Event()
    processor = SecondImageBlockingProcessor(
        first_completed=first_completed,
        second_started=second_started,
        release_second=release_second,
    )
    client = _client(
        tmp_path,
        pipeline_factory=lambda _options=None: ProcessingPipeline([processor]),
    )

    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[
            ("files", _negative_upload(tmp_path, "first.tiff")),
            ("files", _negative_upload(tmp_path, "second.tiff")),
        ],
    )

    assert response.status_code == 201
    job_id = response.json()["id"]
    assert first_completed.wait(timeout=1)
    assert second_started.wait(timeout=1)

    progress = client.get(f"/jobs/{job_id}")
    assert progress.status_code == 200
    job = progress.json()
    assert job["status"] == "running"
    assert [image["status"] for image in job["images"]] == ["success", "running"]

    release_second.set()
    completed_job = _wait_for_job(client, job_id)
    assert completed_job["status"] == "success"


def test_batch_job_is_partial_when_one_image_fails(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[
            ("files", _negative_upload(tmp_path, "valid.tiff")),
            ("files", ("broken.png", b"not a valid image", "image/png")),
        ],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    assert job["status"] == "partial_success"
    assert [image["status"] for image in job["images"]] == ["success", "failed"]

    failed = job["images"][1]
    assert failed["filename"] == "broken.png"
    assert failed["errors"][0]["stage"] == "decode_bw"
    assert not any(artifact["type"] == "positive" for artifact in failed["artifacts"])
    failed_original = next(
        artifact for artifact in failed["artifacts"] if artifact["type"] == "original"
    )
    failed_preview = client.get(failed_original["preview_url"])
    assert failed_preview.status_code == 422
    assert "Предпросмотр" in failed_preview.json()["detail"]

    archive_response = client.get(job["download_url"])
    assert archive_response.status_code == 200
    with ZipFile(BytesIO(archive_response.content)) as archive:
        names = archive.namelist()

    assert len(names) == 1
    assert names[0].endswith("/positive/valid_positive.tiff")
    assert "original" not in names[0]
    assert "broken" not in names[0]


def test_optional_failure_after_positive_preserves_artifact(tmp_path):
    client = _client(tmp_path, pipeline_factory=_pipeline_with_optional_failure)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    image = job["images"][0]
    assert job["status"] == "partial_success"
    assert image["status"] == "partial_success"
    assert image["errors"][0]["stage"] == "restoration_stub"
    assert image["errors"][0]["recoverable"] is True

    artifacts = {artifact["type"]: artifact for artifact in image["artifacts"]}
    assert "positive" in artifacts
    preview = client.get(artifacts["positive"]["preview_url"])
    assert preview.status_code == 200
    archive = client.get(job["download_url"])
    assert archive.status_code == 200


def test_optional_failure_after_already_positive_preserves_base_result(tmp_path):
    client = _client(
        tmp_path,
        pipeline_factory=_already_positive_pipeline_with_optional_failure,
    )
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "already_positive", "restoration": "lama"},
        files=[("files", _positive_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    image = job["images"][0]
    assert job["status"] == "partial_success"
    assert image["status"] == "partial_success"
    assert image["errors"][0]["stage"] == "restoration_stub"
    artifacts = {artifact["type"] for artifact in image["artifacts"]}
    assert artifacts == {"original"}


def test_invalid_input_processing_returns_clear_400(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "colorize"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 400
    assert "Input processing must be one of" in response.json()["detail"]


def test_unknown_job_form_field_returns_clear_400(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"unexpected": "value"},
        files=[("files", _positive_upload(tmp_path))],
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Unknown form fields" in detail
    assert "unexpected" in detail
    assert "files, input_processing, restoration, final_processing, creative_prompt" in detail


def test_already_positive_off_returns_only_original_artifact(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "already_positive", "restoration": "off"},
        files=[("files", _positive_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    assert job["status"] == "success"

    image = job["images"][0]
    assert {artifact["type"] for artifact in image["artifacts"]} == {"original"}


@pytest.mark.parametrize(
    ("input_processing", "restoration", "expected_artifacts"),
    [
        ("already_positive", "off", {"original"}),
        ("already_positive", "telea", {"original", "restored"}),
        ("already_positive", "lama", {"original", "restored"}),
        ("bw_negative", "off", {"original", "positive"}),
        ("bw_negative", "telea", {"original", "positive", "restored"}),
        ("bw_negative", "lama", {"original", "positive", "restored"}),
    ],
)
def test_public_artifact_matrix(tmp_path, input_processing, restoration, expected_artifacts):
    client = _client(tmp_path)
    upload = (
        _positive_upload(tmp_path)
        if input_processing == "already_positive"
        else _negative_upload(tmp_path)
    )
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": input_processing, "restoration": restoration},
        files=[("files", upload)],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    image = job["images"][0]
    assert {artifact["type"] for artifact in image["artifacts"]} == expected_artifacts


def test_restoration_values_are_accepted_and_serialized(tmp_path):
    client = _client(tmp_path)

    for restoration in ("off", "telea", "lama"):
        response = client.post_multipart(
            "/jobs",
            data={"input_processing": "bw_negative", "restoration": restoration},
            files=[("files", _negative_upload(tmp_path, f"{restoration}.tiff"))],
        )

        assert response.status_code == 201
        submitted_job = response.json()
        job = _wait_for_job(client, submitted_job["id"])
        assert job["restoration"] == restoration


def test_final_processing_values_are_accepted_and_serialized(tmp_path):
    client = _client(tmp_path)

    standard = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative", "final_processing": "standard"},
        files=[("files", _negative_upload(tmp_path, "standard.tiff"))],
    )
    assert standard.status_code == 201
    standard_job = _wait_for_job(client, standard.json()["id"])
    assert standard_job["final_processing"] == "standard"

    creative = client.post_multipart(
        "/jobs",
        data={
            "input_processing": "bw_negative",
            "final_processing": "creative",
            "creative_prompt": "clean archival print",
        },
        files=[("files", _negative_upload(tmp_path, "creative.tiff"))],
    )
    assert creative.status_code == 201
    job = _wait_for_job(client, creative.json()["id"])
    assert job["final_processing"] == "creative"
    artifacts = {artifact["type"] for artifact in job["images"][0]["artifacts"]}
    assert artifacts == {"original", "positive", "creative"}


def test_invalid_final_processing_value_returns_clear_400(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative", "final_processing": "magic"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 400
    assert "Final processing must be one of" in response.json()["detail"]


@pytest.mark.parametrize("data", [
    {"input_processing": "bw_negative", "final_processing": "creative"},
    {
        "input_processing": "bw_negative",
        "final_processing": "creative",
        "creative_prompt": "   ",
    },
])
def test_creative_requires_non_blank_prompt(tmp_path, data):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data=data,
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 400
    assert "creative_prompt is required" in response.json()["detail"]


def test_invalid_restoration_value_returns_clear_400(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative", "restoration": "magic"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 400
    assert "Restoration must be one of" in response.json()["detail"]


def test_missing_input_processing_defaults_to_bw_negative(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"restoration": "off"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    assert job["input_processing"] == "bw_negative"


def test_default_registry_persists_job_for_new_app_instance(tmp_path):
    setup_logging(tmp_path / "logs")
    jobs_root = tmp_path / "jobs"
    service = JobService(
        storage=FileSystemArtifactStore(jobs_root),
        pipeline_factory=_default_test_pipeline,
    )
    client = ASGITestClient(create_app(job_service=service))
    response = client.post_multipart(
        "/jobs",
        data={"input_processing": "bw_negative"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = _wait_for_job(client, response.json()["id"])
    assert (jobs_root / job["id"] / "job.json").is_file()

    reloaded_client = ASGITestClient(
        create_app(
            job_service=JobService(
                storage=FileSystemArtifactStore(jobs_root),
                pipeline_factory=_default_test_pipeline,
            )
        )
    )
    jobs_response = reloaded_client.get("/jobs")
    assert jobs_response.status_code == 200
    assert [listed_job["id"] for listed_job in jobs_response.json()["jobs"]] == [
        job["id"]
    ]

    job_response = reloaded_client.get(f"/jobs/{job['id']}")
    assert job_response.status_code == 200
    artifacts = {
        artifact["type"]: artifact
        for artifact in job_response.json()["images"][0]["artifacts"]
    }
    assert reloaded_client.get(artifacts["positive"]["preview_url"]).status_code == 200
    assert reloaded_client.get(artifacts["positive"]["download_url"]).status_code == 200


def test_legacy_job_directory_is_visible_through_api(tmp_path):
    setup_logging(tmp_path / "logs")
    jobs_root = tmp_path / "jobs"
    original = (
        jobs_root
        / "legacy-job"
        / "image-1"
        / "original"
        / "legacy_scan.tiff"
    )
    positive = (
        jobs_root
        / "legacy-job"
        / "image-1"
        / "positive"
        / "legacy_scan_positive.tiff"
    )
    original.parent.mkdir(parents=True)
    positive.parent.mkdir(parents=True)
    write_image(original, synthetic_bw_negative_16bit())
    write_image(positive, synthetic_bw_negative_16bit())
    client = ASGITestClient(
        create_app(
            job_service=JobService(
                storage=FileSystemArtifactStore(jobs_root),
                pipeline_factory=_default_test_pipeline,
            )
        )
    )

    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    job = jobs_response.json()["jobs"][0]
    assert job["id"] == "legacy-job"
    assert job["legacy"] is True

    job_response = client.get("/jobs/legacy-job")
    assert job_response.status_code == 200
    artifacts = {
        artifact["type"]: artifact
        for artifact in job_response.json()["images"][0]["artifacts"]
    }
    assert client.get(artifacts["positive"]["preview_url"]).status_code == 200
    download = client.get(artifacts["positive"]["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/tiff"
