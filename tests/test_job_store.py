from __future__ import annotations

import json

from filmpipe.domain.models import (
    Artifact,
    ArtifactType,
    ImageProcessingResult,
    ProcessingJob,
    ProcessingOptions,
    ProcessingStatus,
)
from filmpipe.infrastructure.job_store import FileSystemJobRegistry, job_to_manifest


def test_job_manifest_roundtrip_uses_relative_artifact_paths(tmp_path):
    jobs_root = tmp_path / "jobs"
    artifact_path = jobs_root / "job-1" / "image-1" / "positive" / "scan_positive.tiff"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"positive")
    job = ProcessingJob(
        id="job-1",
        inputs=[tmp_path / "upload" / "scan.tiff"],
        options=ProcessingOptions(),
        status=ProcessingStatus.SUCCESS,
        results=[
            ImageProcessingResult(
                image_id="image-1",
                filename="scan.tiff",
                status=ProcessingStatus.SUCCESS,
                artifacts=[
                    Artifact(
                        type=ArtifactType.POSITIVE,
                        job_id="job-1",
                        image_id="image-1",
                        path=artifact_path,
                        filename="scan_positive.tiff",
                        mime_type="image/tiff",
                    )
                ],
            )
        ],
    )
    registry = FileSystemJobRegistry(jobs_root)

    registry.save(job)

    manifest = json.loads((jobs_root / "job-1" / "job.json").read_text("utf-8"))
    artifact_manifest = manifest["images"][0]["artifacts"][0]
    assert artifact_manifest["relative_path"] == "image-1/positive/scan_positive.tiff"
    assert "path" not in artifact_manifest
    assert not manifest["inputs"]

    reloaded = FileSystemJobRegistry(jobs_root)
    restored = reloaded.get("job-1")
    assert restored is not None
    assert restored.results[0].artifacts[0].path == artifact_path.resolve()


def test_legacy_job_directory_is_reconstructed(tmp_path):
    jobs_root = tmp_path / "jobs"
    original = jobs_root / "legacy-job" / "image-1" / "original" / "scan.tiff"
    positive = jobs_root / "legacy-job" / "image-1" / "positive" / "scan_positive.tiff"
    original.parent.mkdir(parents=True)
    positive.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    positive.write_bytes(b"positive")

    registry = FileSystemJobRegistry(jobs_root)

    job = registry.get("legacy-job")
    assert job is not None
    assert job.legacy is True
    assert job.status == ProcessingStatus.SUCCESS
    assert job.inputs == [original]
    assert [artifact.type for artifact in job.results[0].artifacts] == [
        ArtifactType.ORIGINAL,
        ArtifactType.POSITIVE,
    ]


def test_job_to_manifest_falls_back_to_original_artifacts_for_transient_inputs(tmp_path):
    jobs_root = tmp_path / "jobs"
    original = jobs_root / "job-1" / "image-1" / "original" / "scan.tiff"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    job = ProcessingJob(
        id="job-1",
        inputs=[tmp_path / "temp" / "scan.tiff"],
        options=ProcessingOptions(),
        status=ProcessingStatus.SUCCESS,
        results=[
            ImageProcessingResult(
                image_id="image-1",
                filename="scan.tiff",
                status=ProcessingStatus.SUCCESS,
                artifacts=[
                    Artifact(
                        type=ArtifactType.ORIGINAL,
                        job_id="job-1",
                        image_id="image-1",
                        path=original,
                        filename="scan.tiff",
                    )
                ],
            )
        ],
    )

    manifest = job_to_manifest(job, jobs_root)

    assert manifest["inputs"] == ["image-1/original/scan.tiff"]
