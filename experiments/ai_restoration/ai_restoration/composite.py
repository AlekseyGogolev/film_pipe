from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class ChangeMetrics:
    changed_pixels_outside_mask: int
    changed_pixels_inside_mask: int
    max_abs_diff_outside_mask: float
    max_abs_diff_inside_mask: float


def composite_restoration(
    original: npt.NDArray[np.generic],
    restored_candidate: npt.NDArray[np.generic],
    restoration_mask: npt.NDArray[np.uint8],
    *,
    feather_radius: int = 0,
) -> npt.NDArray[np.generic]:
    _validate_same_image_shape(original, restored_candidate)
    mask_bool = _mask_bool(restoration_mask, original.shape[:2])
    if not mask_bool.any():
        return original.copy()

    if feather_radius <= 0:
        output = original.copy()
        if original.ndim == 2:
            output[mask_bool] = restored_candidate[mask_bool]
        else:
            output[mask_bool, :] = restored_candidate[mask_bool, :]
        return output

    alpha = _feather_alpha(mask_bool, feather_radius)
    if original.ndim == 3:
        alpha = alpha[:, :, np.newaxis]

    blended = (
        original.astype(np.float64) * (1.0 - alpha)
        + restored_candidate.astype(np.float64) * alpha
    )
    return _cast_like(blended, original)


def change_metrics(
    original: npt.NDArray[np.generic],
    restored: npt.NDArray[np.generic],
    restoration_mask: npt.NDArray[np.uint8],
) -> ChangeMetrics:
    _validate_same_image_shape(original, restored)
    mask_bool = _mask_bool(restoration_mask, original.shape[:2])
    diff = np.abs(restored.astype(np.float64) - original.astype(np.float64))
    spatial_diff = diff if diff.ndim == 2 else diff.max(axis=2)

    outside = ~mask_bool
    inside = mask_bool
    changed_outside = int(np.count_nonzero(spatial_diff[outside] > 0))
    changed_inside = int(np.count_nonzero(spatial_diff[inside] > 0))
    max_outside = float(spatial_diff[outside].max()) if outside.any() else 0.0
    max_inside = float(spatial_diff[inside].max()) if inside.any() else 0.0

    return ChangeMetrics(
        changed_pixels_outside_mask=changed_outside,
        changed_pixels_inside_mask=changed_inside,
        max_abs_diff_outside_mask=max_outside,
        max_abs_diff_inside_mask=max_inside,
    )


def diff_visualization(
    original: npt.NDArray[np.generic],
    restored: npt.NDArray[np.generic],
) -> npt.NDArray[np.uint8]:
    _validate_same_image_shape(original, restored)
    diff = np.abs(restored.astype(np.float64) - original.astype(np.float64))
    spatial_diff = diff if diff.ndim == 2 else diff.max(axis=2)
    max_diff = float(spatial_diff.max()) if spatial_diff.size else 0.0
    if max_diff <= 0.0:
        return np.zeros(spatial_diff.shape, dtype=np.uint8)
    return np.clip(np.round(spatial_diff / max_diff * 255.0), 0, 255).astype(np.uint8)


def _validate_same_image_shape(
    first: npt.NDArray[np.generic],
    second: npt.NDArray[np.generic],
) -> None:
    if first.shape != second.shape:
        raise ValueError(f"Image shapes differ: {first.shape} != {second.shape}")


def _mask_bool(mask: npt.NDArray[np.uint8], shape: tuple[int, int]) -> npt.NDArray[np.bool_]:
    if mask.shape != shape:
        raise ValueError(f"Mask shape {mask.shape} does not match image shape {shape}")
    return mask > 0


def _feather_alpha(mask_bool: npt.NDArray[np.bool_], feather_radius: int) -> npt.NDArray[np.float64]:
    radius = max(1, int(feather_radius))
    kernel_size = radius * 2 + 1
    alpha = mask_bool.astype(np.float64)
    alpha = cv2.GaussianBlur(alpha, (kernel_size, kernel_size), sigmaX=0)
    alpha[~mask_bool] = 0.0
    max_alpha = float(alpha.max()) if alpha.size else 0.0
    if max_alpha > 0.0:
        alpha = alpha / max_alpha
    return np.clip(alpha, 0.0, 1.0)


def _cast_like(
    image: npt.NDArray[np.float64],
    reference: npt.NDArray[np.generic],
) -> npt.NDArray[np.generic]:
    if np.issubdtype(reference.dtype, np.integer):
        info = np.iinfo(reference.dtype)
        return np.clip(np.round(image), info.min, info.max).astype(reference.dtype)
    return image.astype(reference.dtype)
