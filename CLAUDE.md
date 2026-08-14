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
- **Every non-trivial change gets both a `PLAN.md` design entry and a `PROGRESS.md` milestone** —
  in the *same* PR as the code, not afterwards. "Non-trivial" is anything that changes how the app
  behaves or how it is built: a new route through the save path, a contract change, a defect whose
  cause is worth knowing. A one-line typo fix is not; a fix that changes what a Save *writes* is.
  The milestone gets the next free number and `*(unplanned)*` when it was not on the roadmap (see
  M43.1, M93). This is the rule that keeps the design docs from becoming a description of the app as
  it was first imagined rather than as it is.

## Gotchas (cost real time if missed)
- **`insert_pdf` copies pages, not documents.** Everything a PDF keeps at the *catalog* level — the
  accessibility structure tree, `/MarkInfo`, Reader Extensions `/Perms`, the `/Names` tree,
  encryption — is invisible to it and vanishes without a word. It hides well, because the pages come
  through perfectly: the output opens correctly in every viewer while a tagged, AES-encrypted form
  has quietly become untagged, unencrypted and fully permitted (M93, found by TC-002 — *the app*
  did this, not just the bridge). **The tell is to check the catalog, not the pages**:
  `doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")`, `doc.permissions`,
  `doc.metadata["encryption"]`. Two of these can be repaired afterwards and were (the outline M33,
  the metadata stores M53); the structure tree cannot, which is why the save now avoids the graft
  entirely when no page has moved.
- **`tobytes()` writes the copy *decrypted* unless told otherwise**, and `Document.is_encrypted` is
  False for a file that opened without a password — so an owner-password-restricted document reads
  as "not encrypted" and silently saves that way. Ask `doc.metadata["encryption"]` (or
  `doc.permissions != -4`), never `is_encrypted`, when the question is "was this file protected".
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
- **A key routed "through the view" never arrives if a child widget accepted it — and
  `QAbstractItemView` accepts `Space`.** Qt walks a key up the parent chain only while it stays
  *unaccepted*, so M89.2's `Space` was simply gone whenever focus sat in a sidebar panel, and the
  document could not be paged at all until you clicked back on the page (M91.4). Worse, the panel
  did something with it: `selectionCommand → Select` adds the current row to the very selection
  Delete Pages acts on. The fix pattern is `event.ignore()` in the panel + a fallback in
  `MainWindow.keyPressEvent` — **never** a `QAction` shortcut, which fires *before* the focused
  widget and would steal the key from the inline editors. When a key "does nothing", find out who
  accepted it before assuming nothing is bound. **A `QLineEdit` with a validator is the same trap
  wearing gloves**: it accepts the key and the validator drops the character, so the press is
  invisible — that is how `Space` died in the M91.3 page counter.
- **When a *key* looks broken, suspect the wheel that is still running.** A flywheel mouse and
  Windows' smooth scrolling keep emitting wheel events for seconds after the hand leaves them, and
  those events undo whatever a key or click just did. It hides well: scrolling up at offset 0 is a
  no-op, so the coast is invisible until a paging key gives it somewhere to go. The tells are
  **speed-dependence** ("100% if I spin fast, never if I scroll slowly") and a **count of dead
  presses that tracks how hard they spun**. This has now been diagnosed twice — M78 in the
  slideshow, M91.4 in ordinary reading, because the first fix was scoped inside `if self.slideshow`.
  A repro that fires keys with no wheel in flight cannot see it, so **replay the wheel with
  timestamps** (`QWheelEvent.setTimestamp`) when a report is intermittent.
- **`editingFinished` fires on *every* focus-out, not only after an edit.** The Qt docs say
  "contents have changed"; `QLineEdit::focusOutEvent` says `if (hasAcceptableInput() || fixup())
  emit editingFinished()` — measured, no modification check. So a field wired straight to an action
  re-runs it every time the reader clicks away, and if that action *moves* something (M91.4:
  `goto_page` re-seats the view on the page's top) it silently fights them. Guard on `isModified()`,
  which Qt sets on user edits and clears on `setText`.
- **A plain `QWidget` added to a `QToolBar` will eat the bar.** `addWidget` leaves it on the default
  **Preferred** size policy and the toolbar's layout hands it every spare pixel — M91.3's page counter
  stretched to 627 px in an 1100 px window and pushed the whole zoom cluster *off the right-hand end*.
  Always `setSizePolicy(Fixed, …)` (or a fixed width, which is why `ZoomWidget` never showed it). The
  failure mode is chrome that is simply **not there**, so grab the bar and look.
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
**Current: v0.17.1 shipped** — a **security patch plus the last M92 fix**. `pypdf` 6.14.2 → 6.15.0
clears two Moderate parse-DoS advisories (GHSA-fwg2-594c-jp42, GHSA-fp3f-mc75-235c) reachable through
`PyPdfEngine`'s `PdfReader`, so a crafted PDF is the attack surface; it was found by the weekly
`audit` job, not a person, and bumped by hand on Windows per `RELEASE.md` §2 — the episode also
settled a month-old contradiction where Dependabot security-update PRs were enabled against a policy
documenting them as off (`PROGRESS.md` §Open follow-ups). **M92.6** rides along, having merged after
the v0.17.0 tag: the Pages sidebar rolls continuously instead of jumping 2.76 thumbnails a detent.
Both are fixes, so this stays a patch. It supersedes
**v0.17.0** — **scrolling that behaves**: M91 (whitespace fidelity, glyph
legibility, reading position) + M92 (mouse-wheel scrolling). A wheel click moves a defined,
zoom-scaled distance instead of a slice of the window, eased over 200 ms behind **View ▸ Smooth
Scrolling**; the coast-mute is bounded, prefetch is off the scroll's critical path, and the glide
lands on the first/last page instead of restarting into it. Reading gains an editable page counter
and `Space`/`PgUp`/`PgDn` paging from anywhere. **1.0 was deliberately not taken** — the gate (clean
-machine install, the dead Donate link, two flaky tests, background rendering) is listed in
`PROGRESS.md`. v0.17.0 in turn superseded **v0.16.2** — a reading-bar legibility patch: the resting reading bar drops
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
**M0–M38 and R1–R6 are all complete**; the **MCP / Agent Bridge (M39–M44) is scheduled next**, after
a one-PR `os.replace` flake fix — its reserved v0.11.0 is long spent, so its version is assigned at
tag time.
For live status — shipped versions, per-release notes, release links, milestone ticks, and **Open
follow-ups** — see `PROGRESS.md` (the single source of status; read it first). Design/spec, including
§Future enhancements for what's next, lives in `PLAN.md`.
