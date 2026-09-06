from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.line_width import (
    LineWidthEstimationError,
    estimate_line_width,
)


def vertical_stroke(
    *,
    width_px: int,
    background: int = 230,
    foreground: int = 40,
    size: int = 101,
) -> np.ndarray:
    image = np.full((size, size), background, dtype=np.uint8)
    centre = size // 2
    left = centre - width_px // 2
    image[:, left : left + width_px] = foreground
    return image


@pytest.mark.parametrize(
    ("true_width", "expected_tolerance"),
    [
        (1, 1.0),
        (3, 1.0),
        (5, 1.0),
        (7, 1.5),
        (11, 2.0),
    ],
)
def test_estimates_vertical_stroke_width(
    true_width: int,
    expected_tolerance: float,
) -> None:
    image = vertical_stroke(width_px=true_width)

    result = estimate_line_width(image, (50.0, 50.0))

    assert result.width_px == pytest.approx(
        float(true_width),
        abs=expected_tolerance,
    )
    assert result.contrast_ratio > 0.7


def test_grayscale_not_binary_contrast_is_supported() -> None:
    image = vertical_stroke(
        width_px=5,
        background=175,
        foreground=105,
    )

    result = estimate_line_width(image, (50.0, 50.0))

    assert result.width_px == pytest.approx(5.0, abs=1.0)
    assert 0.30 < result.contrast_ratio < 0.50
    assert result.background_intensity > result.line_intensity


def test_click_can_snap_a_few_pixels_to_the_curve() -> None:
    image = vertical_stroke(width_px=5)

    result = estimate_line_width(image, (53.0, 50.0))

    assert abs(result.sample_point_px.x_px - 50.0) <= 2.0
    assert result.width_px == pytest.approx(5.0, abs=1.0)


def test_resolution_scaling_changes_estimate_proportionally() -> None:
    low = vertical_stroke(width_px=3, size=81)
    high = np.repeat(np.repeat(low, 3, axis=0), 3, axis=1)

    low_result = estimate_line_width(low, (40.0, 40.0))
    high_result = estimate_line_width(high, (121.0, 121.0))

    ratio = high_result.width_px / low_result.width_px
    assert ratio == pytest.approx(3.0, rel=0.25)


def test_grid_crossing_does_not_turn_width_into_full_grid_line_length() -> None:
    image = np.full((121, 121), 235, dtype=np.uint8)

    # Thick curve-like diagonal.
    for offset in range(-2, 3):
        for y in range(15, 106):
            x = y + offset
            if 0 <= x < image.shape[1]:
                image[y, x] = 35

    # Long horizontal and vertical grid lines crossing at the click.
    image[59:62, :] = np.minimum(image[59:62, :], 70)
    image[:, 59:62] = np.minimum(image[:, 59:62], 70)

    result = estimate_line_width(image, TracePoint(60.0, 60.0))

    # The exact rasterized diagonal thickness depends on orientation, but the
    # multi-angle estimate must remain local rather than measuring the long grid.
    assert 2.0 <= result.width_px <= 10.0


def test_rgb_input_is_supported() -> None:
    gray = vertical_stroke(width_px=5)
    rgb = np.repeat(gray[..., None], 3, axis=2)

    result = estimate_line_width(rgb, (50.0, 50.0))

    assert result.width_px == pytest.approx(5.0, abs=1.0)


def test_blank_low_contrast_image_is_rejected() -> None:
    image = np.full((60, 60), 210, dtype=np.uint8)

    with pytest.raises(LineWidthEstimationError, match="contrast"):
        estimate_line_width(image, (30.0, 30.0))


def test_point_outside_image_is_rejected() -> None:
    image = vertical_stroke(width_px=3)

    with pytest.raises(LineWidthEstimationError, match="inside"):
        estimate_line_width(image, (-1.0, 50.0))
