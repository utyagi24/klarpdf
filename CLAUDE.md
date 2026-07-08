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
- **Branch + commit + PR for every change — never leave edits uncommitted or on `main`.** This
  applies to **planning/docs** (`PLAN.md`, `PROGRESS.md`, `CLAUDE.md`), not just code. The moment a
  change is ready, create a branch (`plan/…`, `feat/m39-…`, `fix/…`, `docs/…`), commit, push, and
  open a PR with `gh` — **proactively, without being asked**. This is standing authorization; it
  overrides the default of committing only on request. The only exceptions: a throwaway the user said
  not to keep, or when the user explicitly says to hold off. (Local `gh`/`git` quirks live in memory.)
- **Always branch from an up-to-date `main`.** Before creating a branch, check what's checked out
  (`git branch --show-current`); a new branch must be based on **`origin/main`**, *not* on whatever
  feature branch is currently active — else that branch's commits ride into your PR (e.g. an unrelated
  open PR leaking into a new one). Use `git fetch origin && git switch -c <name> origin/main`. The one
  exception is *intentionally stacking* on an open PR — then base the branch on it **and** set the PR's
  base to match. Sanity-check before pushing: `git diff --stat origin/main..HEAD` should list only your
  own files.
- **Delete the local branch after its PR merges.** Once a PR is merged, switch back and prune:
  `git checkout main && git pull --ff-only && git branch -d <branch>`, plus `git fetch --prune` to
  drop stale remote-tracking refs. Don't let merged branches pile up. (GitHub auto-deletes the remote
  head branch on merge, so only the local copy needs cleaning.)
- **One PR per milestone** (implementation); one PR per logical unit for planning/process changes. In
  the same PR, tick the milestone's box in `PROGRESS.md` and link the PR.
- **Cite sources.** Tie claims/numbers back to a `PLAN.md` section; don't present assumptions as facts.
- **Where things live — update in exactly one place** (avoid the drift that let a stale status blurb
  triplicate across the docs). *Status* — shipped versions, release links, per-release notes, milestone
  ticks, open follow-ups → **`PROGRESS.md`** only. *Design / spec* — architecture, packaging,
  verification, the roadmap & rationale of each milestone → **`PLAN.md`**. *How we work* — conventions,
  gotchas, environment → **`CLAUDE.md`**. The other two **link**, never restate. Rule of thumb:
  **status → PROGRESS; design → PLAN; process → CLAUDE.**

## Gotchas (cost real time if missed)
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements-win.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `util/paths.py:normalize_path()` is the single identity chokepoint.
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status
**Current: v0.9.6 shipped** — the planned roadmap **M0–M38 is complete**. For live status — shipped
versions, per-release notes, release links, milestone ticks, and **Open follow-ups** — see
`PROGRESS.md` (the single source of status; read it first). Design/spec, including §Future
enhancements for what's next, lives in `PLAN.md`.
