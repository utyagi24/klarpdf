# Dependencies

Versions have a single source of truth: **`requirements.in`** (top-level floor pins) → compiled locks:

| Lock | For | Pinning | Produced |
|---|---|---|---|
| `requirements-dev.txt` | WSL dev + tests | exact `==`, **no hashes** | now (WSL), via `pip-compile requirements-dev.in` |
| `requirements.txt` | Windows ship build | exact `==` + `sha256` per **win_amd64** wheel | M6 (Windows), via `pip-compile --generate-hashes` |

`--require-hashes` is **not** shareable across platforms: Linux `manylinux` wheels and Windows
`win_amd64` wheels have different hashes. So dev (Linux) installs by version only; the hashed,
offline, vendored lock is the Windows ship build's job. See PLAN.md §Development environment.

## Runtime libraries (`requirements.in`)
| Library | Purpose | License | Floor |
|---|---|---|---|
| **PySide6** | Qt6 GUI; drag/drop, clipboard, `QLocalServer` single-instance IPC | LGPL-3.0 | `>=6.7` |
| **PyMuPDF** (`fitz`) | render pages/thumbnails + lossless object-level page editing | AGPL-3.0 (or Artifex commercial) | `>=1.25.5` |
| **pypdf** | pure-Python fallback edit engine | BSD-3-Clause | `>=4.0` |

> **PyMuPDF AGPL:** fine for private / own-machine builds; public distribution must offer the
> corresponding source (this repo at the exact tag satisfies it). See PLAN.md AGPL note.

## Test / build toolchain
| Tool | Purpose | Pinned where | Introduced |
|---|---|---|---|
| **pytest** | headless model/save tests | `requirements-dev.txt` | M0 |
| **pip-tools** (`pip-compile`) | generate the locked requirements | dev/build env | M0 |
| **Python** | interpreter — 3.12.x exact | — | WSL 3.12.3 (M0); Windows python.org 3.12.x (M6) |
| **PyInstaller** | freeze the Windows app | `requirements.txt` (build) | M6/M8 (Windows) |
| **Inno Setup** | build the Windows installer | recorded here at M8 | M8 (Windows) |

Exact resolved versions live in the lock files (`requirements-dev.txt` now; `requirements.txt` at M6).
