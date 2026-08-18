from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np

from filmpipe.application.jobs import JobService
from filmpipe.domain.models import (
    ArtifactType,
    InputProcessingMode,
    ProcessingOptions,
    ProcessingStatus,
    RestorationMode,
)
from filmpipe.domain.processor import ProcessingContext
from filmpipe.infrastructure.logging import get_logger, setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import default_pipeline, process_image
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing import restoration as restoration_module
from filmpipe.processing.processors import (
    AIRestorationProcessor,
    DecodeBWImageProcessor,
    DecodePositiveImageProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)
from filmpipe.processing.restoration import (
    DetectionResult,
    MaskPostprocessConfig,
    RestorerCandidate,
)

from tests.image_fixtures import read_image, synthetic_bw_negative_16bit, write_image


@dataclass
class FakeDetector:
    calls: int = 0
    should_fail: bool = False
    failure_message: str = "detector boom"
    close_calls: int = 0
    events: list[str] | None = None

    def detect(self, image: np.ndarray) -> DetectionResult:
        self.calls += 1
        if self.events is not None:
            self.events.append("detector_detect")
        if self.should_fail:
            raise RuntimeError(self.failure_message)
        probability = np.zeros(image.shape[:2], dtype=np.float32)
        probability[image.shape[0] // 2, image.shape[1] // 2] = 1.0
        return DetectionResult(probability=probability, metadata={"name": "fake"})

    def close(self) -> None:
        self.close_calls += 1
        if self.events is not None:
            self.events.append("detector_closed")


@dataclass
class FakeRestorer:
    calls: int = 0
    should_fail: bool = False
    fail_first_only: bool = False
    close_calls: int = 0
    events: list[str] | None = None
    required_event_before_restore: str | None = None

    def restore(
        self,
        image: np.ndarray,
        restoration_mask: np.ndarray,
    ) -> RestorerCandidate:
        self.calls += 1
        if (
            self.required_event_before_restore is not None
            and (
                self.events is None
                or self.required_event_before_restore not in self.events
            )
        ):
            raise RuntimeError(
                f"missing lifecycle event: {self.required_event_before_restore}"
            )
        if self.events is not None:
            self.events.append("restorer_restore")
        if self.should_fail or (self.fail_first_only and self.calls == 1):
            raise RuntimeError("restorer boom")
        candidate = np.zeros_like(image)
        if image.ndim == 2:
            candidate[restoration_mask > 0] = np.iinfo(image.dtype).max
        else:
            candidate[restoration_mask > 0, :] = np.iinfo(image.dtype).max
        return RestorerCandidate(image=candidate, metadata={"name": "fake"})

    def close(self) -> None:
        self.close_calls += 1
        if self.events is not None:
            self.events.append("restorer_closed")


def _pipeline(
    *,
    detector: FakeDetector | None = None,
    restorer: FakeRestorer | None = None,
    input_processing: InputProcessingMode = InputProcessingMode.BW_NEGATIVE,
) -> ProcessingPipeline:
    detector = detector or FakeDetector()
    restorer = restorer or FakeRestorer()
    restoration = AIRestorationProcessor(
        detector_factory=lambda: detector,
        restorer_factories={
            RestorationMode.TELEA: lambda: restorer,
            RestorationMode.LAMA: lambda: restorer,
        },
        mask_config=MaskPostprocessConfig(threshold=0.5, dilation=0, mode="none"),
    )
    if input_processing == InputProcessingMode.ALREADY_POSITIVE:
        processors = [DecodePositiveImageProcessor()]
    else:
        processors = [
            DecodeBWImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
        ]
    processors.append(restoration)
    return ProcessingPipeline(processors)


def _real_telea_pipeline(*, detector: FakeDetector) -> ProcessingPipeline:
    return ProcessingPipeline(
        [
            DecodeBWImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
            AIRestorationProcessor(
                detector_factory=lambda: detector,
                mask_config=MaskPostprocessConfig(threshold=0.5, dilation=1, mode="none"),
            ),
        ]
    )


def _negative(tmp_path: Path, filename: str = "scan.tiff") -> Path:
    return write_image(tmp_path / filename, synthetic_bw_negative_16bit())


def _positive(tmp_path: Path, filename: str = "positive.tiff") -> Path:
    image = np.tile(np.linspace(10000, 50000, 64, dtype=np.uint16), (32, 1))
    return write_image(tmp_path / filename, image)


def test_ai_runtime_site_packages_env_is_added_lazily(tmp_path, monkeypatch):
    runtime_site_packages = tmp_path / "runtime" / "site-packages"
    runtime_site_packages.mkdir(parents=True)
    runtime_text = str(runtime_site_packages.resolve())
    monkeypatch.setenv("FILMPIPE_AI_RUNTIME_SITE_PACKAGES", str(runtime_site_packages))
    monkeypatch.setattr(restoration_module, "_AI_RUNTIME_PATHS_PREPARED", False)

    added: tuple[Path, ...] = ()
    try:
        added = restoration_module.prepare_ai_runtime_paths()

        assert runtime_site_packages.resolve() in added
        assert runtime_text in sys.path
    finally:
        for path in added:
            path_text = str(path)
            while path_text in sys.path:
                sys.path.remove(path_text)
        monkeypatch.setattr(restoration_module, "_AI_RUNTIME_PATHS_PREPARED", False)


def test_restoration_off_skips_restoration_processor_and_only_creates_positive(tmp_path):
    setup_logging(tmp_path / "logs")
    options = ProcessingOptions(restoration=RestorationMode.OFF)
    pipeline = default_pipeline(options)

    assert "ai_restoration" not in [processor.name for processor in pipeline.processors]

    result = process_image(
        _negative(tmp_path),
        options=options,
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=pipeline,
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is None


def test_successful_telea_restoration_preserves_positive_and_creates_restored(tmp_path):
    setup_logging(tmp_path / "logs")
    detector = FakeDetector()
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.TELEA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_real_telea_pipeline(detector=detector),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert detector.calls == 1
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is not None


def test_successful_lama_restoration_uses_fake_adapter_without_gpu_inference(tmp_path):
    setup_logging(tmp_path / "logs")
    detector = FakeDetector()
    restorer = FakeRestorer()
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(detector=detector, restorer=restorer),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert detector.close_calls == 1
    assert restorer.calls == 1
    assert restorer.close_calls == 1
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is not None


def test_positive_input_can_go_directly_to_restoration(tmp_path):
    setup_logging(tmp_path / "logs")
    result = process_image(
        _positive(tmp_path),
        options=ProcessingOptions(
            input_processing=InputProcessingMode.ALREADY_POSITIVE,
            restoration=RestorationMode.LAMA,
        ),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(
            restorer=FakeRestorer(),
            input_processing=InputProcessingMode.ALREADY_POSITIVE,
        ),
        job_id="job-1",
        image_id="image-1",
    )

    original = read_image(result.artifact(ArtifactType.ORIGINAL).path)
    restored = read_image(result.artifact(ArtifactType.RESTORED).path)

    assert result.status == ProcessingStatus.SUCCESS
    assert result.artifact(ArtifactType.POSITIVE) is None
    assert int(original[0, 0]) == 10000
    assert int(original[0, -1]) == 50000
    assert np.array_equal(
        restored[original.shape[0] // 2 - 1, :],
        original[original.shape[0] // 2 - 1, :],
    )


def test_detector_failure_after_positive_is_partial_success(tmp_path):
    setup_logging(tmp_path / "logs")
    detector = FakeDetector(should_fail=True)
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(detector=detector),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert detector.close_calls == 1
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is None
    assert result.errors[0].stage == "defect_detection"
    assert result.errors[0].recoverable is True


def test_missing_pytorch_detector_failure_has_actionable_user_message(tmp_path):
    setup_logging(tmp_path / "logs")
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(
            detector=FakeDetector(
                should_fail=True,
                failure_message="PyTorch is not installed for the Microsoft detector runtime.",
            )
        ),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert "PyTorch не установлен" in result.errors[0].user_message
    assert "Restoration: Off" in result.errors[0].user_message


def test_restorer_failure_after_positive_is_partial_success(tmp_path):
    setup_logging(tmp_path / "logs")
    detector = FakeDetector()
    restorer = FakeRestorer(should_fail=True)
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(detector=detector, restorer=restorer),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.PARTIAL_SUCCESS
    assert detector.close_calls == 1
    assert restorer.close_calls == 1
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert result.artifact(ArtifactType.RESTORED) is None
    assert result.errors[0].stage == "restoration"
    assert result.errors[0].recoverable is True


def test_restoration_off_does_not_create_detector_or_restorer_runtime(tmp_path):
    setup_logging(tmp_path / "logs")
    context = ProcessingContext(
        job_id="job-1",
        image_id="image-1",
        filename="scan.tiff",
        options=ProcessingOptions(restoration=RestorationMode.OFF),
        artifact_store=FileSystemArtifactStore(tmp_path / "jobs"),
        logger=get_logger(job_id="job-1", image_id="image-1"),
        working_positive=np.zeros((4, 4), dtype=np.uint16),
    )
    processor = AIRestorationProcessor(
        detector_factory=lambda: (_ for _ in ()).throw(RuntimeError("detector created")),
        restorer_factories={
            RestorationMode.LAMA: lambda: (_ for _ in ()).throw(
                RuntimeError("restorer created")
            )
        },
    )

    result = processor.process(context.working_positive, context)

    assert not result.errors


def test_detector_cleanup_happens_before_restorer_starts(tmp_path):
    setup_logging(tmp_path / "logs")
    events: list[str] = []
    detector = FakeDetector(events=events)
    restorer = FakeRestorer(
        events=events,
        required_event_before_restore="detector_closed",
    )
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(detector=detector, restorer=restorer),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert events.index("detector_closed") < events.index("restorer_restore")


def test_final_composite_keeps_pixels_outside_restoration_mask_identical(tmp_path):
    setup_logging(tmp_path / "logs")
    result = process_image(
        _negative(tmp_path),
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        pipeline=_pipeline(restorer=FakeRestorer()),
        job_id="job-1",
        image_id="image-1",
    )

    positive = read_image(result.artifact(ArtifactType.POSITIVE).path)
    restored = read_image(result.artifact(ArtifactType.RESTORED).path)
    mask = np.zeros(positive.shape[:2], dtype=bool)
    mask[positive.shape[0] // 2, positive.shape[1] // 2] = True

    assert np.array_equal(restored[~mask], positive[~mask])
    assert np.count_nonzero(restored[mask] != positive[mask]) == 1


def test_batch_restoration_failure_for_one_image_does_not_stop_others(tmp_path):
    setup_logging(tmp_path / "logs")
    restorer = FakeRestorer(fail_first_only=True)
    storage = FileSystemArtifactStore(tmp_path / "jobs")
    service = JobService(
        storage=storage,
        pipeline_factory=lambda: _pipeline(restorer=restorer),
    )
    first = _negative(tmp_path, "first.tiff")
    second = _negative(tmp_path, "second.tiff")

    job = service.process(
        [first, second],
        options=ProcessingOptions(restoration=RestorationMode.LAMA),
        job_id="job-1",
    )

    assert job.status == ProcessingStatus.PARTIAL_SUCCESS
    assert [result.status for result in job.results] == [
        ProcessingStatus.PARTIAL_SUCCESS,
        ProcessingStatus.SUCCESS,
    ]
    assert job.results[0].artifact(ArtifactType.RESTORED) is None
    assert job.results[1].artifact(ArtifactType.RESTORED) is not None
