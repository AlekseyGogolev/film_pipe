from __future__ import annotations

import pytest

from filmpipe.domain.models import ArtifactType
from filmpipe.infrastructure.storage import ArtifactStorageError, FileSystemArtifactStore


def test_storage_saves_original_without_overwrite(tmp_path):
    source = tmp_path / "scan.txt"
    source.write_bytes(b"negative")
    store = FileSystemArtifactStore(tmp_path / "jobs")

    artifact = store.save_original("job-1", "image-1", source)

    assert artifact.type == ArtifactType.ORIGINAL
    assert artifact.path.read_bytes() == b"negative"

    source.write_bytes(b"changed")
    assert artifact.path.read_bytes() == b"negative"

    with pytest.raises(ArtifactStorageError):
        store.save_original("job-1", "image-1", source)


def test_storage_saves_artifacts_separately_and_no_empty_dirs(tmp_path):
    source = tmp_path / "scan.txt"
    source.write_bytes(b"negative")
    store = FileSystemArtifactStore(tmp_path / "jobs")

    original = store.save_original("job-1", "image-1", source)
    positive = store.save_artifact("job-1", "image-1", ArtifactType.POSITIVE, original.path)

    assert positive.type == ArtifactType.POSITIVE
    assert positive.path.read_bytes() == b"negative"
    assert "positive" in positive.path.parts
    assert not (tmp_path / "jobs" / "job-1" / "image-1" / "restored").exists()
