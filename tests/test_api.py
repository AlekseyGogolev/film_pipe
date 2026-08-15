from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

import anyio
import cv2
import numpy as np

from filmpipe.api.app import create_app
from filmpipe.application.jobs import InMemoryJobRegistry, JobService
from filmpipe.infrastructure.logging import setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors import (
    DecodeImageProcessor,
    FailingProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)

from tests.image_fixtures import synthetic_bw_negative_16bit, write_image


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


def _default_test_pipeline() -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            DecodeImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
        ]
    )


def _pipeline_with_optional_failure() -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            DecodeImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
            FailingProcessor(name="restoration_stub", optional=True),
        ]
    )


def _negative_upload(tmp_path, filename: str = "scan.tiff") -> tuple[str, bytes, str]:
    source = write_image(tmp_path / filename, synthetic_bw_negative_16bit())
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
        data={"mode": "bw"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "success"
    assert job["mode"] == "bw"
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
    output = _decode_response_image(preview.content)
    assert output.dtype == np.uint16
    assert output[:, 0].mean() < output[:, -1].mean()

    download = client.get(artifacts["positive"]["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/tiff"
    assert "attachment" in download.headers["content-disposition"]


def test_batch_job_is_partial_when_one_image_fails(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"mode": "bw"},
        files=[
            ("files", _negative_upload(tmp_path, "valid.tiff")),
            ("files", ("broken.png", b"not a valid image", "image/png")),
        ],
    )

    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "partial_success"
    assert [image["status"] for image in job["images"]] == ["success", "failed"]

    failed = job["images"][1]
    assert failed["filename"] == "broken.png"
    assert failed["errors"][0]["stage"] == "decode"
    assert not any(artifact["type"] == "positive" for artifact in failed["artifacts"])

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
        data={"mode": "bw"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 201
    job = response.json()
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


def test_unsupported_mode_returns_clear_400(tmp_path):
    client = _client(tmp_path)
    response = client.post_multipart(
        "/jobs",
        data={"mode": "colorize"},
        files=[("files", _negative_upload(tmp_path))],
    )

    assert response.status_code == 400
    assert "пока не реализован" in response.json()["detail"]
