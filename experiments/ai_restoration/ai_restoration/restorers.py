from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from .io import model_rgb_uint8_to_source, to_model_rgb_uint8, write_image


@dataclass(frozen=True)
class RestorerCandidate:
    image: npt.NDArray[np.generic]
    metadata: dict[str, Any]


def telea_restore(
    image: npt.NDArray[np.generic],
    restoration_mask: npt.NDArray[np.uint8],
    *,
    radius: float = 3.0,
) -> RestorerCandidate:
    mask = _mask8(restoration_mask)
    if not np.any(mask):
        return RestorerCandidate(
            image=image.copy(),
            metadata={"name": "opencv_telea", "device": "cpu", "radius": radius},
        )

    if image.ndim == 2:
        restored = cv2.inpaint(image, mask, float(radius), cv2.INPAINT_TELEA)
    elif image.ndim == 3 and image.shape[2] == 3 and image.dtype == np.uint8:
        restored = cv2.inpaint(image, mask, float(radius), cv2.INPAINT_TELEA)
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        restored = image.copy()
        for channel in range(3):
            restored[:, :, channel] = cv2.inpaint(
                image[:, :, channel],
                mask,
                float(radius),
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
            "radius": radius,
        },
    )


@dataclass
class LaMaCliRestorer:
    models_root: Path
    python_executable: str | None = None
    device_preference: str = "auto"
    refine: bool = False

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
                "LaMa files are missing. Run experiments/ai_restoration/download_models.py. "
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

        with torch.no_grad():
            masked_image = image_tensor * (1.0 - mask_tensor)
            model_input = torch.cat([masked_image, mask_tensor], dim=1)
            predicted = model(model_input)
            inpainted = mask_tensor * predicted + (1.0 - mask_tensor) * image_tensor

        output = inpainted[0, :, :height, :width].permute(1, 2, 0)
        output_np = output.detach().cpu().numpy()
        return np.clip(np.round(output_np * 255.0), 0, 255).astype(np.uint8)

    def _load_native_model(self) -> Any:
        if self._model is not None:
            return self._model

        self._validate_paths()
        if str(self.repo_path) not in sys.path:
            sys.path.insert(0, str(self.repo_path))

        try:
            torch = importlib.import_module("torch")
            yaml = importlib.import_module("yaml")
            omegaconf = importlib.import_module("omegaconf")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "LaMa runtime dependencies are missing. From experiments/ai_restoration, "
                "run: python -m pip install -r requirements-lama.txt"
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
            "implementation_note": "Loads the official LaMa generator/checkpoint directly to avoid the outdated official bin/predict.py dependency stack.",
            "refine": self.refine,
            "bit_depth_note": "LaMa inference uses 8-bit RGB working copy; final composite preserves source dtype outside mask.",
        }
        if status_note is not None:
            metadata["status_note"] = status_note
        return metadata


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


def _mask8(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    if mask.ndim != 2:
        raise ValueError("Restoration mask must be 2D")
    return ((mask > 0).astype(np.uint8) * 255).astype(np.uint8)
