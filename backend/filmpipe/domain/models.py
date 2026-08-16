from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(UTC)


class InputProcessingMode(str, Enum):
    ALREADY_POSITIVE = "already_positive"
    BW_NEGATIVE = "bw_negative"


class RestorationMode(str, Enum):
    OFF = "off"
    TELEA = "telea"
    LAMA = "lama"


class ArtifactType(str, Enum):
    ORIGINAL = "original"
    POSITIVE = "positive"
    RESTORED = "restored"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


@dataclass
class ProcessingOptions:
    input_processing: InputProcessingMode = InputProcessingMode.BW_NEGATIVE
    restoration: RestorationMode = RestorationMode.OFF


@dataclass(frozen=True)
class Artifact:
    type: ArtifactType
    job_id: str
    image_id: str
    path: Path
    filename: str
    mime_type: str = "application/octet-stream"
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ProcessingError:
    stage: str
    user_message: str
    technical_message: str | None = None
    recoverable: bool = False
    exception_type: str | None = None

    @classmethod
    def from_exception(
        cls,
        *,
        stage: str,
        user_message: str,
        exc: Exception,
        recoverable: bool = False,
    ) -> ProcessingError:
        return cls(
            stage=stage,
            user_message=user_message,
            technical_message=str(exc),
            recoverable=recoverable,
            exception_type=type(exc).__name__,
        )


@dataclass
class ImageProcessingResult:
    image_id: str
    filename: str
    status: ProcessingStatus = ProcessingStatus.PENDING
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[ProcessingError] = field(default_factory=list)

    def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts.append(artifact)

    def add_error(self, error: ProcessingError) -> None:
        self.errors.append(error)

    def artifact(self, artifact_type: ArtifactType) -> Artifact | None:
        return next(
            (artifact for artifact in self.artifacts if artifact.type == artifact_type),
            None,
        )

    @property
    def has_positive(self) -> bool:
        return self.artifact(ArtifactType.POSITIVE) is not None


@dataclass
class ProcessingJob:
    id: str
    inputs: list[Path]
    options: ProcessingOptions
    status: ProcessingStatus = ProcessingStatus.PENDING
    results: list[ImageProcessingResult] = field(default_factory=list)
    errors: list[ProcessingError] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def recompute_status(self) -> ProcessingStatus:
        self.updated_at = utc_now()

        if not self.results:
            self.status = ProcessingStatus.PENDING
            return self.status

        statuses = {result.status for result in self.results}
        if statuses == {ProcessingStatus.SUCCESS}:
            self.status = ProcessingStatus.SUCCESS
        elif statuses == {ProcessingStatus.FAILED}:
            self.status = ProcessingStatus.FAILED
        elif statuses <= {ProcessingStatus.PENDING, ProcessingStatus.RUNNING}:
            self.status = ProcessingStatus.RUNNING
        else:
            self.status = ProcessingStatus.PARTIAL_SUCCESS

        return self.status
