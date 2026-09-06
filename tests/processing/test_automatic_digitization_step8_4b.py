from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    TrackerStopReason,
    track_downward,
    track_downward_result,
)


def test_good_curve_reaches_image_bottom_with_high_confidence() -> None:
    image = np.full((30, 60), 240, dtype=np.uint8)
    image[:, 29:32] = 20

    result = track_downward_result(
        image,
        TracePoint(30.0, 5.0),
        settings=AutomaticTraceSettings(search_half_width_px=5),
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert result.stop_y_px is None
    assert result.raw_samples_px[0] == TracePoint(30.0, 5.0)
    assert result.raw_samples_px[-1] == TracePoint(30.0, 29.0)
    assert len(result.raw_samples_px) == 25
    assert len(result.confidences) == len(result.raw_samples_px)
    assert result.confidences[0] == pytest.approx(1.0)
    assert min(result.confidences[1:]) > 0.8


def test_curve_loss_stops_instead_of_following_blank_background() -> None:
    image = np.full((35, 70), 240, dtype=np.uint8)
    image[:18, 31:34] = 15

    result = track_downward_result(
        image,
        (32.0, 4.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=6,
            max_consecutive_misses=2,
        ),
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(18.0)
    assert result.consecutive_misses == 3
    assert result.raw_samples_px[-1].y_px == pytest.approx(17.0)
    assert all(point.y_px < 18.0 for point in result.raw_samples_px)


def test_isolated_unreliable_row_is_joined_after_reacquisition() -> None:
    image = np.full((24, 50), 240, dtype=np.uint8)
    image[:, 24] = 10
    image[12, :] = 10  # full-width dark row: no local contrast

    result = track_downward_result(
        image,
        (24.0, 5.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=5,
            max_consecutive_misses=1,
        ),
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    ys = [point.y_px for point in result.raw_samples_px]
    assert 12.0 in ys
    assert 11.0 in ys
    assert 13.0 in ys
    assert all(point.x_px == pytest.approx(24.0) for point in result.raw_samples_px)
    confidence_by_y = dict(zip(ys, result.confidences))
    assert confidence_by_y[12.0] == pytest.approx(0.0)


def test_horizontal_jump_limit_prevents_switch_to_nearby_competing_trace() -> None:
    image = np.full((30, 70), 245, dtype=np.uint8)
    image[:14, 19:22] = 30
    image[14:, 28] = 0  # dark competitor inside search window, but too far

    result = track_downward_result(
        image,
        (20.0, 3.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=12,
            max_horizontal_step_px=4.0,
            max_consecutive_misses=1,
        ),
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(14.0)
    assert max(point.x_px for point in result.raw_samples_px) <= 20.0


def test_local_window_still_prevents_jump_to_distant_darker_curve() -> None:
    image = np.full((25, 100), 245, dtype=np.uint8)
    image[:, 19:22] = 35
    image[:, 70] = 0

    raw = track_downward(
        image,
        (20.0, 2.0),
        settings=AutomaticTraceSettings(search_half_width_px=6),
    )

    assert all(point.x_px == pytest.approx(20.0) for point in raw)


def test_faint_but_continuous_curve_can_be_accepted_with_default_thresholds() -> None:
    image = np.full((22, 45), 240, dtype=np.uint8)
    image[:, 16] = 180  # 25% local contrast

    result = track_downward_result(
        image,
        (16.0, 2.0),
        settings=AutomaticTraceSettings(search_half_width_px=4),
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert result.raw_samples_px[-1] == TracePoint(16.0, 21.0)
    assert min(result.confidences[1:]) >= 0.45


def test_insufficient_contrast_stops_the_tracker() -> None:
    image = np.full((20, 40), 240, dtype=np.uint8)
    image[:8, 20] = 20
    image[8:, 20] = 235

    result = track_downward_result(
        image,
        (20.0, 2.0),
        settings=AutomaticTraceSettings(
            search_half_width_px=4,
            min_contrast_ratio=0.08,
            max_consecutive_misses=0,
        ),
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(8.0)
    assert result.raw_samples_px[-1].y_px == pytest.approx(7.0)


def test_float_zero_to_one_image_uses_same_relative_contrast_logic() -> None:
    image = np.full((18, 36), 0.9, dtype=float)
    image[:, 13] = 0.2

    result = track_downward_result(
        image,
        (13.0, 3.0),
        settings=AutomaticTraceSettings(search_half_width_px=4),
    )

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert result.raw_samples_px[-1] == TracePoint(13.0, 17.0)


def test_track_downward_keeps_backward_compatible_tuple_api() -> None:
    image = np.full((12, 30), 230, dtype=np.uint8)
    image[:, 9:12] = 20

    raw = track_downward(
        image,
        TracePoint(10.0, 2.0),
        settings=AutomaticTraceSettings(search_half_width_px=4),
    )

    assert isinstance(raw, tuple)
    assert raw[0] == TracePoint(10.0, 2.0)
    assert raw[-1] == TracePoint(10.0, 11.0)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("search_half_width_px", 0, "positive integer"),
        ("search_half_width_px", True, "positive integer"),
        ("min_contrast_ratio", -0.01, "between 0 and 1"),
        ("min_contrast_ratio", 1.01, "between 0 and 1"),
        ("max_horizontal_step_px", 0, "positive finite"),
        ("max_horizontal_step_px", float("inf"), "positive finite"),
        ("min_confidence", -0.1, "between 0 and 1"),
        ("min_confidence", 1.1, "between 0 and 1"),
        ("max_consecutive_misses", -1, "non-negative integer"),
        ("max_consecutive_misses", 1.5, "non-negative integer"),
    ],
)
def test_invalid_confidence_settings_are_rejected(
    field: str,
    value,
    message: str,
) -> None:
    kwargs = {field: value}
    with pytest.raises(AutomaticTraceError, match=message):
        AutomaticTraceSettings(**kwargs)


def test_blank_image_stops_quickly_with_low_confidence() -> None:
    image = np.full((18, 40), 220, dtype=np.uint8)

    result = track_downward_result(
        image,
        (20.0, 3.0),
        settings=AutomaticTraceSettings(max_consecutive_misses=1),
    )

    assert result.stop_reason is TrackerStopReason.LOST_CURVE
    assert result.stop_y_px == pytest.approx(4.0)
    assert result.stop_confidence is not None
    assert result.stop_confidence < 0.45
    assert result.raw_samples_px == (TracePoint(20.0, 3.0),)
