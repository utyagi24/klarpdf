# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freezes KlarPDF into two artifacts from one analysis (PLAN.md, Packaging §3):

  dist/klarpdf/klarpdf.exe       (--onedir)   bundled by the Inno Setup installer; fast startup
  dist/klarpdf-portable-x64.exe  (--onefile)  portable, run-anywhere build

Artifact names carry an explicit -x64 suffix (only architecture built today, via win_amd64-pinned
wheels on a windows-latest x64 runner) so a future arm64 build can't collide with or be mistaken
for this one.

Both are windowed (no console). Build on Windows; cannot be cross-built from WSL. Run from the
repo root:  py -3.12 -m PyInstaller packaging/klarpdf.spec --noconfirm
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # spec lives in packaging/; ROOT is the repo root
ICON = str(ROOT / "packaging" / "klarpdf.ico")  # embedded in both exes (M10)

# --- Windows version resource ---------------------------------------------------------------
# Read the single source of the version rather than restating it (version.py). Until now the exes
# carried *no* version resource at all — Explorer's Properties -> Details was blank, and an unsigned
# binary with no version info is a mild antivirus heuristic. RELEASE.md always claimed version.py
# "feeds the PyInstaller exe metadata"; this is what finally makes that true.
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402

# The Win32 FIXEDFILEINFO struct wants exactly four integers; SemVer gives three.
_parts = tuple(int(p) for p in __version__.split(".")) + (0,)
assert len(_parts) == 4, f"unexpected version shape: {__version__!r}"


def _version_resource(exe_name: str) -> str:
    """Write a PyInstaller version file for ``exe_name`` and return its path (Windows only).

    Per-exe because ``OriginalFilename`` must name the artifact it is embedded in — the onedir exe is
    ``klarpdf.exe``, the portable one is ``klarpdf-portable-x64.exe``.
    """
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_parts, prodvers=_parts, mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [  # US English, Unicode
                StringStruct("CompanyName", "KlarPDF contributors"),
                StringStruct("FileDescription", "KlarPDF — local, offline PDF viewer and editor"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", Path(exe_name).stem),
                StringStruct("LegalCopyright", "Copyright (C) 2026 KlarPDF contributors. AGPL-3.0-or-later."),
                StringStruct("OriginalFilename", exe_name),
                StringStruct("ProductName", "KlarPDF"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    out = ROOT / "build" / f"version_info_{Path(exe_name).stem}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(str(info), encoding="utf-8")
    return str(out)


_WIN = sys.platform == "win32"
VERSION_ONEDIR = _version_resource("klarpdf.exe") if _WIN else None
VERSION_ONEFILE = _version_resource("klarpdf-portable-x64.exe") if _WIN else None

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # The hand-authored toolbar SVGs (rendered at runtime by ui/icons.py).
        (str(ROOT / "ui" / "icons"), "ui/icons"),
        # AGPL §5 obliges us to ship the license with the binary, and Help ▸ Open-Source Licenses
        # reads these at runtime via util/resources.py. Land them at the bundle root, which is
        # where `resource_root()` looks. Drop either entry and that dialog shows a placeholder —
        # the headless suite cannot catch it, so `tests/test_about_dialog.py` asserts this list.
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_LICENSES"), "."),
    ],
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

# --- onedir: the installer's payload (dist/klarpdf/klarpdf.exe + Qt/PyMuPDF/CPython) ---
exe_onedir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # binaries land in COLLECT below, not inside the exe
    name="klarpdf",
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
    version=VERSION_ONEDIR,
)
coll = COLLECT(
    exe_onedir,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="klarpdf",
)

# --- onefile: portable single exe (dist/klarpdf-portable-x64.exe) ---
exe_onefile = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="klarpdf-portable-x64",
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
    version=VERSION_ONEFILE,
)
