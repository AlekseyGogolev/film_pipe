from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from filmpipe.domain.models import (
    ArtifactType,
    ImageProcessingResult,
    ProcessingError,
    ProcessingOptions,
    ProcessingStatus,
)
from filmpipe.domain.processor import ProcessingContext
from filmpipe.infrastructure.logging import get_logger
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors.stubs import PositiveArtifactStubProcessor


def process_image(
    input_path: Path | str,
    *,
    options: ProcessingOptions | None = None,
    storage: FileSystemArtifactStore | None = None,
    pipeline: ProcessingPipeline | None = None,
    job_id: str | None = None,
    image_id: str | None = None,
) -> ImageProcessingResult:
    source = Path(input_path)
    job_id = job_id or uuid4().hex
    image_id = image_id or uuid4().hex
    options = options or ProcessingOptions()
    storage = storage or FileSystemArtifactStore()
    pipeline = pipeline or default_pipeline()

    logger = get_logger(job_id=job_id, image_id=image_id)
    result = ImageProcessingResult(
        image_id=image_id,
        filename=source.name,
        status=ProcessingStatus.RUNNING,
    )

    logger.info("image_processing_started")
    try:
        original = storage.save_original(job_id, image_id, source)
    except Exception as exc:
        logger.exception("original_storage_failed")
        result.add_error(
            ProcessingError.from_exception(
                stage="original_storage",
                user_message=f"Не удалось подготовить исходный файл {source.name}.",
                exc=exc,
                recoverable=False,
            )
        )
        result.status = ProcessingStatus.FAILED
        return result

    result.add_artifact(original)
    context = ProcessingContext(
        job_id=job_id,
        image_id=image_id,
        filename=source.name,
        options=options,
        artifact_store=storage,
        logger=logger,
        artifacts={ArtifactType.ORIGINAL: original},
    )

    result = pipeline.run(original.path, context, result)
    logger.info("image_processing_completed", extra={"status": result.status.value})
    return result


def default_pipeline() -> ProcessingPipeline:
    return ProcessingPipeline(processors=[PositiveArtifactStubProcessor()])
