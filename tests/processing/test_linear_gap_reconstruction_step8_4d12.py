from __future__ import annotations

import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    connect_sample_gaps_linearly,
)


def test_linear_connector_preserves_endpoints_and_fills_each_image_row() -> None:
    start = TracePoint(10.0, 2.5)
    end = TracePoint(18.0, 6.5)

    samples, confidences = connect_sample_gaps_linearly(
        (start, end),
        (0.9, 0.8),
    )

    assert samples[0] == start
    assert samples[-1] == end
    assert [point.y_px for point in samples] == [
        2.5,
        3.0,
        4.0,
        5.0,
        6.0,
        6.5,
    ]
    assert [point.x_px for point in samples] == pytest.approx(
        [10.0, 11.0, 13.0, 15.0, 17.0, 18.0]
    )
    assert confidences == pytest.approx((0.9, 0.0, 0.0, 0.0, 0.0, 0.8))


def test_consecutive_rows_are_not_modified() -> None:
    original = (
        TracePoint(20.0, 4.0),
        TracePoint(21.0, 5.0),
        TracePoint(22.0, 6.0),
    )
    original_confidences = (1.0, 0.85, 0.8)

    samples, confidences = connect_sample_gaps_linearly(
        original,
        original_confidences,
    )

    assert samples == original
    assert confidences == original_confidences


def test_invalid_or_backwards_samples_are_rejected_transactionally() -> None:
    with pytest.raises(AutomaticTraceError, match="equal length"):
        connect_sample_gaps_linearly(
            (TracePoint(10.0, 2.0), TracePoint(10.0, 3.0)),
            (1.0,),
        )

    with pytest.raises(AutomaticTraceError, match="strictly increasing"):
        connect_sample_gaps_linearly(
            (TracePoint(10.0, 3.0), TracePoint(10.0, 2.0)),
            (1.0, 0.8),
        )
