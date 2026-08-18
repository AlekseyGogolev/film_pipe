from __future__ import annotations

import importlib
import math
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from filmpipe.domain.models import ArtifactType, ProcessingError, RestorationMode
from filmpipe.domain.processor import ProcessingContext, ProcessorResult
from filmpipe.processing.ai_runtime import (
    ai_runtime_lifecycle,
    release_torch_cuda_cache,
    runtime_label,
    torch_inference_context,
)
from filmpipe.processing.image import FilmImage

OUTPUT_SUFFIX = ".tiff"
_AI_RUNTIME_PATHS_PREPARED = False


class DefectDetector(Protocol):
    def detect(self, image: npt.NDArray[np.generic]) -> DetectionResult:
        ...


class Restorer(Protocol):
    def restore(
        self,
        image: npt.NDArray[np.generic],
        restoration_mask: npt.NDArray[np.uint8],
    ) -> RestorerCandidate:
        ...


@dataclass(frozen=True)
class DetectionResult:
    probability: npt.NDArray[np.float32]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RestorerCandidate:
    image: npt.NDArray[np.generic]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaskPostprocessConfig:
    threshold: float = 0.4
    dilation: int = 2
    mode: str = "scene_lines"


@dataclass(frozen=True)
class MaskPostprocessResult:
    binary_mask: npt.NDArray[np.uint8]
    restoration_mask: npt.NDArray[np.uint8]
    metadata: dict[str, Any]


@dataclass
class AIRestorationProcessor:
    name: str = "ai_restoration"
    optional: bool = True
    detector_factory: Callable[[], DefectDetector] = field(
        default_factory=lambda: default_detector_factory
    )
    restorer_factories: Mapping[RestorationMode, Callable[[], Restorer]] = field(
        default_factory=lambda: {
            RestorationMode.TELEA: lambda: TeleaRestorer(),
            RestorationMode.LAMA: lambda: LaMaRestorer(default_models_root()),
        }
    )
    mask_config: MaskPostprocessConfig = field(default_factory=MaskPostprocessConfig)
    output_suffix: str = OUTPUT_SUFFIX

    def process(self, image: Any, context: ProcessingContext) -> ProcessorResult:
        mode = context.options.restoration
        if mode == RestorationMode.OFF:
            return ProcessorResult.success(image=image)

        positive_image = context.working_positive
        if positive_image is None and isinstance(image, FilmImage):
            positive_image = image.data
        if positive_image is None:
            return _recoverable_failure(
                "restoration",
                f"Рабочий позитив {context.filename} создан не был, restoration пропущен.",
                "Missing working positive before AI restoration",
            )

        started = perf_counter()
        context.logger.info(
            "restoration_started detector=microsoft_bopbtl_scratch restorer=%s",
            mode.value,
            extra={"processor": self.name},
        )

        positive_image = np.asarray(positive_image)

        try:
            detector = self.detector_factory()
            with ai_runtime_lifecycle(
                detector,
                logger=context.logger,
                processor=self.name,
                stage="defect_detection",
                provider=runtime_label(detector, "microsoft_bopbtl_scratch"),
            ) as active_detector:
                detector_result = active_detector.detect(positive_image)
            probability = validate_probability_mask(
                detector_result.probability,
                positive_image.shape[:2],
            )
        except Exception as exc:
            context.logger.exception(
                "defect_detection_failed",
                extra={"processor": self.name},
            )
            return _exception_failure(
                stage="defect_detection",
                user_message=_detector_failure_message(context.filename, exc),
                exc=exc,
            )

        try:
            mask_result = postprocess_mask(
                probability,
                positive_image,
                threshold=self.mask_config.threshold,
                dilation=self.mask_config.dilation,
                mode=self.mask_config.mode,
            )
            restorer = self._restorer(mode)
            with ai_runtime_lifecycle(
                restorer,
                logger=context.logger,
                processor=self.name,
                stage="restoration",
                provider=runtime_label(restorer, mode.value),
            ) as active_restorer:
                candidate = active_restorer.restore(
                    positive_image,
                    mask_result.restoration_mask,
                )
            restored = composite_restoration(
                positive_image,
                candidate.image,
                mask_result.restoration_mask,
            )
            artifact = self._save_restored_artifact(restored, context)
        except Exception as exc:
            context.logger.exception(
                "restoration_failed",
                extra={"processor": self.name},
            )
            return _exception_failure(
                stage="restoration",
                user_message=f"Не удалось выполнить restoration {context.filename}.",
                exc=exc,
            )

        duration_ms = (perf_counter() - started) * 1000.0
        context.metadata["restoration"] = {
            "mode": mode.value,
            "detector": detector_result.metadata,
            "mask": mask_result.metadata,
            "restorer": candidate.metadata,
            "duration_ms": duration_ms,
        }
        context.logger.info(
            "restoration_completed restorer=%s mask_pixels=%s duration_ms=%.2f",
            mode.value,
            int(np.count_nonzero(mask_result.restoration_mask > 0)),
            duration_ms,
            extra={"processor": self.name},
        )
        return ProcessorResult.success(image=image, artifacts=[artifact])

    def _restorer(self, mode: RestorationMode) -> Restorer:
        factory = self.restorer_factories.get(mode)
        if factory is None:
            raise ValueError(f"Unsupported restoration mode: {mode.value}")
        return factory()

    def _save_restored_artifact(
        self,
        restored: npt.NDArray[np.generic],
        context: ProcessingContext,
    ):
        with TemporaryDirectory(prefix="filmpipe-restored-") as temporary_dir:
            source_stem = Path(context.filename).stem or "image"
            temporary_path = Path(temporary_dir) / f"{source_stem}{self.output_suffix}"
            write_image(temporary_path, restored)
            return context.artifact_store.save_artifact(
                context.job_id,
                context.image_id,
                ArtifactType.RESTORED,
                temporary_path,
            )


@dataclass
class MicrosoftScratchDetector:
    models_root: Path
    device_preference: str = "auto"
    input_size: str = "full_size"
    tile_size: int = 1024
    tile_overlap: int = 128
    name: str = "microsoft_bopbtl_scratch"
    model_id: str = "FT_Epoch_latest.pt"

    def __post_init__(self) -> None:
        self.models_root = Path(self.models_root)
        self.repo_path = self.models_root / "microsoft_bopbtl" / "repo"
        self.global_path = self.repo_path / "Global"
        self.checkpoint_path = (
            self.global_path / "checkpoints" / "detection" / "FT_Epoch_latest.pt"
        )
        self._torch: Any | None = None
        self._model: Any | None = None
        self._device: Any | None = None

    def detect(self, image: npt.NDArray[np.generic]) -> DetectionResult:
        rgb = to_model_rgb_uint8(image)
        fallback_after_oom = False
        try:
            if self.tile_size > 0 and max(rgb.shape[:2]) > self.tile_size:
                probability = self._detect_tiled(rgb)
                mode = "tiled"
            else:
                probability = self._predict_rgb(rgb)
                mode = "single"
        except Exception as exc:
            if not _is_cuda_oom(exc):
                raise
            self._clear_cuda_cache()
            if self.tile_size > 0:
                raise RuntimeError(
                    "CUDA out of memory during tiled Microsoft detector inference. "
                    "Retry with a smaller detector tile or use CPU."
                ) from exc
            self.tile_size = 1024
            self.tile_overlap = min(self.tile_overlap, 128)
            probability = self._detect_tiled(rgb)
            mode = "tiled_after_cuda_oom"
            fallback_after_oom = True

        return DetectionResult(
            probability=probability,
            metadata={
                "name": "microsoft_bopbtl_scratch",
                "source": "https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life",
                "checkpoint": str(self.checkpoint_path),
                "checkpoint_name": "FT_Epoch_latest.pt",
                "license": "MIT",
                "device": str(self._device),
                "input_size": self.input_size,
                "inference_mode": mode,
                "tile_size": self.tile_size,
                "tile_overlap": self.tile_overlap if mode.startswith("tiled") else 0,
                "fallback_after_cuda_oom": fallback_after_oom,
            },
        )

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        if not self.global_path.exists():
            raise FileNotFoundError(
                "Microsoft detector files are missing. Run "
                "experiments/ai_restoration/download_models.py or set "
                "FILMPIPE_AI_MODELS_ROOT to the prepared models directory."
            )
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(
                "Microsoft scratch checkpoint is missing: "
                f"{self.checkpoint_path}"
            )

        prepare_ai_runtime_paths()
        try:
            torch = importlib.import_module("torch")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PyTorch is not installed for the Microsoft detector runtime. "
                "Install FilmPipe with the ai extra or set FILMPIPE_AI_RUNTIME_VENV "
                "to a prepared AI virtualenv."
            ) from exc

        if str(self.global_path) not in sys.path:
            sys.path.insert(0, str(self.global_path))
        networks = importlib.import_module("detection_models.networks")

        device = self._resolve_device(torch)
        model = networks.UNet(
            in_channels=1,
            out_channels=1,
            depth=4,
            conv_num=2,
            wf=6,
            padding=True,
            batch_norm=True,
            up_mode="upsample",
            with_tanh=False,
            sync_bn=True,
            antialiasing=True,
        )
        try:
            checkpoint = torch.load(
                self.checkpoint_path,
                map_location="cpu",
                weights_only=False,
            )
        except TypeError:
            checkpoint = torch.load(self.checkpoint_path, map_location="cpu")
        state_dict = checkpoint.get("model_state", checkpoint)
        try:
            model.load_state_dict(state_dict)
        except RuntimeError:
            stripped = {
                key.removeprefix("module."): value for key, value in state_dict.items()
            }
            model.load_state_dict(stripped)
        model.to(device)
        model.eval()

        self._torch = torch
        self._device = device
        self._model = model
        return model

    def close(self) -> None:
        torch = self._torch
        self._model = None
        self._device = None
        self._torch = None
        release_torch_cuda_cache(torch)

    def _clear_cuda_cache(self) -> None:
        if self._torch is not None and self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()

    def _resolve_device(self, torch: Any) -> Any:
        preference = self.device_preference.lower()
        if preference == "auto":
            return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        if preference.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested for detector, but torch.cuda is unavailable.")
        return torch.device(preference)

    def _predict_rgb(self, rgb: npt.NDArray[np.uint8]) -> npt.NDArray[np.float32]:
        model = self._load_model()
        torch = self._torch
        if torch is None or self._device is None:
            raise RuntimeError("Detector model did not initialize correctly.")

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        prepared, restore = _prepare_detector_input(gray, self.input_size)
        tensor = torch.from_numpy(prepared[np.newaxis, np.newaxis, :, :] * 2.0 - 1.0)
        tensor = tensor.to(self._device)
        with torch_inference_context(torch):
            prediction = torch.sigmoid(model(tensor))
        probability = prediction[0, 0].detach().cpu().numpy().astype(np.float32)
        del prediction, tensor
        return np.clip(restore(probability), 0.0, 1.0).astype(np.float32)

    def _detect_tiled(self, rgb: npt.NDArray[np.uint8]) -> npt.NDArray[np.float32]:
        height, width = rgb.shape[:2]
        tile = max(16, int(self.tile_size))
        overlap = max(0, min(int(self.tile_overlap), tile - 1))
        y_positions = _tile_positions(height, tile, overlap)
        x_positions = _tile_positions(width, tile, overlap)
        accumulation = np.zeros((height, width), dtype=np.float64)
        weights = np.zeros((height, width), dtype=np.float64)

        for y0 in y_positions:
            y1 = min(y0 + tile, height)
            for x0 in x_positions:
                x1 = min(x0 + tile, width)
                tile_rgb = rgb[y0:y1, x0:x1, :]
                tile_prob = self._predict_rgb(tile_rgb).astype(np.float64)
                weight = _tile_weight(
                    tile_prob.shape,
                    overlap=overlap,
                    touches_top=y0 == 0,
                    touches_bottom=y1 == height,
                    touches_left=x0 == 0,
                    touches_right=x1 == width,
                )
                accumulation[y0:y1, x0:x1] += tile_prob * weight
                weights[y0:y1, x0:x1] += weight

        weights[weights == 0.0] = 1.0
        return np.clip(accumulation / weights, 0.0, 1.0).astype(np.float32)


@dataclass
class TeleaRestorer:
    radius: float = 3.0

    def restore(
        self,
        image: npt.NDArray[np.generic],
        restoration_mask: npt.NDArray[np.uint8],
    ) -> RestorerCandidate:
        mask = _mask8(restoration_mask)
        if not np.any(mask):
            return RestorerCandidate(
                image=image.copy(),
                metadata={"name": "opencv_telea", "device": "cpu", "radius": self.radius},
            )

        if image.ndim == 2:
            restored = cv2.inpaint(image, mask, float(self.radius), cv2.INPAINT_TELEA)
        elif image.ndim == 3 and image.shape[2] == 3 and image.dtype == np.uint8:
            restored = cv2.inpaint(image, mask, float(self.radius), cv2.INPAINT_TELEA)
        elif image.ndim == 3 and image.shape[2] in (3, 4):
            restored = image.copy()
            for channel in range(3):
                restored[:, :, channel] = cv2.inpaint(
                    image[:, :, channel],
                    mask,
                    float(self.radius),
                    cv2.INPAINT_TELEA,
                )
        else:
            raise ValueError(f"Unsupported image shape for TELEA: {image.shape}")

        return RestorerCandidate(
            image=restored.astype(image.dtype, copy=False),
            metadata={
                "name": "opencv_telea",
                "algorithm": "cv2.INPAINT_TELEA",
                "device": "cpu",
                "radius": self.radius,
            },
        )


@dataclass
class LaMaRestorer:
    models_root: Path
    python_executable: str | None = None
    device_preference: str = "auto"
    refine: bool = False
    name: str = "lama_big_lama_native"
    model_id: str = "big-lama"

    def __post_init__(self) -> None:
        self.models_root = Path(self.models_root)
        self.repo_path = self.models_root / "lama" / "repo"
        self.model_path = self.models_root / "lama" / "big-lama"
        if self.python_executable is None:
            self.python_executable = sys.executable
        self._model: Any | None = None
        self._torch: Any | None = None
        self._device: Any | None = None

    def restore(
        self,
        image: npt.NDArray[np.generic],
        restoration_mask: npt.NDArray[np.uint8],
    ) -> RestorerCandidate:
        self._validate_paths()
        mask = _mask8(restoration_mask)
        if not np.any(mask):
            return RestorerCandidate(
                image=image.copy(),
                metadata=self._metadata(status_note="empty mask, inference skipped"),
            )

        rgb_output = self._predict_native(to_model_rgb_uint8(image), mask)
        restored = model_rgb_uint8_to_source(rgb_output, image)
        return RestorerCandidate(image=restored, metadata=self._metadata())

    def _validate_paths(self) -> None:
        checkpoint = self.model_path / "models" / "best.ckpt"
        config = self.model_path / "config.yaml"
        modules_dir = self.repo_path / "saicinpainting"
        missing = [path for path in (modules_dir, checkpoint, config) if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(
                "LaMa files are missing. Run experiments/ai_restoration/download_models.py "
                "or set FILMPIPE_AI_MODELS_ROOT to the prepared models directory. "
                f"Missing: {missing_text}"
            )

    def _predict_native(
        self,
        rgb: npt.NDArray[np.uint8],
        mask: npt.NDArray[np.uint8],
    ) -> npt.NDArray[np.uint8]:
        model = self._load_native_model()
        torch = self._torch
        device = self._device
        if torch is None or device is None:
            raise RuntimeError("LaMa native model did not initialize correctly.")

        height, width = rgb.shape[:2]
        image_tensor = torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1)
        mask_tensor = torch.from_numpy((mask > 0).astype(np.float32))[None, :, :]
        image_tensor = image_tensor[None, :, :, :].to(device)
        mask_tensor = mask_tensor[None, :, :, :].to(device)
        image_tensor = _pad_tensor_to_modulo(torch, image_tensor, 8)
        mask_tensor = _pad_tensor_to_modulo(torch, mask_tensor, 8)

        with torch_inference_context(torch):
            masked_image = image_tensor * (1.0 - mask_tensor)
            model_input = torch.cat([masked_image, mask_tensor], dim=1)
            predicted = model(model_input)
            inpainted = mask_tensor * predicted + (1.0 - mask_tensor) * image_tensor

        output = inpainted[0, :, :height, :width].permute(1, 2, 0)
        output_np = output.detach().cpu().numpy()
        del (
            output,
            inpainted,
            predicted,
            model_input,
            masked_image,
            mask_tensor,
            image_tensor,
        )
        return np.clip(np.round(output_np * 255.0), 0, 255).astype(np.uint8)

    def _load_native_model(self) -> Any:
        if self._model is not None:
            return self._model

        self._validate_paths()
        if str(self.repo_path) not in sys.path:
            sys.path.insert(0, str(self.repo_path))

        prepare_ai_runtime_paths()
        try:
            torch = importlib.import_module("torch")
            yaml = importlib.import_module("yaml")
            omegaconf = importlib.import_module("omegaconf")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "LaMa runtime dependencies are missing. Install FilmPipe with the ai "
                "extra or set FILMPIPE_AI_RUNTIME_VENV to a prepared AI virtualenv."
            ) from exc

        modules = importlib.import_module("saicinpainting.training.modules")
        generator_class = modules.FFCResNetGenerator
        config = omegaconf.OmegaConf.create(
            yaml.safe_load((self.model_path / "config.yaml").read_text(encoding="utf-8"))
        )
        generator_config = dict(
            omegaconf.OmegaConf.to_container(config.generator, resolve=True)
        )
        kind = generator_config.pop("kind")
        if kind != "ffc_resnet":
            raise ValueError(f"Unsupported LaMa generator kind: {kind}")

        model = generator_class(**generator_config)
        checkpoint_path = self.model_path / "models" / "best.ckpt"
        try:
            checkpoint = torch.load(
                checkpoint_path,
                map_location="cpu",
                weights_only=False,
            )
        except TypeError:
            checkpoint = torch.load(checkpoint_path, map_location="cpu")

        state_dict = checkpoint.get("state_dict", checkpoint)
        generator_state = {
            key.removeprefix("generator."): value
            for key, value in state_dict.items()
            if key.startswith("generator.")
        }
        if not generator_state:
            raise ValueError(f"No generator weights found in LaMa checkpoint: {checkpoint_path}")
        model.load_state_dict(generator_state, strict=True)

        device = self._resolve_device(torch)
        model.to(device)
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)

        self._torch = torch
        self._device = device
        self._model = model
        return model

    def close(self) -> None:
        torch = self._torch
        self._model = None
        self._device = None
        self._torch = None
        release_torch_cuda_cache(torch)

    def _resolve_device(self, torch: Any) -> Any:
        preference = self.device_preference.lower()
        if preference == "auto":
            return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        if preference.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested for LaMa, but torch.cuda is unavailable.")
        return torch.device(preference)

    def _resolved_device_label(self) -> str:
        if self._device is not None:
            return str(self._device)
        if self.device_preference.lower() == "auto":
            try:
                torch = importlib.import_module("torch")
                return "cuda:0" if torch.cuda.is_available() else "cpu"
            except Exception:
                return "auto"
        return self.device_preference

    def _metadata(self, *, status_note: str | None = None) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "name": "lama_big_lama_native",
            "source": "https://github.com/advimman/lama",
            "checkpoint": str(self.model_path / "models" / "best.ckpt"),
            "checkpoint_name": "Big-LaMa best.ckpt",
            "license": "Apache-2.0",
            "device": self._resolved_device_label(),
            "refine": self.refine,
            "bit_depth_note": (
                "LaMa inference uses 8-bit RGB working copy; final composite "
                "preserves source dtype outside mask."
            ),
        }
        if status_note is not None:
            metadata["status_note"] = status_note
        return metadata


def default_detector_factory() -> DefectDetector:
    return MicrosoftScratchDetector(default_models_root())


def prepare_ai_runtime_paths() -> tuple[Path, ...]:
    """Expose optional AI runtime packages without making basic processing depend on them."""
    global _AI_RUNTIME_PATHS_PREPARED
    if _AI_RUNTIME_PATHS_PREPARED:
        return ()

    _AI_RUNTIME_PATHS_PREPARED = True
    added: list[Path] = []
    for candidate in _ai_runtime_site_packages_candidates():
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        resolved_text = str(resolved)
        if resolved_text in sys.path:
            continue
        sys.path.append(resolved_text)
        added.append(resolved)
    return tuple(added)


def _ai_runtime_site_packages_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured_site_packages = os.environ.get("FILMPIPE_AI_RUNTIME_SITE_PACKAGES")
    if configured_site_packages:
        candidates.append(Path(configured_site_packages))

    configured_venv = os.environ.get("FILMPIPE_AI_RUNTIME_VENV")
    if configured_venv:
        candidates.extend(_venv_site_packages(Path(configured_venv)))

    experiment_venv = Path("experiments") / "ai_restoration" / ".venv"
    if experiment_venv.exists():
        candidates.extend(_venv_site_packages(experiment_venv))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return tuple(unique)


def _venv_site_packages(venv_path: Path) -> tuple[Path, ...]:
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    return (
        venv_path / "lib" / version / "site-packages",
        venv_path / "lib64" / version / "site-packages",
    )


def default_models_root() -> Path:
    configured = os.environ.get("FILMPIPE_AI_MODELS_ROOT")
    if configured:
        return Path(configured)

    production_root = Path("models") / "ai_restoration"
    if production_root.exists():
        return production_root

    return Path("experiments") / "ai_restoration" / "models"


def read_image_artifact(path: Path | str) -> npt.NDArray[np.generic]:
    source = Path(path)
    encoded = np.frombuffer(source.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"OpenCV could not decode image: {source}")
    if image.ndim not in (2, 3):
        raise ValueError(f"Unsupported image dimensions: {image.ndim}")
    if image.dtype not in (np.uint8, np.uint16):
        raise ValueError(f"Unsupported image dtype: {image.dtype}")
    if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
        raise ValueError(f"Unsupported channel count: {image.shape[2]}")
    return image


def write_image(path: Path, image: npt.NDArray[np.generic]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError(f"OpenCV failed to encode image: {path}")
    path.write_bytes(encoded.tobytes())


def validate_probability_mask(
    probability: npt.NDArray[np.float32],
    expected_shape: tuple[int, int],
) -> npt.NDArray[np.float32]:
    probability = np.asarray(probability, dtype=np.float32)
    if probability.shape != expected_shape:
        raise ValueError(
            f"Probability mask shape {probability.shape} does not match {expected_shape}"
        )
    if not np.isfinite(probability).all():
        raise ValueError("Probability mask contains non-finite values")
    return np.clip(probability, 0.0, 1.0).astype(np.float32, copy=False)


def postprocess_mask(
    probability: npt.NDArray[np.float32],
    source_image: npt.NDArray[np.generic],
    *,
    threshold: float,
    dilation: int,
    mode: str = "scene_lines",
) -> MaskPostprocessResult:
    raw_binary = binary_mask_from_probability(probability, threshold)
    if mode in ("none", "off", ""):
        restoration = dilate_mask(raw_binary, dilation)
        return MaskPostprocessResult(
            binary_mask=raw_binary,
            restoration_mask=restoration,
            metadata={"mode": "none"},
        )
    if mode != "scene_lines":
        raise ValueError("mask_postprocess must be one of: scene_lines, none")

    gray8 = _robust_gray8(source_image)
    scene_support, hough_line_count = _scene_line_support(gray8)
    records, labels = _component_records(raw_binary, probability, gray8, scene_support)
    filtered, kept, removed, params = _filter_scene_line_components(
        records,
        labels,
        raw_binary.shape,
    )
    restoration = dilate_mask(filtered, dilation)
    return MaskPostprocessResult(
        binary_mask=filtered,
        restoration_mask=restoration,
        metadata={
            "mode": "scene_lines",
            "raw_component_count": len(records),
            "kept_component_count": len(kept),
            "removed_component_count": len(removed),
            "removed_pixels_before_dilation": int(
                sum(record["area"] for record in removed)
            ),
            "hough_line_count": hough_line_count,
            "parameters": params,
        },
    )


def binary_mask_from_probability(
    probability: npt.NDArray[np.float32],
    threshold: float,
) -> npt.NDArray[np.uint8]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    return ((probability >= threshold).astype(np.uint8) * 255).astype(np.uint8)


def dilate_mask(mask: npt.NDArray[np.uint8], dilation: int) -> npt.NDArray[np.uint8]:
    if dilation <= 0:
        return _binary_uint8(mask)
    kernel_size = int(dilation) * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.dilate(_binary_uint8(mask), kernel, iterations=1)


def composite_restoration(
    original: npt.NDArray[np.generic],
    restored_candidate: npt.NDArray[np.generic],
    restoration_mask: npt.NDArray[np.uint8],
) -> npt.NDArray[np.generic]:
    if original.shape != restored_candidate.shape:
        raise ValueError(f"Image shapes differ: {original.shape} != {restored_candidate.shape}")
    mask_bool = _mask_bool(restoration_mask, original.shape[:2])
    if not mask_bool.any():
        return original.copy()

    output = original.copy()
    if original.ndim == 2:
        output[mask_bool] = restored_candidate[mask_bool]
    else:
        output[mask_bool, :] = restored_candidate[mask_bool, :]
    return output


def to_model_rgb_uint8(image: npt.NDArray[np.generic]) -> npt.NDArray[np.uint8]:
    image8 = _to_uint8(image)
    if image8.ndim == 2:
        return cv2.cvtColor(image8, cv2.COLOR_GRAY2RGB)
    channels = int(image8.shape[2])
    if channels == 3:
        return cv2.cvtColor(image8, cv2.COLOR_BGR2RGB)
    if channels == 4:
        return cv2.cvtColor(image8, cv2.COLOR_BGRA2RGB)
    raise ValueError(f"Unsupported channel count for model input: {channels}")


def model_rgb_uint8_to_source(
    rgb: npt.NDArray[np.uint8],
    reference: npt.NDArray[np.generic],
) -> npt.NDArray[np.generic]:
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Model output must be RGB uint8")
    if rgb.shape[:2] != reference.shape[:2]:
        rgb = cv2.resize(
            rgb,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    if reference.ndim == 2:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        return _from_uint8(gray, reference.dtype)

    channels = int(reference.shape[2])
    if channels == 3:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        return _from_uint8(bgr, reference.dtype)
    if channels == 4:
        bgra = np.empty(reference.shape, dtype=np.uint8)
        bgra[:, :, :3] = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        bgra[:, :, 3] = _to_uint8(reference[:, :, 3])
        return _from_uint8(bgra, reference.dtype)
    raise ValueError(f"Unsupported reference channel count: {channels}")


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


def _exception_failure(
    *,
    stage: str,
    user_message: str,
    exc: Exception,
) -> ProcessorResult:
    return ProcessorResult.failure(
        ProcessingError.from_exception(
            stage=stage,
            user_message=user_message,
            exc=exc,
            recoverable=True,
        ),
        stop_pipeline=False,
    )


def _detector_failure_message(filename: str, exc: Exception) -> str:
    message = str(exc).lower()
    if "pytorch is not installed" in message or "no module named 'torch'" in message:
        return (
            "AI restoration недоступен: PyTorch не установлен. "
            "Выберите Restoration: Off или установите AI runtime."
        )
    return f"Не удалось обнаружить дефекты для restoration {filename}."


def _binary_uint8(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError("Mask must be 2D")
    return ((mask > 0).astype(np.uint8) * 255).astype(np.uint8)


def _mask8(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    return _binary_uint8(mask)


def _mask_bool(mask: npt.NDArray[np.uint8], shape: tuple[int, int]) -> npt.NDArray[np.bool_]:
    if mask.shape != shape:
        raise ValueError(f"Mask shape {mask.shape} does not match image shape {shape}")
    return mask > 0


def _robust_gray8(image: npt.NDArray[np.generic]) -> npt.NDArray[np.uint8]:
    if image.ndim == 3:
        if image.shape[2] == 4:
            gray = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    gray32 = gray.astype(np.float32)
    low, high = np.percentile(gray32, [0.5, 99.5])
    if high <= low:
        low = float(gray32.min()) if gray32.size else 0.0
        high = float(gray32.max()) if gray32.size else 1.0
    scaled = (gray32 - low) / max(float(high - low), 1e-6)
    return np.clip(np.round(scaled * 255.0), 0, 255).astype(np.uint8)


def _scene_line_support(gray8: npt.NDArray[np.uint8]) -> tuple[npt.NDArray[np.uint8], int]:
    blurred = cv2.GaussianBlur(gray8, (3, 3), 0)
    edges = cv2.Canny(blurred, 40, 120)
    min_line = max(35, int(min(gray8.shape[:2]) * 0.035))
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=35,
        minLineLength=min_line,
        maxLineGap=12,
    )
    support = np.zeros_like(gray8, dtype=np.uint8)
    line_count = 0
    if lines is not None:
        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = [int(value) for value in line]
            if math.hypot(x2 - x1, y2 - y1) < min_line:
                continue
            cv2.line(support, (x1, y1), (x2, y2), 255, thickness=9)
            line_count += 1
    support = cv2.dilate(
        support,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )
    return support, line_count


def _component_records(
    binary: npt.NDArray[np.uint8],
    probability: npt.NDArray[np.float32],
    gray8: npt.NDArray[np.uint8],
    scene_line_support: npt.NDArray[np.uint8],
) -> tuple[list[dict[str, Any]], npt.NDArray[np.int32]]:
    component_count, labels, stats, _centers = cv2.connectedComponentsWithStats(
        _binary_uint8(binary),
        connectivity=8,
    )
    top_hat = cv2.morphologyEx(
        gray8,
        cv2.MORPH_TOPHAT,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)),
    )
    records: list[dict[str, Any]] = []
    for label in range(1, component_count):
        x, y, width, height, area = [int(value) for value in stats[label]]
        component = labels == label
        ys, xs = np.nonzero(component)
        geometry = _pca_geometry(np.column_stack([xs, ys]))
        probs = probability[component]
        records.append(
            {
                "label": label,
                "x": x,
                "y": y,
                "w": width,
                "h": height,
                "area": area,
                "mean_prob": float(np.mean(probs)),
                "max_prob": float(np.max(probs)),
                "scene_line_overlap": float(np.mean(scene_line_support[component] > 0)),
                "bright_support": float(np.mean(top_hat[component])),
                "local_contrast": _local_contrast(component.astype(np.uint8), gray8),
                **geometry,
            }
        )
    return records, labels


def _filter_scene_line_components(
    records: list[dict[str, Any]],
    labels: npt.NDArray[np.int32],
    image_shape: tuple[int, int],
) -> tuple[npt.NDArray[np.uint8], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    height, width = image_shape
    max_expected_scratch = max(90.0, 0.24 * float(min(height, width)))
    filtered = np.zeros((height, width), dtype=np.uint8)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for record in records:
        reasons: list[str] = []
        line_like = record["elongation"] >= 8.0 or (
            record["pca_width"] <= 12.0 and record["pca_len"] >= 60.0
        )
        if line_like and record["pca_len"] > max_expected_scratch and record["area"] >= 80:
            reasons.append("over_expected_scratch_length")
        if (
            line_like
            and record["pca_len"] >= 50.0
            and record["area"] >= 25
            and record["local_contrast"] <= -0.035
        ):
            reasons.append("dark_scene_line")
        if (
            record["scene_line_overlap"] >= 0.35
            and record["area"] >= 3
            and record["local_contrast"] < 0.08
            and record["bright_support"] < 35.0
        ):
            reasons.append("hough_scene_fragment")

        output_record = {**record, "remove_reasons": reasons}
        if reasons:
            removed.append(output_record)
        else:
            kept.append(output_record)
            filtered[labels == record["label"]] = 255

    params = {
        "max_expected_scratch": max_expected_scratch,
        "line_like_elongation_min": 8.0,
        "line_like_length_min": 60.0,
        "dark_scene_contrast_max": -0.035,
        "hough_overlap_min": 0.35,
        "hough_contrast_max": 0.08,
        "hough_bright_support_max": 35.0,
    }
    return filtered, kept, removed, params


def _pca_geometry(points_xy: npt.NDArray[np.integer]) -> dict[str, float]:
    if len(points_xy) < 2:
        return {"pca_len": 1.0, "pca_width": 1.0, "elongation": 1.0, "angle": 0.0}
    centered = points_xy.astype(np.float32) - points_xy.mean(axis=0, keepdims=True)
    covariance = np.cov(centered.T)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    major = np.sqrt(max(float(eigenvalues[0]), 0.0)) * 4.0
    minor = np.sqrt(max(float(eigenvalues[1]), 0.0)) * 4.0 if len(eigenvalues) > 1 else 1.0
    return {
        "pca_len": max(major, 1.0),
        "pca_width": max(minor, 1.0),
        "elongation": max(major, 1.0) / max(minor, 1.0),
        "angle": math.degrees(
            math.atan2(float(eigenvectors[1, 0]), float(eigenvectors[0, 0]))
        ),
    }


def _local_contrast(component_mask: npt.NDArray[np.uint8], gray8: npt.NDArray[np.uint8]) -> float:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    expanded = cv2.dilate(component_mask, kernel, iterations=1)
    ring = (expanded > 0) & (component_mask == 0)
    component = component_mask > 0
    if not np.any(component) or not np.any(ring):
        return 0.0
    inside = float(np.median(gray8[component]))
    outside = float(np.median(gray8[ring]))
    return (inside - outside) / 255.0


def _prepare_detector_input(
    gray: npt.NDArray[np.float32],
    input_size: str,
) -> tuple[npt.NDArray[np.float32], Any]:
    height, width = gray.shape
    if input_size == "full_size":
        padded, crop = _pad_to_multiple(gray, 16)

        def restore(probability: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
            return probability[: crop[0], : crop[1]]

        return padded, restore

    if input_size == "scale_256":
        new_width, new_height = _scale_short_side(width, height, 256)
    elif input_size == "resize_256":
        new_width, new_height = 256, 256
    else:
        raise ValueError(
            "Detector input_size must be one of: full_size, scale_256, resize_256"
        )

    resized = cv2.resize(gray, (new_width, new_height), interpolation=cv2.INTER_CUBIC)

    def restore(probability: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        return cv2.resize(probability, (width, height), interpolation=cv2.INTER_LINEAR)

    return resized.astype(np.float32), restore


def _pad_to_multiple(
    image: npt.NDArray[np.float32],
    multiple: int,
) -> tuple[npt.NDArray[np.float32], tuple[int, int]]:
    height, width = image.shape
    padded_height = int(np.ceil(height / multiple) * multiple)
    padded_width = int(np.ceil(width / multiple) * multiple)
    pad_bottom = padded_height - height
    pad_right = padded_width - width
    if pad_bottom == 0 and pad_right == 0:
        return image, (height, width)
    padded = cv2.copyMakeBorder(
        image,
        0,
        pad_bottom,
        0,
        pad_right,
        borderType=cv2.BORDER_REFLECT_101,
    )
    return padded.astype(np.float32), (height, width)


def _scale_short_side(width: int, height: int, short_side: int) -> tuple[int, int]:
    if width < height:
        new_width = short_side
        new_height = round(height / width * short_side)
    else:
        new_height = short_side
        new_width = round(width / height * short_side)
    new_width = max(16, int(round(new_width / 16) * 16))
    new_height = max(16, int(round(new_height / 16) * 16))
    return new_width, new_height


def _tile_positions(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    step = max(1, tile_size - overlap)
    positions = list(range(0, length - tile_size + 1, step))
    last = length - tile_size
    if positions[-1] != last:
        positions.append(last)
    return positions


def _tile_weight(
    shape: tuple[int, int],
    *,
    overlap: int,
    touches_top: bool,
    touches_bottom: bool,
    touches_left: bool,
    touches_right: bool,
) -> npt.NDArray[np.float64]:
    height, width = shape
    weight_y = np.ones(height, dtype=np.float64)
    weight_x = np.ones(width, dtype=np.float64)
    ramp_y = min(overlap, height)
    ramp_x = min(overlap, width)
    epsilon = 1e-3

    if ramp_y > 1 and not touches_top:
        weight_y[:ramp_y] = np.linspace(epsilon, 1.0, ramp_y)
    if ramp_y > 1 and not touches_bottom:
        weight_y[-ramp_y:] = np.linspace(1.0, epsilon, ramp_y)
    if ramp_x > 1 and not touches_left:
        weight_x[:ramp_x] = np.linspace(epsilon, 1.0, ramp_x)
    if ramp_x > 1 and not touches_right:
        weight_x[-ramp_x:] = np.linspace(1.0, epsilon, ramp_x)

    return np.outer(weight_y, weight_x)


def _pad_tensor_to_modulo(torch: Any, tensor: Any, modulo: int) -> Any:
    height, width = tensor.shape[-2:]
    pad_height = (modulo - height % modulo) % modulo
    pad_width = (modulo - width % modulo) % modulo
    if pad_height == 0 and pad_width == 0:
        return tensor
    return torch.nn.functional.pad(
        tensor,
        pad=(0, pad_width, 0, pad_height),
        mode="reflect",
    )


def _to_uint8(image: npt.NDArray[np.generic]) -> npt.NDArray[np.uint8]:
    if image.dtype == np.uint8:
        return image
    if image.dtype == np.uint16:
        return np.round(image.astype(np.float64) / 65535.0 * 255.0).astype(np.uint8)
    raise ValueError(f"Unsupported image dtype for 8-bit conversion: {image.dtype}")


def _from_uint8(
    image: npt.NDArray[np.uint8],
    dtype: np.dtype[np.generic],
) -> npt.NDArray[np.generic]:
    dtype = np.dtype(dtype)
    if dtype == np.dtype(np.uint8):
        return image.astype(np.uint8, copy=False)
    if dtype == np.dtype(np.uint16):
        return np.round(image.astype(np.float64) / 255.0 * 65535.0).astype(np.uint16)
    raise ValueError(f"Unsupported target dtype for model output: {dtype}")


def _is_cuda_oom(exc: Exception) -> bool:
    message = str(exc).lower()
    return "cuda out of memory" in message or exc.__class__.__name__ == "OutOfMemoryError"
