from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filmpipe.domain.models import ArtifactType
from filmpipe.domain.processor import ProcessingContext, ProcessorResult


@dataclass
class NoopProcessor:
    name: str = "noop"
    optional: bool = False

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        return ProcessorResult.success(image=image)


@dataclass
class PositiveArtifactStubProcessor:
    """Agent 1 placeholder: creates a positive artifact without image processing."""

    name: str = "positive_artifact_stub"
    optional: bool = False

    def process(self, image: Path, context: ProcessingContext) -> ProcessorResult:
        source = context.artifacts.get(ArtifactType.ORIGINAL)
        source_path = source.path if source else Path(image)
        artifact = context.artifact_store.save_artifact(
            context.job_id,
            context.image_id,
            ArtifactType.POSITIVE,
            source_path,
        )
        return ProcessorResult.success(image=artifact.path, artifacts=[artifact])


@dataclass
class FailingProcessor:
    name: str
    optional: bool = False
    message: str = "intentional processor failure"

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        raise RuntimeError(self.message)
