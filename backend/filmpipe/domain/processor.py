from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from filmpipe.domain.models import Artifact, ArtifactType, ProcessingError, ProcessingOptions


class ArtifactStore(Protocol):
    def save_original(self, job_id: str, image_id: str, source_path: Path) -> Artifact:
        ...

    def save_artifact(
        self,
        job_id: str,
        image_id: str,
        artifact_type: ArtifactType,
        source_path: Path,
    ) -> Artifact:
        ...


@dataclass
class ProcessingContext:
    job_id: str
    image_id: str
    filename: str
    options: ProcessingOptions
    artifact_store: ArtifactStore
    logger: logging.Logger | logging.LoggerAdapter[Any]
    artifacts: dict[ArtifactType, Artifact] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    image: Any = None
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[ProcessingError] = field(default_factory=list)
    stop_pipeline: bool = False

    @classmethod
    def success(
        cls,
        *,
        image: Any = None,
        artifacts: list[Artifact] | None = None,
    ) -> ProcessorResult:
        return cls(image=image, artifacts=artifacts or [])

    @classmethod
    def failure(
        cls,
        error: ProcessingError,
        *,
        stop_pipeline: bool = True,
    ) -> ProcessorResult:
        return cls(errors=[error], stop_pipeline=stop_pipeline)


class Processor(Protocol):
    name: str
    optional: bool

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        ...
