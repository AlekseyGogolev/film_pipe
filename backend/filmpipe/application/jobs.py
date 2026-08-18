from __future__ import annotations

from collections.abc import Callable, Iterable
from inspect import Parameter, signature
from pathlib import Path
from threading import RLock
from typing import Protocol
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
from filmpipe.processing.engine import default_pipeline, process_image as process_single_image
from filmpipe.processing.pipeline import ProcessingPipeline


class JobRegistry(Protocol):
    def save(self, job: ProcessingJob) -> ProcessingJob: ...

    def get(self, job_id: str) -> ProcessingJob | None: ...

    def list(self) -> list[ProcessingJob]: ...

    def update(
        self,
        job_id: str,
        mutate: Callable[[ProcessingJob], None],
    ) -> ProcessingJob | None: ...


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

    def update(
        self,
        job_id: str,
        mutate: Callable[[ProcessingJob], None],
    ) -> ProcessingJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            mutate(job)
            self._jobs[job.id] = job
            return job


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
        job = self.create_pending_job(inputs, options=options, job_id=job_id)
        job.status = ProcessingStatus.RUNNING

        logger = get_logger(job_id=job.id)
        logger.info(
            "job_started input_processing=%s restoration=%s final_processing=%s inputs=%s",
            options.input_processing.value,
            options.restoration.value,
            options.final_processing.value,
            len(job.inputs),
        )

        for index, input_path in enumerate(job.inputs):
            image_id = job.results[index].image_id
            job.results[index] = self.process_image(
                input_path,
                options=options,
                job_id=job.id,
                image_id=image_id,
            )

        job.recompute_status()
        logger.info("job_completed", extra={"status": job.status.value})
        return job

    def create_pending_job(
        self,
        inputs: Iterable[Path | str],
        *,
        options: ProcessingOptions | None = None,
        job_id: str | None = None,
    ) -> ProcessingJob:
        input_paths = [Path(input_path) for input_path in inputs]
        results = [
            ImageProcessingResult(
                image_id=uuid4().hex,
                filename=input_path.name,
                status=ProcessingStatus.PENDING,
            )
            for input_path in input_paths
        ]
        return ProcessingJob(
            id=job_id or uuid4().hex,
            inputs=input_paths,
            options=options or ProcessingOptions(),
            status=ProcessingStatus.PENDING,
            results=results,
        )

    def process_image(
        self,
        input_path: Path | str,
        *,
        options: ProcessingOptions | None = None,
        job_id: str,
        image_id: str,
    ) -> ImageProcessingResult:
        source = Path(input_path)
        options = options or ProcessingOptions()
        try:
            return process_single_image(
                source,
                options=options,
                storage=self.storage,
                pipeline=self._pipeline_for(options),
                job_id=job_id,
                image_id=image_id,
            )
        except Exception as exc:
            image_logger = get_logger(job_id=job_id, image_id=image_id)
            image_logger.exception("image_processing_unhandled_failed")
            image_result = ImageProcessingResult(
                image_id=image_id,
                filename=source.name,
                status=ProcessingStatus.FAILED,
            )
            image_result.add_error(
                ProcessingError.from_exception(
                    stage="job",
                    user_message=f"Не удалось обработать {source.name}.",
                    exc=exc,
                    recoverable=False,
                )
            )
            return image_result

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
