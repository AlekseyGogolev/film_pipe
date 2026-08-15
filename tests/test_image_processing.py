from __future__ import annotations

import numpy as np

from filmpipe.domain.models import ArtifactType, ProcessingOptions, ProcessingStatus
from filmpipe.domain.processor import ProcessingContext
from filmpipe.infrastructure.logging import get_logger, setup_logging
from filmpipe.infrastructure.storage import FileSystemArtifactStore
from filmpipe.processing.engine import process_image
from filmpipe.processing.image import FilmImage
from filmpipe.processing.processors import (
    DecodeImageProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    ToneNormalizerProcessor,
)

from tests.image_fixtures import read_image, synthetic_bw_negative_16bit, write_image


def _context(tmp_path, filename: str = "scan.tiff") -> ProcessingContext:
    setup_logging(tmp_path / "logs")
    return ProcessingContext(
        job_id="job-1",
        image_id="image-1",
        filename=filename,
        options=ProcessingOptions(),
        artifact_store=FileSystemArtifactStore(tmp_path / "jobs"),
        logger=get_logger(job_id="job-1", image_id="image-1"),
    )


def test_decode_accepts_16_bit_grayscale_tiff(tmp_path):
    source = write_image(tmp_path / "scan.tiff", synthetic_bw_negative_16bit())
    context = _context(tmp_path, source.name)

    result = DecodeImageProcessor().process(source, context)

    assert not result.errors
    assert isinstance(result.image, FilmImage)
    assert result.image.bit_depth == 16
    assert result.image.data.dtype == np.float32
    assert result.image.data.ndim == 2
    assert np.isclose(float(result.image.data.max()), 1.0)


def test_decode_rejects_unsupported_format(tmp_path):
    source = tmp_path / "scan.txt"
    source.write_bytes(b"not an image")
    context = _context(tmp_path, source.name)

    result = DecodeImageProcessor().process(source, context)

    assert result.stop_pipeline is True
    assert result.errors[0].stage == "decode"
    assert "не поддерживается" in result.errors[0].user_message


def test_decode_rejects_corrupted_supported_image(tmp_path):
    source = tmp_path / "scan.png"
    source.write_bytes(b"not a valid png")
    context = _context(tmp_path, source.name)

    result = DecodeImageProcessor().process(source, context)

    assert result.stop_pipeline is True
    assert result.errors[0].stage == "decode"
    assert "декодировать" in result.errors[0].user_message


def test_negative_conversion_inverts_normalized_grayscale(tmp_path):
    source = tmp_path / "scan.tiff"
    image = FilmImage(
        data=np.array([[0.0, 0.25, 1.0]], dtype=np.float32),
        bit_depth=16,
        source_path=source,
        filename=source.name,
    )

    result = NegativeConverterProcessor().process(image, _context(tmp_path, source.name))

    assert not result.errors
    assert np.allclose(result.image.data, [[1.0, 0.75, 0.0]])


def test_tone_normalization_stretches_tonal_range(tmp_path):
    source = tmp_path / "scan.tiff"
    image = FilmImage(
        data=np.array([[0.2, 0.5, 0.8]], dtype=np.float32),
        bit_depth=16,
        source_path=source,
        filename=source.name,
    )
    processor = ToneNormalizerProcessor(low_percentile=0.0, high_percentile=100.0)

    result = processor.process(image, _context(tmp_path, source.name))

    assert not result.errors
    assert np.allclose(result.image.data, [[0.0, 0.5, 1.0]])


def test_positive_writer_saves_16_bit_tiff_artifact(tmp_path):
    source = tmp_path / "scan.tiff"
    image = FilmImage(
        data=np.array([[0.0, 0.5, 1.0]], dtype=np.float32),
        bit_depth=16,
        source_path=source,
        filename=source.name,
    )
    context = _context(tmp_path, source.name)

    result = PositiveArtifactWriterProcessor().process(image, context)

    assert not result.errors
    artifact = result.artifacts[0]
    output = read_image(artifact.path)
    assert artifact.type == ArtifactType.POSITIVE
    assert artifact.path.suffix == ".tiff"
    assert artifact.mime_type == "image/tiff"
    assert output.dtype == np.uint16
    assert output[0, 0] == 0
    assert output[0, -1] == np.iinfo(np.uint16).max


def test_default_pipeline_creates_technically_correct_positive(tmp_path):
    source = write_image(tmp_path / "scan.tiff", synthetic_bw_negative_16bit())

    result = process_image(
        source,
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        job_id="job-1",
        image_id="image-1",
    )

    positive = result.artifact(ArtifactType.POSITIVE)
    assert result.status == ProcessingStatus.SUCCESS
    assert result.artifact(ArtifactType.ORIGINAL) is not None
    assert positive is not None

    output = read_image(positive.path)
    assert output.dtype == np.uint16
    assert output[:, 0].mean() < output[:, -1].mean()
    assert output.max() > 65000


def test_default_pipeline_fails_before_positive_for_corrupt_image(tmp_path):
    source = tmp_path / "scan.png"
    source.write_bytes(b"not a valid image")

    result = process_image(
        source,
        storage=FileSystemArtifactStore(tmp_path / "jobs"),
        job_id="job-1",
        image_id="image-1",
    )

    assert result.status == ProcessingStatus.FAILED
    assert result.artifact(ArtifactType.ORIGINAL) is not None
    assert result.artifact(ArtifactType.POSITIVE) is None
    assert result.errors[0].stage == "decode"
