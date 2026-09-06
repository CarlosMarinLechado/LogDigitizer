from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
    track_downward_result,
)
from logdigitizer.processing.minimum_cost_tracking import (
    build_grayscale_cost_map,
    track_downward_minimum_cost_result,
)


def test_cost_map_is_grayscale_native_and_prefers_dark_stroke() -> None:
    block = np.full((8, 31), 205, dtype=np.uint8)
    block[:, 14:17] = 95

    cost = build_grayscale_cost_map(
        block,
        line_width_px=3.0,
    )

    assert cost.shape == block.shape
    assert np.all((0.0 <= cost) & (cost <= 1.0))
    assert np.mean(cost[:, 15]) < np.mean(cost[:, 3])


def test_minimum_cost_path_looks_ahead_past_single_row_darker_distractor() -> None:
    image = np.full((28, 55), 235, dtype=np.uint8)

    # True continuous 3-pixel curve.
    image[:, 24:27] = 60

    # A one-row darker spot sits nearby. The old greedy tracker follows the
    # darkest row candidate; the short-horizon path should stay on the
    # persistent curve because it evaluates several future rows at once.
    image[12, 20:23] = 0

    settings = AutomaticTraceSettings(
        search_half_width_px=8,
        max_horizontal_step_px=6.0,
        max_consecutive_misses=1,
    )

    greedy = track_downward_result(
        image,
        TracePoint(25.0, 4.0),
        settings=settings,
    )
    path = track_downward_minimum_cost_result(
        image,
        TracePoint(25.0, 4.0),
        settings=settings,
        line_width_px=3.0,
    )

    greedy_x_at_12 = {
        int(point.y_px): point.x_px
        for point in greedy.raw_samples_px
    }[12]
    path_x_at_12 = {
        int(point.y_px): point.x_px
        for point in path.raw_samples_px
    }[12]

    assert greedy_x_at_12 < 24.0
    assert path_x_at_12 == pytest.approx(25.0, abs=1.0)
    assert path.stop_reason is TrackerStopReason.IMAGE_BOTTOM


def test_horizontal_dark_grid_row_does_not_force_sideways_jump() -> None:
    image = np.full((30, 60), 235, dtype=np.uint8)
    image[:, 29:32] = 45
    image[15, :] = 25

    result = track_downward_minimum_cost_result(
        image,
        TracePoint(30.0, 4.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=8,
            max_horizontal_step_px=5.0,
            max_consecutive_misses=2,
        ),
        line_width_px=3.0,
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    after = {
        int(point.y_px): point.x_px
        for point in result.raw_samples_px
        if point.y_px >= 16.0
    }
    assert after
    assert all(abs(x - 30.0) <= 1.0 for x in after.values())


def test_blank_region_still_stops_and_does_not_persist_synthetic_gap_rows() -> None:
    image = np.full((34, 60), 235, dtype=np.uint8)
    image[:16, 28:31] = 35
    image[22:, 28:31] = 35

    result = track_downward_minimum_cost_result(
        image,
        TracePoint(29.0, 4.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=8,
            max_horizontal_step_px=5.0,
            max_consecutive_misses=2,
        ),
        line_width_px=3.0,
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(16.0)
    assert result.raw_samples_px[-1].y_px == pytest.approx(15.0)
    assert all(
        point.y_px < 16.0
        for point in result.raw_samples_px
    )


def test_rgb_input_is_supported() -> None:
    gray = np.full((20, 45), 225, dtype=np.uint8)
    gray[:, 18:21] = 40
    rgb = np.repeat(gray[..., None], 3, axis=2)

    result = track_downward_minimum_cost_result(
        rgb,
        (19.0, 3.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=6,
        ),
        line_width_px=3.0,
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert result.raw_samples_px[-1].y_px == pytest.approx(19.0)
