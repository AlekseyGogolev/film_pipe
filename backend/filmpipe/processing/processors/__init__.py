"""Concrete processors for the FilmPipe processing pipeline."""

from filmpipe.processing.processors.images import (
    DecodeImageProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    SUPPORTED_IMAGE_SUFFIXES,
    ToneNormalizerProcessor,
)
from filmpipe.processing.processors.stubs import (
    FailingProcessor,
    NoopProcessor,
    PositiveArtifactStubProcessor,
)

__all__ = [
    "DecodeImageProcessor",
    "FailingProcessor",
    "NegativeConverterProcessor",
    "NoopProcessor",
    "PositiveArtifactStubProcessor",
    "PositiveArtifactWriterProcessor",
    "SUPPORTED_IMAGE_SUFFIXES",
    "ToneNormalizerProcessor",
]
