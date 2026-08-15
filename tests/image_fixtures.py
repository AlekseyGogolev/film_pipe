from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def synthetic_bw_negative_16bit(
    *,
    width: int = 64,
    height: int = 32,
) -> np.ndarray:
    positive = np.tile(
        np.linspace(0, np.iinfo(np.uint16).max, width, dtype=np.uint16),
        (height, 1),
    )
    return np.iinfo(np.uint16).max - positive


def write_image(path: Path, image: np.ndarray) -> Path:
    ok, encoded = cv2.imencode(path.suffix, image)
    assert ok
    path.write_bytes(encoded.tobytes())
    return path


def read_image(path: Path) -> np.ndarray:
    encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    assert image is not None
    return image
