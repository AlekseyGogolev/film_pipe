from __future__ import annotations

import sys

from filmpipe.domain.models import ArtifactType, ProcessingOptions, ProcessingStatus
from filmpipe.infrastructure.logging import setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import process_image

from tests.image_fixtures import synthetic_bw_negative_16bit, write_image


def test_process_image_smoke_without_http_dependency(tmp_path):
    setup_logging(tmp_path / "logs")
    source = write_image(tmp_path / "scan.tiff", synthetic_bw_negative_16bit())

    result = process_image(
        source,
        options=ProcessingOptions(),
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.SUCCESS
    assert result.artifact(ArtifactType.ORIGINAL) is not None
    assert result.artifact(ArtifactType.POSITIVE) is not None
    assert "fastapi" not in sys.modules
