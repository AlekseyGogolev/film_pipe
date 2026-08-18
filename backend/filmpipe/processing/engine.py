from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from filmpipe.domain.models import (
    ArtifactType,
    FinalProcessingMode,
    ImageProcessingResult,
    InputProcessingMode,
    ProcessingError,
    ProcessingOptions,
    ProcessingStatus,
    RestorationMode,
)
from filmpipe.domain.processor import ProcessingContext, Processor
from filmpipe.infrastructure.logging import get_logger
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.pipeline import ProcessingPipeline
from filmpipe.processing.processors import (
    AIRestorationProcessor,
    DecodeBWImageProcessor,
    DecodePositiveImageProcessor,
    GenerativeProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)


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
    pipeline = pipeline or default_pipeline(options)

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

    logger.info(
        "pipeline_ready input_processing=%s restoration=%s final_processing=%s processors=%s",
        options.input_processing.value,
        options.restoration.value,
        options.final_processing.value,
        ",".join(processor.name for processor in pipeline.processors),
    )
    result = pipeline.run(original.path, context, result)
    logger.info("image_processing_completed", extra={"status": result.status.value})
    return result


def default_pipeline(options: ProcessingOptions | None = None) -> ProcessingPipeline:
    return ProcessingPipeline(default_processors(options))


def default_processors(options: ProcessingOptions | None = None) -> list[Processor]:
    options = options or ProcessingOptions()

    if options.input_processing == InputProcessingMode.ALREADY_POSITIVE:
        processors: list[Processor] = [DecodePositiveImageProcessor()]
    elif options.input_processing == InputProcessingMode.BW_NEGATIVE:
        processors = [
            DecodeBWImageProcessor(),
            NegativeConverterProcessor(),
            ToneNormalizerProcessor(),
            PositiveArtifactWriterProcessor(),
        ]
    else:
        raise ValueError(
            f"Input processing mode {options.input_processing.value} is not implemented."
        )

    if options.restoration != RestorationMode.OFF:
        processors.append(AIRestorationProcessor())
    if options.final_processing == FinalProcessingMode.CREATIVE:
        processors.append(GenerativeProcessor())
    return processors
