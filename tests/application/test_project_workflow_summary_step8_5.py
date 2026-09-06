from __future__ import annotations

from pathlib import Path

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.workflow import WorkflowState


def _make_service(tmp_path: Path):
    source = tmp_path / "scan.png"
    source.write_bytes(b"synthetic image bytes")

    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
    )
    segment = service.add_images([source])[0]

    # Reuse the copied source path as a stand-in rectified artifact. The
    # workflow summary only needs a persisted, resolvable derived_path here.
    segment.extensions["preprocess"] = {
        "derived_path": segment.source_path,
    }

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
    )
    return service, segment, curve


def test_segment_becomes_complete_only_after_all_agreed_requirements(
    tmp_path: Path,
) -> None:
    service, _segment, curve = _make_service(tmp_path)

    summary = service.get_segment_workflow_summary()
    assert summary.preprocessing_state is WorkflowState.COMPLETE
    assert summary.depth_state is WorkflowState.NOT_STARTED
    assert summary.state is WorkflowState.INCOMPLETE

    service.add_depth_calibration_point(
        pixel_y=10.0,
        depth=0.0,
    )
    summary = service.get_segment_workflow_summary()
    assert summary.depth_state is WorkflowState.INCOMPLETE

    service.add_depth_calibration_point(
        pixel_y=100.0,
        depth=90.0,
    )
    summary = service.get_segment_workflow_summary()
    assert summary.depth_state is WorkflowState.COMPLETE
    assert summary.state is WorkflowState.INCOMPLETE

    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=20.0,
        value=0.0,
    )
    summary = service.get_segment_workflow_summary()
    assert summary.curves[0].x_state is WorkflowState.INCOMPLETE

    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=120.0,
        value=150.0,
    )
    summary = service.get_segment_workflow_summary()
    assert summary.curves[0].x_state is WorkflowState.COMPLETE
    assert summary.curves[0].digitization_state is WorkflowState.NOT_STARTED
    assert summary.state is WorkflowState.INCOMPLETE

    section = TraceSection.create_manual(
        (
            TracePoint(40.0, 20.0),
            TracePoint(45.0, 80.0),
        )
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[section],
        )
    )

    summary = service.get_segment_workflow_summary()
    assert summary.curves[0].section_count == 1
    assert summary.curves[0].digitization_state is WorkflowState.COMPLETE
    assert summary.state is WorkflowState.COMPLETE


def test_image_enhancement_is_not_required_for_completion(
    tmp_path: Path,
) -> None:
    service, segment, curve = _make_service(tmp_path)
    assert "working_path" not in segment.extensions["preprocess"]

    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=100.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=100.0,
        value=100.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(30.0, 10.0),
                        TracePoint(40.0, 90.0),
                    )
                )
            ],
        )
    )

    assert (
        service.get_segment_workflow_summary().state
        is WorkflowState.COMPLETE
    )


def test_absent_curve_is_not_a_completion_requirement(
    tmp_path: Path,
) -> None:
    service, _segment, gr = _make_service(tmp_path)
    sp = service.add_curve(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="mV",
        scale_type=ScaleType.LINEAR,
    )
    service.set_curve_present(sp.curve_id, False)

    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=100.0)
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=10.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        gr.curve_id,
        pixel_x=100.0,
        value=150.0,
    )
    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=gr.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(30.0, 10.0),
                        TracePoint(40.0, 90.0),
                    )
                )
            ],
        )
    )

    summary = service.get_segment_workflow_summary()
    assert [item.curve_id for item in summary.curves] == [gr.curve_id]
    assert summary.state is WorkflowState.COMPLETE


def test_zero_present_curves_never_marks_segment_complete(
    tmp_path: Path,
) -> None:
    service, _segment, curve = _make_service(tmp_path)
    service.set_curve_present(curve.curve_id, False)
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=100.0)

    summary = service.get_segment_workflow_summary()
    assert summary.curves == ()
    assert summary.state is WorkflowState.INCOMPLETE


def test_missing_source_is_error_state(tmp_path: Path) -> None:
    service, segment, _curve = _make_service(tmp_path)
    source = service.resolve_segment_source(segment)
    assert source is not None
    source.unlink()

    summary = service.get_segment_workflow_summary()
    assert summary.source_missing is True
    assert summary.state is WorkflowState.ERROR
