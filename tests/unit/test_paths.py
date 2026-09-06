import sys
from pathlib import Path

from logdigitizer.utils.paths import (
    APPLICATION_ICON_RELATIVE_PATH,
    USER_GUIDE_RELATIVE_PATH,
    application_icon_path,
    resolve_project_path,
    to_project_relative,
    user_guide_path,
)


def test_project_relative_path_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "source"
    source.mkdir(parents=True)
    image = source / "image.tif"
    image.write_bytes(b"test")

    stored = to_project_relative(image, root)
    resolved = resolve_project_path(stored, root)

    assert stored == "source/image.tif"
    assert resolved == image.resolve()


def test_bundled_resources_use_pyinstaller_internal_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application_dir = tmp_path / "LogDigitizer"
    internal_dir = application_dir / "_internal"
    internal_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(application_dir / "LogDigitizer.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)

    assert application_icon_path() == internal_dir / APPLICATION_ICON_RELATIVE_PATH
    assert user_guide_path() == internal_dir / USER_GUIDE_RELATIVE_PATH


def test_installed_user_guide_can_override_bundled_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    application_dir = tmp_path / "LogDigitizer"
    internal_dir = application_dir / "_internal"
    installed_guide = application_dir / USER_GUIDE_RELATIVE_PATH
    internal_dir.mkdir(parents=True)
    installed_guide.parent.mkdir(parents=True)
    installed_guide.write_bytes(b"PDF")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(application_dir / "LogDigitizer.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)

    assert user_guide_path() == installed_guide
