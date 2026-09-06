from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from logdigitizer.gui.viewer import ImageViewer


def test_cursor_signal_emits_image_coordinates(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "signal.png"
    PIL.new("RGB", (100, 100), "white").save(path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)

    viewer.resize(300, 300)
    viewer.load_image(path)
    viewer.actual_size()
    viewer.centerOn(50, 50)
    viewer.show()

    qtbot.waitExposed(viewer)

    target_viewport = viewer.mapFromScene(50, 50)
    global_pos = viewer.viewport().mapToGlobal(target_viewport)

    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(target_viewport),
        QPointF(global_pos),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    with qtbot.waitSignal(
        viewer.cursorPositionChanged,
        timeout=1000,
    ) as blocker:
        QApplication.sendEvent(viewer.viewport(), event)

    x, y, valid = blocker.args

    assert valid is True
    assert x == pytest.approx(50.0, abs=1.5)
    assert y == pytest.approx(50.0, abs=1.5)