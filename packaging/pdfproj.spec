# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freezes pdfproj into two artifacts from one analysis (PLAN.md, Packaging §3):

  dist/pdfproj/pdfproj.exe   (--onedir)   bundled by the Inno Setup installer; fast startup
  dist/pdfproj-portable.exe  (--onefile)  portable, run-anywhere build

Both are windowed (no console). Build on Windows; cannot be cross-built from WSL. Run from the
repo root:  py -3.12 -m PyInstaller packaging/pdfproj.spec --noconfirm
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # spec lives in packaging/; ROOT is the repo root
ICON = str(ROOT / "packaging" / "pdfproj.ico")  # embedded in both exes (M10)

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # Ship the hand-authored toolbar SVGs (rendered at runtime by ui/icons.py).
    datas=[(str(ROOT / "ui" / "icons"), "ui/icons")],
    # QtSvg renders the toolbar icons (ui/icons.py); QtPrintSupport drives the print dialog
    # (viewer/printing.py). Pin both so the freeze always carries them + their plugins.
    hiddenimports=["PySide6.QtSvg", "PySide6.QtPrintSupport"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The app imports only QtCore/QtGui/QtWidgets/QtNetwork. PySide6-Essentials still bundles
        # these large, unused module families — drop them to slim both artifacts. (If a freeze ever
        # fails on a missing module, remove the offending name here.)
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtWebChannel",
        "PySide6.QtWebSockets", "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# --- onedir: the installer's payload (dist/pdfproj/pdfproj.exe + Qt/PyMuPDF/CPython) ---
exe_onedir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # binaries land in COLLECT below, not inside the exe
    name="pdfproj",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
coll = COLLECT(
    exe_onedir,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdfproj",
)

# --- onefile: portable single exe (dist/pdfproj-portable.exe) ---
exe_onefile = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pdfproj-portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
