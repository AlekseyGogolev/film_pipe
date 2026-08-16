from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class FilmImage:
    """Processing-local image representation.

    Domain contracts intentionally accept ``Any`` so OpenCV/NumPy stay inside
    the processing implementation instead of leaking into application/API code.
    """

    data: npt.NDArray[np.generic]
    bit_depth: int
    source_path: Path
    filename: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_data(
        self,
        data: npt.NDArray[np.float32],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> FilmImage:
        return FilmImage(
            data=normalized_grayscale_array(data),
            bit_depth=self.bit_depth,
            source_path=self.source_path,
            filename=self.filename,
            metadata={**self.metadata, **(metadata or {})},
        )


def normalized_grayscale_array(data: Any) -> npt.NDArray[np.float32]:
    array = np.asarray(data)
    if array.ndim != 2:
        raise ValueError("Expected a 2D grayscale image")
    if array.size == 0 or array.shape[0] <= 0 or array.shape[1] <= 0:
        raise ValueError("Image must have non-zero width and height")

    normalized = array.astype(np.float32, copy=False)
    if not np.isfinite(normalized).all():
        raise ValueError("Image contains non-finite pixel values")

    return np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)
