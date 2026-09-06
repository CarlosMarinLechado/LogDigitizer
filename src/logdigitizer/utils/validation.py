"""Validation helpers and application-specific exceptions."""

from __future__ import annotations

from pathlib import Path


class LogDigitizerError(Exception):
    """Base exception for expected application errors."""


class ProjectNotFoundError(LogDigitizerError):
    """Raised when a project file does not exist."""


class InvalidProjectError(LogDigitizerError):
    """Raised when project JSON is malformed or structurally invalid."""


class IncompatibleProjectVersionError(LogDigitizerError):
    """Raised when the .logproj format version is unsupported."""


class UnsupportedImageFormatError(LogDigitizerError):
    """Raised when an image extension is not supported in this phase."""


class SourceImageError(LogDigitizerError):
    """Raised when a source image cannot be copied or hashed."""


def validate_project_name(name: str) -> str:
    """Return a normalized non-empty project name."""
    cleaned = name.strip()
    if not cleaned:
        raise InvalidProjectError("Project name cannot be empty.")
    return cleaned


def validate_new_project_directory(path: Path) -> Path:
    """Validate that a new project directory can be created safely."""
    path = path.expanduser().resolve()
    if path.exists():
        raise InvalidProjectError(f"Project directory already exists: {path}")
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise InvalidProjectError(f"Parent directory does not exist: {parent}")
    return path
