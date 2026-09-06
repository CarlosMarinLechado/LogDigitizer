from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.processing.automatic_digitization import AutomaticTraceSettings
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
    AutomaticTrackingSessionState,
)


def test_session_estimates_line_width_automatically_on_start_and_resume() -> None:
    image = np.full((34, 70), 235, dtype=np.uint8)
    image[:12, 18:23] = 35
    image[18:, 31:36] = 35

    settings = AutomaticTraceSettings(
        search_half_width_px=6,
        max_horizontal_step_px=4.0,
        max_consecutive_misses=1,
    )
    session = AutomaticTrackingSession(image, settings=settings)

    session.start((20.0, 3.0))

    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
    assert session.estimated_line_width_px is not None
    first_width = session.estimated_line_width_px
    assert first_width == pytest.approx(5.0, abs=1.5)
    assert len(session.line_width_estimates) == 1

    session.resume((33.0, 18.0))

    assert session.state is AutomaticTrackingSessionState.COMPLETE
    assert len(session.line_width_estimates) == 2
    assert session.estimated_line_width_px == pytest.approx(5.0, abs=1.5)


def test_line_width_estimation_is_best_effort_and_does_not_break_old_tracker() -> None:
    image = np.full((18, 40), 220, dtype=np.uint8)

    session = AutomaticTrackingSession(
        image,
        settings=AutomaticTraceSettings(max_consecutive_misses=1),
    )
    session.start((20.0, 3.0))

    assert session.estimated_line_width_px is None
    assert session.line_width_estimates == ()
    assert session.state is AutomaticTrackingSessionState.WAITING_FOR_RESUME
