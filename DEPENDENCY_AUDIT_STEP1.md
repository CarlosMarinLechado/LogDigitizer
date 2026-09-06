# Log Digitizer dependency audit — Step 1

## Scope

The audit covered the current application entry point, all Python modules under
`src/logdigitizer/`, all tests under `tests/`, and the project metadata. In
total, 60 application modules and 97 test modules were parsed successfully.
The test suite also contains optional dependency checks through
`pytest.importorskip`.

## Direct runtime dependencies

| Distribution | Imported as | Use in Log Digitizer |
|---|---|---|
| PySide6 6.11.2 | `PySide6` | Desktop interface, images, actions, dialogs and graphics |
| QtAwesome 1.4.2 | `qtawesome` | Font Awesome interface icons |
| NumPy 2.5.2 | `numpy` | Image arrays, curve data and numerical processing |
| OpenCV 5.0.0.93 | `cv2` | Image I/O, geometry, rectification and grid detection |
| SciPy 1.18.0 | `scipy` | PCHIP interpolation for manual and assembled curves |

These packages are pinned in `requirements-runtime.txt` to reproduce the
versions already used by the project.

## Development and packaging dependencies

| Distribution | Reason |
|---|---|
| pytest 9.1.1 | Test runner; imported only by tests |
| pytest-qt 4.5.0 | Supplies the `qtbot` fixture used by 47 test modules |
| Pillow 12.3.0 | Creates and reads synthetic images in 32 test modules |
| PyInstaller 6.22.2 | Creates the standalone Windows application |

They are listed in `requirements-dev.txt`, which also includes all runtime
dependencies through `-r requirements-runtime.txt`.

## Packages no longer listed directly

| Package | Audit result |
|---|---|
| colorama | Not imported by the project |
| iniconfig, pluggy, Pygments | Tooling dependencies associated with pytest; pip resolves them |
| PySide6_Addons, PySide6_Essentials, shiboken6 | Components installed automatically with PySide6 |
| QtPy | Compatibility layer installed automatically with QtAwesome |
| packaging | Indirect dependency used by installed tools, not by Log Digitizer source |
| typing_extensions | Indirect compatibility dependency, not imported by the project |

Removing these names from the direct requirements does not prevent pip from
installing them when a selected direct dependency requires them.

## Project configuration findings

- A separate `pytest.ini` is unnecessary: pytest configuration already exists
  in `[tool.pytest.ini_options]` inside `pyproject.toml`.
- The previous `requirements.txt` was a full environment freeze saved as
  UTF-16. It is now a small UTF-8 compatibility file that installs
  `requirements-runtime.txt`.
- `Pillow` is a development-only dependency. It is loaded dynamically by 32
  test modules through `pytest.importorskip("PIL.Image")`; it is not imported
  by the application and is therefore excluded from runtime requirements.
- The ZIP contained `__pycache__` directories and
  `src/digitalizacion_logs.egg-info`. These are generated artifacts and are
  intentionally excluded from the cleaned delivery.
- `pyproject.toml` refers to `README.md`, but the submitted ZIP does not contain
  that file. This must be corrected before installing the project itself with
  `pip install -e .` or building Python package metadata.
- The splash image and the User Guide PDF are application data, not Python
  dependencies. They will be explicitly included later in `LogDigitizer.spec`.

## Files produced

- `requirements-runtime.txt`: direct application dependencies.
- `requirements-dev.txt`: runtime, test and PyInstaller dependencies.
- `requirements.txt`: backward-compatible runtime installer.
- Updated `pyproject.toml`: obsolete Pillow test dependency removed.

The definitive validation is the clean Windows environment and full test run
in Step 2.
