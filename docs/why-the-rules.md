# Why the rules are what they are

`CLAUDE.md` keeps each rule short. This file keeps the full text each long rule had on
2026-11-13: the incident behind it, the measurements and the reasoning. Read a section when
a rule's short form does not settle a question. The rule in `CLAUDE.md` is the one in force.

## README

- **`README.md` is the one sanctioned exception — and it must be updated on every release.** It is the
  shop window for the public repo, so it *does* restate the shipped version, a one-line what's-new
  for the **current release only** (history lives in GitHub Releases / `docs/releases.md`), and a
  **Features** inventory; a visitor won't go read `PROGRESS.md`. That restatement is exactly what
  rots: README sat on `v0.9.4` through both v0.9.5 and v0.9.6. So the release checklist
  (`RELEASE.md` §3 step 2) names `README.md` alongside `PROGRESS.md` and `CLAUDE.md`, and the
  version bump and the three status lines land **together** with the release PR. Everything deeper
  stays a link.
  **But the Features inventory is not a status line, and it rides the change, not the release.** A
  version can only be written once it exists; "what the app does" is true the moment the code lands,
  and parking it until some future release PR is precisely the rot this bullet is about — someone
  has to remember, and the README sat on `v0.9.4` through two releases because nobody did. So: a PR
  that adds or changes a user-facing behaviour updates the Features list *in that PR* (M93 did, for
  document fidelity and permission carry-through); only the version, the what's-new line and the
  release links wait for the tag.

## uv first

- **User-facing install examples lead with `uv`; `pipx` is the named equivalent, never the lead.**
  Decided 2026-09-07, after `QUICKSTART.md` opened with `pipx install klarpdf` and then used
  `uv tool update-shell` for the very next step — two tools in five lines, with no statement that
  either was preferred. The choice is not fashion, it is two project-specific facts. **`uv` is
  already mandatory for one of the four install paths**: the `.mcpb` Claude Desktop bundle is
  launched with `uv run` (`packaging/mcp/mcpb/manifest.json`), so leading with `pipx` tells part of
  the audience to install two tool managers. And **`uv` removes a prerequisite rather than adding
  one**: the bridge needs Python 3.11–3.14, `pipx` is itself a Python application and so
  presupposes a suitable one, while `uv` is a single binary that downloads a Python when none fits
  (`uv tool install` does it by default — `--no-python-downloads` exists to turn it off).
  `pipx` stays **fully documented and equally supported**, not demoted to a footnote: it works for
  every path but the Desktop bundle, it is PyPA-governed and distro-packaged, and a reader who
  already has it should not be pushed to install a second tool. The rule is about *order and
  consistency*, so it is mechanical: in a code block, `uv` is the line and `pipx` is a trailing
  `# pipx: <equivalent>` comment; in prose and tables, `uv` is named first. Applies to
  `README.md`, `klarpdf/mcp_bridge/README.md`, `klarpdf/mcp_bridge/QUICKSTART.md` and any text
  `install.py` prints. `PLAN.md`, `PROGRESS.md` and the milestone files are records, not instructions — do not rewrite their
  history to match.

## Issues and follow-ups

- **A reproducible defect in shipped code goes to GitHub Issues; anything still open to
  interpretation stays in `PROGRESS.md`** (owner, 2026-08-25). These are not two backlogs competing
  for the same items — they hold different *kinds* of item, and the test is what the next person
  has to do before they can start. **File an issue** when it is a known defect in the released build
  or in the code on `main`, unambiguous and readily reproducible: someone can follow the steps, see
  it happen, and know when it is fixed. ([#288](https://github.com/utyagi24/klarpdf/issues/288) —
  the Pages sidebar scrolling the newly inserted page out of view — is the first.) **Keep it in
  `PROGRESS.md` §Open follow-ups** when it still needs a *decision* before anyone can work it: a
  deferred design question, a rejection and its reason, a trade-off measured but not settled. The
  Flattened-PDF export dropping encryption is exactly the second kind — reproducible, but "carry
  through / warn / offer the choice" is unanswered, so filing it as a bug would assert a verdict
  nobody has reached.
  Three consequences. **The issue is the tracker, not a copy** — an item that becomes an issue does
  not also get a follow-up bullet, or the drift the *where things live* rule exists to prevent moves
  into a new pair of files. **A follow-up that gets decided graduates** into an issue or a milestone
  and leaves a one-line pointer behind, the way TC-007's items graduated into M99–M101. And **the
  fix still owes its milestone file** under the rules above: the issue
  records the *report* and the PR closes it with `Fixes #N`, but where the defect came from and what
  the fix changed are design and status, and they live where design and status live. **A fix built
  in parts closes its issue with its last part**: that PR says `Fixes #N`, and the earlier ones say
  `Part of #N`. M152 said `Part of #360` in both of its PRs, so their merge left #360 open.

  **Every issue is labelled with its type and the part it lives in** (owner, 2026-09-18). It gets
  `bug` or `enhancement`, plus each of these that applies:

  * `app`: seen in the desktop app (its code: `app.py`, `main_window.py`, `edit_commands.py`,
    `launcher.py`, `platform_integration.py`, `viewer/`, `organize/`, `ui/`, `store/`);
  * `mcp-bridge`: seen through the bridge's tools (`klarpdf/mcp_bridge/`);
  * `core`: the fix belongs in `klarpdf/model/` or `klarpdf/util/`.

  `core` is the one to get right, because it is what tells whoever picks the issue up that the
  change reaches both surfaces and owes tests on both (*Two consumers share one core*, below). Where
  an issue *shows* is not always where its fix goes. #361 is a bridge feature that writes through
  the core's `set_outline_override`, so it carries `mcp-bridge` and `core`. Check the code before
  labelling, as for any other claim, and relabel when the fix turns out to go elsewhere.

## Investigate once

- **Investigate a defect once, when it is fixed** (owner, 2026-09-19). Filing an issue, planning a
  milestone for it and building the fix each used to dig into the same defect, so one cause was
  worked out three times over. The M149–M152 planning session re-measured issues that had been
  measured when they were filed, and would have been measured again when built. Each step now does
  only its own job:

  * **Filing an issue** records the report: what the reader sees, the steps and the document that
    reproduce it, the environment, and what was expected. If the work that found it already knows
    the cause, say so in a line. Do not go looking for it. Label from what is known then; the
    session that fixes it relabels if the fix goes elsewhere.
  * **Planning** groups issues from what their tickets already say: which share a theme, the order,
    the surfaces. It runs no new measurements and writes no design.
  * **Building the fix** is where the diagnosis happens: the root cause, the measurements, the
    design, the questions for the owner, and the milestone file.

  Each step starts in a fresh session, so a diagnosis done early gets done again anyway.

## Milestone numbers

- **Every non-trivial change gets a milestone file** (`docs/milestones/<id>.md`) —
  in the *same* PR as the code, not afterwards. "Non-trivial" is anything that changes how the app
  behaves or how it is built: a new route through the save path, a contract change, a defect whose
  cause is worth knowing. A one-line typo fix is not; a fix that changes what a Save *writes* is.
  The milestone gets the next free number and `*(unplanned)*` when it was not on the roadmap (see
  M43.1, M93). **"Next free" means free across the open PRs too, not just `main`** — milestone
  numbers are claimed the moment a PR defines one, and two branches that both read `main` will both
  pick the same next number and collide at merge. This is not hypothetical: M143 was written as M138
  on 2026-09-09 and had to be renumbered, because an open PR had already taken M138–M142 (three
  implemented, two as planned roadmap entries — **a reserved number counts as taken**). So before
  choosing, check what is claimed:
  `for n in $(gh pr list --state open --json number -q '.[].number'); do gh pr diff $n | grep -E '^\+' | grep -oE '\bM[0-9]{2,3}\b'; done | sort -u`.
  Worth doing even when no PR looks related — the collision is with the *number*, not the subject. This is the rule that keeps the design docs from becoming a description of the app as
  it was first imagined rather than as it is.

## Two consumers

- **Two consumers share one core — every change answers for both.** `klarpdf/model/` and
  `klarpdf/util/` are reached by the **GUI app** (`app.py`, `main_window.py`) and by the **MCP
  bridge** (`klarpdf/mcp_bridge/`), so a change to the core is a change to *both* whether or not
  the session was thinking about both. The failure is silent and
  runs in either direction: a fix aimed at the app changes what a bridge tool writes, or a bridge
  feature changes what Save does. So every behaviour change, design entry and new feature names the
  surfaces it touches, and a core behaviour change wants a test on **both** sides
  (`tests/test_mcp_*.py` for the bridge) rather than only the one the session was holding.

  **What is not core.** The app's own code lives outside `klarpdf/` (`viewer/`, `organize/`, `ui/`,
  `store/` and the top-level modules). It is not in the wheel, the bridge imports none of it, and a
  fix to it is `app`, not `core`. So the folder a file goes in decides it: code only the app uses
  goes outside `klarpdf/`, and when the bridge needs something the app keeps, it moves into
  `klarpdf/model/` first, as the markup palette did in M101. `tests/test_mcp_no_qt.py` fails if the
  bridge loads the app's code, or if a module in `klarpdf/model/` or `klarpdf/util/` is one it never
  uses (`PLAN.md` §M147).

  It distorts *documents* as much as code: a fact stated from whichever surface is in hand gets filed
  as a fact about the core. M114's entry called *"the output goes to a new path"* an obstacle — true
  of the bridge always and of the GUI only on `Save As`, and misleading either way, because **no**
  surface writes to the original file (both `MainWindow._write_to` and `klarpdf/mcp_bridge/transforms.py`
  `_write` materialise into a temp beside the target and `atomic_replace` it in — deliberately the
  same shape, M38.5). Stated per-surface it looked like an obstacle; stated generally it pointed at
  the fix. When a claim says "we write / we open / we refuse", check it at every surface — the
  chokepoints are `PyMuPDFEngine.materialize`, `MainWindow._write_to`, and `transforms._write`.

  This is the **consumer** axis. The **OS** axis — Windows ships, Linux is a seam — is a separate
  question with its own rule (**Keep OS-specific code quarantined**, under Gotchas); WSL is a
  development environment, not a third product surface.

## Verify the checks

- **The thing that verifies the code needs verifying too — break it and watch it fail.** This is
  already the standard for product code: `test_packaging_layout.py`, `sync_pins.py --check` and
  `install.py`'s startup check were each confirmed by reverting the fix and reading the failure. The
  gap is that **test fixtures, CI jobs and runbook steps were held to "it passed"**, which for those
  three proves nothing — a job can pass because it tests the wrong thing, and a runbook line can be
  wrong until the day someone follows it. M133–M136 produced four failures of exactly this kind, all
  caught by CI rather than locally, none of which reached a user but each of which cost a cycle:

  - **A CI job is not done until it has gone red on purpose.** The `installer` job's macOS leg
    existed to prove `install.py` works at `~/Library/Application Support/…`, whose **space** makes
    `venv` write a `/bin/sh` exec shebang — and it passed `--install-dir` to a space-free path, so it
    tested everything except its reason for existing. Break the subject, confirm the job fails, then
    keep the job.
  - **No CI job may depend on a published artifact of the version under development.** That leg
    installed `klarpdf==<baked version>` from an index, which works only while that version is
    already published — so it could only ever test the *previous* release, and broke on the v0.19.0
    release PR with `No matching distribution found`. Build the artifact in the job; do not fetch it.
  - **A test that executes a fixture is POSIX-only until proven otherwise.** Two `tests/test_installer.py`
    cases wrote `#!/bin/sh` stand-ins and `chmod`'d them; Windows `CreateProcess` cannot run one
    (`WinError 193`). The local suite is Linux, so it passed. This repo ships on Windows and runs a
    `windows` job for that reason — a shebang or a `chmod` in a test is a signal to check it.
  - **A runbook step is not written until it has been executed once.** `RELEASE.md` §3 step 1 said
    `build_mcpb.py --validate` regenerates `manifest.json`. It does not — that version is set inside
    `stage()`, which writes the staged copy. The line was wrong from the day it was written and was
    found by the first person to follow it, mid-release.

  **The common shape, and why it is worth a rule rather than four fixes:** local runs cover one OS at
  one point in time, and every one of these lived in a context that excludes — a different platform, a
  different point in the release cycle, a document nobody had run. So a green local suite is grounds
  to **push**, never grounds to believe a change is correct. That is the same statement as
  §Gotchas' *"a green Windows + WSL suite does not mean CI is green"*, one level up: it applies to
  the tests as much as to the code.

## Tests before a push

- **Run the tests for your change locally, and let CI run the full suite** (owner, 2026-09-25).
  Before pushing, run the test files that cover the code you changed. Check that each new test
  fails when the fix is broken on purpose, as the bullet above asks. Both take seconds, and the
  second can only be done locally. Then push. CI runs the full suite on Linux (about 7 minutes) and
  on Windows (about 9 minutes). Wait for it once in the background, not by polling, and report its
  result.

  Run the full suite locally too when a change reaches code that every test goes through: app
  startup (`PdfApp`), `tests/conftest.py`, or the core in `klarpdf/model/` and `klarpdf/util/`.
  There a CI failure costs a round trip of 10 minutes or more, and a local run finds it sooner. An
  event filter on the whole app is one of these, because every test's events pass through it. A
  filter that is off in the tests is not.

  **Why:** the full suite takes about 5 minutes on WSL, and it repeats CI's Linux job. WSL cannot
  run the Windows one. A local run costs few tokens when its output goes to a file and only the
  last lines are read, so skipping it saves time, not context.

  **Do not hand a test run to a separate agent.** It starts with none of the session's context, so
  telling it what to run and reading its report cost more than the run itself. The exception is a
  run with many failures that need digging through. Even then, `pytest -rf --tb=line`, one line per
  failure, is usually cheaper.

## Ruff and mypy

- **Run Ruff and mypy before a push, and give every suppression a reason** (M167, M172, M173).
  `invoke lint` runs the pinned Ruff, with the same version and rules as the required `lint`
  check. It then runs `invoke typecheck`, as the `typecheck` check does: the pinned mypy over
  `klarpdf/` and the app, twice, as Windows and as Linux. A few lines of the app run on one
  platform only, and mypy skips the other's. Ruff takes under a second. Each mypy run takes about
  5 seconds the first time and well under one after that. Every function says what it takes and
  what it returns, and mypy refuses one that does not (M172.2, M173.2). Use the names the core has
  for the kinds of mark (`TextMarkup`, `AdditiveMark`, `Mark` in `klarpdf/model/page_edits.py`,
  and `PageEdit` for anything a page's marks list holds), and for a box, a point, a colour and a
  word (`Box`, `Point`, `Color`, `Word` in `klarpdf/model/tuples.py`). Use `object` only where a
  function is written to take anything. A `tuple`, `dict` or `list` in a type says what it holds,
  and mypy refuses one that does not (M174). `Any` is for PyMuPDF's own
  outline rows and link dicts, whose items change type, under the names `TocEntry` and `LinkDict`.
  A bridge reply names every field, in `klarpdf/mcp_bridge/replies.py`. A tool still says it returns
  `dict[str, Any]` and hands its reply over through `_sent`, because the MCP library builds each
  tool's reply schema from that type and drops it for a name it cannot resolve (M174.2). What an
  agent sends is `object` until the code checks it, and so is what the app's settings file holds,
  which a person can edit (M174.3). A module that loads no Qt
  (`viewer/links.py`, `viewer/pixmap_cache.py`) imports its Qt names for type checking only,
  under `if TYPE_CHECKING:`. `tests/test_mcp_no_qt.py` fails if `viewer/links.py` loads Qt.
  `invoke lint --fix` also sorts imports and makes Ruff's other safe fixes. A line over 120
  characters, a one-letter name such as `l`, and `%` formatting are fixed by hand (M171). There
  is no formatter, so Ruff checks the layout but does not rewrite it. A rule is switched on only
  once nothing breaks it, in the PR that clears its findings, so
  a red `lint` is always the PR's own doing. When a finding is deliberate, suppress that one line and
  say why, in the form the core already uses:
  `# noqa: BLE001 — a malformed /AP is not worth failing a whole listing over`, or
  `# type: ignore[code]  # why` (mypy needs the second `#`). A bare `# noqa` is a decision nobody
  can review, and 10 of the 23 in the code on 2026-11-08 suppressed nothing at all. Since M168, Ruff
  refuses a suppression with no code or one that suppresses nothing, and
  `tests/test_project_rules.py` refuses one with no reason. Ruff refuses to run at any version but the pinned one, so an
  editor's bundled copy reports the mismatch instead of a different answer. Point VS Code's Ruff
  extension at the venv with `"ruff.importStrategy": "fromEnvironment"`. A Ruff bump follows
  `RELEASE.md` §1. Which rules are on, where, and why: `PLAN.md` §*Linting, dead code and type
  checking*.

## Incremental save

- **An incremental save is refused for exactly four reasons, and PyMuPDF's own default is one of
  them.** M116 appends to the file a document was opened from instead of rewriting it. Measured on
  1.27.2.3, `save(incremental=True)` raises for: any `garbage` level; a **stream-opened** document or
  a save to a second path (`ValueError: incremental needs original file`); a **repaired** file
  (`doc.is_repaired` — MuPDF will not chain onto offsets it had to guess); and *changing encryption*
  — which includes **`encryption=PDF_ENCRYPT_NONE`, the default**, on a plain unprotected PDF, so
  `PDF_ENCRYPT_KEEP` has to be passed explicitly for a file with no encryption at all. Two more
  facts that shape the code: `Document.stream` hands back the **exact bytes** a stream-opened
  document was opened from (the same object — keeping it costs nothing) where `tobytes()`
  re-serialises, which is why `VirtualDocument.origin_bytes()` exists; and an incremental save with
  nothing dirty writes **0 bytes**, so a save of an unedited document is now a byte-identical copy.

## Shape growth

- **PyMuPDF grows a `Square`/`Circle` annotation's `/Rect` by exactly 1.0 pt per side, whatever the
  border width** — not by `width / 2`, which is what a round trip that has to undo the growth would
  reasonably assume, and what `parse_annotation` did assume until M120. Measured on 1.27.2.3 across
  widths **0.25–20.0**, `add_rect_annot` and `add_circle_annot` alike. The two agree at exactly
  **2.0**, which is `Shape.width`'s default and the reason this went unseen for so long: at any
  other width a shape changed size on every save→reopen→save — 2 pt a side per save at 6 pt wide —
  and it was silent ([#292](https://github.com/utyagi24/klarpdf/issues/292), found by M117's per-kind
  comparison of a left-in-place mark against a redrawn one; **fixed in M120**, where the inset is now
  the measured constant `_SHAPE_RECT_GROWTH`). The lesson generalises past shapes: **when a read-back
  has to undo something the writer did, measure what the writer actually did** rather than deriving
  it — and test the round trip at more than the default, since the default is the value most likely
  to be the one that happens to work. `FreeText` does track `border_width / 2`, so the two paths in
  the same module disagree **and must stay that way**; a test pins each against the library, so a
  PyMuPDF upgrade that changes either fails there with the reason rather than silently resizing
  documents.

## FreeText drawing

- **PyMuPDF's FreeText drawing is a starting point, not a limit.** Its simple appearance has no
  vertical alignment: it puts the text at the top of the box, padded by 1.5 × the border, so an
  outline moves the text. #427 was first fixed by moving the view to match, which brought back the
  top-heavy boxes and the owner rejected it. The file can follow the view instead: everything after
  the drawing's one `cm` line is the text, so rewriting that line moves it alone
  (`_place_textbox_text`, `PLAN.md` §M164). **Write straight to the objects, last**:
  `annot.update()` redraws it, and `set_info` marks it for a redraw. `/RD` is no way round this,
  because MuPDF folds it into the `/Rect` on update. A box read back keeps where its drawing put
  the text (`TextBox.origin`), so nothing already saved moves. A test of a tiny shift should check
  the number, not only pixels: a 0.005 pt move changed 752 pixels on one page and none on another.

## Attribute a segfault

- **Two different bugs have now worn that same costume — so *attribute* the segfault, don't pattern-
  match it.** M88.3 was a genuine lifetime bug (a slot on a freed view). The M89 one looked identical
  — same crash, same constructor, ~74% in — but was **the suite exhausting the runner's memory**: it
  leaked every window it opened, reaching ~107,000 widgets and 8 GiB RSS. The tell that separates
  them: if the *reported test moves* when you change something unrelated, it is resource exhaustion,
  not a bug in the named test. The cheap way to find out is `workflow_dispatch` probe branches
  (`gh workflow run test.yml --ref <branch>`) that split the change into parts, plus a `conftest`
  hook printing RSS / fd / `QApplication.allWidgets()` growth — an answer in two CI runs instead of a
  guess. `tests/test_no_widget_leak.py` now pins the invariant.

## Keys a child accepted

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

## A coasting wheel

- **When a *key* looks broken, suspect the wheel that is still running.** A flywheel mouse and
  Windows' smooth scrolling keep emitting wheel events for seconds after the hand leaves them, and
  those events undo whatever a key or click just did. It hides well: scrolling up at offset 0 is a
  no-op, so the coast is invisible until a paging key gives it somewhere to go. The tells are
  **speed-dependence** ("100% if I spin fast, never if I scroll slowly") and a **count of dead
  presses that tracks how hard they spun**. This has now been diagnosed twice — M78 in the
  slideshow, M91.4 in ordinary reading, because the first fix was scoped inside `if self.slideshow`.
  A repro that fires keys with no wheel in flight cannot see it, so **replay the wheel with
  timestamps** (`QWheelEvent.setTimestamp`) when a report is intermittent.

## WSLg popups

- **On WSLg, a Qt window that is hidden rather than closed stays on the desktop.** On Wayland, Qt
  6.11 hides a window by removing its role and keeps its surface. WSLg keeps drawing that surface
  until it is destroyed. Qt hides some popups instead of closing them: a combo box's list after a
  choice, and a menu bar's menu when its title is clicked again or another title opens. Both stayed
  on the desktop until the app quit ([#388](https://github.com/utyagi24/klarpdf/issues/388),
  [#396](https://github.com/utyagi24/klarpdf/issues/396), M153). `ui/popup_closer.py`, which
  `PdfApp` installs, now closes any popup that Qt only hid. It does not touch other windows, so
  close a window of the app's own rather than hide it. To see what Qt sends the display server, run
  with `WAYLAND_DEBUG=1`. A script cannot open a popup on WSLg, because the display server needs a
  real click first, so check popups by hand. On Wayland, `platform_integration.py` drops Qt's line
  *"This plugin supports grabbing the mouse only for popup windows"*, which Qt's menu bar printed
  twice per second click on a menu title. It is harmless there. Take it off
  `HARMLESS_WAYLAND_LINES` to see it again (`PLAN.md` §M153).

## MCP descriptions

- **An MCP tool description over ~2 KB is cut in half in transit, and nothing errors.** Claude Code
  truncates a tool description at **2,048** characters (`yfe` in the client binary) and appends
  `… [truncated]`; the *same* constant truncates the server's `instructions` block, so that is not
  the uncapped escape hatch it looks like — ours fits only because it is under the cap, with 209
  characters to spare on the default build. The `--read-only` build is shorter, because it leaves
  out the advice on writing (M176). `redact_text` lost **69%** of its 6,573 characters
  this way, and it was the wrong 69%: three milestones of agent-facing documentation written into a
  channel that silently discards it, found by a tester saying "I cannot see this" rather than by any
  test. **So a description is a budget: 1,900 characters, and the overflow goes to
  `klarpdf://docs/{tool}` — never into a bigger description.** `tests/test_mcp_docs.py` enforces it
  over the *live* server, so a new tool is covered the day it is registered; when it fails, move
  reference material to the resource (capped at 100,000 chars) rather than raising the budget. Split
  by kind — what the caller needs *before* calling stays, what they need to read the *reply* moves.
  One trap underneath it: the SDK sends `fn.__doc__` **verbatim** (no `inspect.getdoc`), so a
  docstring's indentation is billed against the cap — ~1,800 chars across the tools — which is why
  `guarded` runs `cleandoc`. See `PLAN.md` §Architecture and §M105.

## find_tables

- **PyMuPDF's `find_tables` locates tables; do not let it read them.** Four habits, met in M141
  and M148. **On a page with `/Rotate` it measures the page as displayed**, while `get_text` reports the
  page unrotated — carry words into the displayed orientation before comparing, and convert boxes back
  at the boundary, since `search`, `clip` and `redact_regions` all work unrotated. **It keeps the page's
  characters and edges in module-level lists and turns the process-wide `set_small_glyph_heights` on
  while it runs**, and the MCP SDK runs tool calls concurrently in worker threads — so `get_tables`
  holds a lock, which cannot shield *other* tools from the glyph setting (`PROGRESS.md` §Open
  follow-ups). **Its own text page and `Table.extract()` both damage text**: the first breaks words on
  some documents (`'P'`, `'rcent'`), and the second puts each character in whichever cell its centre
  falls in, which cuts words at a column edge and once moved an address's underscore onto a line of
  its own. Cells are built from the ordinary word extraction instead (`PLAN.md` §M141). **Every
  vertical edge it builds from text runs the full height of the page's text**, so any short line
  two of them cross becomes a cell border: the underline of a link at the top of every page started
  Cisco's tables there, with the prose above them in their first row. A region's top and bottom
  lines are judged against the table before its outer bands are trusted (`PLAN.md` §M148).

## Text twice

- **A PDF can carry its text twice, with only one copy visible.** Some converters add a hidden copy
  of every line (render mode 3: no fill, no stroke) at slightly different sizes, and a scanner's OCR
  layer is the same thing with nothing visible above it. `get_text()` returns both, so reading code
  sees every line twice, in two fonts — on `SpaceX-EUProspectus.pdf` the copy's wandering sizes
  added 31 bogus styles to M140's reply, and code that groups text by position welds each line to
  its twin (an M140 prototype returned `1.1 1.1 Risks… Risks…`, and 98 of 100 headings stopped
  matching their titles). **The tell is
  `span["char_flags"] & (FZ_STEXT_FILLED | FZ_STEXT_STROKED) == 0`** (and `alpha == 0`);
  `get_texttrace()` reports `type == 3`. `get_heading_candidates` skips such spans; `get_tables`
  does not yet and declines every table on that file
  ([#352](https://github.com/utyagi24/klarpdf/issues/352)); `extract_text` returns both copies.

## Cloud commit identity

- **A cloud session commits as `Claude <noreply@anthropic.com>` unless told otherwise, and the
  `emails` check rejects it.** The container's `~/.gitconfig` sets that identity, and
  `.github/workflows/author-email-guard.yml` accepts only a GitHub no-reply address as author or
  committer (G7). The fix belongs in the environment, not the repo, because a repo file would sign
  collaborators' commits as the owner. So the environment's settings set `GIT_AUTHOR_NAME`,
  `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL`, which override every git
  config file. **Before the first commit, check `git var GIT_AUTHOR_IDENT`.** If it says
  `anthropic.com`, the variables are missing: stop and ask, rather than commit. Commits that were
  already pushed have to be re-authored and force-pushed, which needs the owner's go-ahead. PRs
  [#406](https://github.com/utyagi24/klarpdf/pull/406) and
  [#407](https://github.com/utyagi24/klarpdf/pull/407) both failed the check this way (2026-09-26).

## Lock file moves

- **Moving a directory that holds a lock file outside the repo root leaves a phantom behind in
  GitHub's dependency graph, and only GitHub Support can remove it.** Dependabot scans such a file
  (today, `packaging/mcp/mcpb/uv.lock`) under a snapshot key built from its directory, and GitHub
  keeps the newest snapshot per key. Move the directory and the old key is never refreshed, so the
  file is reported as present on `main` forever, frozen at its old pins. Every later advisory
  against those pins raises an alert that no commit can close: M133's move produced #31–#33, #35
  and #39–#47, one critical among them. The dependency-submission API cannot clear it, because the
  `dependabot` detector name is reserved (HTTP 422, measured 2026-09-30). So **do not move such a
  directory without a reason worth the tail**. If one does move, write to GitHub Support in the
  same PR, and note the old path in `RELEASE.md` §2 so its alerts are recognised and dismissed as
  *inaccurate*. Files at the repo root are safe: they share one key that every scan refreshes
  (`PROGRESS.md` §Open follow-ups).

## The old Status section

**Current: v0.21.0 shipped** — **files that open, text boxes that keep their look, and checks
that keep the code clean (M158–M169)**. An encrypted file with no `/ID` no longer saves into a copy
nobody can open (M161). Text boxes keep their look and all their words after Microsoft Edge moves or
shrinks them, and save where KlarPDF shows them (M163, M164, M166). A fit no longer hangs the window
by chasing its own scrollbar (M160), and a drag survives the editor's commit (M165). The bridge's
pinned `mcp`, `pyjwt` and `opentelemetry-api` move up, and refusals keep their text on `mcp` 2.3
(M158, M162). Underneath, Ruff is a required check, the project's rules are enforced, and a
dead-code test runs with the suite (M167–M169). All fixes, but a minor by the owner's call.
It supersedes **v0.20.0** — **structure an agent can use, and a window that keeps up
(M138–M157)**. The bridge grows from 19 tools to 23: `get_links`, `set_outline` (whose entries can
now land on their heading), `get_heading_candidates` and `get_tables`, which declines a table the
page contradicts rather than guessing. In the app, a slow page no longer freezes the window (M152,
the 1.0 gate's Item E), links and bookmarks land on the spot they name and survive a page move
(M150), and zoom and resize keep the page you are reading (M151). pypdf leaves the installer
(M156), and `pyjwt` (M157) and `httpx2` (M144) are bumped for advisories the bridge could not
reach.
It supersedes **v0.19.0** — **the bridge you can actually install (M133–M136)**. Installing it
was nine commands and the step that failed was the path; it is now `pipx install klarpdf`, or a
single downloaded **`install.py`** that needs nothing but a supported Python — no clone, no `uv`, no
`pipx`, no global `pip`. **`klarpdf` is on PyPI** with all 29 dependencies pinned exactly, so every
route resolves the audited set rather than whatever is newest; publishing is Trusted Publishing off
`release: published`, so **no API token exists anywhere** and the existing manual smoke test gates
PyPI too. Underneath: `packaging/` says which product each file builds (**M133**), and the wheel
installs **one** top-level name rather than four (**M134**) — `model`, `util` and a plain `version`
would otherwise collide with anyone else's. **The four milestones each found their defect by doing
rather than reading**: a directory move repoints every relative path computed from `__file__` and
none of them contains the string you grep for (M133); a grep that is *wrong* looks exactly like one
that is clean, so a negative result needs a positive control (M134); and a readme ships *inside* the
wheel, so it must be true before the upload, not after — the TestPyPI rehearsal caught the project
page advertising "the bridge is not published to PyPI" (M135). Also shipping: **M137**, the dev lock
is compiled off Windows, and **pypdf 6.15.0 → 6.17.0** clearing three parse-DoS advisories.
**1.0 is still deliberately not taken** — the gate is listed in `PROGRESS.md`.
It supersedes **v0.18.0** — **the MCP / Agent Bridge (M39–M44)**, the roadmap's last unticked box.
`klarpdf/mcp_bridge/` exposes the core engine to Claude Code, Claude Desktop and other agentic clients as a
local MCP server: **19 tools**, every write tool leaving its input byte-identical, no network, no Qt.
It is a **separate, optional component** — the installer is untouched. **M44's verification pass is
the part worth remembering**: doing it by hand found four defects no automated check could see — the
`.mcpb` could not be built on Windows at all (**M127**), it declared a Python range no Python could
satisfy (**M128**, PEP 440 syntax in a node-semver field), and row 10's own instructions did not put
the lock in the bundle (**M129**), which would have answered a question carried since M42 *wrongly*.
That question is settled: `uv run --directory` honours a committed `uv.lock`, so the bundle ships
one. Shipping alongside: **M101** annotation, **M116/M117** incremental save (a highlight appends
1,865 bytes instead of rewriting 8.8 MB), **M110/M111** save/export cost, **M120** shape resize,
**M121** Insert Blank Page. **1.0 is still deliberately not taken** — the gate (clean-machine
install, the dead Donate link, one flaky test, background rendering) is listed in `PROGRESS.md`.
It supersedes **v0.17.1** — a **security patch plus the last M92 fix**. `pypdf` 6.14.2 → 6.15.0
clears two Moderate parse-DoS advisories (GHSA-fwg2-594c-jp42, GHSA-fp3f-mc75-235c). They were
thought reachable through `PyPdfEngine`'s `PdfReader`, but nothing in the app ever called it.
M156 removed pypdf from the installer (`PLAN.md` §M156). The bump was found by the weekly
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
**M0–M44 and R1–R6 are all complete**, the bridge included — its reserved v0.11.0 was long spent by
the time it shipped, so it took **v0.18.0** at tag time. **M138–M157** shipped in v0.20.0, except
**M142** (Markdown export), which the owner deferred. The named work left is the **1.0 gate**, in
`PROGRESS.md`.
For live status — shipped versions, per-release notes, release links, milestone ticks, and **Open
follow-ups** — see `PROGRESS.md` (the single source of status; read it first). Design/spec, including
§Future enhancements for what's next, lives in `PLAN.md`.
