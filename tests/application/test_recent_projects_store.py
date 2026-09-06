from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings

from logdigitizer.application.recent_projects import (
    MAX_RECENT_PROJECTS,
    RecentProjectsStore,
)


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(
        str(tmp_path / "recent-projects.ini"),
        QSettings.Format.IniFormat,
    )


def test_recent_projects_keep_only_five_and_sort_by_last_successful_save(
    tmp_path: Path,
) -> None:
    store = RecentProjectsStore(_settings(tmp_path))
    files: list[Path] = []
    for index in range(MAX_RECENT_PROJECTS + 2):
        project_file = tmp_path / f"project-{index}.logproj"
        project_file.touch()
        files.append(project_file)
        store.record_saved_project(
            project_file,
            f"Project {index}",
            saved_at_ns=100 + index,
        )

    entries = store.entries()

    assert len(entries) == MAX_RECENT_PROJECTS
    assert [entry.name for entry in entries] == [
        "Project 6",
        "Project 5",
        "Project 4",
        "Project 3",
        "Project 2",
    ]

    # Saving an existing project moves it to the top without duplicating it.
    store.record_saved_project(
        files[4],
        "Project 4",
        saved_at_ns=999,
    )
    entries = store.entries()
    assert entries[0].name == "Project 4"
    assert sum(entry.project_path == files[4].resolve() for entry in entries) == 1


def test_recent_project_can_be_removed_after_file_is_moved_or_deleted(
    tmp_path: Path,
) -> None:
    store = RecentProjectsStore(_settings(tmp_path))
    project_file = tmp_path / "missing.logproj"
    project_file.touch()
    store.record_saved_project(project_file, "Missing", saved_at_ns=10)

    project_file.unlink()
    store.remove_project(project_file)

    assert store.entries() == ()
