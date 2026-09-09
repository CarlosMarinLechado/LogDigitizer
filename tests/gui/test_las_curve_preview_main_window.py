from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")
PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import CurveDefinition
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.gui.main_window import MainWindow
from logdigitizer.utils.paths import ProjectPaths


def _exportable_service(tmp_path) -> ProjectService:
    service = ProjectService()
    project = LogProject.create(name="Toolbar preview")
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
    )
    project.add_curve(curve)
    segment = Segment.create(
        order=1,
        name="Image 1",
        source_path="image.png",
    )
    project.add_segment(segment)
    service.project = project
    service.paths = ProjectPaths(
        root=tmp_path,
        project_file=tmp_path / "Toolbar preview.logproj",
    )
    service.active_segment_id = segment.segment_id
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=100.0, depth=10.0)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
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
                        TracePoint(25.0, 0.0),
                        TracePoint(25.0, 100.0),
                    )
                )
            ],
        )
    )
    service.dirty = False
    return service


def test_las_curve_preview_has_own_toolbar_action_and_opens_modeless(
    qtbot,
    tmp_path,
):
    window = MainWindow(service=_exportable_service(tmp_path))
    qtbot.addWidget(window)

    assert window.las_curve_preview_action.isEnabled()
    assert window.main_toolbar.widgetForAction(
        window.las_curve_preview_action
    ) is not None

    window.las_curve_preview_action.trigger()

    dialog = window._las_curve_preview_dialog
    assert dialog is not None
    assert dialog.windowTitle() == "LAS Curve Preview"
    assert not dialog.isModal()
    assert dialog.isVisible()

    dialog.close()



def test_las_preview_navigation_zooms_without_selecting_implicated_curve(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    service = _exportable_service(tmp_path)
    window = MainWindow(service=service)
    qtbot.addWidget(window)

    # The navigation handler only needs a loaded raster plus the existing
    # scientific depth calibration.
    image_path = tmp_path / "navigation.png"
    PIL.new("L", (300, 400), 180).save(image_path)
    window.viewer.load_image(image_path)

    curve = service.get_curves()[0]
    segment = service.active_segment
    assert segment is not None

    # Any attempt to select/focus the implicated curve is a regression.
    monkeypatch.setattr(
        window.curve_calibration_controller,
        "refresh",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError("LAS preview navigation selected a curve")
            )
            if kwargs.get("selected_curve_id") == curve.curve_id
            else None
        ),
    )
    monkeypatch.setattr(
        window.manual_digitization_controller,
        "refresh",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError("LAS preview navigation selected a curve")
            )
            if kwargs.get("selected_curve_id") == curve.curve_id
            else None
        ),
    )
    monkeypatch.setattr(
        window.project_panel,
        "focus_digitization_curve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("LAS preview navigation focused a curve")
        ),
    )
    monkeypatch.setattr(
        window,
        "_quality_control_curve_x_near_y",
        lambda _curve_id, _y: 75.0,
    )

    captured: dict[str, object] = {}

    def capture_focus(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        window.viewer,
        "focus_image_interval",
        capture_focus,
    )

    window._navigate_from_las_curve_preview(
        curve.curve_id,
        segment.segment_id,
        5.0,
    )

    assert captured
    assert captured["tight_focus"] is True
    assert captured["center_x_px"] == pytest.approx(75.0)
    assert captured["anchor_y_px"] == pytest.approx(50.0)

    # For a 400 px-high test image the Phase 3B tight-navigation window is
    # 22 px above and below the transition, giving a clearly local inspection.
    assert captured["y_start_px"] == pytest.approx(28.0)
    assert captured["y_stop_px"] == pytest.approx(72.0)
