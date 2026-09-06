from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
)
from logdigitizer.processing.minimum_cost_tracking import (
    track_downward_minimum_cost_result,
)


def test_continuous_tracker_stops_when_gap_exceeds_tolerance() -> None:
    image = np.full((42, 70), 235, dtype=np.uint8)
    image[:15, 33:36] = 35
    image[21:, 33:36] = 35
    settings = AutomaticTraceSettings(
        search_half_width_px=8,
        max_horizontal_step_px=4.0,
        max_consecutive_misses=2,
    )

    result = track_downward_minimum_cost_result(
        image,
        TracePoint(34.0, 4.0),
        settings=settings,
        line_width_px=3.0,
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(15.0)
    assert max(point.y_px for point in result.raw_samples_px) < 21.0


def test_gap_does_not_accumulate_extra_lateral_flexibility() -> None:
    image = np.full((44, 80), 235, dtype=np.uint8)
    image[:14, 29:32] = 35
    image[20:, 37:40] = 35
    settings = AutomaticTraceSettings(
        search_half_width_px=12,
        max_horizontal_step_px=2.0,
        max_consecutive_misses=10,
    )

    result = track_downward_minimum_cost_result(
        image,
        TracePoint(30.0, 4.0),
        settings=settings,
        line_width_px=3.0,
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert all(
        point.x_px < 36.0
        for point, confidence in zip(
            result.raw_samples_px,
            result.confidences,
        )
        if confidence > 0.0
    )
