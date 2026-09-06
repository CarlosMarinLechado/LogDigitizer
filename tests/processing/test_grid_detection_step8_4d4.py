from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.processing.grid_detection import (
    GridDetectionError,
    detect_grid_penalty_mask,
)


def make_grid_image(height: int = 180, width: int = 180) -> np.ndarray:
    image = np.full((height, width), 235, dtype=np.uint8)
    for x in range(20, width, 30):
        image[:, max(0, x - 1) : min(width, x + 2)] = 65
    for y in range(15, height, 30):
        image[max(0, y - 1) : min(height, y + 2), :] = 65
    return image


def test_hough_detects_repeated_grid_families() -> None:
    result = detect_grid_penalty_mask(make_grid_image(), line_width_px=3.0)
    assert result.has_grid
    assert result.vertical_line_count >= 3
    assert result.horizontal_line_count >= 3
    assert result.penalty_mask.dtype == np.float32
    assert float(np.max(result.penalty_mask)) == pytest.approx(1.0)


def test_single_long_straight_line_is_not_grid() -> None:
    image = np.full((180, 180), 235, dtype=np.uint8)
    image[:, 89:92] = 40
    result = detect_grid_penalty_mask(image, line_width_px=3.0)
    assert result.vertical_line_count == 0
    assert result.horizontal_line_count == 0
    assert not np.any(result.penalty_mask)


def test_isolated_parallel_curve_is_excluded_from_regular_grid_family() -> None:
    image = np.full((180, 180), 235, dtype=np.uint8)
    for x in (20, 50, 80, 110, 140):
        image[:, x - 1 : x + 2] = 55
    image[:, 88:91] = 25

    result = detect_grid_penalty_mask(image, line_width_px=3.0)

    grid_penalty = float(np.mean(result.penalty_mask[:, 80]))
    curve_penalty = float(np.mean(result.penalty_mask[:, 89]))
    assert result.vertical_line_count >= 4
    assert grid_penalty > 0.60
    assert curve_penalty < 0.50


def test_grid_mask_does_not_modify_source() -> None:
    image = make_grid_image()
    before = image.copy()
    detect_grid_penalty_mask(image, line_width_px=3.0)
    assert np.array_equal(image, before)


def test_rgb_input_is_supported() -> None:
    gray = make_grid_image()
    rgb = np.repeat(gray[..., None], 3, axis=2)
    result = detect_grid_penalty_mask(rgb, line_width_px=3.0)
    assert result.has_grid
    assert result.penalty_mask.shape == gray.shape


@pytest.mark.parametrize("bad_width", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_line_width_is_rejected(bad_width: float) -> None:
    with pytest.raises(GridDetectionError, match="positive finite"):
        detect_grid_penalty_mask(
            make_grid_image(),
            line_width_px=bad_width,
        )
