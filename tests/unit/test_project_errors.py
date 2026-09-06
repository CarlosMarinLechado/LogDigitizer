import json
from pathlib import Path

import pytest

from logdigitizer.io.project_store import load_project
from logdigitizer.utils.validation import (
    IncompatibleProjectVersionError,
    InvalidProjectError,
    ProjectNotFoundError,
)


def test_missing_project_file_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        load_project(tmp_path / "missing.logproj")


def test_corrupt_json_has_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.logproj"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(InvalidProjectError):
        load_project(path)


def test_incompatible_format_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.logproj"
    path.write_text(
        json.dumps({"format_version": 999, "project": {}}),
        encoding="utf-8",
    )
    with pytest.raises(IncompatibleProjectVersionError):
        load_project(path)
