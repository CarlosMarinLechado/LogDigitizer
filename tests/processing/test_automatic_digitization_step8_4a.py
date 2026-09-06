from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    track_downward,
)


def test_tracks_center_of_vertical_dark_stroke_one_sample_per_row() -> None:
    image = np.full((30, 60), 240, dtype=np.uint8)
    image[:, 29:32] = 20

    raw = track_downward(
        image,
        TracePoint(30.0, 5.0),
        settings=AutomaticTraceSettings(search_half_width_px=5),
    )

    assert len(raw) == 25
    assert raw[0] == TracePoint(30.0, 5.0)
    assert [point.y_px for point in raw] == list(map(float, range(5, 30)))
    assert all(point.x_px == pytest.approx(30.0) for point in raw)


def test_tracks_slowly_sloping_curve_inside_local_window() -> None:
    image = np.full((45, 80), 235, dtype=np.uint8)
    expected_x: dict[int, float] = {}

    for y in range(45):
        x = 18 + y // 4
        expected_x[y] = float(x)
        image[y, x] = 10

    raw = track_downward(
        image,
        (expected_x[4], 4.0),
        settings=AutomaticTraceSettings(search_half_width_px=3),
    )

    by_y = {int(point.y_px): point.x_px for point in raw}
    assert set(by_y) == set(range(4, 45))
    for y, expected in expected_x.items():
        if y >= 4:
            assert by_y[y] == pytest.approx(expected)


def test_local_window_prevents_jump_to_distant_darker_curve() -> None:
    image = np.full((25, 100), 245, dtype=np.uint8)
    image[:, 19:22] = 35
    image[:, 70] = 0

    raw = track_downward(
        image,
        (20.0, 2.0),
        settings=AutomaticTraceSettings(search_half_width_px=6),
    )

    assert all(point.x_px == pytest.approx(20.0) for point in raw)


def test_fractional_user_click_is_preserved_and_following_y_is_strictly_larger() -> None:
    image = np.full((12, 30), 220, dtype=np.uint8)
    image[:, 9:12] = 15

    raw = track_downward(
        image,
        (10.25, 4.4),
        settings=AutomaticTraceSettings(search_half_width_px=4),
    )

    assert raw[0] == TracePoint(10.25, 4.4)
    assert raw[1].y_px == 5.0
    assert all(
        first.y_px < second.y_px
        for first, second in zip(raw, raw[1:])
    )


def test_rgb_input_is_converted_internally_without_special_calling_code() -> None:
    image = np.full((16, 40, 3), 230, dtype=np.uint8)
    image[:, 24:27, :] = 25

    raw = track_downward(
        image,
        (25.0, 3.0),
        settings=AutomaticTraceSettings(search_half_width_px=4),
    )

    assert raw[-1] == TracePoint(25.0, 15.0)


@pytest.mark.parametrize(
    "start",
    [
        (-0.1, 4.0),
        (20.0, -1.0),
        (40.0, 2.0),
        (2.0, 20.0),
    ],
)
def test_start_outside_image_is_rejected(start) -> None:
    image = np.full((20, 40), 200, dtype=np.uint8)

    with pytest.raises(AutomaticTraceError, match="inside"):
        track_downward(image, start)


@pytest.mark.parametrize("width", [0, -1, 2.5, True])
def test_invalid_search_half_width_is_rejected(width) -> None:
    with pytest.raises(AutomaticTraceError, match="positive integer"):
        AutomaticTraceSettings(search_half_width_px=width)


def test_nan_image_is_rejected() -> None:
    image = np.full((10, 10), 200.0)
    image[4, 4] = np.nan

    with pytest.raises(AutomaticTraceError, match="NaN"):
        track_downward(image, (4.0, 2.0))
