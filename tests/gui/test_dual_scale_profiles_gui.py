from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsScene

from logdigitizer.domain.calibration import (
    CurveDefinition,
    DEFAULT_HORIZONTAL_SCALE_ID,
    HorizontalCalibrationPoint,
    HorizontalScaleProfile,
)
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.gui.calibration_panel import CalibrationPanel
from logdigitizer.gui.digitization_panel import DigitizationPanel
from logdigitizer.gui.digitized_curves import SavedDigitizedCurvesOverlay
from logdigitizer.gui.overlays import OverlayManager


def profiles():
    return (
        HorizontalScaleProfile(
            DEFAULT_HORIZONTAL_SCALE_ID,
            (
                HorizontalCalibrationPoint(10.0, 0.0),
                HorizontalCalibrationPoint(110.0, 100.0),
            ),
        ),
        HorizontalScaleProfile(
            "wide-scale",
            (
                HorizontalCalibrationPoint(10.0, 0.0),
                HorizontalCalibrationPoint(110.0, 1000.0),
            ),
        ),
    )


def test_calibration_uses_one_table_refreshed_by_selected_scale(qtbot) -> None:
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    panel.set_project_available(True)
    panel.set_curves((curve,), curve.curve_id)
    panel.set_curve_presence(True, segment_available=True)
    panel.set_image_available(True)
    panel.set_horizontal_scales(profiles(), curve)

    assert panel.scale_profile_combo.count() == 2
    assert panel.scale_profile_combo.itemText(0) == "GR: 0–100 API"
    assert panel.scale_profile_combo.itemText(1) == "GR: 0–1000 API"
    assert panel.x_table.rowCount() == 2

    panel.scale_profile_combo.setCurrentIndex(1)
    assert panel.selected_scale_id == "wide-scale"
    assert panel.x_table.item(1, 2).text() == "1000 API"


def test_digitization_places_scale_and_offset_on_the_same_row(qtbot) -> None:
    panel = DigitizationPanel()
    qtbot.addWidget(panel)
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    panel.set_curves((curve,), curve.curve_id)
    panel.set_scale_profiles(profiles(), curve)
    panel.set_context(
        project_available=True,
        segment_available=True,
        image_ready=True,
        readiness_message="Ready",
        saved_section_count=0,
        can_start=True,
    )

    assert panel.scale_profile_combo.isEnabled()
    panel.scale_profile_combo.setCurrentIndex(1)
    panel.scale_offset_spin.setValue(1)
    assert panel.scale_offset_badge.text() == "GR · 0–1000 API · +1 scale"

    panel.set_draft_state(True, 0)
    assert not panel.scale_profile_combo.isEnabled()
    assert not panel.scale_offset_spin.isEnabled()


def test_overlay_connects_a_short_transition_between_different_scales(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = SavedDigitizedCurvesOverlay(manager, lambda point: QPointF(point))
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    fine = TraceSection.create_manual(
        (TracePoint(90.0, 10.0), TracePoint(105.0, 30.0)),
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    wide = TraceSection.create_manual(
        (TracePoint(12.0, 32.0), TracePoint(20.0, 50.0)),
        scale_id="wide-scale",
    )

    overlay.set_curves(
        (curve,),
        (DigitizedCurve(curve.curve_id, [fine, wide]),),
        active_curve_id=curve.curve_id,
    )

    assert len(overlay.connector_items) == 1
    connector = overlay.connector_items[0]
    assert connector.upper_scale_id == DEFAULT_HORIZONTAL_SCALE_ID
    assert connector.lower_scale_id == "wide-scale"
    assert connector.pen().style() == Qt.PenStyle.DashLine

