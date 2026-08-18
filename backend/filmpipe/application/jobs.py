from __future__ import annotations

from collections.abc import Callable, Iterable
from inspect import Parameter, signature
from pathlib import Path
from threading import RLock
from uuid import uuid4

from filmpipe.domain.models import (
    ImageProcessingResult,
    ProcessingError,
    ProcessingJob,
    ProcessingOptions,
    ProcessingStatus,
)
from filmpipe.infrastructure.logging import get_logger
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import default_pipeline, process_image
from filmpipe.processing.pipeline import ProcessingPipeline


class InMemoryJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, ProcessingJob] = {}
        self._lock = RLock()

    def save(self, job: ProcessingJob) -> ProcessingJob:
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ProcessingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[ProcessingJob]:
        with self._lock:
            return list(self._jobs.values())


class JobService:
    def __init__(
        self,
        *,
        storage: FileSystemArtifactStore | None = None,
        pipeline_factory: Callable[..., ProcessingPipeline] = default_pipeline,
    ) -> None:
        self.storage = storage or FileSystemArtifactStore()
        self.pipeline_factory = pipeline_factory

    def process(
        self,
        inputs: Iterable[Path | str],
        *,
        options: ProcessingOptions | None = None,
        job_id: str | None = None,
    ) -> ProcessingJob:
        options = options or ProcessingOptions()
        input_paths = [Path(input_path) for input_path in inputs]
        job = ProcessingJob(
            id=job_id or uuid4().hex,
            inputs=input_paths,
            options=options,
            status=ProcessingStatus.RUNNING,
        )

        logger = get_logger(job_id=job.id)
        logger.info(
            "job_started input_processing=%s restoration=%s final_processing=%s inputs=%s",
            options.input_processing.value,
            options.restoration.value,
            options.final_processing.value,
            len(input_paths),
        )

        for input_path in input_paths:
            image_id = uuid4().hex
            try:
                image_result = process_image(
                    input_path,
                    options=options,
                    storage=self.storage,
                    pipeline=self._pipeline_for(options),
                    job_id=job.id,
                    image_id=image_id,
                )
            except Exception as exc:
                image_logger = get_logger(job_id=job.id, image_id=image_id)
                image_logger.exception("image_processing_unhandled_failed")
                image_result = ImageProcessingResult(
                    image_id=image_id,
                    filename=input_path.name,
                    status=ProcessingStatus.FAILED,
                )
                image_result.add_error(
                    ProcessingError.from_exception(
                        stage="job",
                        user_message=f"Не удалось обработать {input_path.name}.",
                        exc=exc,
                        recoverable=False,
                    )
                )
            job.results.append(image_result)

        job.recompute_status()
        logger.info("job_completed", extra={"status": job.status.value})
        return job

    def _pipeline_for(self, options: ProcessingOptions) -> ProcessingPipeline:
        try:
            parameters = signature(self.pipeline_factory).parameters.values()
        except (TypeError, ValueError):
            return self.pipeline_factory(options)

        accepts_positional = any(
            parameter.kind
            in {
                Parameter.POSITIONAL_ONLY,
                Parameter.POSITIONAL_OR_KEYWORD,
                Parameter.VAR_POSITIONAL,
            }
            for parameter in parameters
        )
        if accepts_positional:
            return self.pipeline_factory(options)
        accepts_options_keyword = any(
            parameter.kind == Parameter.VAR_KEYWORD or parameter.name == "options"
            for parameter in parameters
        )
        if accepts_options_keyword:
            return self.pipeline_factory(options=options)
        return self.pipeline_factory()
