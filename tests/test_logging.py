from __future__ import annotations

from filmpipe.domain.models import ArtifactType, ImageProcessingResult, ProcessingOptions, ProcessingStatus
from filmpipe.domain.processor import ProcessingContext
from filmpipe.infrastructure.logging import get_logger, setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors import FailingProcessor, PositiveArtifactStubProcessor


def test_user_error_excludes_stack_trace_and_log_keeps_context(tmp_path):
    log_path = setup_logging(tmp_path / "logs")
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
    pipeline = ProcessingPipeline(
        [
            PositiveArtifactStubProcessor(),
            FailingProcessor(name="restoration_stub", optional=True),
        ]
    )

    output = pipeline.run(source, context, result)
    log_text = log_path.read_text(encoding="utf-8")

    assert "Traceback" not in output.errors[0].user_message
    assert "job_id=job-1" in log_text
    assert "image_id=image-1" in log_text
    assert "processor=restoration_stub" in log_text
    assert "Traceback" in log_text
