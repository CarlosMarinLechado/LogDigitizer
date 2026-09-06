from __future__ import annotations

import numpy as np

from logdigitizer.processing.automatic_digitization import AutomaticTraceSettings
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
)


def test_session_builds_grid_mask_after_first_click() -> None:
    image = np.full((150, 150), 235, dtype=np.uint8)
    for x in (20, 45, 70, 95, 120):
        image[:, x - 1 : x + 2] = 65
    for y in (20, 45, 70, 95, 120):
        image[y - 1 : y + 2, :] = 65
    image[:, 80:83] = 30

    session = AutomaticTrackingSession(
        image,
        settings=AutomaticTraceSettings(
            search_half_width_px=10,
            max_consecutive_misses=4,
        ),
    )

    assert session.grid_penalty_result is None
    session.start((81.0, 5.0))

    result = session.grid_penalty_result
    assert result is not None
    assert result.has_grid
    assert session.grid_penalty_mask is result.penalty_mask
