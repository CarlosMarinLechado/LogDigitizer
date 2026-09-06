# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller configuration for the Windows onedir distribution."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve()
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "logdigitizer"
ICON_FILE = PROJECT_ROOT / "assets" / "LogDigitizer.ico"

# Application resources are copied below PyInstaller's _internal directory.
datas = [
    (str(PACKAGE_ROOT / "splash_screen.png"), "."),
    (
        str(PROJECT_ROOT / "docs" / "LogDigitizer_User_Guide.pdf"),
        "docs",
    ),
    (str(ICON_FILE), "assets"),
]

# QtAwesome loads its icon fonts and metadata dynamically at run time.
datas += collect_data_files("qtawesome")

a = Analysis(
    [str(PACKAGE_ROOT / "app.py")],
    pathex=[str(SRC_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PIL", "pytest", "pytestqt", "PyInstaller"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LogDigitizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON_FILE)],
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LogDigitizer",
)
