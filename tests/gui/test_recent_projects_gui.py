from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from logdigitizer.application.project_service import ProjectService
from logdigitizer.application.recent_projects import RecentProjectsStore
from logdigitizer.gui.main_window import MainWindow


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(
        str(tmp_path / "recent-ui.ini"),
        QSettings.Format.IniFormat,
    )


def test_start_page_and_file_menu_share_the_same_recent_project_order(
    qtbot,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = RecentProjectsStore(settings)
    for index, stamp in ((1, 100), (2, 300), (3, 200)):
        path = tmp_path / f"p{index}.logproj"
        path.touch()
        store.record_saved_project(
            path,
            f"Project {index}",
            saved_at_ns=stamp,
        )

    window = MainWindow(settings=settings)
    qtbot.addWidget(window)

    assert window.central_stack.currentWidget() is window.welcome_page
    assert [
        action.text() for action in window.recent_projects_menu.actions()
    ] == [
        "1. Project 2",
        "2. Project 3",
        "3. Project 1",
    ]
    assert [
        button.text().splitlines()[0]
        for button in window._welcome_recent_buttons
    ] == ["Project 2", "Project 3", "Project 1"]


def test_successful_save_moves_current_project_to_top_of_recent_history(
    qtbot,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    old_file = tmp_path / "old.logproj"
    old_file.touch()
    RecentProjectsStore(settings).record_saved_project(
        old_file,
        "Old project",
        saved_at_ns=1,
    )

    service = ProjectService()
    service.new_project(
        name="Current project",
        project_directory=tmp_path / "current_project",
    )
    window = MainWindow(service=service, settings=settings)
    qtbot.addWidget(window)

    window.save_project()

    entries = window.recent_projects.entries()
    assert entries[0].name == "Current project"
    assert entries[0].project_path == service.paths.project_file.resolve()
    assert window.central_stack.currentWidget() is window.viewer


def test_missing_recent_project_is_removed_when_user_tries_to_open_it(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    missing = tmp_path / "moved.logproj"
    store = RecentProjectsStore(settings)
    store.record_saved_project(missing, "Moved project", saved_at_ns=10)

    window = MainWindow(settings=settings)
    qtbot.addWidget(window)
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args: messages.append("warning"),
    )

    window._open_recent_project(missing)

    assert window.recent_projects.entries() == ()
    assert messages == ["warning"]
    assert [
        action.text() for action in window.recent_projects_menu.actions()
    ] == ["No recent projects"]
