from __future__ import annotations

import pytest

from logdigitizer.domain.digitization import (
    DigitizationError,
    DigitizedCurve,
    TracePoint,
    TraceSection,
    TraceSource,
)


def automatic(
    x_offset: float,
    first_y: int,
    last_y: int,
) -> TraceSection:
    return TraceSection.create_automatic(
        raw_samples=tuple(
            TracePoint(x_offset + float(y_px), float(y_px))
            for y_px in range(first_y, last_y + 1)
        )
    )


def test_new_automatic_trace_replaces_older_automatic_y_range() -> None:
    old = automatic(10.0, 0, 10)
    newest = automatic(100.0, 3, 7)
    digitized = DigitizedCurve("curve-gr", [old])

    affected = digitized.apply_automatic_override(newest)

    assert affected == 1
    assert len(digitized.sections) == 3
    upper, saved_newest, lower = digitized.sections
    assert upper.source is TraceSource.AUTOMATIC
    assert saved_newest is newest
    assert lower.source is TraceSource.AUTOMATIC

    assert upper.raw_samples_px == old.raw_samples_px[:3] + (
        newest.raw_samples_px[0],
    )
    assert saved_newest.raw_samples_px == newest.raw_samples_px
    assert lower.raw_samples_px == (
        newest.raw_samples_px[-1],
    ) + old.raw_samples_px[8:]

    assert upper.raw_samples_px[-1] == saved_newest.raw_samples_px[0]
    assert saved_newest.raw_samples_px[-1] == lower.raw_samples_px[0]


def test_automatic_trace_is_split_around_manual_correction() -> None:
    manual = TraceSection.create_manual(
        (
            TracePoint(50.0, 4.0),
            TracePoint(55.0, 5.0),
            TracePoint(60.0, 6.0),
        )
    )
    incoming = automatic(100.0, 0, 10)
    digitized = DigitizedCurve("curve-gr", [manual])

    affected = digitized.apply_automatic_override(incoming)

    assert affected == 0
    assert len(digitized.sections) == 3
    upper, saved_manual, lower = digitized.sections
    assert saved_manual is manual
    assert upper.source is TraceSource.AUTOMATIC
    assert lower.source is TraceSource.AUTOMATIC
    assert upper.raw_samples_px == incoming.raw_samples_px[:4] + (
        manual.control_points_px[0],
    )
    assert lower.raw_samples_px == (
        manual.control_points_px[-1],
    ) + incoming.raw_samples_px[7:]
    assert upper.raw_samples_px[-1] == manual.control_points_px[0]
    assert lower.raw_samples_px[0] == manual.control_points_px[-1]

    automatic_inside_manual = {
        point
        for section in digitized.sections
        if section.source is TraceSource.AUTOMATIC
        for point in section.raw_samples_px
        if 4.0 < point.y_px < 6.0
    }
    assert automatic_inside_manual == set()


def test_automatic_commit_repairs_legacy_automatic_overlaps() -> None:
    legacy_upper = automatic(10.0, 0, 8)
    legacy_lower = automatic(30.0, 4, 12)
    disjoint_new = automatic(60.0, 20, 22)
    digitized = DigitizedCurve(
        "curve-gr",
        [legacy_upper, legacy_lower],
    )

    assert digitized.apply_automatic_override(disjoint_new) == 0

    geometric = [
        section.raw_samples_px
        for section in digitized.sections
        if len(section.raw_samples_px) >= 2
    ]
    for first, second in zip(geometric, geometric[1:]):
        assert first[-1].y_px <= second[0].y_px
        if first[-1].y_px == second[0].y_px:
            assert first[-1] == second[0]


def test_add_section_rejects_competing_geometry_transactionally() -> None:
    first = TraceSection.create_manual(
        (TracePoint(10.0, 0.0), TracePoint(15.0, 5.0))
    )
    conflicting = TraceSection.create_automatic(
        raw_samples=(TracePoint(80.0, 3.0), TracePoint(85.0, 8.0))
    )
    digitized = DigitizedCurve("curve-gr", [first])

    with pytest.raises(DigitizationError, match="cannot overlap in Y"):
        digitized.add_section(conflicting)

    assert digitized.sections == [first]


def test_add_section_allows_one_identical_shared_endpoint() -> None:
    upper = TraceSection.create_manual(
        (TracePoint(10.0, 0.0), TracePoint(15.0, 5.0))
    )
    lower = TraceSection.create_manual(
        (TracePoint(15.0, 5.0), TracePoint(20.0, 10.0))
    )
    digitized = DigitizedCurve("curve-gr", [upper])

    digitized.add_section(lower)

    assert digitized.sections == [upper, lower]


def test_add_section_rejects_different_x_at_a_shared_y() -> None:
    upper = TraceSection.create_manual(
        (TracePoint(10.0, 0.0), TracePoint(15.0, 5.0))
    )
    lower = TraceSection.create_manual(
        (TracePoint(40.0, 5.0), TracePoint(45.0, 10.0))
    )
    digitized = DigitizedCurve("curve-gr", [upper])

    with pytest.raises(DigitizationError, match="different X values"):
        digitized.add_section(lower)

    assert digitized.sections == [upper]


def test_different_project_curves_may_share_the_same_y_range() -> None:
    gr = DigitizedCurve(
        "curve-gr",
        [
            TraceSection.create_manual(
                (TracePoint(10.0, 0.0), TracePoint(15.0, 10.0))
            )
        ],
    )
    sp = DigitizedCurve(
        "curve-sp",
        [
            TraceSection.create_manual(
                (TracePoint(90.0, 0.0), TracePoint(85.0, 10.0))
            )
        ],
    )

    assert gr.curve_id != sp.curve_id
    assert gr.sections[0].start_y_px == sp.sections[0].start_y_px
    assert gr.sections[0].end_y_px == sp.sections[0].end_y_px
