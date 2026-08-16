from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from .io import to_model_rgb_uint8


@dataclass(frozen=True)
class DetectorResult:
    probability: npt.NDArray[np.float32]
    metadata: dict[str, Any]


@dataclass
class MicrosoftScratchDetector:
    models_root: Path
    device_preference: str = "auto"
    input_size: str = "full_size"
    tile_size: int = 0
    tile_overlap: int = 128

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

    def detect(self, image: npt.NDArray[np.generic]) -> DetectorResult:
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
                    "Retry with a smaller tile, for example: "
                    "--tile-size 768 --tile-overlap 96, or use --device cpu."
                ) from exc
            self.tile_size = 1024
            self.tile_overlap = min(self.tile_overlap, 128)
            probability = self._detect_tiled(rgb)
            mode = "tiled_after_cuda_oom"
            fallback_after_oom = True

        return DetectorResult(
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
                "Microsoft repo is missing. Run experiments/ai_restoration/download_models.py."
            )
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(
                "Microsoft scratch checkpoint is missing. Run download_models.py and check "
                f"{self.checkpoint_path}."
            )

        try:
            torch = importlib.import_module("torch")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "PyTorch is not installed in this experiment environment. "
                "From experiments/ai_restoration, run: "
                "python -m pip install -r requirements.txt. "
                "For NVIDIA GPU, install the CUDA PyTorch wheel from "
                "https://pytorch.org/get-started/locally/ first."
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
        with torch.no_grad():
            prediction = torch.sigmoid(model(tensor))
        probability = prediction[0, 0].detach().cpu().numpy().astype(np.float32)
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


def _is_cuda_oom(exc: Exception) -> bool:
    message = str(exc).lower()
    return "cuda out of memory" in message or exc.__class__.__name__ == "OutOfMemoryError"
