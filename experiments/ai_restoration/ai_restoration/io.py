from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt


SUPPORTED_INPUT_SUFFIXES = frozenset({".tif", ".tiff", ".png", ".jpg", ".jpeg"})
LOSSLESS_OUTPUT_SUFFIXES = frozenset({".tif", ".tiff", ".png"})


@dataclass(frozen=True)
class ImageData:
    path: Path
    array: npt.NDArray[np.generic]
    bit_depth: int
    max_value: int
    channels: int

    @property
    def height(self) -> int:
        return int(self.array.shape[0])

    @property
    def width(self) -> int:
        return int(self.array.shape[1])

    @property
    def output_suffix(self) -> str:
        suffix = self.path.suffix.lower()
        return suffix if suffix in LOSSLESS_OUTPUT_SUFFIXES else ".tiff"


def read_image(path: Path) -> ImageData:
    path = Path(path)
    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"OpenCV could not decode image: {path}")
    if image.ndim not in (2, 3):
        raise ValueError(f"Unsupported image dimensions: {image.ndim}")
    if image.dtype == np.uint8:
        bit_depth = 8
        max_value = int(np.iinfo(np.uint8).max)
    elif image.dtype == np.uint16:
        bit_depth = 16
        max_value = int(np.iinfo(np.uint16).max)
    else:
        raise ValueError(f"Unsupported image dtype: {image.dtype}")
    channels = 1 if image.ndim == 2 else int(image.shape[2])
    if channels not in (1, 3, 4):
        raise ValueError(f"Unsupported channel count: {channels}")
    return ImageData(
        path=path,
        array=image,
        bit_depth=bit_depth,
        max_value=max_value,
        channels=channels,
    )


def write_image(path: Path, image: npt.NDArray[np.generic]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError(f"OpenCV failed to encode image: {path}")
    path.write_bytes(encoded.tobytes())


def copy_positive(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def iter_input_images(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Input path not found: {path}")
    images = [
        child
        for child in path.iterdir()
        if child.is_file() and child.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    ]
    return sorted(images)


def safe_stem(path: Path) -> str:
    stem = Path(path).stem or "image"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return safe or "image"


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
