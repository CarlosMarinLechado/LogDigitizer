from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QSettings

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.gui.main_window import MainWindow
from logdigitizer.gui.viewer import ImageViewer


def create_test_image(
    path: Path,
    size: tuple[int, int] = (1400, 1100),
) -> None:
    PIL.new("L", size, 128).save(path)


def test_viewer_navigation_hud_is_fixed_and_accepts_colored_mnemonic(
    qtbot,
    tmp_path: Path,
) -> None:
    image = tmp_path / "viewer.png"
    create_test_image(image, size=(500, 400))

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(420, 320)
    viewer.show()
    viewer.load_image(image)

    viewer.set_navigation_hud(
        depth_text="526.3 m",
        curve_mnemonic="GR",
        curve_value_text="67.4 API",
        mnemonic_color="#C64545",
    )

    assert not viewer.navigation_hud.isHidden()
    html = viewer.navigation_hud.text()
    assert "526.3 m" in html
    assert "GR" in html
    assert "67.4 API" in html
    assert "#C64545" in html

    previous_x = viewer.navigation_hud.x()
    viewer.resize(520, 320)
    qtbot.wait(10)
    assert viewer.navigation_hud.x() > previous_x


def test_viewer_emits_visible_center_when_scrolled(
    qtbot,
    tmp_path: Path,
) -> None:
    image = tmp_path / "large.png"
    create_test_image(image)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(360, 280)
    viewer.show()
    viewer.load_image(image)
    viewer.actual_size()
    viewer.centerOn(700.0, 550.0)

    before = viewer.visible_center_image_point()
    assert before is not None

    vbar = viewer.verticalScrollBar()
    assert vbar.maximum() > vbar.minimum()

    with qtbot.waitSignal(
        viewer.visibleCenterImageChanged,
        timeout=1000,
    ) as blocker:
        vbar.setValue(min(vbar.maximum(), vbar.value() + 45))

    x, y, valid = blocker.args
    assert valid is True
    after = viewer.visible_center_image_point()
    assert after is not None
    assert x == pytest.approx(after.x(), abs=1.5)
    assert y == pytest.approx(after.y(), abs=1.5)
    assert after.y() > before.y()


def test_main_window_hud_uses_cursor_position_then_center_navigation(
    qtbot,
    tmp_path: Path,
) -> None:
    image = tmp_path / "segment.png"
    create_test_image(image, size=(1000, 1000))

    service = ProjectService()
    service.new_project(
        name="HUD",
        project_directory=tmp_path / "HUD_project",
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
    service.add_depth_calibration_point(
        pixel_y=0.0,
        depth=0.0,
    )
    service.add_depth_calibration_point(
        pixel_y=999.0,
        depth=999.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=0.0,
        value=0.0,
    )
    service.add_horizontal_calibration_point(
        curve.curve_id,
        pixel_x=999.0,
        value=999.0,
    )

    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )
    window = MainWindow(
        service=service,
        settings=settings,
    )
    qtbot.addWidget(window)
    window.resize(1100, 760)
    window.show()

    # Use the image directly so this test stays focused on HUD behavior rather
    # than preprocessing-file setup.
    window.viewer.load_image(image)
    window.viewer.actual_size()
    window._active_depth_calibration = service.get_depth_calibration()
    window.curve_calibration_controller.refresh(
        selected_curve_id=curve.curve_id
    )

    window._update_coordinates(
        125.0,
        250.0,
        True,
    )
    cursor_html = window.viewer.navigation_hud.text()
    assert "250.0 m" in cursor_html
    assert "125.0 API" in cursor_html
    assert "GR" in cursor_html
    assert "#00B8D9" in cursor_html

    # Navigation has priority after scrolling/panning/zooming and therefore
    # replaces the cursor readout with the current viewport-center values.
    window._update_navigation_hud_from_center_signal(
        500.0,
        600.0,
        True,
    )
    center_html = window.viewer.navigation_hud.text()
    assert "600.0 m" in center_html
    assert "500.0 API" in center_html
    assert "250.0 m" not in center_html

    # Leaving the raster restores the visible-center HUD instead of hiding it.
    window.viewer.centerOn(400.0, 450.0)
    window._update_coordinates(
        0.0,
        0.0,
        False,
    )
    restored_html = window.viewer.navigation_hud.text()
    assert "Depth" in restored_html
    assert "GR" in restored_html
