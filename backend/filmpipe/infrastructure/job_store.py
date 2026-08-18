from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import mimetypes
from pathlib import Path
from threading import RLock
from typing import Any

from filmpipe.domain.models import (
    Artifact,
    ArtifactType,
    FinalProcessingMode,
    ImageProcessingResult,
    InputProcessingMode,
    ProcessingError,
    ProcessingJob,
    ProcessingOptions,
    ProcessingStatus,
    RestorationMode,
    utc_now,
)
from filmpipe.infrastructure.storage import _safe_segment


MANIFEST_FILENAME = "job.json"
SCHEMA_VERSION = 1


class JobStoreError(RuntimeError):
    pass


class FileSystemJobRegistry:
    """Filesystem-backed registry that keeps API-safe job manifests."""

    def __init__(self, root: Path | str = Path("data/jobs")) -> None:
        self.root = Path(root)
        self._jobs: dict[str, ProcessingJob] = {}
        self._lock = RLock()
        self._load_jobs()

    def save(self, job: ProcessingJob) -> ProcessingJob:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            job_dir = _job_dir(self.root, job.id)
            job_dir.mkdir(parents=True, exist_ok=True)
            manifest = job_to_manifest(job, self.root)
            _write_json_atomic(job_dir / MANIFEST_FILENAME, manifest)
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ProcessingJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[ProcessingJob]:
        with self._lock:
            return sorted(
                self._jobs.values(),
                key=lambda job: (job.created_at, job.updated_at, job.id),
                reverse=True,
            )

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
            return self.save(job)

    def _load_jobs(self) -> None:
        with self._lock:
            if not self.root.is_dir():
                return

            jobs: dict[str, ProcessingJob] = {}
            for job_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
                manifest_path = job_dir / MANIFEST_FILENAME
                try:
                    job = (
                        load_job_manifest(manifest_path, self.root)
                        if manifest_path.is_file()
                        else load_legacy_job(job_dir, self.root)
                    )
                except Exception:
                    continue
                if job is not None:
                    jobs[job.id] = job
            self._jobs = jobs


def job_to_manifest(job: ProcessingJob, jobs_root: Path | str) -> dict[str, Any]:
    root = Path(jobs_root)
    job_dir = _job_dir(root, job.id)
    return {
        "schema_version": SCHEMA_VERSION,
        "id": job.id,
        "status": job.status.value,
        "input_processing": job.options.input_processing.value,
        "restoration": job.options.restoration.value,
        "final_processing": job.options.final_processing.value,
        "creative_prompt": job.options.creative_prompt,
        "created_at": _datetime_to_json(job.created_at),
        "updated_at": _datetime_to_json(job.updated_at),
        "inputs": _job_input_paths(job, job_dir),
        "images": [_image_to_manifest(job_dir, result) for result in job.results],
        "errors": [_error_to_manifest(error) for error in job.errors],
        "legacy": job.legacy,
    }


def load_job_manifest(manifest_path: Path | str, jobs_root: Path | str) -> ProcessingJob:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise JobStoreError("Job manifest must be a JSON object.")
    return job_from_manifest(manifest, jobs_root)


def job_from_manifest(manifest: dict[str, Any], jobs_root: Path | str) -> ProcessingJob:
    root = Path(jobs_root)
    job_id = str(manifest["id"])
    job_dir = _job_dir(root, job_id)
    options = ProcessingOptions(
        input_processing=_enum_value(
            InputProcessingMode,
            manifest.get("input_processing"),
            InputProcessingMode.BW_NEGATIVE,
        ),
        restoration=_enum_value(
            RestorationMode,
            manifest.get("restoration"),
            RestorationMode.OFF,
        ),
        final_processing=_enum_value(
            FinalProcessingMode,
            manifest.get("final_processing"),
            FinalProcessingMode.STANDARD,
        ),
        creative_prompt=_optional_str(manifest.get("creative_prompt")),
    )
    return ProcessingJob(
        id=job_id,
        inputs=[
            _resolve_relative_path(job_dir, relative_path)
            for relative_path in _string_list(manifest.get("inputs"))
        ],
        options=options,
        status=_enum_value(
            ProcessingStatus,
            manifest.get("status"),
            ProcessingStatus.PENDING,
        ),
        results=[
            _image_from_manifest(job_id, job_dir, image)
            for image in _object_list(manifest.get("images"))
        ],
        errors=[
            _error_from_manifest(error)
            for error in _object_list(manifest.get("errors"))
        ],
        created_at=_datetime_from_json(manifest.get("created_at")),
        updated_at=_datetime_from_json(manifest.get("updated_at")),
        legacy=bool(manifest.get("legacy", False)),
    )


def load_legacy_job(job_dir: Path | str, _jobs_root: Path | str) -> ProcessingJob | None:
    directory = Path(job_dir)
    if not directory.is_dir():
        return None

    job_id = directory.name
    results = [
        result
        for image_dir in sorted(path for path in directory.iterdir() if path.is_dir())
        if image_dir.name != "inputs"
        for result in [_legacy_image_result(job_id, image_dir)]
        if result is not None
    ]
    if not results:
        return None

    artifact_paths = [
        artifact.path
        for result in results
        for artifact in result.artifacts
        if artifact.path.is_file()
    ]
    created_at, updated_at = _inferred_job_times(directory, artifact_paths)
    job = ProcessingJob(
        id=job_id,
        inputs=[
            artifact.path
            for result in results
            for artifact in result.artifacts
            if artifact.type == ArtifactType.ORIGINAL
        ],
        options=ProcessingOptions(),
        status=ProcessingStatus.PENDING,
        results=results,
        created_at=created_at,
        updated_at=updated_at,
        legacy=True,
    )
    job.recompute_status()
    job.created_at = created_at
    job.updated_at = updated_at
    return job


def _image_to_manifest(
    job_dir: Path,
    result: ImageProcessingResult,
) -> dict[str, Any]:
    return {
        "id": result.image_id,
        "filename": result.filename,
        "status": result.status.value,
        "artifacts": [
            _artifact_to_manifest(job_dir, artifact) for artifact in result.artifacts
        ],
        "errors": [_error_to_manifest(error) for error in result.errors],
    }


def _image_from_manifest(
    job_id: str,
    job_dir: Path,
    manifest: dict[str, Any],
) -> ImageProcessingResult:
    image_id = str(manifest["id"])
    return ImageProcessingResult(
        image_id=image_id,
        filename=str(manifest.get("filename") or image_id),
        status=_enum_value(
            ProcessingStatus,
            manifest.get("status"),
            ProcessingStatus.PENDING,
        ),
        artifacts=[
            _artifact_from_manifest(job_id, image_id, job_dir, artifact)
            for artifact in _object_list(manifest.get("artifacts"))
        ],
        errors=[
            _error_from_manifest(error)
            for error in _object_list(manifest.get("errors"))
        ],
    )


def _artifact_to_manifest(job_dir: Path, artifact: Artifact) -> dict[str, Any]:
    relative_path = _relative_to_job_dir(artifact.path, job_dir)
    if relative_path is None:
        fallback = Path(artifact.image_id) / artifact.type.value / artifact.filename
        relative_path = fallback.as_posix()
    return {
        "type": artifact.type.value,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "relative_path": relative_path,
        "created_at": _datetime_to_json(artifact.created_at),
    }


def _artifact_from_manifest(
    job_id: str,
    image_id: str,
    job_dir: Path,
    manifest: dict[str, Any],
) -> Artifact:
    artifact_type = _enum_value(
        ArtifactType,
        manifest.get("type"),
        ArtifactType.ORIGINAL,
    )
    filename = str(manifest.get("filename") or "artifact")
    relative_path = manifest.get("relative_path")
    if relative_path is None:
        relative_path = Path(image_id) / artifact_type.value / filename
    path = _resolve_relative_path(job_dir, str(relative_path))
    return Artifact(
        type=artifact_type,
        job_id=job_id,
        image_id=image_id,
        path=path,
        filename=filename,
        mime_type=str(
            manifest.get("mime_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        ),
        created_at=_datetime_from_json(manifest.get("created_at")),
    )


def _error_to_manifest(error: ProcessingError) -> dict[str, Any]:
    return {
        "stage": error.stage,
        "user_message": error.user_message,
        "technical_message": error.technical_message,
        "recoverable": error.recoverable,
        "exception_type": error.exception_type,
    }


def _error_from_manifest(manifest: dict[str, Any]) -> ProcessingError:
    return ProcessingError(
        stage=str(manifest.get("stage") or "job"),
        user_message=str(
            manifest.get("user_message")
            or manifest.get("message")
            or "Произошла ошибка обработки."
        ),
        technical_message=_optional_str(manifest.get("technical_message")),
        recoverable=bool(manifest.get("recoverable", False)),
        exception_type=_optional_str(manifest.get("exception_type")),
    )


def _legacy_image_result(job_id: str, image_dir: Path) -> ImageProcessingResult | None:
    artifacts: list[Artifact] = []
    for artifact_type in ArtifactType:
        artifact_dir = image_dir / artifact_type.value
        if not artifact_dir.is_dir():
            continue
        for artifact_path in sorted(path for path in artifact_dir.iterdir() if path.is_file()):
            artifacts.append(_legacy_artifact(job_id, image_dir.name, artifact_type, artifact_path))

    if not artifacts:
        return None

    original = next(
        (artifact for artifact in artifacts if artifact.type == ArtifactType.ORIGINAL),
        None,
    )
    filename = original.filename if original is not None else artifacts[0].filename
    has_generated = any(artifact.type != ArtifactType.ORIGINAL for artifact in artifacts)
    return ImageProcessingResult(
        image_id=image_dir.name,
        filename=filename,
        status=ProcessingStatus.SUCCESS if has_generated or original is not None else ProcessingStatus.FAILED,
        artifacts=artifacts,
    )


def _legacy_artifact(
    job_id: str,
    image_id: str,
    artifact_type: ArtifactType,
    artifact_path: Path,
) -> Artifact:
    return Artifact(
        type=artifact_type,
        job_id=job_id,
        image_id=image_id,
        path=artifact_path,
        filename=artifact_path.name,
        mime_type=mimetypes.guess_type(artifact_path.name)[0]
        or "application/octet-stream",
        created_at=datetime.fromtimestamp(artifact_path.stat().st_mtime, UTC),
    )


def _job_input_paths(job: ProcessingJob, job_dir: Path) -> list[str]:
    input_paths = [
        relative_path
        for input_path in job.inputs
        for relative_path in [_relative_to_job_dir(input_path, job_dir)]
        if relative_path is not None
    ]
    if input_paths:
        return input_paths

    return [
        relative_path
        for result in job.results
        for artifact in result.artifacts
        if artifact.type == ArtifactType.ORIGINAL
        for relative_path in [_relative_to_job_dir(artifact.path, job_dir)]
        if relative_path is not None
    ]


def _inferred_job_times(
    job_dir: Path,
    artifact_paths: list[Path],
) -> tuple[datetime, datetime]:
    if artifact_paths:
        timestamps = [path.stat().st_mtime for path in artifact_paths]
        return (
            datetime.fromtimestamp(min(timestamps), UTC),
            datetime.fromtimestamp(max(timestamps), UTC),
        )
    timestamp = job_dir.stat().st_mtime
    inferred = datetime.fromtimestamp(timestamp, UTC)
    return inferred, inferred


def _job_dir(root: Path, job_id: str) -> Path:
    return root / _safe_segment(job_id)


def _relative_to_job_dir(path: Path, job_dir: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(job_dir.resolve()).as_posix()
    except ValueError:
        return None


def _resolve_relative_path(job_dir: Path, relative_path: str) -> Path:
    candidate_relative_path = Path(relative_path)
    if candidate_relative_path.is_absolute():
        raise JobStoreError("Manifest paths must be relative.")
    candidate = (job_dir / candidate_relative_path).resolve()
    if not candidate.is_relative_to(job_dir.resolve()):
        raise JobStoreError("Manifest path escapes the job directory.")
    return candidate


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _datetime_to_json(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _datetime_from_json(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _enum_value(enum_type, value: Any, default):
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
