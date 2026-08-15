from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import uuid4

from filmpipe.domain.models import ProcessingJob, ProcessingMode, ProcessingOptions, ProcessingStatus
from filmpipe.infrastructure.logging import get_logger
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import default_pipeline, process_image
from filmpipe.processing.pipeline import ProcessingPipeline


class JobService:
    def __init__(
        self,
        *,
        storage: FileSystemArtifactStore | None = None,
        pipeline_factory: Callable[[], ProcessingPipeline] = default_pipeline,
    ) -> None:
        self.storage = storage or FileSystemArtifactStore()
        self.pipeline_factory = pipeline_factory

    def process(
        self,
        inputs: Iterable[Path | str],
        *,
        options: ProcessingOptions | None = None,
        selected_modes: list[ProcessingMode] | None = None,
        job_id: str | None = None,
    ) -> ProcessingJob:
        options = options or ProcessingOptions()
        input_paths = [Path(input_path) for input_path in inputs]
        job = ProcessingJob(
            id=job_id or uuid4().hex,
            inputs=input_paths,
            options=options,
            selected_modes=selected_modes or [options.mode],
            status=ProcessingStatus.RUNNING,
        )

        logger = get_logger(job_id=job.id)
        logger.info("job_started")

        for input_path in input_paths:
            image_result = process_image(
                input_path,
                options=options,
                storage=self.storage,
                pipeline=self.pipeline_factory(),
                job_id=job.id,
                image_id=uuid4().hex,
            )
            job.results.append(image_result)

        job.recompute_status()
        logger.info("job_completed", extra={"status": job.status.value})
        return job
