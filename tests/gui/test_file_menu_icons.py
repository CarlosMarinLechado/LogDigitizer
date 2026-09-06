from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from logdigitizer.gui.main_window import MainWindow


def test_file_menu_recent_projects_and_add_image_have_icons(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    assert not window.recent_projects_menu.icon().isNull()
    assert not window.add_action.icon().isNull()
