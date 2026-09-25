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
  the same PR, tick the milestone's box in `PROGRESS.md` and link the PR.
- **Cite sources.** Tie claims/numbers back to a `PLAN.md` section; don't present assumptions as facts.
- **Where things live — update in exactly one place** (avoid the drift that let a stale status blurb
  triplicate across the docs). *Status* — shipped versions, release links, per-release notes, milestone
  ticks, open follow-ups → **`PROGRESS.md`** only. *Design / spec* — architecture, packaging,
  verification, the roadmap & rationale of each milestone → **`PLAN.md`**. *How we work* — conventions,
  gotchas, environment → **`CLAUDE.md`**. The other two **link**, never restate. Rule of thumb:
  **status → PROGRESS; design → PLAN; process → CLAUDE; a reproducible defect → a GitHub issue**
  (the last slot is new — see *A reproducible defect in shipped code goes to GitHub Issues* below).
- **`README.md` is the one sanctioned exception — and it must be updated on every release.** It is the
  shop window for the public repo, so it *does* restate the shipped version, a one-line what's-new
  for the **current release only** (history lives in GitHub Releases / `PROGRESS.md`), and a
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
  `install.py` prints. `PLAN.md`/`PROGRESS.md` are records, not instructions — do not rewrite their
  history to match.

- **Capture the follow-up in the session that found it.** *Where things live* routes open
  follow-ups to `PROGRESS.md`; this is the part that rule assumes — that they get written at
  all. Anything deferred, **rejected**, or noticed-but-not-fixed is committed before the session
  ends, rejections with their reason so the next one does not re-derive a settled argument. Chat
  scrollback is not a backlog: sessions end and context is compacted, and an item held only in an
  assistant's working memory is lost silently at either boundary — three real findings from the
  TC-007/TC-008 rounds were sitting there on 2026-08-18 (`PROGRESS.md` §Open follow-ups has them
  now).
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
  fix still owes its `PLAN.md` entry and `PROGRESS.md` milestone** under the rules above: the issue
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
    design, the questions for the owner, and the `PLAN.md` entry.

  Each step starts in a fresh session, so a diagnosis done early gets done again anyway.
- **Every non-trivial change gets both a `PLAN.md` design entry and a `PROGRESS.md` milestone** —
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
- **Never rebuild the scene inside a Qt callback.** `scene.clear()` during `showEvent` /
  `paintEvent` / an event handler destroys every `QGraphicsItem` while Qt is still walking them.
  Defer to the event loop with a `QTimer` **parented to the view** (it is then cancelled on
  destruction, and a burst of events collapses into one pass).
- **An MCP tool description over ~2 KB is cut in half in transit, and nothing errors.** Claude Code
  truncates a tool description at **2,048** characters (`yfe` in the client binary) and appends
  `… [truncated]`; the *same* constant truncates the server's `instructions` block, so that is not
  the uncapped escape hatch it looks like — ours fits only because it is under the cap, with 195
  characters to spare on the `--read-only` build. `redact_text` lost **69%** of its 6,573 characters
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
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
  That is the **app's build** requirement and nothing else. The MCP bridge is `pip`-installed rather
  than frozen, so `requires-python` genuinely gates it, and since M132 it is `>=3.11,<3.15` — do not
  "fix" a bridge file back to 3.12 for consistency with this line.
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements-win.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `klarpdf/util/paths.py:normalize_path()` is the single identity chokepoint.
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status
**Current: v0.19.0 shipped** — **the bridge you can actually install (M133–M136)**. Installing it
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
**M0–M44 and R1–R6 are all complete**, the bridge included — its reserved v0.11.0 was long spent by
the time it shipped, so it took **v0.18.0** at tag time. **M138–M140** (document structure for
agents — `get_links`, `set_outline`, heading candidates) are scheduled but unstarted; the other
named work is the **1.0 gate**. Both are in `PROGRESS.md`.
For live status — shipped versions, per-release notes, release links, milestone ticks, and **Open
follow-ups** — see `PROGRESS.md` (the single source of status; read it first). Design/spec, including
§Future enhancements for what's next, lives in `PLAN.md`.
