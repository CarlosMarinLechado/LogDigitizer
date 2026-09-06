"""Persistent recent-project history for the desktop application."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings


MAX_RECENT_PROJECTS = 5
RECENT_PROJECTS_SETTINGS_KEY = "recentProjects/items"


@dataclass(frozen=True, slots=True)
class RecentProjectEntry:
    """One successfully saved project remembered by the application."""

    name: str
    path: str
    saved_at_ns: int

    @property
    def project_path(self) -> Path:
        return Path(self.path)


class RecentProjectsStore:
    """Read/write the five most recently saved projects through ``QSettings``.

    Opening a project does not change this history. Only a successful save (or
    the initial save performed while creating a project) should call
    :meth:`record_saved_project`.
    """

    def __init__(
        self,
        settings: QSettings,
        *,
        maximum: int = MAX_RECENT_PROJECTS,
    ) -> None:
        self._settings = settings
        self._maximum = max(1, int(maximum))

    def entries(self) -> tuple[RecentProjectEntry, ...]:
        """Return valid stored entries ordered from most to least recent."""
        raw = self._settings.value(RECENT_PROJECTS_SETTINGS_KEY, "")
        if not raw:
            return ()
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        if not isinstance(payload, list):
            return ()

        entries: list[RecentProjectEntry] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                name = str(item["name"]).strip()
                path = str(item["path"]).strip()
                saved_at_ns = int(item["saved_at_ns"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not name or not path or saved_at_ns < 0:
                continue
            key = self._path_key(Path(path))
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                RecentProjectEntry(
                    name=name,
                    path=path,
                    saved_at_ns=saved_at_ns,
                )
            )

        entries.sort(key=lambda entry: entry.saved_at_ns, reverse=True)
        return tuple(entries[: self._maximum])

    def record_saved_project(
        self,
        project_file: Path,
        project_name: str,
        *,
        saved_at_ns: int | None = None,
    ) -> tuple[RecentProjectEntry, ...]:
        """Move one successfully saved project to the top of the history."""
        path = Path(project_file).expanduser().resolve()
        name = str(project_name).strip() or path.stem
        timestamp = time.time_ns() if saved_at_ns is None else int(saved_at_ns)
        if timestamp < 0:
            raise ValueError("saved_at_ns cannot be negative.")

        requested_key = self._path_key(path)
        retained = [
            entry
            for entry in self.entries()
            if self._path_key(entry.project_path) != requested_key
        ]
        entries = [
            RecentProjectEntry(
                name=name,
                path=str(path),
                saved_at_ns=timestamp,
            ),
            *retained,
        ][: self._maximum]
        self._write(entries)
        return tuple(entries)

    def remove_project(self, project_file: Path) -> tuple[RecentProjectEntry, ...]:
        """Remove one remembered path, for example after it was moved/deleted."""
        requested_key = self._path_key(Path(project_file))
        entries = [
            entry
            for entry in self.entries()
            if self._path_key(entry.project_path) != requested_key
        ]
        self._write(entries)
        return tuple(entries)

    def clear(self) -> None:
        self._settings.remove(RECENT_PROJECTS_SETTINGS_KEY)
        self._settings.sync()

    def _write(self, entries: list[RecentProjectEntry]) -> None:
        payload = [
            {
                "name": entry.name,
                "path": entry.path,
                "saved_at_ns": entry.saved_at_ns,
            }
            for entry in entries[: self._maximum]
        ]
        self._settings.setValue(
            RECENT_PROJECTS_SETTINGS_KEY,
            json.dumps(payload, ensure_ascii=False),
        )
        self._settings.sync()

    @staticmethod
    def _path_key(path: Path) -> str:
        """Return a stable path key; Windows paths compare case-insensitively."""
        return os.path.normcase(str(Path(path).expanduser().resolve()))
