from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import AutomaticTraceSettings
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
    AutomaticTrackingSessionError,
    AutomaticTrackingSessionState,
)


def make_gap_image() -> np.ndarray:
    image = np.full((32, 60), 240, dtype=np.uint8)
    image[:12, 19:22] = 15
    image[17:, 27:30] = 15
    return image


def settings() -> AutomaticTraceSettings:
    return AutomaticTraceSettings(
        search_half_width_px=6,
        max_horizontal_step_px=4.0,
        max_consecutive_misses=1,
    )


def test_session_stops_then_reconnects_resume_without_raw_gap() -> None:
    session = AutomaticTrackingSession(make_gap_image(), settings=settings())

    first = session.start((20.0, 3.0))
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    assert first.stop_y_px == pytest.approx(12.0)
    before = session.raw_samples_px
    assert before[-1].y_px == pytest.approx(11.0)

    second = session.resume((28.0, 17.0))
    assert session.state is AutomaticTrackingSessionState.COMPLETE
    assert second.stop_y_px is None
    assert session.raw_samples_px[: len(before)] == before
    assert TracePoint(28.0, 17.0) in session.raw_samples_px
    assert session.raw_samples_px[-1].y_px == pytest.approx(31.0)
    assert session.last_splice_point_px is not None
    assert session.last_correction_point_px == TracePoint(28.0, 17.0)

    ys = [point.y_px for point in session.raw_samples_px]
    assert all(y in ys for y in (12.0, 13.0, 14.0, 15.0, 16.0))


def test_resume_click_must_be_below_last_accepted_sample() -> None:
    session = AutomaticTrackingSession(make_gap_image(), settings=settings())
    session.start((20.0, 3.0))

    with pytest.raises(AutomaticTrackingSessionError, match="below"):
        session.resume((20.0, 11.0))


def test_resume_click_must_stay_inside_image() -> None:
    session = AutomaticTrackingSession(make_gap_image(), settings=settings())
    session.start((20.0, 3.0))

    with pytest.raises(AutomaticTrackingSessionError, match="inside"):
        session.resume((60.0, 17.0))


def test_resume_is_not_allowed_before_loss_or_after_completion() -> None:
    image = np.full((18, 40), 230, dtype=np.uint8)
    image[:, 10] = 20
    session = AutomaticTrackingSession(image)

    with pytest.raises(AutomaticTrackingSessionError, match="only resume"):
        session.resume((10.0, 2.0))

    session.start((10.0, 2.0))
    assert session.state is AutomaticTrackingSessionState.COMPLETE

    with pytest.raises(AutomaticTrackingSessionError, match="only resume"):
        session.resume((10.0, 10.0))


def test_start_can_only_happen_once() -> None:
    session = AutomaticTrackingSession(make_gap_image(), settings=settings())
    session.start((20.0, 3.0))

    with pytest.raises(AutomaticTrackingSessionError, match="already"):
        session.start((20.0, 4.0))


def test_fractional_explicit_restart_is_preserved_exactly() -> None:
    image = make_gap_image()
    session = AutomaticTrackingSession(image, settings=settings())
    session.start((20.25, 3.4))

    session.resume((28.25, 17.4))

    assert TracePoint(20.25, 3.4) == session.raw_samples_px[0]
    assert TracePoint(28.25, 17.4) in session.raw_samples_px


def test_multiple_losses_can_be_resumed_repeatedly() -> None:
    image = np.full((40, 70), 240, dtype=np.uint8)
    image[:8, 12] = 10
    image[12:21, 20] = 10
    image[26:, 28] = 10
    cfg = AutomaticTraceSettings(
        search_half_width_px=4,
        max_horizontal_step_px=3.0,
        max_consecutive_misses=0,
    )
    session = AutomaticTrackingSession(image, settings=cfg)

    session.start((12.0, 2.0))
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    session.resume((20.0, 12.0))
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    session.resume((28.0, 26.0))
    assert session.state is AutomaticTrackingSessionState.COMPLETE
