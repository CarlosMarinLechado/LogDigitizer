
from __future__ import annotations

import numpy as np

from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
)
from logdigitizer.processing.automatic_tracking_session import AutomaticTrackingSession


def test_session_uses_continuous_settings_without_a_style_switch() -> None:
    image = np.full((42, 70), 235, dtype=np.uint8)
    image[:, 33:36] = 35

    session = AutomaticTrackingSession(
        image,
        settings=AutomaticTraceSettings(
            search_half_width_px=8,
            max_horizontal_step_px=4.0,
            max_consecutive_misses=2,
        ),
    )
    result = session.start((34.0, 4.0))

    assert not hasattr(session.effective_settings, "line_style")
    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
