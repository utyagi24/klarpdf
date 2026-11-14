# CLAUDE.md — KlarPDF

Local, offline, **native-Windows** PDF viewer + page editor in Python (PySide6 + PyMuPDF), shipped
as a pinned/auditable offline Windows installer. Replaces macOS Preview's view + splice/split
workflow on Windows. Built **Windows-first** with Linux-ready seams.

## Start here, in order
1. **`PROGRESS.md`** — what is shipped, what is open, the 1.0 list and the open follow-ups. Read it
   first.
2. **`PLAN.md`** — the design that holds across milestones: product spec, architecture, dependencies
   & packaging, portability, the phased **Build order**, the **Execution** section, the
   **Verification** matrix and the roadmaps.
3. **`docs/milestones/<id>.md`** — one milestone's design and its record, such as
   `docs/milestones/M152.md`. Open the one you need. Search the folder; do not read it whole.

## How we work (conventions — follow these)
- **Hybrid dev (WSL + Windows).** The cross-platform code (`klarpdf/model/`, `viewer/`, `organize/`) and the
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
  the same PR, record the milestone as done (step 2 under *Where things live*).
- **Cite sources.** Tie claims/numbers back to a `PLAN.md` section or a milestone file; don't present
  assumptions as facts.
- **Where things live — update in exactly one place** (avoid the drift that let a stale status blurb
  triplicate across the docs).
  * **`PROGRESS.md`**: status. The current release's notes, the open milestones, the 1.0 list and
    the open follow-ups.
  * **`PLAN.md`**: design that holds across milestones: architecture, packaging, verification and
    the roadmaps.
  * **`docs/milestones/<id>.md`**: everything about one milestone. `## Design` says why and how;
    `## Progress` records it as done and links the PR. A part such as M152.1 goes in its parent's
    file. A group planned as one, such as M133–M136, has one file, `M133-M136.md`.
  * **`docs/releases.md`**: the notes of every release before the current one.
  * **`CLAUDE.md`**: how we work: conventions, gotchas, environment.
  * **`docs/why-the-rules.md`**: the incidents and measurements behind the longer rules in
    `CLAUDE.md`. Each rule there links to its section.
  * **A reproducible defect**: a GitHub issue (see *A reproducible defect in shipped code goes to
    GitHub Issues* below).

  The others **link**, never restate. A milestone moves through these files in its own PRs, so
  nothing waits for a cleanup:
  1. **Planned**: create `docs/milestones/M178.md` with its `## Design`, and add an open
     `- [ ] **M178**` line to its roadmap in `PROGRESS.md`. An unplanned milestone does steps 1 and
     2 in one PR.
  2. **Done**: add `## Progress` to the file, with the PR link. Take the `- [ ]` line out of
     `PROGRESS.md` and add the milestone to that roadmap's `Done, each with its record…` line.
  3. **Released**: the release PR moves the previous release's notes from the top of `PROGRESS.md`
     to the top of `docs/releases.md` (`RELEASE.md` §3 step 2).

  `PLAN.md` changes only when a milestone changes the design that holds across milestones. It is
  not where a new milestone's write-up goes. Cite a milestone by its file, `docs/milestones/M178.md`.
  An older citation such as `PLAN.md §M152` still works, because `PLAN.md` kept each moved heading
  with a link to its file.
- **`README.md` is the one exception, and it is updated on every release.** It restates the shipped
  version, a one-line what's-new for the current release, and a **Features** list. The version and
  the what's-new change in the release PR (`RELEASE.md` §3 step 2). The Features list changes in the
  PR that changes a user-facing behaviour, not at the release. Why: `docs/why-the-rules.md` §README.
- **Install examples lead with `uv`; `pipx` is the named equivalent, never the lead** (2026-09-07).
  In a code block, `uv` is the line and `pipx` is a trailing `# pipx: <equivalent>` comment. In prose
  and tables, `uv` is named first. This applies to `README.md`, `klarpdf/mcp_bridge/README.md`,
  `klarpdf/mcp_bridge/QUICKSTART.md` and any text `install.py` prints. `PLAN.md`, `PROGRESS.md` and
  the milestone files are records: do not rewrite them to match. Why: `docs/why-the-rules.md` §uv first.

- **Capture the follow-up in the session that found it.** *Where things live* routes open
  follow-ups to `PROGRESS.md`; this is the part that rule assumes — that they get written at
  all. Anything deferred, **rejected**, or noticed-but-not-fixed is committed before the session
  ends, rejections with their reason so the next one does not re-derive a settled argument. Chat
  scrollback is not a backlog: sessions end and context is compacted, and an item held only in an
  assistant's working memory is lost silently at either boundary — three real findings from the
  TC-007/TC-008 rounds were sitting there on 2026-08-18 (`PROGRESS.md` §Open follow-ups has them
  now).
- **A reproducible defect in shipped code goes to GitHub Issues. Anything that still needs a
  decision stays in `PROGRESS.md` §Open follow-ups** (owner, 2026-08-25). An item that becomes an
  issue does not also get a follow-up bullet. A follow-up that gets decided becomes an issue or a
  milestone, and leaves a one-line pointer. The fix still owes its milestone file. A fix built in
  parts says `Part of #N` in each PR and `Fixes #N` in the last one.
  **Label every issue** (owner, 2026-09-18) `bug` or `enhancement`, plus each of these that
  applies: `app` (seen in the desktop app), `mcp-bridge` (seen through the bridge's tools) and
  `core` (the fix belongs in `klarpdf/model/` or `klarpdf/util/`, so it owes tests on both
  surfaces). Check the code before labelling, and relabel when the fix goes elsewhere.
  Details: `docs/why-the-rules.md` §Issues and follow-ups.
- **Investigate a defect once, when it is fixed** (owner, 2026-09-19). **Filing an issue** records
  the report: what the reader sees, the steps, the document, the environment and what was expected.
  Do not go looking for the cause. **Planning** groups issues from what their tickets say, and
  measures nothing. **Building the fix** is where the diagnosis, measurements, design, questions for
  the owner and the milestone file happen. Why: `docs/why-the-rules.md` §Investigate once.
- **Every non-trivial change gets a milestone file** (`docs/milestones/<id>.md`), in the same PR as
  the code. Non-trivial means it changes how the app behaves or how it is built; a typo fix is not.
  The milestone takes the next free number, and `*(unplanned)*` if it was not on the roadmap.
  **"Next free" counts the open PRs too, and a reserved number counts as taken.** Check first:
  `for n in $(gh pr list --state open --json number -q '.[].number'); do gh pr diff $n | grep -E '^\+' | grep -oE '\bM[0-9]{2,3}\b'; done | sort -u`.
  Why: `docs/why-the-rules.md` §Milestone numbers.
- **Two consumers share one core, so every change answers for both.** `klarpdf/model/` and
  `klarpdf/util/` are used by the app (`app.py`, `main_window.py`) and by the MCP bridge
  (`klarpdf/mcp_bridge/`). Every behaviour change, design entry and feature names the surfaces it
  touches. A core change wants a test on both sides (`tests/test_mcp_*.py` for the bridge). Code
  only the app uses goes outside `klarpdf/`; when the bridge needs it, move it into
  `klarpdf/model/` first. `tests/test_mcp_no_qt.py` enforces this. When a claim says "we write",
  "we open" or "we refuse", check it at every surface: `PyMuPDFEngine.materialize`,
  `MainWindow._write_to` and `transforms._write`. No surface writes to the original file. The OS
  is a separate rule (*Keep OS-specific code quarantined*). Details: `docs/why-the-rules.md` §Two consumers.

- **Break a check on purpose and watch it fail.** A test fixture, CI job or runbook step is not
  done because it passed. Break what it checks and confirm it fails, then keep it. A CI job builds
  what it tests; it never fetches a published copy of the version under development. A test that
  runs a `#!/bin/sh` fixture or uses `chmod` fails on Windows. A runbook step is written down only
  after it has been run once. A green local suite is grounds to push, not proof the change is
  right. Examples: `docs/why-the-rules.md` §Verify the checks.

- **Run the tests for your change locally, and let CI run the full suite** (owner, 2026-09-25).
  Run the test files that cover your change. Check that each new test fails when the fix is broken.
  Then push, and wait for CI once in the background, not by polling (Linux about 7 minutes,
  Windows about 9). Also run the full suite locally (about 5 minutes on WSL) when the change
  reaches `PdfApp` startup, `tests/conftest.py`, or `klarpdf/model/` and `klarpdf/util/`. Send the
  output to a file and read only the end. Do not hand a test run to a separate agent; for many
  failures, use `pytest -rf --tb=line`. Why: `docs/why-the-rules.md` §Tests before a push.

- **Run `invoke lint` before a push, and give every suppression a reason** (M167, M172, M173). It
  runs the pinned Ruff, then mypy as Windows and as Linux, like the required `lint` and `typecheck`
  checks. Every function says what it takes and returns. Use the core's type names: `TextMarkup`,
  `AdditiveMark`, `Mark` and `PageEdit` in `klarpdf/model/page_edits.py`, and `Box`, `Point`,
  `Color` and `Word` in `klarpdf/model/tuples.py`. A `tuple`, `dict` or `list` says what it holds.
  Suppress one line and say why: `# noqa: BLE001 — <reason>` or `# type: ignore[code]  # <reason>`.
  `invoke lint --fix` makes the safe fixes; long lines, one-letter names and `%` formatting are fixed
  by hand. Details, including `Any`, bridge replies and Qt imports: `docs/why-the-rules.md` §Ruff and mypy. Which
  rules are on, and why: `PLAN.md` §*Linting, dead code and type checking*.
- **Shipped code that nothing in shipped code uses is dead, and a test says so** (M169, #409).
  `tests/test_no_dead_code.py` runs vulture over the shipped files git tracks, with the rest of the
  suite. When it names your code, delete the code, or have its tests use the path the app runs:
  a test of a helper the app never calls checks nothing a user does. Whitelist it in
  `tests/dead_code_whitelist.py`, with the reason, only when something vulture cannot see calls it
  (Qt by name, the MCP SDK), or when it is a read-only accessor the tests observe state through.
  An MCP tool needs no entry, and neither does a field of a `TypedDict`: code reads it by its key,
  which vulture cannot see, so the test skips it (M174.2). A deletion often frees something else:
  run the test again.
- **Compare, don't guess.** Before adding a rule that decides something about a document, ask
  whether it compares two things the page actually shows and answers yes or no, or guesses from what
  documents usually look like — a threshold, a tuned distance, a shape filter. Guesses get beaten by
  the next document, so prefer a check that needs no tuned number; and when a check grows into a
  repair, keep the check. This is measured, not taste: M141's first attempt (PR #348) carried ~22
  tuned numbers, six of them beaten outright by real documents, and at least three of its five
  repairs that *rebuilt* what a cell should say were beaten too — while its two checks that compared
  the page with the output never were (`PLAN.md` §M141).
- **Fix a class of bug once, then question the approach.** A second report of the same kind of
  defect is evidence against the approach, not a new case to patch — #348 patched left-clipped labels
  four times before anyone counted, and six in all. After a fix, check what it newly touches, not
  only the case that prompted it, and compare every run against **fixed expectations you have
  verified**, not only against the previous run: the M141 rewrite lost a label column in one round,
  and six rounds of run-to-run diffs did not notice. Keep measured numbers out of user-facing
  documentation unless a test re-checks them — `klarpdf://docs/get_tables` once said a reader
  returned tables "with no damage of any kind", true when written and false eight rounds later.
- **Choose new test documents for the least-tested part of a feature.** A corpus that grows by adding
  whatever broke last keeps finding shapes it has already seen: nine rounds of ruled financial
  statements found ~20 defects in #348, while one round on a chart-heavy statistics annual found two
  serious ones nobody had looked for.
- **Check what the document holds before concluding anything about the code.** Before calling a
  result a defect — or a success — compare it with the page: `extract_text`, `search`, `render_page`.
  It costs one call; in M141's test rounds it withdrew three findings that were the document's doing,
  and the same comparison caught both regressions the first attempt shipped.

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
- **MuPDF writes a new `/ID` on every save, and an RC4 or AES-128 key is made from the old one.**
  A file encrypted with no `/ID` was keyed on the empty string. The spec requires one, but some
  producers leave it out. `PDF_ENCRYPT_KEEP` keeps that key while MuPDF writes a fresh `/ID`, so
  the output opens for nobody, and `fresh_source`'s round trip raises *document closed or
  encrypted*. Every fixture PyMuPDF writes has an `/ID`, so the suite cannot see this unless it
  builds the shape: `write_idless_pdf` in `tests/conftest.py`. `pin_file_id` is the fix (M161,
  [#422](https://github.com/utyagi24/klarpdf/issues/422)), and any new path that opens an
  encrypted file and writes it with `KEEP` needs it too.
- **An incremental save is refused for exactly four reasons** (PyMuPDF 1.27.2.3, M116): any
  `garbage` level; a stream-opened document or a save to another path; a repaired file
  (`doc.is_repaired`); and a change of encryption. The default `PDF_ENCRYPT_NONE` counts as a
  change, so pass `PDF_ENCRYPT_KEEP` even for an unencrypted file. `Document.stream` returns the
  exact bytes a stream-opened document came from (`VirtualDocument.origin_bytes()`). An incremental
  save with nothing changed writes 0 bytes. Details: `docs/why-the-rules.md` §Incremental save.
- **PyMuPDF grows a `Square`/`Circle` annotation's `/Rect` by exactly 1.0 pt per side, at any border
  width** (1.27.2.3; M120, [#292](https://github.com/utyagi24/klarpdf/issues/292)).
  `parse_annotation` undoes it with `_SHAPE_RECT_GROWTH`. `FreeText` grows by `border_width / 2`,
  and the two must stay different; a test pins each against the library. When a read-back undoes
  something the writer did, measure what the writer did, and test at more than the default value.
  Details: `docs/why-the-rules.md` §Shape growth.
- **Text extraction reports a letter whose ink the clip cuts off.** `get_text` lists a character
  if any of it is inside the drawing, so a test that compares extracted letters passes while the
  bottom of a `g` or the end of a word is gone in every viewer (M166). To ask whether text is
  clipped, read each character's ink with `flags=fitz.TEXT_ACCURATE_BBOXES` and compare it with
  the clip, on text that has letters reaching below the line.
- **mypy cannot see PyMuPDF's `PDF_` constants, so `klarpdf/` reads them from one file.** PyMuPDF
  copies names such as `PDF_ANNOT_HIGHLIGHT` in with a loop when it is imported, so they exist
  when the code runs and are missing from its types. In `klarpdf/`, import them from
  `klarpdf/model/pymupdf_constants.py`, and add a line there for a new one. `fitz.PDF_…` fails
  the `typecheck` check there and in the app (M173). Only the tests, which are not type-checked,
  still write `fitz.PDF_…`. PyMuPDF's types have one more gap: a `Rect` and a `Document` can be
  looped over, and mypy says they cannot. Read a rect's four numbers by name and use
  `doc.pages()` (`PLAN.md` §M172).
- **A method or property named like one of Qt's own hides it.** The page view's `scale`
  property hid `QGraphicsView.scale()`, and two style controls' `style()` hid `QWidget.style()`,
  until M173 renamed them. Nothing called Qt's versions on those objects, so nothing broke, but
  the next call would have. mypy reports it as `[override]`: pick another name rather than
  suppress it. Check the new name with the dead-code test too. vulture matches names only, so a
  name another class already uses counts as used, and the first new name for `style()` hid an
  unused method that way (`PLAN.md` §M173).
- **An annotation crashes once its page is gone.** `doc[0].first_annot` lets the page be collected,
  and an annotation only holds a weak reference to it. Then `.rect` is a Windows access violation
  that takes down the whole test run, and `.parent` is a `ReferenceError`. Keep the page in a
  variable: `page = doc[0]`, then `page.first_annot`. Both happened in M163–M166's scripts and fixtures.
- **PyMuPDF's FreeText drawing is a starting point, not a limit** (M164, #427). It puts the text at
  the top of the box, so an outline moves the text. Move the text in the file instead: everything
  after the drawing's one `cm` line is the text, so rewriting that line moves it alone
  (`_place_textbox_text`). Write to the objects last, because `annot.update()` and `set_info` redraw
  it; `/RD` does not help. A box read back keeps where its drawing put the text (`TextBox.origin`).
  Test a tiny shift by its number, not only by pixels. Details: `docs/why-the-rules.md` §FreeText drawing.
- **`Page.get_pixmap` reads the whole page on every call, clip or not.** It builds a display list
  internally each time, so a small clip saves the drawing but not the reading. On the NADA cover a
  16 px piece took 44–64 ms, nearly all of it reading; from a kept `page.get_displaylist()` it took
  3–4 ms (M152.2). Two consequences: draw repeated pieces of one page from a kept display list, and
  a test that counts drawings by patching `Page.get_pixmap` and `DisplayList.get_pixmap` sees every
  whole-page drawing twice unless it ignores the inner call. The whole-page measurement that
  rejected display lists in the M152 plan was right for whole pages and wrong for pieces: measure
  the operation you will actually repeat.
- **A page picture's width in pixels is not its size on screen, and the tests cannot tell.** Every
  page picture carries the screen's pixel ratio (M88.2): on the owner's 1.75× laptop, a picture
  1,000 pixels wide covers 571 units of the scene. Size or place an item from
  `pixmap.deviceIndependentSize()`, never from `pixmap.width()`. The offscreen suite runs at one
  pixel to a point, where the two are equal, so the mistake passes every test that does not set
  `view._dpr`. M152.2's PR had one until the owner's hand check: on the laptop, a picture kept
  across a zoom showed at 57% of its size (432 px wide instead of 756). A test that checks where a
  picture lands should also run at another ratio: set `view._dpr`, or use `_fake_dpr` in
  `tests/test_dpi_scale.py`.
- **A green Windows + WSL suite does not mean CI is green — and Qt failures are *segfaults*, not
  assertion failures.** M88.3 crashed the Ubuntu runner inside `QGraphicsView`'s constructor ~74%
  into the suite while both local platforms ran it green, because the fault was in Qt's C++ and
  surfaced far from its cause. When CI fails and the local suite passes, read the log for
  `Fatal Python error: Segmentation fault` before assuming a flaky test.
- **Find what caused a CI segfault before fixing it.** M88.3 was a lifetime bug. M89 looked the same
  and was the suite running out of memory, with about 107,000 leaked widgets. If the reported test
  moves when you change something unrelated, it is resource exhaustion. Split the change across
  `workflow_dispatch` probe branches (`gh workflow run test.yml --ref <branch>`) and print memory,
  file and widget counts from a `conftest` hook. `tests/test_no_widget_leak.py` pins the invariant.
  Details: `docs/why-the-rules.md` §Attribute a segfault.
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
  `tests/test_project_rules.py` fails on a `.connect` to a `windowHandle()` signal in shipped code
  (M168).
- **A key routed "through the view" never arrives if a child widget accepted it, and
  `QAbstractItemView` accepts `Space`** (M91.4). Call `event.ignore()` in the panel and handle the key
  in `MainWindow.keyPressEvent`. Never use a `QAction` shortcut for this: it fires before the focused
  widget and steals the key from the inline editors. A `QLineEdit` with a validator swallows keys
  too. When a key does nothing, find out who accepted it. Details: `docs/why-the-rules.md` §Keys a child accepted.
- **When a key looks broken, suspect a mouse wheel that is still coasting** (M78, M91.4). A
  flywheel mouse and Windows' smooth scrolling send wheel events for seconds, and they undo what a
  key just did. The tells: it depends on how fast the wheel was spun, and the number of dead presses
  grows with it. To reproduce it, replay the wheel with timestamps (`QWheelEvent.setTimestamp`).
  Details: `docs/why-the-rules.md` §A coasting wheel.
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
- **A menu item passes a flag to the method it calls.** `QAction.triggered` sends `checked`, a
  `bool`, and PySide6 hands it to any method that can take an argument. So connecting
  `self._paste_pages` directly calls `_paste_pages(False)`, and `False` counts as 0: Paste Pages
  would paste before page 1 every time. Keep the `lambda:` that drops the flag. Ruff's `PLW0108`
  calls such a lambda needless and suggests exactly this change, so each one carries a `noqa` that
  says why (M170).
- **On WSLg, a Qt window that is hidden rather than closed stays on the desktop** (#388, #396,
  M153). `ui/popup_closer.py` closes any popup Qt only hid. Close the app's own windows rather than
  hide them. Run with `WAYLAND_DEBUG=1` to see what Qt sends. Check popups by hand: a script cannot
  open one on WSLg. `platform_integration.py` drops one harmless Wayland line
  (`HARMLESS_WAYLAND_LINES`). Details: `docs/why-the-rules.md` §WSLg popups and `docs/milestones/M153.md`.
- **Never rebuild the scene inside a Qt callback.** `scene.clear()` during `showEvent` /
  `paintEvent` / an event handler destroys every `QGraphicsItem` while Qt is still walking them.
  Defer to the event loop with a `QTimer` **parented to the view** (it is then cancelled on
  destruction, and a burst of events collapses into one pass).
- **A drag keeps an item in the scene, and `_build_scene` ends every drag before it clears it.** A
  new kind of drag needs its item dropped in `cancel_drags` (or `_end_drags`), or a zoom or an undo
  mid-drag raises on a dead object at every mouse move. It does not crash, it only prints `Internal
  C++ object … already deleted` to a console, and the drag is lost. So it hides: the editor's delayed
  commit did this from M27 until the owner saw the console in M165. To test anything that depends on
  focus, use `QTest.mousePress` on the viewport. Calling `view.mousePressEvent` directly skips the
  focus change, which is the step this defect needed.
- **An MCP tool description over 2,048 characters is cut off in transit, and nothing errors.**
  Claude Code truncates it, and the server's `instructions` too. Keep each description within
  1,900 characters, and put the rest in `klarpdf://docs/{tool}` (capped at 100,000). What the
  caller needs before calling stays; what they need to read the reply moves.
  `tests/test_mcp_docs.py` enforces this on the live server. When it fails, move text to the
  resource; do not raise the budget. The SDK sends `__doc__` verbatim, so `guarded` runs `cleandoc`.
  Details: `docs/why-the-rules.md` §MCP descriptions, `PLAN.md` §Architecture and `docs/milestones/M105.md`.
- **PyMuPDF's `find_tables` locates tables; do not let it read them** (M141, M148). On a page with
  `/Rotate` it measures the page as displayed, while `get_text` reports it unrotated: convert at
  the boundary. It keeps state in module-level lists and turns on the process-wide
  `set_small_glyph_heights`, so `get_tables` holds a lock. Build cells from the ordinary word
  extraction, not from its text page or `Table.extract()`. Its vertical edges built from text run
  the full height of the page's text, so a region's top and bottom lines are checked against the
  table. Details: `docs/why-the-rules.md` §find_tables.
- **A PDF can carry its text twice, with only one copy visible.** Some converters add an invisible
  copy of every line (render mode 3), and a scan's OCR layer is the same. `get_text()` returns both.
  The tell is `span["char_flags"] & (FZ_STEXT_FILLED | FZ_STEXT_STROKED) == 0` (and `alpha == 0`);
  `get_texttrace()` reports `type == 3`. `get_heading_candidates` skips such spans. `get_tables`
  does not yet ([#352](https://github.com/utyagi24/klarpdf/issues/352)), and `extract_text` returns
  both copies. Details: `docs/why-the-rules.md` §Text twice.
- **A cloud session commits as `Claude <noreply@anthropic.com>` unless told otherwise, and the
  `emails` check rejects it** (G7). **Before the first commit, run `git var GIT_AUTHOR_IDENT`.** If
  it says `anthropic.com`, stop and ask rather than commit. The fix is in the environment's
  settings: `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL`.
  Re-authoring pushed commits needs the owner's go-ahead. Details: `docs/why-the-rules.md` §Cloud commit identity.
- **Do not move a directory that holds a lock file outside the repo root without a strong reason.**
  GitHub's dependency graph keeps the old path forever, frozen at its old pins, and only GitHub
  Support can remove it. M133's move left 13 alerts that no commit can close. If such a directory
  moves, write to GitHub Support in the same PR and note the old path in `RELEASE.md` §2. Files at
  the repo root are safe. Details: `docs/why-the-rules.md` §Lock file moves.
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
  That is the **app's build** requirement and nothing else. The MCP bridge is `pip`-installed rather
  than frozen, so `requires-python` genuinely gates it, and since M132 it is `>=3.11,<3.15` — do not
  "fix" a bridge file back to 3.12 for consistency with this line.
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements-win.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `klarpdf/util/paths.py:normalize_path()` is the single identity chokepoint.
  Ruff enforces it (M168): `sys.platform`, `os.name`, `platform.system`, `os.startfile`, `winreg`
  and `ctypes`'s Windows names are banned everywhere except `platform_integration.py`,
  `packaging/`, `tasks.py`, `tools/` and `tests/` (`[tool.ruff.lint.flake8-tidy-imports]` in
  `pyproject.toml`).
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status

**Current: v0.21.0 shipped.** 1.0 is deliberately not taken yet; the gate is in `PROGRESS.md`
§Toward 1.0. What each release brought: the top of `PROGRESS.md` for the current release,
`docs/releases.md` and GitHub Releases for earlier ones. Design and what comes next: `PLAN.md`.
The Status text this replaced: `docs/why-the-rules.md` §The old Status section.
