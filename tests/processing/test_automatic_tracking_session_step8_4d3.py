from __future__ import annotations

import numpy as np

from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
)
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
)


def test_session_uses_minimum_cost_engine_without_changing_public_workflow() -> None:
    image = np.full((28, 55), 235, dtype=np.uint8)
    image[:, 24:27] = 60
    image[12, 20:23] = 0

    session = AutomaticTrackingSession(
        image,
        settings=AutomaticTraceSettings(
            search_half_width_px=8,
            max_horizontal_step_px=6.0,
            max_consecutive_misses=1,
        ),
    )
    result = session.start((25.0, 4.0))

    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM
    x_by_y = {
        int(point.y_px): point.x_px
        for point in session.raw_samples_px
    }
    assert abs(x_by_y[12] - 25.0) <= 1.0
    assert session.estimated_line_width_px is not None
    assert session.effective_settings.search_half_width_px >= 1
