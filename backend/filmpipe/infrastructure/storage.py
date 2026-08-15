from __future__ import annotations

import mimetypes
import re
import shutil
from pathlib import Path

from filmpipe.domain.models import Artifact, ArtifactType


class ArtifactStorageError(RuntimeError):
    pass


class FileSystemArtifactStore:
    """Non-destructive filesystem storage for job artifacts."""

    def __init__(self, root: Path | str = Path("data/jobs")) -> None:
        self.root = Path(root)

    def save_original(self, job_id: str, image_id: str, source_path: Path) -> Artifact:
        return self._copy_artifact(
            job_id=job_id,
            image_id=image_id,
            artifact_type=ArtifactType.ORIGINAL,
            source_path=source_path,
            filename=Path(source_path).name,
        )

    def save_artifact(
        self,
        job_id: str,
        image_id: str,
        artifact_type: ArtifactType,
        source_path: Path,
    ) -> Artifact:
        source = Path(source_path)
        filename = f"{_safe_stem(source.stem)}_{artifact_type.value}{source.suffix}"
        return self._copy_artifact(
            job_id=job_id,
            image_id=image_id,
            artifact_type=artifact_type,
            source_path=source,
            filename=filename,
        )

    def _copy_artifact(
        self,
        *,
        job_id: str,
        image_id: str,
        artifact_type: ArtifactType,
        source_path: Path,
        filename: str,
    ) -> Artifact:
        source = Path(source_path)
        if not source.is_file():
            raise ArtifactStorageError(f"Source file does not exist: {source}")

        artifact_dir = self.root / _safe_segment(job_id) / _safe_segment(image_id) / artifact_type.value
        destination = artifact_dir / _safe_filename(filename)
        if destination.exists():
            raise ArtifactStorageError(f"Artifact already exists: {destination}")

        artifact_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        return Artifact(
            type=artifact_type,
            job_id=job_id,
            image_id=image_id,
            path=destination,
            filename=destination.name,
            mime_type=mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
        )


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "item"


def _safe_stem(value: str) -> str:
    return _safe_segment(value)


def _safe_filename(value: str) -> str:
    path = Path(value)
    stem = _safe_stem(path.stem)
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix)
    return f"{stem}{suffix}" if suffix else stem
