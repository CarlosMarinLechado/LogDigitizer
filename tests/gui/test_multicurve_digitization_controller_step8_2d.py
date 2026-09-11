from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint, TraceSection
from logdigitizer.gui.main_window import MainWindow


def add_ready_curve(
    service: ProjectService,
    *,
    name: str,
    mnemonic: str,
    color: str,
    x_offset: float,
) -> str:
    curve = service.add_curve(
        name=name,
        mnemonic=mnemonic,
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color=color,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=10.0 + x_offset,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=100.0 + x_offset,
        value=100.0,
    )
    digitized = DigitizedCurve(curve_id=curve.curve_id)
    digitized.add_section(
        TraceSection.create_manual(
            (
                TracePoint(20.0 + x_offset, 20.0),
                TracePoint(30.0 + x_offset, 60.0),
                TracePoint(25.0 + x_offset, 100.0),
            )
        )
    )
    service.set_digitized_curve(digitized)
    return curve.curve_id


def make_window_with_two_saved_curves(qtbot, tmp_path: Path):
    source = tmp_path / "scan.png"
    PIL.new("L", (180, 140), 225).save(source)

    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
    )
    segment = service.add_images([source])[0]
    segment.extensions["preprocess"] = {"derived_path": segment.source_path}
    service.add_depth_calibration_point(pixel_y=10.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=130.0, depth=120.0)

    first_id = add_ready_curve(
        service,
        name="Gamma Ray",
        mnemonic="GR",
        color="#00B8D9",
        x_offset=0.0,
    )
    second_id = add_ready_curve(
        service,
        name="Second Curve",
        mnemonic="C2",
        color="#FF8C00",
        x_offset=35.0,
    )

    window = MainWindow(service=service)
    qtbot.addWidget(window)
    window.resize(900, 650)
    window.show()
    window._load_active_segment()
    window.manual_digitization_controller.refresh(selected_curve_id=first_id)
    return window, first_id, second_id


def test_controller_restores_all_saved_curves_and_highlights_selected(
    qtbot,
    tmp_path: Path,
) -> None:
    window, first_id, second_id = make_window_with_two_saved_curves(qtbot, tmp_path)

    items = window.viewer.saved_digitized_curves.path_items
    assert {item.curve_id for item in items} == {first_id, second_id}
    by_curve = {item.curve_id: item for item in items}
    assert by_curve[first_id].active is True
    assert by_curve[second_id].active is False
    assert by_curve[first_id].opacity() == pytest.approx(1.0)
    assert by_curve[second_id].opacity() == pytest.approx(1.0)


def test_legacy_visibility_controls_cannot_hide_or_dim_saved_curves(
    qtbot,
    tmp_path: Path,
) -> None:
    window, first_id, second_id = make_window_with_two_saved_curves(qtbot, tmp_path)
    panel = window.digitization_panel

    assert panel.show_saved_curves is True
    assert panel.dim_inactive_curves is False
    assert panel.show_saved_check.isHidden()
    assert panel.dim_inactive_check.isHidden()
    assert not panel.show_saved_check.isEnabled()
    assert not panel.dim_inactive_check.isEnabled()

    # Compatibility widgets may still be modified programmatically by legacy
    # code, but they no longer affect the saved-curve overlay.
    panel.dim_inactive_check.setChecked(True)
    panel.show_saved_check.setChecked(False)

    items = window.viewer.saved_digitized_curves.path_items
    assert {item.curve_id for item in items} == {first_id, second_id}
    assert all(item.opacity() == pytest.approx(1.0) for item in items)



def test_all_saved_curves_remain_full_opacity_when_selection_changes(
    qtbot,
    tmp_path: Path,
) -> None:
    window, first_id, second_id = make_window_with_two_saved_curves(qtbot, tmp_path)

    window.manual_digitization_controller.refresh(selected_curve_id=second_id)

    items = {
        item.curve_id: item
        for item in window.viewer.saved_digitized_curves.path_items
    }
    assert set(items) == {first_id, second_id}
    assert items[first_id].active is False
    assert items[second_id].active is True
    assert items[first_id].opacity() == pytest.approx(1.0)
    assert items[second_id].opacity() == pytest.approx(1.0)
