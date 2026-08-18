from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

import cv2
import numpy as np
import pytest

from filmpipe.application.jobs import JobService
from filmpipe.domain.models import (
    ArtifactType,
    FinalProcessingMode,
    InputProcessingMode,
    ProcessingOptions,
    ProcessingStatus,
    RestorationMode,
)
from filmpipe.domain.processor import ProcessingContext, ProcessorResult
from filmpipe.infrastructure.logging import get_logger, setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import default_pipeline, process_image
from filmpipe.processing import generative as generative_module
from filmpipe.processing.generative import (
    CreativeRequest,
    CreativeResult,
    GenerativeProcessor,
    StableDiffusionCppConfig,
    StableDiffusionCppProvider,
    select_creative_source,
)
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.restoration import (
    AIRestorationProcessor,
    DetectionResult,
    MaskPostprocessConfig,
    RestorerCandidate,
)
from filmpipe.processing.processors import (
    DecodeBWImageProcessor,
    DecodePositiveImageProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)

from tests.image_fixtures import read_image, synthetic_bw_negative_16bit, write_image


@dataclass
class FakeCreativeProvider:
    name: str = "fake_creative_provider"
    model_id: str = "fake-model"
    should_fail: bool = False
    fail_first_only: bool = False
    calls: int = 0
    close_calls: int = 0
    events: list[str] | None = None
    required_event_before_generate: str | None = None
    requests: list[CreativeRequest] = field(default_factory=list)

    def generate(self, request: CreativeRequest) -> CreativeResult:
        self.calls += 1
        if (
            self.required_event_before_generate is not None
            and (
                self.events is None
                or self.required_event_before_generate not in self.events
            )
        ):
            raise RuntimeError(
                f"missing lifecycle event: {self.required_event_before_generate}"
            )
        if self.events is not None:
            self.events.append("creative_generate")
        self.requests.append(request)
        if self.should_fail or (self.fail_first_only and self.calls == 1):
            raise RuntimeError("creative boom")

        encoded = np.frombuffer(request.source_path.read_bytes(), dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        assert decoded is not None
        ok, output = cv2.imencode(request.output_path.suffix, decoded)
        assert ok
        request.output_path.write_bytes(output.tobytes())
        return CreativeResult(output_path=request.output_path, metadata={"fake": True})

    def close(self) -> None:
        self.close_calls += 1
        if self.events is not None:
            self.events.append("creative_closed")


@dataclass
class LifecycleDetector:
    events: list[str]

    def detect(self, image: np.ndarray) -> DetectionResult:
        self.events.append("detector_detect")
        probability = np.zeros(image.shape[:2], dtype=np.float32)
        probability[image.shape[0] // 2, image.shape[1] // 2] = 1.0
        return DetectionResult(probability=probability, metadata={"name": "fake"})

    def close(self) -> None:
        self.events.append("detector_closed")


@dataclass
class LifecycleRestorer:
    events: list[str]

    def restore(
        self,
        image: np.ndarray,
        restoration_mask: np.ndarray,
    ) -> RestorerCandidate:
        self.events.append("restorer_restore")
        candidate = np.zeros_like(image)
        if image.ndim == 2:
            candidate[restoration_mask > 0] = np.iinfo(image.dtype).max
        else:
            candidate[restoration_mask > 0, :] = np.iinfo(image.dtype).max
        return RestorerCandidate(image=candidate, metadata={"name": "fake"})

    def close(self) -> None:
        self.events.append("restorer_closed")


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


def _negative(tmp_path: Path, filename: str = "scan.tiff") -> Path:
    return write_image(tmp_path / filename, synthetic_bw_negative_16bit())


def _positive(tmp_path: Path, filename: str = "positive.tiff") -> Path:
    image = np.tile(np.linspace(10000, 50000, 64, dtype=np.uint16), (32, 1))
    return write_image(tmp_path / filename, image)


def _context(tmp_path: Path) -> ProcessingContext:
    setup_logging(tmp_path / "logs")
    store = FileSystemArtifactStore(tmp_path / "jobs")
    original_source = write_image(tmp_path / "original.tiff", synthetic_bw_negative_16bit())
    original = store.save_original("job-1", "image-1", original_source)
    return ProcessingContext(
        job_id="job-1",
        image_id="image-1",
        filename=original_source.name,
        options=ProcessingOptions(),
        artifact_store=store,
        logger=get_logger(job_id="job-1", image_id="image-1"),
        artifacts={ArtifactType.ORIGINAL: original},
    )


def _technical_processors() -> list[Any]:
    return [
        DecodeBWImageProcessor(),
        NegativeConverterProcessor(),
        ToneNormalizerProcessor(),
        PositiveArtifactWriterProcessor(),
    ]


def _creative_pipeline(provider: FakeCreativeProvider) -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            *_technical_processors(),
            GenerativeProcessor(provider_factory=lambda: provider),
        ]
    )


def test_standard_default_pipeline_does_not_include_creative_processor():
    pipeline = default_pipeline(
        ProcessingOptions(
            input_processing=InputProcessingMode.BW_NEGATIVE,
            restoration=RestorationMode.LAMA,
            final_processing=FinalProcessingMode.STANDARD,
        )
    )

    assert [processor.name for processor in pipeline.processors] == [
        "decode_bw",
        "negative_conversion",
        "tone_normalization",
        "positive_artifact_writer",
        "ai_restoration",
    ]


@pytest.mark.parametrize(
    ("input_processing", "restoration", "expected_processors"),
    [
        (
            InputProcessingMode.ALREADY_POSITIVE,
            RestorationMode.OFF,
            ["decode_positive", "generative_processing"],
        ),
        (
            InputProcessingMode.ALREADY_POSITIVE,
            RestorationMode.LAMA,
            ["decode_positive", "ai_restoration", "generative_processing"],
        ),
        (
            InputProcessingMode.BW_NEGATIVE,
            RestorationMode.OFF,
            [
                "decode_bw",
                "negative_conversion",
                "tone_normalization",
                "positive_artifact_writer",
                "generative_processing",
            ],
        ),
        (
            InputProcessingMode.BW_NEGATIVE,
            RestorationMode.LAMA,
            [
                "decode_bw",
                "negative_conversion",
                "tone_normalization",
                "positive_artifact_writer",
                "ai_restoration",
                "generative_processing",
            ],
        ),
    ],
)
def test_creative_pipeline_appends_generative_processing_after_restoration(
    input_processing,
    restoration,
    expected_processors,
):
    pipeline = default_pipeline(
        ProcessingOptions(
            input_processing=input_processing,
            restoration=restoration,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        )
    )

    assert [processor.name for processor in pipeline.processors] == expected_processors


def test_creative_source_selection_prefers_context_artifacts(tmp_path):
    context = _context(tmp_path)
    context.working_positive = np.ones((4, 4), dtype=np.float32)
    positive_source = write_image(tmp_path / "positive.tiff", synthetic_bw_negative_16bit())
    restored_source = write_image(tmp_path / "restored.tiff", synthetic_bw_negative_16bit())
    positive = context.artifact_store.save_artifact(
        context.job_id,
        context.image_id,
        ArtifactType.POSITIVE,
        positive_source,
    )
    restored = context.artifact_store.save_artifact(
        context.job_id,
        context.image_id,
        ArtifactType.RESTORED,
        restored_source,
    )
    context.artifacts[ArtifactType.POSITIVE] = positive
    context.artifacts[ArtifactType.RESTORED] = restored

    assert select_creative_source(context, None).kind == "restored"

    del context.artifacts[ArtifactType.RESTORED]
    assert select_creative_source(context, None).kind == "positive"

    del context.artifacts[ArtifactType.POSITIVE]
    assert select_creative_source(context, None).kind == "working_positive"

    context.working_positive = None
    assert select_creative_source(context, None).kind == "original"


def test_process_image_creative_success_creates_creative_and_preserves_positive(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = FakeCreativeProvider()
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(
            restoration=RestorationMode.OFF,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_creative_pipeline(provider),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert provider.calls == 1
    assert provider.close_calls == 1
    assert provider.requests[0].source_kind == "positive"
    assert result.artifact(ArtifactType.POSITIVE) is not None
    creative = result.artifact(ArtifactType.CREATIVE)
    assert creative is not None
    assert creative.path.suffix == ".png"
    assert creative.mime_type == "image/png"
    assert read_image(creative.path).dtype == np.uint8


def test_creative_success_prefers_restored_over_positive(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = FakeCreativeProvider()
    pipeline = ProcessingPipeline(
        [
            *_technical_processors(),
            RestoredArtifactStubProcessor(),
            GenerativeProcessor(provider_factory=lambda: provider),
        ]
    )

    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(
            restoration=RestorationMode.LAMA,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=pipeline,
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert provider.requests[0].source_kind == "restored"
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is not None
    assert result.artifact(ArtifactType.CREATIVE) is not None


def test_creative_failure_after_positive_is_recoverable_partial_success(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = FakeCreativeProvider(should_fail=True)
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(
            restoration=RestorationMode.OFF,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_creative_pipeline(provider),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert provider.close_calls == 1
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.CREATIVE) is None
    assert result.errors[0].stage == "generative_processing"
    assert result.errors[0].recoverable is True


def test_missing_creative_runtime_is_recoverable_partial_success(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = StableDiffusionCppProvider(
        config=StableDiffusionCppConfig(
            sd_cli=tmp_path / "missing-sd-cli",
            diffusion_model=tmp_path / "missing-model.gguf",
            vae=tmp_path / "missing-vae.safetensors",
            clip_l=tmp_path / "missing-clip-l.safetensors",
            t5xxl=tmp_path / "missing-t5xxl.safetensors",
            timeout_sec=1,
        )
    )
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(
            restoration=RestorationMode.OFF,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=ProcessingPipeline(
            [
                *_technical_processors(),
                GenerativeProcessor(provider_factory=lambda: provider),
            ]
        ),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.CREATIVE) is None
    assert "missing" in (result.errors[0].technical_message or "").lower()
    assert result.errors[0].recoverable is True


def test_stable_diffusion_cpp_timeout_terminates_process(tmp_path, monkeypatch):
    class TimeoutProcess:
        pid = 12345
        returncode = None
        terminated = False
        killed = False

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired("sd-cli", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    process = TimeoutProcess()
    monkeypatch.setattr(generative_module.subprocess, "Popen", lambda *_, **__: process)
    required_paths = {
        "sd_cli": tmp_path / "sd-cli",
        "diffusion_model": tmp_path / "model.gguf",
        "vae": tmp_path / "vae.safetensors",
        "clip_l": tmp_path / "clip.safetensors",
        "t5xxl": tmp_path / "t5.safetensors",
    }
    for path in required_paths.values():
        path.write_bytes(b"present")
    provider = StableDiffusionCppProvider(
        config=StableDiffusionCppConfig(**required_paths, timeout_sec=1)
    )

    with pytest.raises(RuntimeError, match="timed out"):
        provider.generate(
            CreativeRequest(
                source_path=tmp_path / "source.png",
                prompt="clean archival print",
                job_id="job-1",
                image_id="image-1",
                output_path=tmp_path / "out.png",
                source_kind="positive",
            )
        )

    assert process.terminated is True
    assert process.killed is False
    assert provider._process is None


def test_standard_generative_processor_does_not_create_creative_runtime(tmp_path):
    context = _context(tmp_path)
    context.options = ProcessingOptions(final_processing=FinalProcessingMode.STANDARD)
    processor = GenerativeProcessor(
        provider_factory=lambda: (_ for _ in ()).throw(RuntimeError("provider created"))
    )

    result = processor.process(None, context)

    assert not result.errors


def test_creative_starts_after_restoration_runtime_cleanup(tmp_path):
    setup_logging(tmp_path / "logs")
    events: list[str] = []
    detector = LifecycleDetector(events)
    restorer = LifecycleRestorer(events)
    provider = FakeCreativeProvider(
        events=events,
        required_event_before_generate="restorer_closed",
    )
    pipeline = ProcessingPipeline(
        [
            *_technical_processors(),
            AIRestorationProcessor(
                detector_factory=lambda: detector,
                restorer_factories={RestorationMode.LAMA: lambda: restorer},
                mask_config=MaskPostprocessConfig(threshold=0.5, dilation=0, mode="none"),
            ),
            GenerativeProcessor(provider_factory=lambda: provider),
        ]
    )

    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(
            restoration=RestorationMode.LAMA,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=pipeline,
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert events.index("restorer_closed") < events.index("creative_generate")
    assert provider.close_calls == 1


def test_creative_failure_after_already_positive_base_preserves_original(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = FakeCreativeProvider(should_fail=True)
    pipeline = ProcessingPipeline(
        [
            DecodePositiveImageProcessor(),
            GenerativeProcessor(provider_factory=lambda: provider),
        ]
    )
    result = process_image(
        _positive(tmp_path),
        options=ProcessingOptions(
            input_processing=InputProcessingMode.ALREADY_POSITIVE,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=pipeline,
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert result.artifact(ArtifactType.ORIGINAL) is not None
    assert result.artifact(ArtifactType.POSITIVE) is None
    assert result.artifact(ArtifactType.CREATIVE) is None
    assert provider.requests[0].source_kind == "working_positive"
    assert result.errors[0].recoverable is True


def test_batch_creative_failure_for_one_image_does_not_stop_others(tmp_path):
    setup_logging(tmp_path / "logs")
    provider = FakeCreativeProvider(fail_first_only=True)
    service = JobService(
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline_factory=lambda options: _creative_pipeline(provider),
    )

    job = service.process(
        [_negative(tmp_path, "first.tiff"), _negative(tmp_path, "second.tiff")],
        options=ProcessingOptions(
            restoration=RestorationMode.OFF,
            final_processing=FinalProcessingMode.CREATIVE,
            creative_prompt="clean archival print",
        ),
        job_id="job-1",
    )

    assert provider.calls == 2
    assert job.status == ProcessingStatus.PARTIAL_SUCCESS
    assert [result.status for result in job.results] == [
        ProcessingStatus.PARTIAL_SUCCESS,
        ProcessingStatus.SUCCESS,
    ]
    assert job.results[0].artifact(ArtifactType.CREATIVE) is None
    assert job.results[1].artifact(ArtifactType.CREATIVE) is not None
