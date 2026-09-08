from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from logdigitizer.gui.viewer import ImageViewer


def test_focus_image_interval_zooms_and_centers_near_requested_gap(
    qtbot,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "qc_focus.png"
    PIL.new("L", (600, 1200), 220).save(image_path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(900, 700)
    viewer.show()
    viewer.load_image(image_path)
    fitted_zoom = viewer.zoom_factor

    viewer.focus_image_interval(
        y_start_px=500.0,
        y_stop_px=520.0,
        anchor_y_px=510.0,
        center_x_px=200.0,
    )

    assert viewer.zoom_factor > fitted_zoom
    center = viewer.visible_center_image_point()
    assert center is not None
    assert center.y() == pytest.approx(510.0, abs=3.0)
    assert center.x() == pytest.approx(200.0, abs=3.0)



def test_tight_focus_is_materially_closer_than_review_focus(
    qtbot,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "qc_tight_focus.png"
    PIL.new("L", (600, 1200), 220).save(image_path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(900, 700)
    viewer.show()
    viewer.load_image(image_path)

    viewer.focus_image_interval(
        y_start_px=500.0,
        y_stop_px=520.0,
        anchor_y_px=510.0,
        center_x_px=200.0,
        tight_focus=False,
    )
    review_zoom = viewer.zoom_factor

    viewer.focus_image_interval(
        y_start_px=500.0,
        y_stop_px=520.0,
        anchor_y_px=510.0,
        center_x_px=200.0,
        tight_focus=True,
    )
    interpolate_zoom = viewer.zoom_factor

    # For a tiny interval, inspection mode remains clearly closer than
    # Review, while preserving more surrounding context than the previous v4.
    assert interpolate_zoom > review_zoom * 1.3
    center = viewer.visible_center_image_point()
    assert center is not None
    assert center.y() == pytest.approx(510.0, abs=3.0)
