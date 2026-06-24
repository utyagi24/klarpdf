# Dependencies

Versions have a single source of truth: **`requirements.in`** (top-level floor pins) → compiled locks:

| Lock | For | Pinning | Produced |
|---|---|---|---|
| `requirements-dev.txt` | dev + tests (WSL/Windows) | exact `==`, **no hashes** | `pip-compile requirements-dev.in` |
| `requirements-win.txt` | Windows ship build | exact `==` + `sha256` per wheel | **M6** (Windows), `pip-compile --generate-hashes` |
| `requirements-build-win.txt` | build toolchain (PyInstaller) | exact `==` + `sha256` (`--allow-unsafe`) | **M8** (Windows), `pip-compile --generate-hashes` |

`--require-hashes` is **not** shareable across platforms: Linux `manylinux` wheels and Windows
`win_amd64` wheels have different hashes. So dev installs by version only; the hashed, offline,
vendored lock is the Windows ship build's job. See PLAN.md §Development environment.

**Vendored wheels are not committed.** The `win_amd64` wheel set (~94 MB) is a local build input,
re-fetched offline-thereafter with
`pip download -r requirements-win.txt --only-binary=:all: -d vendor/wheels`.
[`vendor/wheels-sources.md`](vendor/wheels-sources.md) records each wheel's exact version, `sha256`
(same as `requirements-win.txt`), and source URL, so the set is reproducible and auditable without
storing binaries in git (which also dodges GitHub's 100 MB/file limit). The **M8 installer bundles**
the wheels, so target machines need no Python and no network.

## Runtime libraries (`requirements.in`)
| Library | Purpose | License | Floor | Locked |
|---|---|---|---|---|
| **PySide6-Essentials** | Qt6 GUI (QtCore/QtGui/QtWidgets/QtSvg) + `QtNetwork` IPC + `QtPrintSupport` printing | LGPL-3.0 | `>=6.7` | `6.11.1` |
| **PyMuPDF** (`fitz`) | render pages/thumbnails + lossless object-level page editing | AGPL-3.0 (or Artifex commercial) | `>=1.25.5` | `1.27.2.3` |
| **pypdf** | pure-Python fallback edit engine | BSD-3-Clause | `>=6.13.3` | `6.13.3` |
| _shiboken6_ (transitive) | PySide6 C++/Python binding runtime | LGPL-3.0 | — | `6.11.1` |

> **Why Essentials, not the full `PySide6` meta:** the app imports only QtCore/QtGui/QtWidgets/
> QtSvg/QtNetwork/QtPrintSupport — all in Essentials (`QtSvg` renders the M10 toolbar icons,
> `QtPrintSupport` drives M12 printing; no new package). The ~161 MB Addons set (QtWebEngine/Charts/Multimedia/QtPdf…) is
> unused — we render via PyMuPDF, not QtPdf — so excluding it shrinks the bundle, the installer, and
> the audit surface. The bump path is unchanged: edit `requirements.in` → re-compile → re-vendor.

> **PyMuPDF AGPL:** fine for private / own-machine builds; public distribution must offer the
> corresponding source (this repo at the exact tag satisfies it). See PLAN.md AGPL note.

## Test / build toolchain
| Tool | Purpose | Version | Pinned where |
|---|---|---|---|
| **Python** | interpreter — 3.12.x exact | Windows **3.12.10** (python.org); WSL 3.12.3 | — |
| **pip-tools** (`pip-compile`) | generate the locked requirements | **7.5.3** | dev/build env |
| **pytest** | headless model/save tests | see `requirements-dev.txt` | `requirements-dev.txt` |
| **invoke** | task runner — `invoke <task>` orchestrates build steps (`tasks.py`; see `RELEASE.md`) | **3.0.3** | `requirements-dev.txt` |
| **PyInstaller** | freeze the app (onedir + onefile) | **6.21.0** | `requirements-build-win.txt` (hashed) |
| **Inno Setup** | build the Windows installer | **6.7.3** | here (native tool; `winget install JRSoftware.InnoSetup`, CI `choco install innosetup`) |

M6 produced the hashed `win_amd64` ship lock (`requirements-win.txt`) on python.org **3.12.10** with
**pip-tools 7.5.3**. M8 added the **build** toolchain — **PyInstaller 6.21.0** (hashed in
`requirements-build-win.txt`) and **Inno Setup 6.7.3** — driven by `packaging/build.ps1`
(`.github/workflows/release.yml` runs the same on CI). Exact resolved versions are the **Locked**
columns and the lock files.
