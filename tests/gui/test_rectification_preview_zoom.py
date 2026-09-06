from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.rectification_preview import RectificationPreviewDialog


def make_dialog(qtbot) -> RectificationPreviewDialog:
    image = np.zeros((4000, 1800), dtype=np.uint8)
    dialog = RectificationPreviewDialog(image, (1800, 4000))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(60)
    return dialog


def test_preview_opens_fit_to_window(qtbot) -> None:
    dialog = make_dialog(qtbot)
    assert dialog.viewer.zoom_factor < 1.0
    assert "Zoom:" in dialog.zoom_label.text()


def test_preview_actual_size_is_one_to_one(qtbot) -> None:
    dialog = make_dialog(qtbot)
    dialog.viewer.actual_size()
    assert dialog.viewer.zoom_factor == pytest.approx(1.0, abs=1e-9)


def test_preview_zoom_in_out_changes_scale(qtbot) -> None:
    dialog = make_dialog(qtbot)
    initial = dialog.viewer.zoom_factor
    dialog.viewer.zoom_in()
    assert dialog.viewer.zoom_factor > initial
    dialog.viewer.zoom_out()
    assert dialog.viewer.zoom_factor == pytest.approx(initial, rel=1e-6)


def test_fit_restores_initial_fit_after_manual_zoom(qtbot) -> None:
    dialog = make_dialog(qtbot)
    fitted = dialog.viewer.zoom_factor
    dialog.viewer.actual_size()
    assert dialog.viewer.zoom_factor != pytest.approx(fitted)
    dialog.viewer.fit_to_window()
    assert dialog.viewer.zoom_factor == pytest.approx(fitted, rel=1e-3)
