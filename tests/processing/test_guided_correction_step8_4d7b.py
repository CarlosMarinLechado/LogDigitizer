from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing import automatic_tracking_session as session_module
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    TrackerStopReason,
    TrackingResult,
)
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
    AutomaticTrackingSessionState,
)
from logdigitizer.processing.grid_detection import GridPenaltyResult
from logdigitizer.processing.line_width import LineWidthEstimate
from logdigitizer.processing.minimum_cost_tracking import (
    AnchoredPathResult,
    track_between_points_minimum_cost_result,
)


def _tracking_result(
    points: tuple[TracePoint, ...],
    *,
    stop_reason: TrackerStopReason = TrackerStopReason.IMAGE_BOTTOM,
    stop_y_px: float | None = None,
) -> TrackingResult:
    return TrackingResult(
        raw_samples_px=points,
        confidences=tuple(0.9 for _point in points),
        stop_reason=stop_reason,
        stop_y_px=stop_y_px,
    )


def test_anchored_path_follows_curve_and_preserves_exact_endpoints() -> None:
    image = np.full((30, 50), 235, dtype=np.uint8)
    expected_x: dict[int, float] = {}
    for y_px in range(4, 21):
        x_px = 12 + round(0.75 * (y_px - 4))
        expected_x[y_px] = float(x_px)
        image[y_px, x_px - 1 : x_px + 2] = 35

    start = TracePoint(12.25, 4.0)
    end = TracePoint(24.25, 20.0)
    result = track_between_points_minimum_cost_result(
        image,
        start,
        end,
        settings=AutomaticTraceSettings(
            search_half_width_px=18,
            max_horizontal_step_px=3.0,
        ),
        line_width_px=3.0,
    )

    assert result.raw_samples_px[0] == start
    assert result.raw_samples_px[-1] == end
    assert [point.y_px for point in result.raw_samples_px] == [
        float(y_px) for y_px in range(4, 21)
    ]
    assert len(result.raw_samples_px) == len(result.confidences)
    for point in result.raw_samples_px[1:-1]:
        assert point.x_px == pytest.approx(
            expected_x[int(point.y_px)],
            abs=1.0,
        )


def test_anchored_path_applies_the_same_soft_grid_penalty() -> None:
    image = np.full((22, 35), 220, dtype=np.uint8)
    settings = AutomaticTraceSettings(
        search_half_width_px=8,
        max_horizontal_step_px=3.0,
    )
    start = TracePoint(15.0, 3.0)
    end = TracePoint(15.0, 18.0)

    plain = track_between_points_minimum_cost_result(
        image,
        start,
        end,
        settings=settings,
        line_width_px=3.0,
    )

    penalty = np.zeros(image.shape, dtype=np.float32)
    penalty[:, 15] = 1.0
    penalized = track_between_points_minimum_cost_result(
        image,
        start,
        end,
        settings=settings,
        line_width_px=3.0,
        grid_penalty_mask=penalty,
    )

    assert all(
        point.x_px == 15.0
        for point in plain.raw_samples_px
    )
    assert penalized.raw_samples_px[0] == start
    assert penalized.raw_samples_px[-1] == end
    assert any(
        point.x_px != 15.0
        for point in penalized.raw_samples_px[1:-1]
    )


def test_anchored_path_preserves_fractional_click_coordinates() -> None:
    image = np.full((16, 30), 225, dtype=np.uint8)
    start = TracePoint(10.25, 3.4)
    end = TracePoint(12.75, 8.6)

    result = track_between_points_minimum_cost_result(
        image,
        start,
        end,
        settings=AutomaticTraceSettings(
            search_half_width_px=8,
            max_horizontal_step_px=3.0,
        ),
        line_width_px=3.0,
    )

    assert result.raw_samples_px[0] == start
    assert result.raw_samples_px[-1] == end
    assert [point.y_px for point in result.raw_samples_px] == [
        3.4,
        4.0,
        5.0,
        6.0,
        7.0,
        8.0,
        8.6,
    ]


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ((10.0, 8.0), (10.0, 8.0), "below"),
        ((10.0, 9.0), (10.0, 8.0), "below"),
        ((10.0, 2.0), (25.0, 12.0), "search width"),
    ],
)
def test_anchored_path_rejects_invalid_or_unreasonable_endpoints(
    start: tuple[float, float],
    end: tuple[float, float],
    message: str,
) -> None:
    image = np.full((20, 40), 230, dtype=np.uint8)

    with pytest.raises(AutomaticTraceError, match=message):
        track_between_points_minimum_cost_result(
            image,
            start,
            end,
            settings=AutomaticTraceSettings(
                search_half_width_px=10,
                max_horizontal_step_px=3.0,
            ),
        )


def test_session_merges_bridge_once_then_relaunches_from_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((40, 60), 230, dtype=np.uint8)

    monkeypatch.setattr(
        session_module,
        "estimate_line_width",
        lambda _image, point: LineWidthEstimate(
            width_px=3.0,
            sample_point_px=point,
            contrast_ratio=0.8,
            line_intensity=30.0,
            background_intensity=230.0,
        ),
    )
    monkeypatch.setattr(
        session_module,
        "detect_grid_penalty_mask",
        lambda *_args, **_kwargs: GridPenaltyResult(
            penalty_mask=np.zeros(image.shape, dtype=np.float32),
            vertical_line_count=0,
            horizontal_line_count=0,
        ),
    )

    downward_calls: list[TracePoint] = []

    def fake_downward(
        _image: np.ndarray,
        start_point: TracePoint,
        **_kwargs,
    ) -> TrackingResult:
        downward_calls.append(start_point)
        y_start = int(start_point.y_px)
        points = tuple(
            TracePoint(
                10.0 if len(downward_calls) > 1 or y_px < 14 else 30.0,
                float(y_px),
            )
            for y_px in range(y_start, 40)
        )
        return _tracking_result(points)

    bridge_calls: list[tuple[TracePoint, TracePoint]] = []

    def fake_bridge(
        _image: np.ndarray,
        start_point: TracePoint,
        end_point: TracePoint,
        **_kwargs,
    ) -> AnchoredPathResult:
        bridge_calls.append((start_point, end_point))
        points = tuple(
            TracePoint(10.0, float(y_px))
            for y_px in range(
                int(start_point.y_px),
                int(end_point.y_px) + 1,
            )
        )
        return AnchoredPathResult(
            raw_samples_px=points,
            confidences=tuple(1.0 for _point in points),
        )

    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_downward,
    )
    monkeypatch.setattr(
        session_module,
        "track_between_points_minimum_cost_result",
        fake_bridge,
    )

    session = AutomaticTrackingSession(image)
    session.start((10.0, 2.0))
    result = session.correct((10.0, 20.0))

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    assert session.state is AutomaticTrackingSessionState.COMPLETE
    assert bridge_calls == [
        (TracePoint(10.0, 8.0), TracePoint(10.0, 20.0))
    ]
    assert downward_calls[-1] == TracePoint(10.0, 20.0)
    assert [point.y_px for point in session.raw_samples_px] == [
        float(y_px) for y_px in range(2, 40)
    ]
    assert session.raw_samples_px.count(TracePoint(10.0, 20.0)) == 1
    assert all(point.x_px == 10.0 for point in session.raw_samples_px)
    assert len(session.raw_samples_px) == len(session.confidences)


def test_successive_resume_anchors_reconstruct_every_loss_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((40, 60), 230, dtype=np.uint8)

    monkeypatch.setattr(
        session_module,
        "estimate_line_width",
        lambda _image, point: LineWidthEstimate(
            width_px=3.0,
            sample_point_px=point,
            contrast_ratio=0.8,
            line_intensity=30.0,
            background_intensity=230.0,
        ),
    )
    monkeypatch.setattr(
        session_module,
        "detect_grid_penalty_mask",
        lambda *_args, **_kwargs: GridPenaltyResult(
            penalty_mask=np.zeros(image.shape, dtype=np.float32),
            vertical_line_count=0,
            horizontal_line_count=0,
        ),
    )

    downward_call = 0

    def fake_downward(
        _image: np.ndarray,
        start_point: TracePoint,
        **_kwargs,
    ) -> TrackingResult:
        nonlocal downward_call
        downward_call += 1
        if downward_call == 1:
            points = tuple(
                TracePoint(10.0, float(y_px))
                for y_px in range(2, 11)
            )
            return _tracking_result(
                points,
                stop_reason=TrackerStopReason.LOST_CURVE,
                stop_y_px=11.0,
            )
        if downward_call == 2:
            points = tuple(
                TracePoint(20.0, float(y_px))
                for y_px in range(14, 24)
            )
            return _tracking_result(
                points,
                stop_reason=TrackerStopReason.LOST_CURVE,
                stop_y_px=24.0,
            )
        points = tuple(
            TracePoint(30.0, float(y_px))
            for y_px in range(27, 40)
        )
        return _tracking_result(points)

    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_downward,
    )
    monkeypatch.setattr(
        session_module,
        "track_between_points_minimum_cost_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "Loss/resume must use the straight connector."
            )
        ),
    )

    session = AutomaticTrackingSession(image)
    session.start((10.0, 2.0))
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME

    session.resume((20.0, 14.0))
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    session.resume((30.0, 27.0))

    assert session.state is AutomaticTrackingSessionState.COMPLETE
    assert session.last_splice_point_px == TracePoint(20.0, 23.0)
    assert [point.y_px for point in session.raw_samples_px] == [
        float(y_px) for y_px in range(2, 40)
    ]
    x_by_y = {
        int(point.y_px): point.x_px
        for point in session.raw_samples_px
    }
    assert x_by_y[11] == pytest.approx(12.5)
    assert x_by_y[12] == pytest.approx(15.0)
    assert x_by_y[24] == pytest.approx(22.5)
    assert x_by_y[25] == pytest.approx(25.0)
    assert session.raw_samples_px.count(TracePoint(20.0, 14.0)) == 1
    assert session.raw_samples_px.count(TracePoint(30.0, 27.0)) == 1
    assert session.last_correction_point_px == TracePoint(30.0, 27.0)
    assert len(session.raw_samples_px) == len(session.confidences)


def test_failed_resume_relaunch_leaves_the_existing_draft_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((30, 50), 230, dtype=np.uint8)
    points = tuple(
        TracePoint(12.0, float(y_px))
        for y_px in range(2, 11)
    )

    monkeypatch.setattr(
        session_module,
        "estimate_line_width",
        lambda _image, point: LineWidthEstimate(
            width_px=3.0,
            sample_point_px=point,
            contrast_ratio=0.8,
            line_intensity=30.0,
            background_intensity=230.0,
        ),
    )
    monkeypatch.setattr(
        session_module,
        "detect_grid_penalty_mask",
        lambda *_args, **_kwargs: GridPenaltyResult(
            penalty_mask=np.zeros(image.shape, dtype=np.float32),
            vertical_line_count=0,
            horizontal_line_count=0,
        ),
    )
    downward_call = 0

    def fake_downward(*_args, **_kwargs) -> TrackingResult:
        nonlocal downward_call
        downward_call += 1
        if downward_call == 1:
            return _tracking_result(
                points,
                stop_reason=TrackerStopReason.LOST_CURVE,
                stop_y_px=11.0,
            )
        raise AutomaticTraceError("Forced relaunch failure.")

    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_downward,
    )
    monkeypatch.setattr(
        session_module,
        "track_between_points_minimum_cost_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "Loss/resume must not call the minimum-cost connector."
            )
        ),
    )

    session = AutomaticTrackingSession(image)
    session.start((12.0, 2.0))
    samples_before = session.raw_samples_px
    confidences_before = session.confidences
    estimates_before = session.line_width_estimates
    settings_before = session.effective_settings

    with pytest.raises(AutomaticTraceError, match="relaunch"):
        session.resume((12.0, 14.0))

    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    assert session.raw_samples_px == samples_before
    assert session.confidences == confidences_before
    assert session.line_width_estimates == estimates_before
    assert session.effective_settings == settings_before
    assert session.last_splice_point_px is None
    assert session.last_correction_point_px is None
