from __future__ import annotations

from filmpipe.domain.models import ArtifactType, ImageProcessingResult, ProcessingOptions, ProcessingStatus
from filmpipe.domain.processor import ProcessingContext
from filmpipe.infrastructure.logging import get_logger, setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors import FailingProcessor, PositiveArtifactStubProcessor


def _context(tmp_path, processors):
    setup_logging(tmp_path / "logs")
    source = tmp_path / "scan.txt"
    source.write_bytes(b"negative")
    store = FileSystemArtifactStore(tmp_path / "jobs")
    original = store.save_original("job-1", "image-1", source)
    context = ProcessingContext(
        job_id="job-1",
        image_id="image-1",
        filename=source.name,
        options=ProcessingOptions(),
        artifact_store=store,
        logger=get_logger(job_id="job-1", image_id="image-1"),
        artifacts={ArtifactType.ORIGINAL: original},
    )
    result = ImageProcessingResult(
        image_id="image-1",
        filename=source.name,
        status=ProcessingStatus.RUNNING,
        artifacts=[original],
    )
    return source, context, result, ProcessingPipeline(processors)


def test_pipeline_success_creates_positive(tmp_path):
    source, context, result, pipeline = _context(tmp_path, [PositiveArtifactStubProcessor()])

    output = pipeline.run(source, context, result)

    assert output.status == ProcessingStatus.SUCCESS
    assert output.artifact(ArtifactType.ORIGINAL) is not None
    assert output.artifact(ArtifactType.POSITIVE) is not None
    assert not output.errors


def test_pipeline_mandatory_failure_before_positive_fails_image(tmp_path):
    source, context, result, pipeline = _context(
        tmp_path,
        [FailingProcessor(name="decode", optional=False)],
    )

    output = pipeline.run(source, context, result)

    assert output.status == ProcessingStatus.FAILED
    assert output.artifact(ArtifactType.POSITIVE) is None
    assert output.errors[0].stage == "decode"


def test_pipeline_optional_failure_preserves_positive_as_partial_success(tmp_path):
    source, context, result, pipeline = _context(
        tmp_path,
        [
            PositiveArtifactStubProcessor(),
            FailingProcessor(name="restoration_stub", optional=True),
        ],
    )

    output = pipeline.run(source, context, result)

    assert output.status == ProcessingStatus.PARTIAL_SUCCESS
    assert output.artifact(ArtifactType.POSITIVE) is not None
    assert output.errors[0].recoverable is True
