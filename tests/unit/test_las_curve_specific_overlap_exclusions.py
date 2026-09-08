from __future__ import annotations

import pytest

from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    CurveDefinition,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
)
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.io.las_export import prepare_las_export, preview_las_export
from logdigitizer.processing.curve_assembly import CurveAssemblyInput, build_project_curve


def _curve(mnemonic: str, unit: str) -> CurveDefinition:
    return CurveDefinition.create(name=mnemonic, mnemonic=mnemonic, unit=unit)


def _five_sample_gap(curve: CurveDefinition):
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(200.0, 20.0),
        ),
        unit=DepthUnit.METERS,
    )
    return build_project_curve(
        curve.curve_id,
        (
            CurveAssemblyInput(
                segment_id=f"image-{curve.curve_id}",
                segment_name=f"Image {curve.mnemonic}",
                depth_calibration=calibration,
                digitized_curve=DigitizedCurve(
                    curve_id=curve.curve_id,
                    sections=[
                        TraceSection.create_manual(
                            (TracePoint(40.0, 0.0), TracePoint(40.0, 40.0))
                        ),
                        TraceSection.create_manual(
                            (TracePoint(100.0, 100.0), TracePoint(100.0, 200.0))
                        ),
                    ],
                ),
                horizontal_calibrations={
                    DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                        0.0, 0.0, 100.0, 100.0
                    )
                },
            ),
        ),
    )


def test_legacy_overlap_exclusion_does_not_inhibit_either_curve() -> None:
    gr = _curve("GR", "API")
    sp = _curve("SP", "MV")
    assemblies = {
        gr.curve_id: _five_sample_gap(gr),
        sp.curve_id: _five_sample_gap(sp),
    }
    exclusions = {gr.curve_id: ((5.0, 9.0),)}

    preview = preview_las_export(
        assemblies,
        (gr, sp),
        1.0,
        gap_qc_excluded_intervals_by_curve=exclusions,
    )

    assert preview.internal_gaps == ()
    assert {gap.curve_id for gap in preview.interpolated_gaps} == {gr.curve_id, sp.curve_id}

    plan = prepare_las_export(
        assemblies,
        (gr, sp),
        step_depth=1.0,
        gap_qc_excluded_intervals_by_curve=exclusions,
    )
    gr_values = plan.sampled.values_for(gr.curve_id)
    sp_values = plan.sampled.values_for(sp.curve_id)

    for depth in (5.0, 6.0, 7.0, 8.0, 9.0):
        index = plan.sampled.depths.index(depth)
        assert gr_values[index] is not None
        assert sp_values[index] is not None
