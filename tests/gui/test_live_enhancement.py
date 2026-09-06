from __future__ import annotations
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from logdigitizer.gui.live_enhancement import LiveEnhancementController
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.processing.enhancement import EnhancementSettings


@pytest.fixture
def viewer(qtbot, tmp_path: Path):
    path = tmp_path / "rectified.png"
    PIL.fromarray(np.full((220, 160), 100, dtype=np.uint8)).save(path)
    widget = ImageViewer()
    qtbot.addWidget(widget)
    widget.resize(700, 500)
    widget.show()
    widget.load_image(path)
    widget.actual_size()
    return widget


def test_live_enhancement_changes_canvas_not_base(qtbot, viewer):
    controller = LiveEnhancementController(viewer)
    base = viewer.source_image_array()
    controller.set_base_image(base)
    controller.set_settings(EnhancementSettings(white_point=180))
    qtbot.wait(controller.DEBOUNCE_MS + 80)

    assert viewer.displayed_image_array().mean() > base.mean()
    np.testing.assert_array_equal(viewer.source_image_array(), base)


def test_live_update_preserves_zoom(qtbot, viewer):
    controller = LiveEnhancementController(viewer)
    controller.set_base_image(viewer.source_image_array())
    viewer.zoom_in()
    viewer.zoom_in()
    zoom = viewer.zoom_factor

    controller.set_settings(EnhancementSettings(midtones=1.35))
    qtbot.wait(controller.DEBOUNCE_MS + 80)

    assert viewer.zoom_factor == pytest.approx(zoom, rel=1e-9)


def test_reset_returns_to_rectified_base(qtbot, viewer):
    controller = LiveEnhancementController(viewer)
    base = viewer.source_image_array()
    controller.set_base_image(base)
    controller.set_settings(EnhancementSettings(black_point=30))
    qtbot.wait(controller.DEBOUNCE_MS + 80)

    controller.reset()
    qtbot.wait(controller.DEBOUNCE_MS + 80)

    np.testing.assert_array_equal(viewer.displayed_image_array(), base)
