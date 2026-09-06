from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsScene

from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.gui.digitized_curves import SavedDigitizedCurvesOverlay
from logdigitizer.gui.overlays import OverlayManager


def make_digitized(curve_id: str, x0: float) -> DigitizedCurve:
    curve = DigitizedCurve(curve_id=curve_id)
    curve.add_section(
        TraceSection.create_manual(
            (
                TracePoint(x0, 10.0),
                TracePoint(x0 + 8.0, 50.0),
                TracePoint(x0 + 3.0, 90.0),
            )
        )
    )
    return curve


def make_automatic_digitized(curve_id: str, x0: float) -> DigitizedCurve:
    return DigitizedCurve(
        curve_id=curve_id,
        sections=[
            TraceSection.create_automatic(
                raw_samples=(
                    TracePoint(x0, 10.0),
                    TracePoint(x0 + 2.0, 30.0),
                    TracePoint(x0 + 8.0, 50.0),
                    TracePoint(x0 + 4.0, 70.0),
                    TracePoint(x0 + 3.0, 90.0),
                )
            )
        ],
    )


def test_saved_overlay_renders_multiple_curves_with_their_own_colors(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(
        manager,
        lambda point: QPointF(point),
    )

    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#00B8D9",
    )
    sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        display_color="#FF8C00",
    )

    overlay.set_curves(
        (gr, sp),
        (make_digitized(gr.curve_id, 20.0), make_digitized(sp.curve_id, 60.0)),
        active_curve_id=gr.curve_id,
        dim_inactive=True,
    )

    assert len(overlay.path_items) == 2
    by_curve = {item.curve_id: item for item in overlay.path_items}
    assert by_curve[gr.curve_id].pen().color().name().upper() == "#00B8D9"
    assert by_curve[sp.curve_id].pen().color().name().upper() == "#FF8C00"
    assert by_curve[gr.curve_id].opacity() == pytest.approx(1.0)
    assert by_curve[sp.curve_id].opacity() < 1.0
    assert by_curve[gr.curve_id].pen().widthF() > by_curve[sp.curve_id].pen().widthF()


def test_saved_overlay_can_show_all_curves_at_full_opacity(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))

    first = CurveDefinition.create(
        name="First",
        mnemonic="A",
        unit="",
        display_color="#7CB342",
    )
    second = CurveDefinition.create(
        name="Second",
        mnemonic="B",
        unit="",
        display_color="#2563EB",
    )
    overlay.set_curves(
        (first, second),
        (make_digitized(first.curve_id, 10.0), make_digitized(second.curve_id, 40.0)),
        active_curve_id=first.curve_id,
        dim_inactive=False,
    )

    assert all(item.opacity() == pytest.approx(1.0) for item in overlay.path_items)


def test_raw_automatic_curve_is_visible_and_dimmed_when_inactive(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))

    gr = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#00B8D9",
    )
    sp = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        display_color="#FF8C00",
    )
    automatic_sp = make_automatic_digitized(sp.curve_id, 60.0)

    overlay.set_curves(
        (gr, sp),
        (make_digitized(gr.curve_id, 20.0), automatic_sp),
        active_curve_id=gr.curve_id,
        dim_inactive=True,
    )

    assert len(overlay.path_items) == 2
    by_curve = {item.curve_id: item for item in overlay.path_items}
    automatic_item = by_curve[sp.curve_id]
    automatic_section = automatic_sp.sections[0]
    assert automatic_item.sample_count == len(
        automatic_section.raw_samples_px
    )
    assert automatic_item.pen().color().name().upper() == "#FF8C00"
    assert automatic_item.opacity() == pytest.approx(
        automatic_item.DIMMED_OPACITY
    )
    assert automatic_item.selectable is False


def test_raw_automatic_curve_is_not_exposed_as_node_editable(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#00B8D9",
    )
    automatic = make_automatic_digitized(curve.curve_id, 20.0)
    section = automatic.sections[0]

    overlay.set_curves(
        (curve,),
        (automatic,),
        active_curve_id=curve.curve_id,
        selection_enabled=True,
        selected_section_id=section.section_id,
    )

    assert len(overlay.path_items) == 1
    assert overlay.path_items[0].selectable is False
    assert overlay.node_items == ()


def test_saved_overlay_focus_mode_keeps_only_active_curve(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))

    first = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#D946EF",
    )
    second = CurveDefinition.create(
        name="Spontaneous Potential",
        mnemonic="SP",
        unit="MV",
        display_color="#00B8D9",
    )

    overlay.set_curves(
        (first, second),
        (make_digitized(first.curve_id, 20.0), make_digitized(second.curve_id, 60.0)),
        active_curve_id=first.curve_id,
        show_all_saved=False,
    )

    assert len(overlay.path_items) == 1
    assert overlay.path_items[0].curve_id == first.curve_id
    assert overlay.path_items[0].active is True
    assert overlay.path_items[0].opacity() == pytest.approx(1.0)


def test_saved_overlay_focus_mode_without_active_curve_shows_nothing(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        display_color="#D946EF",
    )

    overlay.set_curves(
        (curve,),
        (make_digitized(curve.curve_id, 20.0),),
        active_curve_id=None,
        show_all_saved=False,
    )
    assert overlay.path_items == ()
