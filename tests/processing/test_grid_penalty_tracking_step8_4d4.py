from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
)
from logdigitizer.processing.grid_detection import detect_grid_penalty_mask
from logdigitizer.processing.minimum_cost_tracking import (
    track_downward_minimum_cost_result,
)


def test_grid_penalty_keeps_tracker_on_curve_near_darker_grid() -> None:
    image = np.full((140, 140), 235, dtype=np.uint8)
    for x in (25, 50, 75, 100, 125):
        image[:, x - 1 : x + 2] = 25
    for y in (20, 45, 70, 95, 120):
        image[y - 1 : y + 2, :] = np.minimum(
            image[y - 1 : y + 2, :],
            45,
        )
    image[:, 81:84] = 65

    grid = detect_grid_penalty_mask(image, line_width_px=3.0)
    result = track_downward_minimum_cost_result(
        image,
        TracePoint(82.0, 5.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=12,
            max_horizontal_step_px=6.0,
            max_consecutive_misses=4,
        ),
        line_width_px=3.0,
        grid_penalty_mask=grid.penalty_mask,
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert abs(result.raw_samples_px[-1].x_px - 82.0) <= 2.0


def test_horizontal_grid_is_crossable_because_mask_is_soft() -> None:
    image = np.full((130, 130), 235, dtype=np.uint8)
    for x in (15, 40, 65, 90, 115):
        image[:, x - 1 : x + 2] = 70
    for y in (20, 45, 70, 95, 120):
        image[y - 1 : y + 2, :] = 70
    image[:, 74:77] = 35

    grid = detect_grid_penalty_mask(image, line_width_px=3.0)
    result = track_downward_minimum_cost_result(
        image,
        TracePoint(75.0, 5.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=10,
            max_horizontal_step_px=5.0,
            max_consecutive_misses=4,
        ),
        line_width_px=3.0,
        grid_penalty_mask=grid.penalty_mask,
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert result.raw_samples_px[-1].y_px == pytest.approx(129.0)


def test_grid_mask_shape_is_validated() -> None:
    image = np.full((30, 40), 230, dtype=np.uint8)
    image[:, 19:22] = 30

    with pytest.raises(ValueError, match="match"):
        track_downward_minimum_cost_result(
            image,
            (20.0, 3.0),
            grid_penalty_mask=np.zeros((10, 10), dtype=float),
        )
