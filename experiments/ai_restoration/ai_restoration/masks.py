from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from .io import write_image


def load_probability_mask(path: Path, expected_shape: tuple[int, int]) -> npt.NDArray[np.float32]:
    probability = np.load(path).astype(np.float32)
    return validate_probability_mask(probability, expected_shape)


def save_probability_mask(
    probability: npt.NDArray[np.float32],
    npy_path: Path,
    png_path: Path,
) -> None:
    npy_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy_path, validate_probability_mask(probability, probability.shape))
    probability_png = np.clip(np.round(probability * 65535.0), 0, 65535).astype(np.uint16)
    write_image(png_path, probability_png)


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


def postprocess_mask(
    probability: npt.NDArray[np.float32],
    source_image: npt.NDArray[np.generic],
    *,
    threshold: float,
    dilation: int,
    mode: str = "scene_lines",
) -> tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8], dict[str, Any], dict[str, np.ndarray]]:
    raw_binary = binary_mask_from_probability(probability, threshold)
    if mode in ("none", "off", ""):
        restoration = dilate_mask(raw_binary, dilation)
        return raw_binary, restoration, {"mode": "none"}, {"raw_binary_mask": raw_binary}
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
    metadata = {
        "mode": "scene_lines",
        "raw_component_count": len(records),
        "kept_component_count": len(kept),
        "removed_component_count": len(removed),
        "removed_pixels_before_dilation": int(sum(record["area"] for record in removed)),
        "hough_line_count": hough_line_count,
        "parameters": params,
        "removed_components_sample": removed[:50],
    }
    debug_images = {
        "raw_binary_mask": raw_binary,
        "filtered_binary_mask": filtered,
        "scene_line_support": scene_support,
    }
    return filtered, restoration, metadata, debug_images


def save_mask_png(path: Path, mask: npt.NDArray[np.uint8]) -> None:
    write_image(path, _binary_uint8(mask))


def mask_coverage_percent(mask: npt.NDArray[np.uint8]) -> float:
    if mask.size == 0:
        return 0.0
    return float(np.count_nonzero(mask > 0) / mask.size * 100.0)


def _binary_uint8(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError("Mask must be 2D")
    return ((mask > 0).astype(np.uint8) * 255).astype(np.uint8)


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
        "angle": math.degrees(math.atan2(float(eigenvectors[1, 0]), float(eigenvectors[0, 0]))),
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
