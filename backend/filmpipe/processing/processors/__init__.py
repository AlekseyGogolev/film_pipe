"""Concrete processors for the FilmPipe processing pipeline."""

from filmpipe.processing.processors.images import (
    DecodeBWImageProcessor,
    DecodePositiveImageProcessor,
    NegativeConverterProcessor,
    PositiveArtifactWriterProcessor,
    SUPPORTED_IMAGE_SUFFIXES,
    ToneNormalizerProcessor,
)
from filmpipe.processing.restoration import AIRestorationProcessor
from filmpipe.processing.processors.stubs import (
    FailingProcessor,
    NoopProcessor,
    PositiveArtifactStubProcessor,
)

__all__ = [
    "DecodeBWImageProcessor",
    "DecodePositiveImageProcessor",
    "AIRestorationProcessor",
    "FailingProcessor",
    "NegativeConverterProcessor",
    "NoopProcessor",
    "PositiveArtifactStubProcessor",
    "PositiveArtifactWriterProcessor",
    "SUPPORTED_IMAGE_SUFFIXES",
    "ToneNormalizerProcessor",
]
