from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1] / "experiments" / "ai_restoration"
sys.path.insert(0, str(EXPERIMENT_ROOT))

from ai_restoration.composite import change_metrics, composite_restoration  # noqa: E402
from ai_restoration.masks import binary_mask_from_probability, dilate_mask  # noqa: E402
from ai_restoration.restorers import telea_restore  # noqa: E402


def test_composite_keeps_pixels_outside_mask_identical_for_uint16():
    original = np.arange(25, dtype=np.uint16).reshape(5, 5)
    candidate = np.full((5, 5), 65535, dtype=np.uint16)
    mask = np.zeros((5, 5), dtype=np.uint8)
    mask[2, 2] = 255

    restored = composite_restoration(original, candidate, mask)
    metrics = change_metrics(original, restored, mask)

    assert restored.dtype == np.uint16
    assert restored[2, 2] == 65535
    assert np.array_equal(restored[mask == 0], original[mask == 0])
    assert metrics.changed_pixels_outside_mask == 0
    assert metrics.changed_pixels_inside_mask == 1


def test_mask_threshold_and_dilation_are_separate_steps():
    probability = np.zeros((7, 7), dtype=np.float32)
    probability[3, 3] = 0.41

    binary = binary_mask_from_probability(probability, threshold=0.4)
    restoration = dilate_mask(binary, dilation=1)

    assert np.count_nonzero(binary) == 1
    assert np.count_nonzero(restoration) > np.count_nonzero(binary)
    assert restoration[3, 3] == 255


def test_telea_restoration_preserves_uint16_shape_and_dtype():
    image = np.tile(np.linspace(0, 65535, 32, dtype=np.uint16), (16, 1))
    damaged = image.copy()
    damaged[:, 15] = 0
    mask = np.zeros(damaged.shape, dtype=np.uint8)
    mask[:, 15] = 255

    candidate = telea_restore(damaged, mask, radius=3.0)
    restored = composite_restoration(damaged, candidate.image, mask)
    metrics = change_metrics(damaged, restored, mask)

    assert restored.shape == damaged.shape
    assert restored.dtype == np.uint16
    assert metrics.changed_pixels_outside_mask == 0
    assert np.count_nonzero(cv2.absdiff(restored, damaged)) > 0
