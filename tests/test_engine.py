from __future__ import annotations

import subprocess
import sys
import textwrap

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

    script = textwrap.dedent(
        """
        import sys
        from pathlib import Path

        from filmpipe.domain.models import ProcessingOptions
        from filmpipe.infrastructure.logging import setup_logging
        from filmpipe.infrastructure.storage import FileSystemArtifactStore
        from filmpipe.processing.engine import process_image

        setup_logging(Path(sys.argv[3]))
        result = process_image(
            Path(sys.argv[1]),
            options=ProcessingOptions(),
            storage=FileSystemArtifactStore(Path(sys.argv[2])),
            job_id="job-subprocess",
            image_id="image-subprocess",
        )
        assert result.status.value == "success"
        assert "fastapi" not in sys.modules
        """
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            str(tmp_path / "subprocess-jobs"),
            str(tmp_path / "subprocess-logs"),
        ],
        check=True,
    )
