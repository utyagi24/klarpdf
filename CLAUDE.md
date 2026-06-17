# CLAUDE.md — pdfproj

Local, offline, **native-Windows** PDF viewer + page editor in Python (PySide6 + PyMuPDF), shipped
as a pinned/auditable offline Windows installer. Replaces macOS Preview's view + splice/split
workflow on Windows. Built **Windows-first** with Linux-ready seams.

## Start here, in order
1. **`PROGRESS.md`** — the live checklist. Read this **first** to see what's done / in progress / next.
2. **`PLAN.md`** — the single source of truth: product spec, architecture, dependencies & packaging,
   portability, the phased **Build order**, the **Execution** section (milestones M0–M9, progress
   convention, Windows handoff), and the **Verification** matrix.

## How we work (conventions — follow these)
- **Hybrid dev (WSL + Windows).** The cross-platform core (`model/`, `viewer/`, `organize/`) and the
  headless tests run in **WSL**; the GUI iterates via **WSLg**. Only **packaging + Windows
  shell-integration** (PyInstaller, Inno Setup, file-association, single-instance/focus *validation*)
  run on **Windows**. See PLAN.md §Development environment.
- **git is the only bridge** between the WSL checkout (`~/pdfproj`) and the Windows checkout
  (`C:\Users\<you>\pdfproj`). **Never** edit one across `\\wsl$` or `/mnt/c`.
- **One PR per milestone.** In the same PR, tick the milestone's box in `PROGRESS.md` and link the PR.
- **Cite sources.** Tie claims/numbers back to a `PLAN.md` section; don't present assumptions as facts.

## Gotchas (cost real time if missed)
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `util/paths.py:normalize_path()` is the single identity chokepoint.
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status
**v0.2.0 shipped (2026-06-17)** — milestones **M0–M15 complete** (v0.1.0 = M0–M9; v0.2.0 = M10–M15).
Releases: <https://github.com/utyagi24/pdfproj/releases/tag/v0.2.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0>. On top of v0.1.0's view + splice/split +
single-instance + undo/redo + lossless materialize-on-save + freeze→installer→CI pipeline, **v0.2.0**
adds toolbar/app **icons**, a live **zoom %** indicator, **printing** (system dialog), **recent
documents**, and **form filling** on a new **page-edit layer** (`model/page_edits.py` — immutable
per-doc edit descriptors applied at materialize; sources stay read-only). 142 headless tests;
real-Windows + frozen-build validation. **Next:** v0.3.0 "Annotate & Redact" (M16–M18). **Planning
the next release?** Read `PROGRESS.md` (status + **Open follow-ups**), then `PLAN.md` §Next-release
roadmap.
