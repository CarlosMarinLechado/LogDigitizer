from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QSettings

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import (
    DEFAULT_HORIZONTAL_SCALE_ID,
    ScaleType,
)
from logdigitizer.domain.digitization import (
    DigitizedCurve,
    TracePoint,
    TraceSection,
)
from logdigitizer.gui.main_window import MainWindow
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.processing.calibration import pixel_x_to_value


def _image(path: Path, size: tuple[int, int] = (1000, 1000)) -> None:
    PIL.new("L", size, 128).save(path)


def test_viewer_hud_renders_scale_and_existing_trace_warning(
    qtbot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "hud.png"
    _image(path, (500, 400))
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(500, 360)
    viewer.show()
    viewer.load_image(path)

    viewer.set_navigation_hud(
        depth_text="551.0 m",
        curve_mnemonic="GR",
        curve_value_text="205.7 API",
        mnemonic_color="#00B8D9",
        scale_text="0–1000 API · +1 offset",
        warning_text="Active setting: 0–1000 API · +1 offset",
    )

    html = viewer.navigation_hud.text()
    assert "551.0 m" in html
    assert "205.7 API" in html
    assert "Scale" in html
    assert "0–1000 API · +1 offset" in html
    assert "Active setting: 0–1000 API · +1 offset" in html
    assert "#D58B20" in html


def test_hud_uses_saved_section_scale_for_value_and_warns_on_active_mismatch(
    qtbot,
    tmp_path: Path,
) -> None:
    image = tmp_path / "segment.png"
    _image(image)

    service = ProjectService()
    service.new_project(
        name="HUD scale",
        project_directory=tmp_path / "HUD_scale_project",
    )
    segment = service.add_images([image])[0]
    service.select_segment(segment.segment_id)

    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )
    service.add_depth_calibration_point(pixel_y=0.0, depth=0.0)
    service.add_depth_calibration_point(pixel_y=999.0, depth=999.0)

    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
        value=0.0,
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=999.0,
        value=100.0,
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    second = service.add_horizontal_scale(curve.curve_id)
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
        value=0.0,
        scale_id=second.scale_id,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=999.0,
        value=1000.0,
        scale_id=second.scale_id,
    )

    service.set_digitized_curve(
        DigitizedCurve(
            curve_id=curve.curve_id,
            sections=[
                TraceSection.create_manual(
                    (
                        TracePoint(100.0, 100.0),
                        TracePoint(120.0, 299.0),
                    ),
                    scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
                    scale_offset=0,
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(120.0, 300.0),
                        TracePoint(140.0, 499.0),
                    ),
                    scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
                    scale_offset=1,
                ),
                TraceSection.create_manual(
                    (
                        TracePoint(140.0, 500.0),
                        TracePoint(160.0, 699.0),
                    ),
                    scale_id=second.scale_id,
                    scale_offset=0,
                ),
            ],
        )
    )

    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    window = MainWindow(service=service, settings=settings)
    qtbot.addWidget(window)
    window.resize(1100, 760)
    window.show()
    window.viewer.load_image(image)
    window.viewer.actual_size()
    window._active_depth_calibration = service.get_depth_calibration()
    window.curve_calibration_controller.refresh(
        selected_curve_id=curve.curve_id
    )
    window.manual_digitization_controller.refresh(
        selected_curve_id=curve.curve_id
    )

    profiles = service.get_horizontal_scale_profiles(curve.curve_id)
    window.digitization_panel.set_scale_profiles(
        profiles,
        curve,
        selected_scale_id=second.scale_id,
    )
    window.digitization_panel.set_scale_offset(1)
    window._hud_scale_context_source = "digitization"

    x = 499.5
    default_calibration = service.get_horizontal_calibration(
        curve.curve_id,
        scale_id=DEFAULT_HORIZONTAL_SCALE_ID,
    )
    second_calibration = service.get_horizontal_calibration(
        curve.curve_id,
        scale_id=second.scale_id,
    )
    assert default_calibration is not None
    assert second_calibration is not None

    # Saved normal scale: the HUD value and Scale line follow the saved trace,
    # while the warning exposes the different active working settings.
    window._update_navigation_hud_at_position(x, 200.0)
    html = window.viewer.navigation_hud.text()
    expected = pixel_x_to_value(x, default_calibration, scale_offset=0)
    assert f"{expected:.1f} API" in html
    assert "0–100 API" in html
    assert "Active setting: 0–1000 API · +1 offset" in html

    # Same printed scale, but the saved section uses +1 wrap offset.
    window._update_navigation_hud_at_position(x, 400.0)
    html = window.viewer.navigation_hud.text()
    expected = pixel_x_to_value(x, default_calibration, scale_offset=1)
    assert f"{expected:.1f} API" in html
    assert "0–100 API · +1 offset" in html
    assert "Active setting: 0–1000 API · +1 offset" in html

    # A later saved section uses the second printed scale.
    window._update_navigation_hud_at_position(x, 600.0)
    html = window.viewer.navigation_hud.text()
    expected = pixel_x_to_value(x, second_calibration, scale_offset=0)
    assert f"{expected:.1f} API" in html
    assert "0–1000 API" in html
    assert "Active setting: 0–1000 API · +1 offset" in html

    # Once the active controls match the saved section, the warning disappears.
    window.digitization_panel.set_scale_offset(0)
    window._update_navigation_hud_at_position(x, 600.0)
    matching_html = window.viewer.navigation_hud.text()
    assert "0–1000 API" in matching_html
    assert "Active setting:" not in matching_html

    # Outside saved geometry there is no saved context, so the HUD falls back
    # to the currently active scale/offset and no mismatch warning is needed.
    window._update_navigation_hud_at_position(x, 800.0)
    outside_html = window.viewer.navigation_hud.text()
    assert "0–1000 API" in outside_html
    assert "Active setting:" not in outside_html

