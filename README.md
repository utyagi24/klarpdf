# pdfproj

Local, offline, **native-Windows** PDF viewer + page editor (Python · PySide6 · PyMuPDF) — a
trustworthy replacement for macOS Preview's view + splice/split workflow on Windows. The source is
the unit of audit; it ships as a pinned, fully offline Windows installer.

**Status: `v0.2.0` shipped** — [download the installer or portable exe](https://github.com/utyagi24/pdfproj/releases/latest). Milestones **M0–M15 complete**.

**New in v0.2.0:** toolbar + app **icons** (theme-aware), a live **zoom %** indicator with Actual-Size
reset, **printing** via the system dialog, a **File ▸ Open Recent** list, and **form filling** —
click and fill AcroForm fields (text/checkbox/dropdown), saved losslessly.

| Doc | What |
|---|---|
| [PLAN.md](PLAN.md) | Product spec, architecture, dependencies/packaging, portability, build order, **Execution**, verification |
| [PROGRESS.md](PROGRESS.md) | Live milestone checklist (M0–M15) + **Open follow-ups** + the v0.3.0 roadmap |
| [CLAUDE.md](CLAUDE.md) | Orientation + conventions for contributors/agents |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Pinned libraries + build toolchain — exact versions, licenses |

## Use it (Windows)

Grab the [latest release](https://github.com/utyagi24/pdfproj/releases/latest):

- **`pdfproj-setup.exe`** — installer (per-user, no admin). Adds pdfproj to the `.pdf` **Open With**
  list + a Start-Menu shortcut; clean uninstall. *Recommended.*
- **`pdfproj-portable.exe`** — single-file portable build; run from any folder (slower first launch,
  no file association).

No Python and no network needed at install or runtime. Unsigned for now → a one-time SmartScreen
"unknown publisher" prompt. Verify a download against `SHA256SUMS` in the release.

## Develop (WSL)

```bash
# one-time: base Ubuntu python lacks ensurepip
sudo apt install -y python3.12-venv

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                          # 142 headless tests (offscreen Qt)
pythonw launcher.py file.pdf    # run the GUI via WSLg
```

The cross-platform core (`model/`, `viewer/`, `organize/`) + headless tests run in WSL; the GUI
iterates via WSLg. Packaging and Windows shell-integration happen on Windows only
(PLAN.md §Development environment). **git is the only bridge** between the WSL and Windows checkouts.

## Build the Windows installer

On Windows (python.org 3.12 + Inno Setup 6), from the repo root:

```powershell
packaging\build.ps1     # wheels -> clean venv -> freeze -> installer + portable + SHA256SUMS (dist\)
```

CI does the same on a tag: push a `v*` tag and `.github/workflows/release.yml` builds on
`windows-latest` and publishes the GitHub Release (PLAN.md §Packaging §5).

## License / audit notes

PyMuPDF is **AGPL** — building for your own machines is private use; public distribution offers the
corresponding source (this repo at the release tag). Dependencies are pinned with hashes and
vendored for an offline, auditable build. See DEPENDENCIES.md and PLAN.md §Packaging.
