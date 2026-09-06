"""Centralized filesystem path handling."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

APPLICATION_ICON_RELATIVE_PATH = Path("assets") / "LogDigitizer.ico"
USER_GUIDE_RELATIVE_PATH = Path("docs") / "LogDigitizer_User_Guide.pdf"

PROJECT_SUBDIRECTORIES = (
    "source",
    "preprocessed",
    "calibration",
    "digitization",
    "export",
    "cache",
    "logs",
)


def application_root() -> Path:
    """Return the application root in development and packaged builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    package_root = Path(__file__).resolve().parents[1]
    source_root = package_root.parent
    if source_root.name == "src":
        return source_root.parent
    return package_root


def bundled_resource_root() -> Path:
    """Return the root containing bundled read-only application resources."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle_root:
        return Path(bundle_root).resolve()
    return application_root()


def bundled_resource_path(relative_path: Path) -> Path:
    """Resolve a resource in source and PyInstaller builds."""
    return bundled_resource_root() / relative_path


def application_icon_path() -> Path:
    """Return the expected path to the application icon."""
    return bundled_resource_path(APPLICATION_ICON_RELATIVE_PATH)


def user_guide_path() -> Path:
    """Return the expected path to the installed User Guide PDF."""
    installed_path = application_root() / USER_GUIDE_RELATIVE_PATH
    if getattr(sys, "frozen", False) and installed_path.is_file():
        return installed_path
    return bundled_resource_path(USER_GUIDE_RELATIVE_PATH)


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Resolved paths belonging to a project root."""

    root: Path
    project_file: Path

    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def preprocessed(self) -> Path:
        return self.root / "preprocessed"

    @property
    def logs(self) -> Path:
        return self.root / "logs"


def safe_project_stem(name: str) -> str:
    """Create a conservative filename stem while preserving project display name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._-")
    return stem or "project"


def create_project_directories(root: Path) -> None:
    """Create the canonical project directory tree."""
    root.mkdir(parents=False, exist_ok=False)
    for dirname in PROJECT_SUBDIRECTORIES:
        (root / dirname).mkdir()


def to_project_relative(path: Path, project_root: Path) -> str:
    """Return a POSIX relative path if located inside the project root."""
    resolved = path.resolve()
    root = project_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def resolve_project_path(stored_path: str, project_root: Path) -> Path:
    """Resolve a stored relative or absolute path without requiring it to exist."""
    candidate = Path(stored_path)
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()
