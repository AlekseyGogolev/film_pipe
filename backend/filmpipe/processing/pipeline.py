from __future__ import annotations

from typing import Any, Iterable

from filmpipe.domain.models import ArtifactType, ImageProcessingResult, ProcessingError, ProcessingStatus
from filmpipe.domain.processor import ProcessingContext, Processor
from filmpipe.infrastructure.logging import get_logger


class ProcessingPipeline:
    def __init__(self, processors: Iterable[Processor]) -> None:
        self.processors = list(processors)

    def run(
        self,
        initial_image: Any,
        context: ProcessingContext,
        result: ImageProcessingResult | None = None,
    ) -> ImageProcessingResult:
        image_result = result or ImageProcessingResult(
            image_id=context.image_id,
            filename=context.filename,
            status=ProcessingStatus.RUNNING,
        )
        current_image = initial_image
        stopped_by_failure = False

        for processor in self.processors:
            processor_logger = get_logger(
                job_id=context.job_id,
                image_id=context.image_id,
                processor=processor.name,
            )
            processor_logger.info("processor_started")

            try:
                processor_result = processor.process(current_image, context)
            except Exception as exc:
                processor_logger.exception("processor_failed")
                image_result.add_error(
                    ProcessingError.from_exception(
                        stage=processor.name,
                        user_message=(
                            f"Не удалось обработать {context.filename} "
                            f"на этапе {processor.name}."
                        ),
                        exc=exc,
                        recoverable=processor.optional,
                    )
                )
                if processor.optional:
                    continue
                stopped_by_failure = True
                break

            for artifact in processor_result.artifacts:
                image_result.add_artifact(artifact)
                context.artifacts[artifact.type] = artifact

            for error in processor_result.errors:
                image_result.add_error(error)
                processor_logger.error(
                    "processor_reported_error stage=%s recoverable=%s message=%s",
                    error.stage,
                    error.recoverable,
                    error.technical_message or error.user_message,
                )

            if processor_result.image is not None:
                current_image = processor_result.image

            processor_logger.info("processor_completed")

            if processor_result.stop_pipeline:
                stopped_by_failure = not processor.optional
                break

        image_result.status = self._resolve_status(image_result, stopped_by_failure)
        return image_result

    @staticmethod
    def _resolve_status(
        result: ImageProcessingResult,
        stopped_by_failure: bool,
    ) -> ProcessingStatus:
        if stopped_by_failure:
            return ProcessingStatus.PARTIAL_SUCCESS if result.has_positive else ProcessingStatus.FAILED

        if result.errors:
            return ProcessingStatus.PARTIAL_SUCCESS if result.has_positive else ProcessingStatus.FAILED

        return ProcessingStatus.SUCCESS
