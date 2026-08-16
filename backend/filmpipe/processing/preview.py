from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


PREVIEW_MIME_TYPE = "image/png"


class PreviewRenderError(RuntimeError):
    pass


def render_preview_png(path: Path | str) -> bytes:
    source = Path(path)
    try:
        encoded = np.frombuffer(source.read_bytes(), dtype=np.uint8)
    except OSError as exc:
        raise PreviewRenderError(f"Could not read artifact: {source}") from exc

    if encoded.size == 0:
        raise PreviewRenderError("Artifact is empty")

    image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise PreviewRenderError("Artifact could not be decoded as an image")

    preview = _to_browser_u8(image)
    ok, preview_bytes = cv2.imencode(".png", preview)
    if not ok:
        raise PreviewRenderError("OpenCV failed to encode PNG preview")

    return preview_bytes.tobytes()


def _to_browser_u8(image: np.ndarray) -> np.ndarray:
    if image.ndim not in {2, 3}:
        raise PreviewRenderError(f"Unsupported preview dimensions: {image.ndim}")
    if image.ndim == 3 and image.shape[2] not in {1, 3, 4}:
        raise PreviewRenderError(f"Unsupported preview channel count: {image.shape[2]}")

    if image.dtype == np.uint8:
        return image

    if image.dtype == np.uint16:
        return np.rint(image.astype(np.float32) / np.iinfo(np.uint16).max * 255).astype(
            np.uint8
        )

    raise PreviewRenderError(f"Unsupported preview dtype: {image.dtype}")
