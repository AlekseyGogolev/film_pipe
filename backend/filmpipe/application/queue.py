from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import RLock

from filmpipe.application.jobs import JobRegistry, JobService
from filmpipe.domain.models import (
    ImageProcessingResult,
    ProcessingError,
    ProcessingJob,
    ProcessingStatus,
    utc_now,
)
from filmpipe.infrastructure.logging import get_logger


class JobQueue:
    """Single-process queue for local long-running FilmPipe jobs."""

    def __init__(
        self,
        *,
        service: JobService,
        registry: JobRegistry,
        max_workers: int = 1,
    ) -> None:
        self.service = service
        self.registry = registry
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="filmpipe-job",
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = RLock()

    def enqueue(self, job_id: str) -> Future[None]:
        with self._lock:
            future = self._executor.submit(self._run_job, job_id)
            self._futures[job_id] = future
            future.add_done_callback(lambda completed: self._forget(job_id, completed))
            return future

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _run_job(self, job_id: str) -> None:
        logger = get_logger(job_id=job_id)
        job = self.registry.get(job_id)
        if job is None:
            logger.error("queued_job_missing")
            return

        logger.info(
            "queued_job_started input_processing=%s restoration=%s final_processing=%s inputs=%s",
            job.options.input_processing.value,
            job.options.restoration.value,
            job.options.final_processing.value,
            len(job.inputs),
        )

        try:
            self.registry.update(job_id, _mark_job_running)
            inputs = list(job.inputs)
            image_ids = _image_ids_for(job)
            for index, input_path in enumerate(inputs):
                image_id = image_ids[index]
                self.registry.update(
                    job_id,
                    lambda current_job, current_image_id=image_id: _mark_image_running(
                        current_job,
                        current_image_id,
                    ),
                )
                image_result = self.service.process_image(
                    input_path,
                    options=job.options,
                    job_id=job.id,
                    image_id=image_id,
                )
                self.registry.update(
                    job_id,
                    lambda current_job, current_result=image_result: _replace_image(
                        current_job,
                        current_result,
                    ),
                )

            completed = self.registry.update(job_id, _recompute_job_status)
            if completed is not None:
                logger.info("queued_job_completed", extra={"status": completed.status.value})
        except Exception as exc:
            logger.exception("queued_job_unhandled_failed")
            self.registry.update(
                job_id,
                lambda current_job: _mark_job_failed(current_job, exc),
            )

    def _forget(self, job_id: str, future: Future[None]) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
        try:
            future.result()
        except Exception:
            get_logger(job_id=job_id).exception("queued_job_future_failed")


def _image_ids_for(job: ProcessingJob) -> list[str]:
    if len(job.results) >= len(job.inputs):
        return [result.image_id for result in job.results[: len(job.inputs)]]
    return [result.image_id for result in job.results]


def _mark_job_running(job: ProcessingJob) -> None:
    job.status = ProcessingStatus.RUNNING
    job.updated_at = utc_now()


def _mark_image_running(job: ProcessingJob, image_id: str) -> None:
    image = _find_image(job, image_id)
    if image is None:
        return
    image.status = ProcessingStatus.RUNNING
    job.recompute_status()


def _replace_image(job: ProcessingJob, image_result: ImageProcessingResult) -> None:
    for index, current_result in enumerate(job.results):
        if current_result.image_id == image_result.image_id:
            job.results[index] = image_result
            job.recompute_status()
            return
    job.results.append(image_result)
    job.recompute_status()


def _recompute_job_status(job: ProcessingJob) -> None:
    job.recompute_status()


def _mark_job_failed(job: ProcessingJob, exc: Exception) -> None:
    error = ProcessingError.from_exception(
        stage="job_worker",
        user_message="Фоновая обработка job завершилась ошибкой.",
        exc=exc,
        recoverable=False,
    )
    job.errors.append(error)
    for index, result in enumerate(job.results):
        if result.status not in {
            ProcessingStatus.PENDING,
            ProcessingStatus.RUNNING,
        }:
            continue
        failed = ImageProcessingResult(
            image_id=result.image_id,
            filename=result.filename or _input_filename(job, index),
            status=ProcessingStatus.FAILED,
            artifacts=list(result.artifacts),
            errors=[*result.errors, error],
        )
        job.results[index] = failed
    job.status = ProcessingStatus.FAILED
    job.updated_at = utc_now()


def _find_image(job: ProcessingJob, image_id: str) -> ImageProcessingResult | None:
    return next((result for result in job.results if result.image_id == image_id), None)


def _input_filename(job: ProcessingJob, index: int) -> str:
    try:
        return Path(job.inputs[index]).name
    except IndexError:
        return f"image-{index}"
