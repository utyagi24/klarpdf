# pdfproj

Local, offline, **native-Windows** PDF viewer + page editor (Python · PySide6 · PyMuPDF) — a
trustworthy replacement for macOS Preview's view + splice/split workflow on Windows. The source is
the unit of audit; it ships as a pinned, fully offline Windows installer.

**Status: `v0.5.0` shipped** — [download the installer or portable exe](https://github.com/utyagi24/pdfproj/releases/latest). Milestones **M0–M26 complete**.

**New in v0.5.0 — File Safety & Output:** **Revert to Saved** (discard edits, reload from disk); an
**external-change warning** when another program modifies the open file (Reload / Keep, plus an
overwrite guard before Save); and **edits-aware printing** — the printout now shows your annotations,
form values, and redactions (a not-yet-saved redaction no longer prints the original).
**v0.4.0 — Annotate & Redact:** text **highlight** and **text boxes** (drag to move,
double-click to re-edit, auto-growing); and **true destructive redaction** — drag over text or over
a block to permanently remove it at save (verified gone with a cross-engine check; a redacted save
is a confirmed point of no return). Annotate/redact tools are **one-shot armed** gestures.
**v0.3.0:** better **drag-and-drop**, **drag a PDF in from File Explorer**, and a **Grab/Select**
viewer-mode toggle.
**v0.2.0:** theme-aware **icons**, a live **zoom %** indicator, **printing**, **Open Recent**, and
**form filling** (click and fill AcroForm fields, saved losslessly).

| Doc | What |
|---|---|
| [PLAN.md](PLAN.md) | Product spec, architecture, dependencies/packaging, portability, build order, **Execution**, verification |
| [PROGRESS.md](PROGRESS.md) | Live milestone checklist (M0–M26 shipped; v0.6.0–v0.8.0 roadmap) + **Open follow-ups** |
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
pytest                          # 248 headless tests (offscreen Qt)
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
