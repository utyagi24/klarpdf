# CLAUDE.md — KlarPDF

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
  (`C:\Users\<you>\pdfproj`). **Never** edit one across `\\wsl$` or `/mnt/c`. (The **directory** names
  keep the old codename by choice — the GitHub repo is `klarpdf`; git doesn't care, and renaming a
  live working directory buys nothing.)
- **Branch + commit + PR for every change — never leave edits uncommitted or on `main`.** This
  applies to **planning/docs** (`PLAN.md`, `PROGRESS.md`, `CLAUDE.md`), not just code. The moment a
  change is ready, create a branch (`plan/…`, `feat/m39-…`, `fix/…`, `docs/…`), commit, push, and
  open a PR with `gh` — **proactively, without being asked**. This is standing authorization; it
  overrides the default of committing only on request. The only exceptions: a throwaway the user said
  not to keep, or when the user explicitly says to hold off. (Local `gh`/`git` quirks live in memory.)
- **The standing authorization stops at *opening* the PR — merging needs an explicit go-ahead.**
  Branch, commit, push, open: yes, always. `gh pr merge`: **only when the owner says so**, for that
  PR or that batch ("merge all open PRs" covers what is open at the time, not the next PR you write
  five minutes later). This is what review *is* — a fix authored and merged in one breath was never
  reviewed, and reporting a bug is a request to diagnose it, not a pre-approval of the patch. Erring
  the wrong way is not symmetric: an unmerged PR costs one message, while an unwanted merge on a
  public repo is in the history, and on `main` it is what the next release ships.
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
- **`README.md` is the one sanctioned exception — and it must be updated on every release.** It is the
  shop window for the public repo, so it *does* restate the shipped version, a one-line what's-new
  for the **current release only** (history lives in GitHub Releases / `PROGRESS.md`), and a
  **Features** inventory; a visitor won't go read `PROGRESS.md`. That restatement is exactly what
  rots: README sat on `v0.9.4` through both v0.9.5 and v0.9.6. So the release checklist
  (`RELEASE.md` §3 step 2) names `README.md` alongside `PROGRESS.md` and `CLAUDE.md`, and the
  version bump, the three status lines, and the release PR all land **together** — including a
  Features-inventory update whenever the release adds or changes a user-facing feature. Everything
  deeper stays a link.

## Gotchas (cost real time if missed)
- **A green Windows + WSL suite does not mean CI is green — and Qt failures are *segfaults*, not
  assertion failures.** M88.3 crashed the Ubuntu runner inside `QGraphicsView`'s constructor ~74%
  into the suite while both local platforms ran it green, because the fault was in Qt's C++ and
  surfaced far from its cause. When CI fails and the local suite passes, read the log for
  `Fatal Python error: Segmentation fault` before assuming a flaky test.
- **Two different bugs have now worn that same costume — so *attribute* the segfault, don't pattern-
  match it.** M88.3 was a genuine lifetime bug (a slot on a freed view). The M89 one looked identical
  — same crash, same constructor, ~74% in — but was **the suite exhausting the runner's memory**: it
  leaked every window it opened, reaching ~107,000 widgets and 8 GiB RSS. The tell that separates
  them: if the *reported test moves* when you change something unrelated, it is resource exhaustion,
  not a bug in the named test. The cheap way to find out is `workflow_dispatch` probe branches
  (`gh workflow run test.yml --ref <branch>`) that split the change into parts, plus a `conftest`
  hook printing RSS / fd / `QApplication.allWidgets()` growth — an answer in two CI runs instead of a
  guess. `tests/test_no_widget_leak.py` now pins the invariant.
- **Closing a Qt window does not destroy it, and `gc.collect()` will not save you.** `MainWindow` is
  a parentless top-level, and the overlay controllers hold *bound methods of the window* as
  callbacks, so the reference cycle spans into C++ where the collector cannot follow (measured:
  collecting frees nothing). Tests must destroy explicitly — `conftest.py`'s
  `pytest_runtest_teardown` hook does it suite-wide, and its docstring records the four traps
  (hookwrapper vs autouse fixture ordering; drain pending `singleShot`s first;
  `sendPostedEvents(DeferredDelete)` because `processEvents` skips them; clear `PdfApp._windows`).
- **Never connect a widget's slot to a signal on a QObject that outlives it** — in particular
  `self.window().windowHandle()`. PySide6 does **not** reliably drop such a connection when the
  receiver is destroyed (measured), so the slot is later invoked on freed memory: a crash, not an
  exception. Prefer the **widget events** Qt delivers to the widget itself (e.g.
  `QEvent.Type.DevicePixelRatioChange`, `ScreenChangeInternal`), which die with it.
- **Never rebuild the scene inside a Qt callback.** `scene.clear()` during `showEvent` /
  `paintEvent` / an event handler destroys every `QGraphicsItem` while Qt is still walking them.
  Defer to the event loop with a `QTimer` **parented to the view** (it is then cancelled on
  destruction, and a burst of events collapses into one pass).
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements-win.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `util/paths.py:normalize_path()` is the single identity chokepoint.
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status
**Current: v0.16.2 shipped** — a reading-bar legibility patch: the resting reading bar drops
**Undo/Redo** and the second **Rotate** button (they were four mirrored curved-arrow glyphs that read
as two near-identical pairs), leaving one Rotate button, following Preview's toolbar; every verb
stays on its shortcut + menu ([#194](https://github.com/utyagi24/klarpdf/pull/194)). It refines
**v0.16.1 "Simplify & Read" (R6, M71–M79)** — the Preview-inspired UI simplification, built on one
idea: the app at rest is a viewer, the markup kit is chrome you summon
on demand. It splits the single toolbar into a resting **reading bar** + a **markup bar** the Markup
toggle reveals; collapses Redact to one gesture-detecting tool; makes Highlight/Underline/Strike/Pen
**sticky**; folds arrowheads into line style (both-ended + dashed); adds **Match case / Whole words**
to the find bar, an **Annotations sidebar tab**, and **Full Screen / Slideshow / Two-Page** view
modes; and (M78.2–.6) adds arrow-key nudge, text-box reflow, HUS arming swatches and a split style
button, with the sidebar (M79.1–.3) losing its title bar and showing optional tabs only on demand.
**M0–M38 and R1–R6 are all complete**; v0.11.0 stays reserved for the MCP / Agent Bridge (M39–M44).
For live status — shipped versions, per-release notes, release links, milestone ticks, and **Open
follow-ups** — see `PROGRESS.md` (the single source of status; read it first). Design/spec, including
§Future enhancements for what's next, lives in `PLAN.md`.
