from __future__ import annotations

import json
from pathlib import Path

import pytest

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition, ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    DigitizationError,
    TracePoint,
    TraceSection,
    TraceSource,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.domain.states import StageStatus
from logdigitizer.io.project_store import (
    load_project,
    project_to_payload,
    save_project,
)
from logdigitizer.utils.validation import InvalidProjectError


def make_curve() -> CurveDefinition:
    return CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )


def make_segment() -> Segment:
    return Segment.create(
        order=1,
        name="segment_01.png",
        source_path="source/segment_01.png",
    )


def test_trace_section_normalizes_control_points_by_vertical_coordinate() -> None:
    section = TraceSection.create_manual(
        (
            TracePoint(30.0, 300.0),
            TracePoint(10.0, 100.0),
            TracePoint(20.0, 200.0),
        )
    )

    assert [point.y_px for point in section.control_points_px] == [
        100.0,
        200.0,
        300.0,
    ]


def test_duplicate_vertical_coordinates_are_rejected() -> None:
    with pytest.raises(DigitizationError, match="duplicate y_px"):
        TraceSection.create_manual(
            (
                TracePoint(10.0, 100.0),
                TracePoint(20.0, 100.0),
            )
        )


def test_manual_section_cannot_store_automatic_raw_trace() -> None:
    with pytest.raises(DigitizationError, match="Manual sections"):
        TraceSection(
            section_id="manual-1",
            source=TraceSource.MANUAL,
            control_points_px=(TracePoint(10.0, 100.0),),
            raw_samples_px=(TracePoint(10.0, 100.0),),
        )


def test_automatic_section_preserves_raw_and_processing_settings_round_trip() -> None:
    section = TraceSection.create_automatic(
        raw_samples=(
            TracePoint(100.0, 10.0),
            TracePoint(101.0, 11.0),
            TracePoint(100.5, 12.0),
            TracePoint(102.0, 13.0),
        ),
        control_points=(
            TracePoint(100.0, 10.0),
            TracePoint(100.5, 12.0),
            TracePoint(102.0, 13.0),
        ),
        smoothing_strength=0.35,
        simplification_tolerance_px=1.25,
    )

    restored = TraceSection.from_dict(section.to_dict())

    assert restored.source is TraceSource.AUTOMATIC
    assert restored.raw_samples_px == section.raw_samples_px
    assert restored.control_points_px == section.control_points_px
    assert restored.smoothing_strength == pytest.approx(0.35)
    assert restored.simplification_tolerance_px == pytest.approx(1.25)


def test_processing_parameters_can_change_without_modifying_raw_trace() -> None:
    section = TraceSection.create_automatic(
        raw_samples=(
            TracePoint(100.0, 10.0),
            TracePoint(101.0, 11.0),
            TracePoint(102.0, 12.0),
        ),
        control_points=(
            TracePoint(100.0, 10.0),
            TracePoint(102.0, 12.0),
        ),
    )
    original_raw = section.raw_samples_px

    section.set_processing(
        smoothing_strength=0.8,
        simplification_tolerance_px=2.5,
    )
    section.replace_control_points(
        (
            TracePoint(100.0, 10.0),
            TracePoint(101.5, 12.0),
        )
    )

    assert section.raw_samples_px == original_raw
    assert section.smoothing_strength == pytest.approx(0.8)
    assert section.simplification_tolerance_px == pytest.approx(2.5)


def test_digitized_curve_sorts_sections_by_depth_direction() -> None:
    lower = TraceSection.create_manual(
        (TracePoint(20.0, 300.0), TracePoint(25.0, 400.0))
    )
    upper = TraceSection.create_manual(
        (TracePoint(10.0, 100.0), TracePoint(15.0, 200.0))
    )

    digitized = DigitizedCurve("curve-1", [lower, upper])

    assert digitized.sections == [upper, lower]


def test_manual_override_splits_automatic_trace_and_joins_its_endpoints() -> None:
    raw = tuple(
        TracePoint(20.0 + y_px, float(y_px))
        for y_px in range(11)
    )
    automatic = TraceSection.create_automatic(
        raw_samples=raw,
        control_points=(raw[0], raw[5], raw[-1]),
        smoothing_strength=0.35,
        simplification_tolerance_px=1.25,
    )
    manual = TraceSection.create_manual(
        (
            TracePoint(80.0, 3.0),
            TracePoint(90.0, 5.0),
            TracePoint(70.0, 7.0),
        )
    )
    digitized = DigitizedCurve("curve-1", [automatic])

    affected = digitized.apply_manual_override(manual)

    assert affected == 1
    assert len(digitized.sections) == 3
    upper, saved_manual, lower = digitized.sections
    assert upper.section_id == automatic.section_id
    assert upper.source is TraceSource.AUTOMATIC
    assert upper.raw_samples_px == raw[:3] + (manual.control_points_px[0],)
    assert upper.control_points_px == (raw[0], manual.control_points_px[0])
    assert saved_manual is manual
    assert saved_manual.control_points_px == manual.control_points_px
    assert lower.source is TraceSource.AUTOMATIC
    assert lower.raw_samples_px == (manual.control_points_px[-1],) + raw[8:]
    assert lower.control_points_px == (manual.control_points_px[-1], raw[-1])
    assert upper.smoothing_strength == pytest.approx(0.35)
    assert lower.simplification_tolerance_px == pytest.approx(1.25)


def test_newest_manual_override_replaces_overlapping_previous_correction() -> None:
    raw = tuple(
        TracePoint(30.0 + y_px, float(y_px))
        for y_px in range(13)
    )
    digitized = DigitizedCurve(
        "curve-1",
        [TraceSection.create_automatic(raw_samples=raw)],
    )
    first = TraceSection.create_manual(
        (
            TracePoint(80.0, 3.0),
            TracePoint(85.0, 5.0),
            TracePoint(75.0, 7.0),
        )
    )
    newest = TraceSection.create_manual(
        (
            TracePoint(100.0, 5.0),
            TracePoint(110.0, 7.0),
            TracePoint(90.0, 9.0),
        )
    )

    assert digitized.apply_manual_override(first) == 1
    assert digitized.apply_manual_override(newest) == 1

    assert newest in digitized.sections
    assert len(digitized.sections) == 4
    assert [section.start_y_px for section in digitized.sections] == [
        0.0,
        3.0,
        5.0,
        9.0,
    ]
    assert [section.end_y_px for section in digitized.sections] == [
        3.0,
        5.0,
        9.0,
        12.0,
    ]
    original_inside_latest_range = {
        point
        for point in raw
        if newest.start_y_px <= point.y_px <= newest.end_y_px
    }
    remaining_automatic_points = {
        point
        for section in digitized.sections
        if section.source is TraceSource.AUTOMATIC
        for point in section.raw_samples_px
    }
    assert original_inside_latest_range.isdisjoint(
        remaining_automatic_points
    )


def test_automatic_range_eraser_splits_without_joining_and_protects_manual() -> None:
    raw = tuple(
        TracePoint(20.0 + y_px, float(y_px))
        for y_px in range(11)
    )
    automatic = TraceSection.create_automatic(
        raw_samples=raw,
        control_points=(raw[0], raw[5], raw[-1]),
        smoothing_strength=0.4,
        simplification_tolerance_px=1.5,
    )
    manual = TraceSection.create_manual(
        (
            TracePoint(80.0, 4.0),
            TracePoint(90.0, 5.0),
            TracePoint(75.0, 6.0),
        )
    )
    digitized = DigitizedCurve("curve-1", [automatic, manual])

    affected = digitized.erase_automatic_range(7.0, 3.0)

    assert affected == 1
    assert len(digitized.sections) == 3
    upper, saved_manual, lower = digitized.sections
    assert upper.section_id == automatic.section_id
    assert upper.source is TraceSource.AUTOMATIC
    assert upper.raw_samples_px == raw[:3]
    assert upper.control_points_px == ()
    assert upper.end_y_px == 2.0
    assert saved_manual is manual
    assert saved_manual.control_points_px == manual.control_points_px
    assert lower.source is TraceSource.AUTOMATIC
    assert lower.raw_samples_px == raw[8:]
    assert lower.control_points_px == ()
    assert lower.start_y_px == 8.0
    assert lower.smoothing_strength == pytest.approx(0.4)
    assert lower.simplification_tolerance_px == pytest.approx(1.5)


def test_automatic_range_eraser_can_remove_all_auto_and_leave_manual() -> None:
    automatic = TraceSection.create_automatic(
        raw_samples=(
            TracePoint(20.0, 10.0),
            TracePoint(21.0, 11.0),
            TracePoint(22.0, 12.0),
        )
    )
    manual = TraceSection.create_manual(
        (TracePoint(50.0, 10.0), TracePoint(55.0, 12.0))
    )
    digitized = DigitizedCurve("curve-1", [automatic, manual])

    assert digitized.erase_automatic_range(0.0, 20.0) == 1
    assert digitized.sections == [manual]


def test_segment_stores_one_digitization_per_project_curve() -> None:
    segment = make_segment()
    first = DigitizedCurve(
        "curve-1",
        [
            TraceSection.create_manual(
                (TracePoint(10.0, 10.0), TracePoint(20.0, 20.0))
            )
        ],
    )
    replacement = DigitizedCurve(
        "curve-1",
        [
            TraceSection.create_manual(
                (TracePoint(30.0, 30.0), TracePoint(40.0, 40.0))
            )
        ],
    )

    segment.set_digitized_curve(first)
    segment.set_digitized_curve(replacement)

    assert len(segment.digitizations) == 1
    assert segment.get_digitized_curve("curve-1") is replacement


def test_project_json_round_trip_preserves_digitization(tmp_path: Path) -> None:
    project = LogProject.create(name="BH-01")
    curve = make_curve()
    project.add_curve(curve)
    segment = make_segment()
    project.add_segment(segment)

    section = TraceSection.create_automatic(
        raw_samples=(
            TracePoint(50.0, 100.0),
            TracePoint(51.0, 101.0),
            TracePoint(52.0, 102.0),
        ),
        control_points=(
            TracePoint(50.0, 100.0),
            TracePoint(52.0, 102.0),
        ),
        smoothing_strength=0.4,
        simplification_tolerance_px=1.5,
    )
    segment.set_digitized_curve(
        DigitizedCurve(curve.curve_id, [section])
    )
    segment.digitization_status = StageStatus.IN_PROGRESS

    project_file = tmp_path / "BH-01.logproj"
    save_project(project, project_file)
    loaded, _ = load_project(project_file)

    restored_segment = loaded.segments[0]
    restored = restored_segment.get_digitized_curve(curve.curve_id)
    restored_section = restored.sections[0]

    assert restored_segment.digitization_status is StageStatus.IN_PROGRESS
    assert restored_section.raw_samples_px == section.raw_samples_px
    assert restored_section.control_points_px == section.control_points_px
    assert restored_section.smoothing_strength == pytest.approx(0.4)
    assert restored_section.simplification_tolerance_px == pytest.approx(1.5)

    text = project_file.read_text(encoding="utf-8").lower()
    assert "bridge" not in text


def test_project_without_digitizations_field_remains_loadable(tmp_path: Path) -> None:
    project = LogProject.create(name="Legacy")
    segment = make_segment()
    project.add_segment(segment)

    payload = project_to_payload(project)
    payload["project"]["segments"][0].pop("digitizations", None)
    project_file = tmp_path / "legacy.logproj"
    project_file.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    loaded, _ = load_project(project_file)

    assert loaded.segments[0].digitizations == []


def test_load_rejects_digitization_for_unknown_project_curve(tmp_path: Path) -> None:
    project = LogProject.create(name="BH-01")
    segment = make_segment()
    project.add_segment(segment)
    payload = project_to_payload(project)
    payload["project"]["segments"][0]["digitizations"] = [
        DigitizedCurve(
            "missing-curve",
            [
                TraceSection.create_manual(
                    (TracePoint(10.0, 10.0), TracePoint(20.0, 20.0))
                )
            ],
        ).to_dict()
    ]

    project_file = tmp_path / "bad.logproj"
    project_file.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidProjectError, match="unknown project curve"):
        load_project(project_file)


def test_project_service_sets_and_removes_digitization_and_updates_status() -> None:
    project = LogProject.create(name="BH-01")
    curve = make_curve()
    project.add_curve(curve)
    segment = make_segment()
    project.add_segment(segment)

    service = ProjectService()
    service.project = project
    service.active_segment_id = segment.segment_id

    digitized = DigitizedCurve(
        curve.curve_id,
        [
            TraceSection.create_manual(
                (TracePoint(10.0, 100.0), TracePoint(20.0, 200.0))
            )
        ],
    )

    service.set_digitized_curve(digitized)

    assert service.get_digitized_curve(curve.curve_id) is digitized
    assert segment.digitization_status is StageStatus.IN_PROGRESS
    assert service.dirty is True

    removed = service.remove_digitized_curve(curve.curve_id)

    assert removed is digitized
    assert service.get_digitized_curve(curve.curve_id) is None
    assert segment.digitization_status is StageStatus.NOT_STARTED


def test_removing_project_curve_also_removes_its_segment_digitization() -> None:
    project = LogProject.create(name="BH-01")
    curve = make_curve()
    project.add_curve(curve)
    segment = make_segment()
    segment.set_digitized_curve(
        DigitizedCurve(
            curve.curve_id,
            [
                TraceSection.create_manual(
                    (TracePoint(10.0, 100.0), TracePoint(20.0, 200.0))
                )
            ],
        )
    )
    segment.digitization_status = StageStatus.IN_PROGRESS
    project.add_segment(segment)

    service = ProjectService()
    service.project = project
    service.active_segment_id = segment.segment_id

    service.remove_curve(curve.curve_id)

    assert segment.digitizations == []
    assert segment.digitization_status is StageStatus.NOT_STARTED
