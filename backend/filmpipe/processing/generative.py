from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Literal, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from filmpipe.domain.models import Artifact, ArtifactType, FinalProcessingMode, ProcessingError
from filmpipe.domain.processor import ProcessingContext, ProcessorResult
from filmpipe.processing.image import FilmImage

CreativeSourceKind = Literal["restored", "positive", "working_positive", "original"]

DEFAULT_CREATIVE_ROOT = Path("experiments") / "creative_edit"
DEFAULT_SD_CLI = (
    DEFAULT_CREATIVE_ROOT
    / "runtime"
    / "sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan"
    / "sd-cli"
)
DEFAULT_FLUX_MODEL_ROOT = DEFAULT_CREATIVE_ROOT / "models" / "flux"
DEFAULT_OUTPUT_SUFFIX = ".png"


class CreativeProvider(Protocol):
    name: str
    model_id: str

    def generate(self, request: CreativeRequest) -> CreativeResult:
        ...


@dataclass(frozen=True)
class CreativeSource:
    kind: CreativeSourceKind
    path: Path | None = None
    image: Any | None = None
    artifact: Artifact | None = None


@dataclass(frozen=True)
class CreativeRequest:
    source_path: Path
    prompt: str
    job_id: str
    image_id: str
    output_path: Path
    source_kind: CreativeSourceKind


@dataclass(frozen=True)
class CreativeResult:
    output_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StableDiffusionCppConfig:
    sd_cli: Path = DEFAULT_SD_CLI
    diffusion_model: Path = DEFAULT_FLUX_MODEL_ROOT / "flux1-kontext-dev-Q4_K_M.gguf"
    vae: Path = DEFAULT_FLUX_MODEL_ROOT / "ae.safetensors"
    clip_l: Path = DEFAULT_FLUX_MODEL_ROOT / "clip_l.safetensors"
    t5xxl: Path = DEFAULT_FLUX_MODEL_ROOT / "t5xxl_fp16.safetensors"
    backend: str = "diffusion=vulkan0,te=cpu,vae=cpu"
    params_backend: str = "diffusion=vulkan0,te=cpu,vae=cpu"
    max_vram: str = "vulkan0=8"
    cfg_scale: str = "1.0"
    sampling_method: str = "euler"
    steps: int = 12
    width: int = 640
    height: int = 640
    seed: int = 1118877715456453
    timeout_sec: int = 7200
    strength: float | None = None

    @classmethod
    def from_env(cls) -> StableDiffusionCppConfig:
        return cls(
            sd_cli=_env_path("FILMPIPE_CREATIVE_SD_CLI", DEFAULT_SD_CLI),
            diffusion_model=_env_path(
                "FILMPIPE_CREATIVE_DIFFUSION_MODEL",
                DEFAULT_FLUX_MODEL_ROOT / "flux1-kontext-dev-Q4_K_M.gguf",
            ),
            vae=_env_path("FILMPIPE_CREATIVE_VAE", DEFAULT_FLUX_MODEL_ROOT / "ae.safetensors"),
            clip_l=_env_path("FILMPIPE_CREATIVE_CLIP_L", DEFAULT_FLUX_MODEL_ROOT / "clip_l.safetensors"),
            t5xxl=_env_path("FILMPIPE_CREATIVE_T5XXL", DEFAULT_FLUX_MODEL_ROOT / "t5xxl_fp16.safetensors"),
            backend=os.getenv("FILMPIPE_CREATIVE_BACKEND", cls.backend),
            params_backend=os.getenv("FILMPIPE_CREATIVE_PARAMS_BACKEND", cls.params_backend),
            max_vram=os.getenv("FILMPIPE_CREATIVE_MAX_VRAM", cls.max_vram),
            cfg_scale=os.getenv("FILMPIPE_CREATIVE_CFG_SCALE", cls.cfg_scale),
            sampling_method=os.getenv("FILMPIPE_CREATIVE_SAMPLING_METHOD", cls.sampling_method),
            steps=_env_int("FILMPIPE_CREATIVE_STEPS", cls.steps),
            width=_env_int("FILMPIPE_CREATIVE_WIDTH", cls.width),
            height=_env_int("FILMPIPE_CREATIVE_HEIGHT", cls.height),
            seed=_env_int("FILMPIPE_CREATIVE_SEED", cls.seed),
            timeout_sec=_env_int("FILMPIPE_CREATIVE_TIMEOUT_SEC", cls.timeout_sec),
            strength=_env_optional_float("FILMPIPE_CREATIVE_STRENGTH"),
        )

    @property
    def required_paths(self) -> tuple[Path, ...]:
        return (self.sd_cli, self.diffusion_model, self.vae, self.clip_l, self.t5xxl)


@dataclass
class StableDiffusionCppProvider:
    config: StableDiffusionCppConfig = field(default_factory=StableDiffusionCppConfig.from_env)
    name: str = "stable_diffusion_cpp_cli"
    model_id: str = "flux_kontext_q4"

    def generate(self, request: CreativeRequest) -> CreativeResult:
        missing = [path for path in self.config.required_paths if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise RuntimeError(
                "Creative runtime or model files are missing. Prepare "
                "experiments/creative_edit assets or set FILMPIPE_CREATIVE_* env vars. "
                f"Missing: {missing_text}"
            )

        command = self._command(request)
        started = perf_counter()
        try:
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Creative runtime timed out after {self.config.timeout_sec} seconds."
            ) from exc

        duration_ms = (perf_counter() - started) * 1000.0
        if completed.returncode != 0:
            raise RuntimeError(
                "Creative runtime failed "
                f"with exit code {completed.returncode}: {_tail(completed.stderr)}"
            )
        if not request.output_path.is_file():
            raise RuntimeError(
                "Creative runtime completed but did not create the expected output image."
            )

        return CreativeResult(
            output_path=request.output_path,
            metadata={
                "command": shlex.join(command),
                "duration_ms": duration_ms,
                "returncode": completed.returncode,
                "stdout_tail": _tail(completed.stdout),
                "stderr_tail": _tail(completed.stderr),
            },
        )

    def _command(self, request: CreativeRequest) -> list[str]:
        command = [
            str(self.config.sd_cli),
            "--diffusion-model",
            str(self.config.diffusion_model),
            "--vae",
            str(self.config.vae),
            "--clip_l",
            str(self.config.clip_l),
            "--t5xxl",
            str(self.config.t5xxl),
            "-r",
            str(request.source_path),
            "-p",
            request.prompt,
            "-o",
            str(request.output_path),
            "--cfg-scale",
            self.config.cfg_scale,
            "--sampling-method",
            self.config.sampling_method,
            "--steps",
            str(self.config.steps),
            "--backend",
            self.config.backend,
            "--params-backend",
            self.config.params_backend,
            "--max-vram",
            self.config.max_vram,
            "--vae-tiling",
            "-v",
            "-W",
            str(self.config.width),
            "-H",
            str(self.config.height),
            "-s",
            str(self.config.seed),
        ]
        if self.config.strength is not None:
            command.extend(["--strength", str(self.config.strength)])
        return command


@dataclass
class GenerativeProcessor:
    name: str = "generative_processing"
    optional: bool = True
    provider_factory: Callable[[], CreativeProvider] = field(
        default_factory=lambda: default_creative_provider
    )
    output_suffix: str = DEFAULT_OUTPUT_SUFFIX

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        if context.options.final_processing != FinalProcessingMode.CREATIVE:
            return ProcessorResult.success(image=image)

        prompt = (context.options.creative_prompt or "").strip()
        if not prompt:
            return _recoverable_failure(
                self.name,
                f"Creative prompt для {context.filename} пустой, creative-обработка пропущена.",
                "Missing creative_prompt for creative final processing",
            )

        started = perf_counter()
        try:
            source = select_creative_source(context, image)
            with TemporaryDirectory(prefix="filmpipe-creative-") as temporary_dir:
                temporary_root = Path(temporary_dir)
                source_path = temporary_root / "source.png"
                output_path = temporary_root / f"creative{self.output_suffix}"
                prepare_creative_source_file(source, source_path)

                provider = self.provider_factory()
                context.logger.info(
                    "creative_started source=%s provider=%s model=%s",
                    source.kind,
                    provider.name,
                    provider.model_id,
                    extra={"processor": self.name},
                )
                provider_result = provider.generate(
                    CreativeRequest(
                        source_path=source_path,
                        prompt=prompt,
                        job_id=context.job_id,
                        image_id=context.image_id,
                        output_path=output_path,
                        source_kind=source.kind,
                    )
                )
                artifact = context.artifact_store.save_artifact(
                    context.job_id,
                    context.image_id,
                    ArtifactType.CREATIVE,
                    provider_result.output_path,
                )
        except Exception as exc:
            context.logger.exception("creative_failed", extra={"processor": self.name})
            return ProcessorResult.failure(
                ProcessingError.from_exception(
                    stage=self.name,
                    user_message=f"Не удалось выполнить Creative для {context.filename}.",
                    exc=exc,
                    recoverable=True,
                ),
                stop_pipeline=False,
            )

        duration_ms = (perf_counter() - started) * 1000.0
        context.metadata["creative"] = {
            "source": source.kind,
            "provider": provider.name,
            "model_id": provider.model_id,
            "duration_ms": duration_ms,
            "prompt_chars": len(prompt),
            "provider_metadata": provider_result.metadata,
        }
        context.logger.info(
            "creative_completed source=%s provider=%s model=%s duration_ms=%.2f",
            source.kind,
            provider.name,
            provider.model_id,
            duration_ms,
            extra={"processor": self.name},
        )
        return ProcessorResult.success(image=image, artifacts=[artifact])


def default_creative_provider() -> CreativeProvider:
    return StableDiffusionCppProvider()


def select_creative_source(context: ProcessingContext, image: Any) -> CreativeSource:
    restored = context.artifacts.get(ArtifactType.RESTORED)
    if restored is not None:
        return CreativeSource(kind="restored", path=restored.path, artifact=restored)

    positive = context.artifacts.get(ArtifactType.POSITIVE)
    if positive is not None:
        return CreativeSource(kind="positive", path=positive.path, artifact=positive)

    if context.working_positive is not None:
        return CreativeSource(kind="working_positive", image=context.working_positive)

    original = context.artifacts.get(ArtifactType.ORIGINAL)
    if original is not None:
        return CreativeSource(kind="original", path=original.path, artifact=original)

    if isinstance(image, (Path, str)):
        return CreativeSource(kind="original", path=Path(image))

    raise RuntimeError("No creative source is available in the processing context.")


def prepare_creative_source_file(source: CreativeSource, destination: Path) -> Path:
    image = _read_source_image(source)
    prepared = _to_provider_bgr8(image)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(destination.suffix, prepared)
    if not ok:
        raise ValueError(f"OpenCV failed to encode creative source: {destination}")
    destination.write_bytes(encoded.tobytes())
    return destination


def _read_source_image(source: CreativeSource) -> npt.NDArray[np.generic]:
    if isinstance(source.image, FilmImage):
        return np.asarray(source.image.data)
    if source.image is not None:
        return np.asarray(source.image)
    if source.path is None:
        raise ValueError(f"Creative source {source.kind} has no image or path.")

    encoded = np.frombuffer(source.path.read_bytes(), dtype=np.uint8)
    if encoded.size == 0:
        raise ValueError(f"Creative source is empty: {source.path}")
    decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise ValueError(f"OpenCV could not decode creative source: {source.path}")
    return decoded


def _to_provider_bgr8(image: npt.NDArray[np.generic]) -> npt.NDArray[np.uint8]:
    array = np.asarray(image)
    if array.ndim not in (2, 3):
        raise ValueError(f"Unsupported creative source dimensions: {array.ndim}")

    image8 = _to_uint8(array)
    if image8.ndim == 2:
        return cv2.cvtColor(image8, cv2.COLOR_GRAY2BGR)

    channels = int(image8.shape[2])
    if channels == 1:
        return cv2.cvtColor(image8[:, :, 0], cv2.COLOR_GRAY2BGR)
    if channels == 3:
        return image8
    if channels == 4:
        return cv2.cvtColor(image8, cv2.COLOR_BGRA2BGR)
    raise ValueError(f"Unsupported creative source channel count: {channels}")


def _to_uint8(image: npt.NDArray[np.generic]) -> npt.NDArray[np.uint8]:
    if image.dtype == np.uint8:
        return image
    if image.dtype == np.uint16:
        return np.rint(image.astype(np.float32) / np.iinfo(np.uint16).max * 255).astype(
            np.uint8
        )
    if np.issubdtype(image.dtype, np.floating):
        if not np.isfinite(image).all():
            raise ValueError("Creative source contains non-finite pixel values.")
        return np.rint(np.clip(image, 0.0, 1.0) * 255).astype(np.uint8)
    raise ValueError(f"Unsupported creative source dtype: {image.dtype}")


def _recoverable_failure(
    stage: str,
    user_message: str,
    technical_message: str,
) -> ProcessorResult:
    return ProcessorResult.failure(
        ProcessingError(
            stage=stage,
            user_message=user_message,
            technical_message=technical_message,
            recoverable=True,
        ),
        stop_pipeline=False,
    )


def _env_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default)))


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_optional_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return float(value)


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]
