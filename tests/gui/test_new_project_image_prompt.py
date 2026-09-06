from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QFileDialog

import logdigitizer.gui.main_window as main_window_module
from logdigitizer.gui.main_window import MainWindow


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(
        str(tmp_path / "new-project-image-prompt.ini"),
        QSettings.Format.IniFormat,
    )


def _accepted_new_project_dialog(project_directory: Path):
    class AcceptedNewProjectDialog:
        def __init__(self, _parent=None) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        @property
        def values(self) -> dict[str, str]:
            return {
                "name": "New log",
                "well_name": "BH-01",
                "project_directory": str(project_directory),
                "description": "",
            }

    return AcceptedNewProjectDialog


def test_new_project_immediately_prompts_for_source_images(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_directory = tmp_path / "new_log_project"
    window = MainWindow(settings=_settings(tmp_path))
    qtbot.addWidget(window)

    monkeypatch.setattr(
        main_window_module,
        "NewProjectDialog",
        _accepted_new_project_dialog(project_directory),
    )

    selected = (tmp_path / "scan-1.tif", tmp_path / "scan-2.png")
    picker_calls: list[tuple[str, str]] = []

    def fake_picker(_parent, title, _directory, filters):
        picker_calls.append((title, filters))
        return ([str(path) for path in selected], "")

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", fake_picker)

    imported: list[tuple[Path, ...]] = []
    monkeypatch.setattr(
        window,
        "_import_image_paths",
        lambda paths: imported.append(paths) or [],
    )

    window.new_project()

    assert window.service.has_project
    assert picker_calls
    assert picker_calls[0][0] == "Add source images"
    assert "Images (" in picker_calls[0][1]
    assert imported == [selected]
    assert window.recent_projects.entries()[0].name == "New log"


def test_cancel_source_picker_keeps_new_empty_project_valid(
    qtbot,
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_directory = tmp_path / "empty_log_project"
    window = MainWindow(settings=_settings(tmp_path))
    qtbot.addWidget(window)

    monkeypatch.setattr(
        main_window_module,
        "NewProjectDialog",
        _accepted_new_project_dialog(project_directory),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *_args, **_kwargs: ([], ""),
    )

    window.new_project()

    assert window.service.has_project
    assert window.service.project is not None
    assert window.service.project.segments == []
    assert not window.service.dirty
    assert window.central_stack.currentWidget() is window.viewer
    assert window.statusBar().currentMessage() == "Project created"
