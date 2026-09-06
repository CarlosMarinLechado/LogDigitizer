from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing import automatic_tracking_session as session_module
from logdigitizer.processing.automatic_digitization import (
    TrackerStopReason,
    TrackingResult,
)
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
    AutomaticTrackingSessionError,
    AutomaticTrackingSessionState,
)
from logdigitizer.processing.grid_detection import GridPenaltyResult
from logdigitizer.processing.line_width import LineWidthEstimate


def _points(
    *,
    y_start: int,
    y_stop: int,
    x_for_y,
) -> tuple[TracePoint, ...]:
    return tuple(
        TracePoint(float(x_for_y(y)), float(y))
        for y in range(y_start, y_stop)
    )


def _result(
    points: tuple[TracePoint, ...],
    *,
    stop_reason: TrackerStopReason = TrackerStopReason.IMAGE_BOTTOM,
) -> TrackingResult:
    return TrackingResult(
        raw_samples_px=points,
        confidences=tuple(0.9 for _point in points),
        stop_reason=stop_reason,
        stop_y_px=(
            None
            if stop_reason is TrackerStopReason.IMAGE_BOTTOM
            else points[-1].y_px + 1.0
        ),
    )


def _patch_spatial_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    image: np.ndarray,
) -> None:
    def fake_width(
        _image: np.ndarray,
        point: TracePoint,
    ) -> LineWidthEstimate:
        return LineWidthEstimate(
            width_px=3.0,
            sample_point_px=point,
            contrast_ratio=0.8,
            line_intensity=30.0,
            background_intensity=230.0,
        )

    def fake_grid(
        _image: np.ndarray,
        *,
        line_width_px: float | None = None,
    ) -> GridPenaltyResult:
        assert line_width_px == pytest.approx(3.0)
        return GridPenaltyResult(
            penalty_mask=np.zeros(image.shape, dtype=np.float32),
            vertical_line_count=0,
            horizontal_line_count=0,
        )

    monkeypatch.setattr(session_module, "estimate_line_width", fake_width)
    monkeypatch.setattr(
        session_module,
        "detect_grid_penalty_mask",
        fake_grid,
    )


def test_correction_reconnects_width_scaled_splice_without_raw_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((40, 60), 230, dtype=np.uint8)
    _patch_spatial_dependencies(monkeypatch, image)

    def fake_track(
        _image: np.ndarray,
        start_point: TracePoint,
        **_kwargs,
    ) -> TrackingResult:
        if start_point.y_px == 2.0:
            return _result(
                _points(
                    y_start=2,
                    y_stop=40,
                    x_for_y=lambda y: 10.0 if y < 14 else 30.0,
                )
            )
        return _result(
            _points(
                y_start=20,
                y_stop=40,
                x_for_y=lambda _y: 10.0,
            )
        )

    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_track,
    )

    session = AutomaticTrackingSession(image)
    initial = session.start((10.0, 2.0))
    assert any(point.x_px == 30.0 for point in initial.raw_samples_px)

    corrected = session.correct((10.0, 20.0))

    assert corrected.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert session.state is AutomaticTrackingSessionState.COMPLETE
    assert session.last_splice_point_px == TracePoint(10.0, 8.0)
    assert session.last_correction_point_px == TracePoint(10.0, 20.0)
    assert TracePoint(10.0, 20.0) in session.raw_samples_px
    assert all(point.x_px == 10.0 for point in session.raw_samples_px)
    samples_by_y = {
        int(point.y_px): point
        for point in session.raw_samples_px
    }
    assert all(y in samples_by_y for y in range(9, 20))
    assert len(session.raw_samples_px) == len(session.confidences)


@pytest.mark.parametrize(
    "correction",
    [
        (10.0, 2.0),
        (10.0, 39.0),
        (10.0, 40.0),
        (-1.0, 20.0),
    ],
)
def test_correction_rejects_points_outside_active_traced_interval(
    monkeypatch: pytest.MonkeyPatch,
    correction: tuple[float, float],
) -> None:
    image = np.full((40, 60), 230, dtype=np.uint8)
    _patch_spatial_dependencies(monkeypatch, image)
    initial_points = _points(
        y_start=2,
        y_stop=40,
        x_for_y=lambda _y: 10.0,
    )
    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        lambda *_args, **_kwargs: _result(initial_points),
    )

    session = AutomaticTrackingSession(image)
    session.start((10.0, 2.0))
    before = session.raw_samples_px

    with pytest.raises(AutomaticTrackingSessionError, match="inside"):
        session.correct(correction)

    assert session.raw_samples_px == before
    assert session.last_splice_point_px is None
    assert session.last_correction_point_px is None


def test_two_successive_corrections_replace_each_new_provisional_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((40, 60), 230, dtype=np.uint8)
    _patch_spatial_dependencies(monkeypatch, image)
    call_index = 0

    def fake_track(
        _image: np.ndarray,
        start_point: TracePoint,
        **_kwargs,
    ) -> TrackingResult:
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            points = _points(
                y_start=2,
                y_stop=40,
                x_for_y=lambda y: 10.0 if y < 14 else 30.0,
            )
        elif call_index == 2:
            points = _points(
                y_start=20,
                y_stop=40,
                x_for_y=lambda y: 10.0 if y < 30 else 35.0,
            )
        else:
            points = _points(
                y_start=int(start_point.y_px),
                y_stop=40,
                x_for_y=lambda _y: 10.0,
            )
        return _result(points)

    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_track,
    )

    session = AutomaticTrackingSession(image)
    session.start((10.0, 2.0))
    session.correct((10.0, 20.0))
    assert any(point.x_px == 35.0 for point in session.raw_samples_px)

    session.correct((10.0, 34.0))

    assert session.last_splice_point_px == TracePoint(10.0, 22.0)
    assert session.last_correction_point_px == TracePoint(10.0, 34.0)
    assert all(point.x_px == 10.0 for point in session.raw_samples_px)
    y_values = [point.y_px for point in session.raw_samples_px]
    assert all(
        second > first
        for first, second in zip(y_values, y_values[1:])
    )


def test_correction_is_not_available_before_start() -> None:
    session = AutomaticTrackingSession(
        np.full((20, 30), 230, dtype=np.uint8)
    )

    with pytest.raises(AutomaticTrackingSessionError, match="started"):
        session.correct((10.0, 8.0))
