from __future__ import annotations

from pathlib import Path

import pytest

from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    CurveDefinition,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
)
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.io.las_export import (
    LasExportError,
    LasScaleTransition,
    LasWellMetadata,
    prepare_las_export,
    preview_las_export,
    serialize_las_2,
    write_las_2,
)
from logdigitizer.processing.curve_assembly import (
    CurveAssemblyInput,
    build_project_curve,
)


def _curve(
    mnemonic: str,
    *,
    name: str | None = None,
    unit: str = "API",
) -> CurveDefinition:
    return CurveDefinition.create(
        name=name or mnemonic,
        mnemonic=mnemonic,
        unit=unit,
    )


def _constant_assembly(
    curve: CurveDefinition,
    *,
    start_depth: float,
    stop_depth: float,
    value: float,
    depth_unit: DepthUnit = DepthUnit.METERS,
):
    return build_project_curve(
        curve.curve_id,
        (
            CurveAssemblyInput(
                segment_id=f"image-{curve.curve_id}",
                segment_name=f"Image {curve.mnemonic}",
                depth_calibration=DepthCalibration(
                    points=(
                        DepthCalibrationPoint(0.0, start_depth),
                        DepthCalibrationPoint(100.0, stop_depth),
                    ),
                    unit=depth_unit,
                ),
                digitized_curve=DigitizedCurve(
                    curve_id=curve.curve_id,
                    sections=[
                        TraceSection.create_manual(
                            (
                                TracePoint(value, 0.0),
                                TracePoint(value, 100.0),
                            )
                        )
                    ],
                ),
                horizontal_calibrations={
                    DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                        0.0,
                        0.0,
                        100.0,
                        100.0,
                    )
                },
            ),
        ),
    )


def test_plan_uses_selected_curve_coverage_and_keeps_other_curve_nulls():
    sp = _curve("SP", unit="MV")
    gr = _curve("GR")
    plan = prepare_las_export(
        {
            sp.curve_id: _constant_assembly(
                sp,
                start_depth=13.2,
                stop_depth=20.0,
                value=25.0,
            ),
            gr.curve_id: _constant_assembly(
                gr,
                start_depth=16.0,
                stop_depth=18.0,
                value=50.0,
            ),
        },
        (sp, gr),
        step_depth=0.2,
    )

    assert plan.coverage_start_depth == pytest.approx(13.2)
    assert plan.coverage_stop_depth == pytest.approx(20.0)
    assert plan.start_depth == pytest.approx(13.2)
    assert plan.stop_depth == pytest.approx(20.0)
    assert plan.row_count == 35
    assert plan.sampled.values_for(gr.curve_id)[0] is None
    assert plan.sampled.values_for(gr.curve_id)[14] == pytest.approx(50.0)


def test_arbitrary_step_aligns_start_and_stop_to_strict_las_grid():
    sp = _curve("SP", unit="MV")
    plan = prepare_las_export(
        {
            sp.curve_id: _constant_assembly(
                sp,
                start_depth=13.2,
                stop_depth=16.4,
                value=25.0,
            )
        },
        (sp,),
        step_depth=0.5,
    )

    assert plan.start_depth == pytest.approx(13.5)
    assert plan.stop_depth == pytest.approx(16.0)
    assert plan.sampled.depths == (13.5, 14.0, 14.5, 15.0, 15.5, 16.0)
    assert plan.row_count == 6


def test_step_resolution_comparison_is_informative_only():
    gr = _curve("GR")
    plan = prepare_las_export(
        {
            gr.curve_id: _constant_assembly(
                gr,
                start_depth=0.0,
                stop_depth=1.0,
                value=10.0,
            )
        },
        (gr,),
        step_depth=0.1,
        first_image_resolution=0.2,
    )

    assert plan.step_below_first_image_resolution
    assert plan.row_count == 11


def test_preview_matches_plan_without_evaluating_the_sample_matrix(
    monkeypatch,
):
    gr = _curve("GR")
    assembly = _constant_assembly(
        gr,
        start_depth=13.2,
        stop_depth=16.4,
        value=10.0,
    )

    def fail_if_sampled(*_args, **_kwargs):
        raise AssertionError("preview must not sample curve values")

    monkeypatch.setattr(
        "logdigitizer.io.las_export.sample_assembled_project_curves",
        fail_if_sampled,
    )
    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        0.5,
        first_image_resolution=0.6,
    )

    assert preview.start_depth == pytest.approx(13.5)
    assert preview.stop_depth == pytest.approx(16.0)
    assert preview.row_count == 6
    assert preview.step_below_first_image_resolution


def test_preview_skips_a_short_edge_section_missed_by_the_grid():
    curve = _curve("GR")
    calibration = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 10.0),
        )
    )
    assembly = build_project_curve(
        curve.curve_id,
        (
            CurveAssemblyInput(
                segment_id="image-1",
                segment_name="Image 1",
                depth_calibration=calibration,
                digitized_curve=DigitizedCurve(
                    curve_id=curve.curve_id,
                    sections=[
                        TraceSection.create_manual(
                            (
                                TracePoint(10.0, 2.0),
                                TracePoint(10.0, 3.0),
                            )
                        ),
                        TraceSection.create_manual(
                            (
                                TracePoint(10.0, 50.0),
                                TracePoint(10.0, 100.0),
                            )
                        ),
                    ],
                ),
                horizontal_calibrations={
                    DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                        0.0,
                        0.0,
                        100.0,
                        100.0,
                    )
                },
            ),
        ),
    )

    preview = preview_las_export(
        {curve.curve_id: assembly},
        (curve,),
        0.5,
    )

    assert preview.coverage_start_depth == pytest.approx(0.2)
    assert preview.start_depth == pytest.approx(5.0)
    assert preview.stop_depth == pytest.approx(10.0)
    assert preview.row_count == 11


def test_serialization_contains_las_sections_metadata_nulls_and_crlf():
    sp = _curve("SP", name="Potencial espontáneo", unit="MV")
    gr = _curve("GR", name="Rayos gamma")
    plan = prepare_las_export(
        {
            sp.curve_id: _constant_assembly(
                sp,
                start_depth=0.0,
                stop_depth=1.0,
                value=25.0,
            ),
            gr.curve_id: _constant_assembly(
                gr,
                start_depth=0.5,
                stop_depth=1.0,
                value=50.0,
            ),
        },
        (sp, gr),
        step_depth=0.5,
    )
    text = serialize_las_2(
        plan,
        LasWellMetadata(
            company="IGME-CSIC",
            well="Sondeo Peñón",
            location="Níjar, Almería",
            county="Almería",
            state="Andalucía",
            country="España",
            unique_well_id="IGME-001",
            other_information="Digitalización histórica",
        ),
    )

    assert "\n" in text
    assert "\r\n" in text
    assert "\n" not in text.replace("\r\n", "")
    assert "~Version Information\r\n" in text
    assert "VERS. 2.0" in text
    assert "WRAP. NO" in text
    assert "STRT.M 0 : START DEPTH" in text
    assert "STOP.M 1.0 : STOP DEPTH" in text
    assert "STEP.M 0.5 : STEP" in text
    assert "NULL. -999.25 : NULL VALUE" in text
    assert "WELL. Sondeo Penon : WELL" in text
    assert "LOC. Nijar, Almeria : LOCATION" in text
    assert "CTRY. Espana : COUNTRY" in text
    assert "SP.MV  : Potencial espontaneo" in text
    assert "GR.API  : Rayos gamma" in text
    assert "Digitalizacion historica" in text
    ascii_lines = text.split("~ASCII Log Data\r\n", 1)[1].split("\r\n")
    assert ascii_lines[0].split() == ["0", "25.0000", "-999.25"]
    assert ascii_lines[1].split() == ["0.5", "25.0000", "50.0000"]
    assert ascii_lines[2].split() == ["1.0", "25.0000", "50.0000"]
    assert all(
        all(float(value) or value in {"0", "0.0"} for value in line.split())
        for line in ascii_lines
    )
    text.encode("ascii")


def test_serialization_rounds_curve_values_to_four_decimals_only():
    gr = _curve("GR")
    plan = prepare_las_export(
        {
            gr.curve_id: _constant_assembly(
                gr,
                start_depth=0.0,
                stop_depth=0.2,
                value=67.68304815641555,
            )
        },
        (gr,),
        step_depth=0.2,
    )

    text = serialize_las_2(plan, LasWellMetadata(well="BH-01"))
    ascii_lines = text.split("~ASCII Log Data\r\n", 1)[1].split("\r\n")

    assert ascii_lines[0].split() == ["0", "67.6830"]
    assert ascii_lines[1].split() == ["0.2", "67.6830"]
    assert "67.68304815641555" not in text


def test_writer_is_atomic_and_requires_las_extension(tmp_path: Path):
    gr = _curve("GR")
    plan = prepare_las_export(
        {
            gr.curve_id: _constant_assembly(
                gr,
                start_depth=0.0,
                stop_depth=1.0,
                value=10.0,
            )
        },
        (gr,),
        step_depth=0.5,
    )
    output = tmp_path / "well.las"

    written = write_las_2(output, plan, LasWellMetadata(well="BH-01"))

    assert written == output.resolve()
    assert output.read_bytes().startswith(b"~Version Information\r\n")
    assert not tuple(tmp_path.glob("*.tmp"))
    with pytest.raises(LasExportError, match=".las extension"):
        write_las_2(
            tmp_path / "well.txt",
            plan,
            LasWellMetadata(well="BH-01"),
        )


def test_invalid_empty_curve_and_null_collision_are_rejected():
    gr = _curve("GR")
    empty = build_project_curve(gr.curve_id, ())
    with pytest.raises(LasExportError, match="no usable digitized data"):
        prepare_las_export(
            {gr.curve_id: empty},
            (gr,),
            step_depth=0.2,
        )

    collision = _constant_assembly(
        gr,
        start_depth=0.0,
        stop_depth=1.0,
        value=-99.0,
    )
    with pytest.raises(LasExportError, match="NULL value"):
        prepare_las_export(
            {gr.curve_id: collision},
            (gr,),
            step_depth=0.2,
            null_value=-99.0,
        )


def test_invalid_las_curve_token_is_rejected():
    curve = _curve("BAD CURVE")
    with pytest.raises(LasExportError, match="cannot contain spaces"):
        prepare_las_export(
            {
                curve.curve_id: _constant_assembly(
                    curve,
                    start_depth=0.0,
                    stop_depth=1.0,
                    value=10.0,
                )
            },
            (curve,),
            step_depth=0.2,
        )


def _gapped_assembly(curve: CurveDefinition):
    """Build two valid sections separated by a 2-depth-unit gap."""
    return build_project_curve(
        curve.curve_id,
        (
            CurveAssemblyInput(
                segment_id=f"gapped-{curve.curve_id}",
                segment_name=f"Gapped {curve.mnemonic}",
                depth_calibration=DepthCalibration(
                    points=(
                        DepthCalibrationPoint(0.0, 0.0),
                        DepthCalibrationPoint(100.0, 10.0),
                    ),
                    unit=DepthUnit.METERS,
                ),
                digitized_curve=DigitizedCurve(
                    curve_id=curve.curve_id,
                    sections=[
                        TraceSection.create_manual(
                            (
                                TracePoint(25.0, 0.0),
                                TracePoint(25.0, 40.0),
                            )
                        ),
                        TraceSection.create_manual(
                            (
                                TracePoint(25.0, 60.0),
                                TracePoint(25.0, 100.0),
                            )
                        ),
                    ],
                ),
                horizontal_calibrations={
                    DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                        0.0,
                        0.0,
                        100.0,
                        100.0,
                    )
                },
            ),
        ),
    )


def test_gap_qc_depends_on_actual_las_grid_and_auto_interpolates_small_run():
    gr = _curve("GR")
    assembly = _gapped_assembly(gr)

    fine = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        1.0,
    )

    assert not fine.has_internal_gaps
    assert fine.internal_gaps == ()
    assert fine.has_interpolated_gaps
    assert fine.interpolated_sample_count == 1
    assert len(fine.interpolated_gaps) == 1
    gap = fine.interpolated_gaps[0]
    assert gap.curve_id == gr.curve_id
    assert gap.mnemonic == "GR"
    assert gap.previous_depth == pytest.approx(4.0)
    assert gap.next_depth == pytest.approx(6.0)
    assert gap.missing_start_depth == pytest.approx(5.0)
    assert gap.missing_stop_depth == pytest.approx(5.0)
    assert gap.missing_sample_count == 1

    # The final LAS matrix contains the interpolated value; saved digitization
    # and curve assembly remain untouched.
    fine_plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
    )
    fine_values = fine_plan.sampled.values_for(gr.curve_id)
    assert fine_values[4] is not None
    assert fine_values[5] == pytest.approx(25.0)
    assert fine_values[6] is not None
    assert assembly.value_at_depth(5.0) is None

    # With STEP=2 the regular grid lands at 0,2,4,6,8,10. The geometric
    # separation remains, but no LAS sample falls inside it, so no interpolation
    # or warning is needed.
    coarse = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        2.0,
    )
    assert coarse.internal_gaps == ()
    assert coarse.interpolated_gaps == ()
    assert not coarse.has_internal_gaps
    assert not coarse.has_interpolated_gaps


def test_gap_qc_ignores_leading_and_trailing_nulls_of_shorter_curve():
    full = _curve("SP", unit="MV")
    short = _curve("GR")
    preview = preview_las_export(
        {
            full.curve_id: _constant_assembly(
                full,
                start_depth=0.0,
                stop_depth=10.0,
                value=25.0,
            ),
            short.curve_id: _constant_assembly(
                short,
                start_depth=3.0,
                stop_depth=7.0,
                value=50.0,
            ),
        },
        (full, short),
        1.0,
    )

    assert preview.internal_gaps == ()
    assert not preview.has_internal_gaps


def _overlap_handoff_gap_assembly(
    curve: CurveDefinition,
    *,
    include_real_gap_below_overlap: bool = False,
):
    """Two calibrated images overlap; their digitized traces need not meet there."""
    upper_depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 0.0),
            DepthCalibrationPoint(100.0, 10.0),
        ),
        unit=DepthUnit.METERS,
    )
    lower_depth = DepthCalibration(
        points=(
            DepthCalibrationPoint(0.0, 6.0),
            DepthCalibrationPoint(100.0, 16.0),
        ),
        unit=DepthUnit.METERS,
    )
    calibration = HorizontalCalibration(0.0, 0.0, 100.0, 100.0)

    upper = CurveAssemblyInput(
        segment_id="image-upper",
        segment_name="Upper image",
        depth_calibration=upper_depth,
        digitized_curve=DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 80.0),  # depth 8.0
                    )
                )
            ],
        ),
        horizontal_calibrations={DEFAULT_HORIZONTAL_SCALE_ID: calibration},
    )

    lower_sections = [
        TraceSection.create_manual(
            (
                TracePoint(25.0, 30.0),  # depth 9.0
                TracePoint(25.0, 60.0),  # depth 12.0
            )
        )
    ]
    if include_real_gap_below_overlap:
        lower_sections.append(
            TraceSection.create_manual(
                (
                    TracePoint(25.0, 70.0),  # depth 13.0
                    TracePoint(25.0, 100.0),  # depth 16.0
                )
            )
        )
    else:
        lower_sections[0] = TraceSection.create_manual(
            (
                TracePoint(25.0, 30.0),  # depth 9.0
                TracePoint(25.0, 100.0),  # depth 16.0
            )
        )

    lower = CurveAssemblyInput(
        segment_id="image-lower",
        segment_name="Lower image",
        depth_calibration=lower_depth,
        digitized_curve=DigitizedCurve(
            curve_id=curve.curve_id,
            sections=lower_sections,
        ),
        horizontal_calibrations={DEFAULT_HORIZONTAL_SCALE_ID: calibration},
    )
    return build_project_curve(curve.curve_id, (upper, lower))


def test_gap_qc_interpolates_one_missing_sample_inside_overlap():
    gr = _curve("GR")
    assembly = _overlap_handoff_gap_assembly(gr)

    # Calibrated image overlap is 6-10 m. The two saved traces stop/start at
    # 8 and 9 m, so STEP=0.5 places one missing sample at 8.5 m.
    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        0.5,
        gap_qc_excluded_intervals=((6.0, 10.0),),
    )
    assert not preview.has_internal_gaps
    assert preview.interpolated_gaps[0].missing_sample_count == 1

    # Legacy overlap exclusions no longer inhibit LAS-only interpolation.
    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=0.5,
        gap_qc_excluded_intervals=((6.0, 10.0),),
    )
    values = plan.sampled.values_for(gr.curve_id)
    missing_index = plan.sampled.depths.index(8.5)
    assert values[missing_index] == pytest.approx((values[missing_index-1] + values[missing_index+1]) / 2)


def test_gap_qc_auto_interpolates_small_gap_outside_overlap_guide_zone():
    gr = _curve("GR")
    assembly = _overlap_handoff_gap_assembly(
        gr,
        include_real_gap_below_overlap=True,
    )

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        0.5,
        gap_qc_excluded_intervals=((6.0, 10.0),),
    )

    # Both small gaps are interpolated, inside and outside the overlap.
    assert preview.internal_gaps == ()
    assert len(preview.interpolated_gaps) == 2
    gap = preview.interpolated_gaps[1]
    assert gap.mnemonic == "GR"
    assert gap.missing_start_depth == pytest.approx(12.5)
    assert gap.missing_stop_depth == pytest.approx(12.5)
    assert gap.missing_sample_count == 1


def test_gap_qc_does_not_infer_displayed_guides_from_curve_assembly():
    gr = _curve("GR")
    assembly = _overlap_handoff_gap_assembly(gr)

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        0.5,
    )

    # Without application-supplied guide exclusions, the 8.5 m sample remains
    # inside QC scope. As a one-sample gap it is classified for interpolation.
    assert preview.internal_gaps == ()
    assert any(
        gap.missing_start_depth == pytest.approx(8.5)
        for gap in preview.interpolated_gaps
    )



def test_las_scale_transition_metadata_fills_only_internal_null_grid_samples():
    gr = _curve("GR")
    assembly = _gapped_assembly(gr)
    transition = LasScaleTransition(
        curve_id=gr.curve_id,
        segment_id="image-1",
        upper_scale_id="scale-a",
        lower_scale_id="scale-b",
        upper_scale_offset=0,
        lower_scale_offset=0,
        upper_depth=4.0,
        lower_depth=6.0,
        upper_value=40.0,
        lower_value=60.0,
        gap_y_px=4.0,
    )
    transition_map = {gr.curve_id: (transition,)}

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        1.0,
        scale_transitions=transition_map,
    )
    assert preview.internal_gaps == ()

    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
        scale_transitions=transition_map,
    )
    values = plan.sampled.values_for(gr.curve_id)
    assert values[4] is not None
    assert values[5] == pytest.approx(50.0)
    assert values[6] is not None


def test_las_scale_transition_does_not_overwrite_existing_sample_values():
    gr = _curve("GR")
    assembly = _constant_assembly(
        gr,
        start_depth=0.0,
        stop_depth=10.0,
        value=25.0,
    )
    transition = LasScaleTransition(
        curve_id=gr.curve_id,
        segment_id="image-1",
        upper_scale_id="scale-a",
        lower_scale_id="scale-b",
        upper_scale_offset=0,
        lower_scale_offset=0,
        upper_depth=4.0,
        lower_depth=6.0,
        upper_value=40.0,
        lower_value=60.0,
        gap_y_px=4.0,
    )

    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
        scale_transitions={gr.curve_id: (transition,)},
    )
    assert plan.sampled.values_for(gr.curve_id)[5] == pytest.approx(25.0)


def _sample_count_gap_assembly(
    curve: CurveDefinition,
    *,
    missing_count: int,
):
    """Create a STEP=1 gap bounded by values 40 and 100."""
    if missing_count not in (5, 6):
        raise ValueError("Test helper supports five or six missing samples.")
    next_valid_depth = 5.0 + float(missing_count)
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
                segment_id=f"sample-gap-{missing_count}",
                segment_name=f"Sample gap {missing_count}",
                depth_calibration=calibration,
                digitized_curve=DigitizedCurve(
                    curve_id=curve.curve_id,
                    sections=[
                        TraceSection.create_manual(
                            (
                                TracePoint(40.0, 0.0),
                                TracePoint(40.0, 40.0),  # depth 4
                            )
                        ),
                        TraceSection.create_manual(
                            (
                                TracePoint(100.0, next_valid_depth * 10.0),
                                TracePoint(100.0, 200.0),
                            )
                        ),
                    ],
                ),
                horizontal_calibrations={
                    DEFAULT_HORIZONTAL_SCALE_ID: HorizontalCalibration(
                        0.0,
                        0.0,
                        100.0,
                        100.0,
                    )
                },
            ),
        ),
    )


def test_five_missing_las_samples_are_interpolated_linearly():
    gr = _curve("GR")
    assembly = _sample_count_gap_assembly(gr, missing_count=5)

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        1.0,
    )
    assert preview.internal_gaps == ()
    assert len(preview.interpolated_gaps) == 1
    assert preview.interpolated_gaps[0].missing_sample_count == 5
    assert preview.interpolated_sample_count == 5

    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
    )
    values = plan.sampled.values_for(gr.curve_id)
    for depth, expected in zip(
        (5.0, 6.0, 7.0, 8.0, 9.0),
        (50.0, 60.0, 70.0, 80.0, 90.0),
    ):
        assert values[plan.sampled.depths.index(depth)] == pytest.approx(expected)


def test_six_missing_las_samples_remain_null_and_require_review():
    gr = _curve("GR")
    assembly = _sample_count_gap_assembly(gr, missing_count=6)

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        1.0,
    )
    assert preview.interpolated_gaps == ()
    assert len(preview.internal_gaps) == 1
    assert preview.internal_gaps[0].missing_sample_count == 6
    assert preview.missing_internal_sample_count == 6

    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
    )
    values = plan.sampled.values_for(gr.curve_id)
    assert all(
        values[plan.sampled.depths.index(depth)] is None
        for depth in (5.0, 6.0, 7.0, 8.0, 9.0, 10.0)
    )


def test_generic_small_gap_interpolation_fills_overlap():
    gr = _curve("GR")
    assembly = _sample_count_gap_assembly(gr, missing_count=5)
    exclusion = ((5.0, 9.0),)

    preview = preview_las_export(
        {gr.curve_id: assembly},
        (gr,),
        1.0,
        gap_qc_excluded_intervals=exclusion,
    )
    assert preview.internal_gaps == ()
    assert len(preview.interpolated_gaps) == 1
    assert preview.interpolated_gaps[0].missing_sample_count == 5

    plan = prepare_las_export(
        {gr.curve_id: assembly},
        (gr,),
        step_depth=1.0,
        gap_qc_excluded_intervals=exclusion,
    )
    values = plan.sampled.values_for(gr.curve_id)
    assert all(
        values[plan.sampled.depths.index(depth)] is not None
        for depth in (5.0, 6.0, 7.0, 8.0, 9.0)
    )


def test_gap_crossing_overlap_protection_is_reported_as_one_complete_run():
    gr = _curve("GR")
    assembly = _sample_count_gap_assembly(gr, missing_count=6)
    preview = preview_las_export(
        {gr.curve_id: assembly}, (gr,), 1.0,
        gap_qc_excluded_intervals=((7.0, 8.0),),
    )
    assert preview.interpolated_gaps == ()
    assert len(preview.internal_gaps) == 1
    gap = preview.internal_gaps[0]
    assert (gap.missing_start_depth, gap.missing_stop_depth) == (5.0, 10.0)
    assert gap.missing_sample_count == 6
