from __future__ import annotations

from pathlib import Path

from filmpipe.domain.models import (
    ImageProcessingResult,
    ProcessingJob,
    ProcessingOptions,
    ProcessingStatus,
)


def _job(*statuses: ProcessingStatus) -> ProcessingJob:
    return ProcessingJob(
        id="job-1",
        inputs=[Path(f"image-{index}.txt") for index, _ in enumerate(statuses)],
        options=ProcessingOptions(),
        selected_modes=[],
        results=[
            ImageProcessingResult(
                image_id=f"image-{index}",
                filename=f"image-{index}.txt",
                status=status,
            )
            for index, status in enumerate(statuses)
        ],
    )


def test_job_status_all_success():
    job = _job(ProcessingStatus.SUCCESS, ProcessingStatus.SUCCESS)

    assert job.recompute_status() == ProcessingStatus.SUCCESS


def test_job_status_all_failed():
    job = _job(ProcessingStatus.FAILED, ProcessingStatus.FAILED)

    assert job.recompute_status() == ProcessingStatus.FAILED


def test_job_status_mixed_partial_success():
    job = _job(ProcessingStatus.SUCCESS, ProcessingStatus.FAILED)

    assert job.recompute_status() == ProcessingStatus.PARTIAL_SUCCESS
