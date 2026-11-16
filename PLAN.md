# Plan: Local, Offline, Native-Windows PDF Viewer + Page Editor (Python)

> **This document holds the design that holds across milestones**: architecture, packaging,
> verification, and the roadmaps. Each milestone's own design and record are in
> `docs/milestones/<id>.md`; a milestone heading below links to its file. Status is in
> **`PROGRESS.md`**. Which file holds what: `CLAUDE.md` §How we work → *Where things live*.

> **Revision (2026-06-15)** — folded in two decisions without changing the product: a
> **Development environment** section (Hybrid — build the cross-platform core + headless tests in
> WSL, iterate the GUI via WSLg, use Windows only for packaging + shell-integration validation) and
> a **Portability** section (Windows-first ship with near-zero-cost Linux-ready seams). The Build
> order is now tagged WSL vs Windows.

## Context

On macOS the user relied on **Preview** to view PDFs and to splice/split them — drag one
PDF onto the end of another, rearrange or delete pages, and move/copy individual pages
between documents, then save or save-as. After moving to **Windows** they have no
trustworthy equivalent: third-party utilities and online services exist, but the user does
**not trust them** with their documents.

We will build a **single, self-contained desktop app** from **readable Python source** (the unit
of audit), shipped as a **bundled Windows installer** that carries all dependencies. It will be
their **default PDF viewer** (the ~90% use) and also do the occasional **splice/split** editing.
Hard requirements gathered from the user:

- **Default Windows PDF viewer**, full Preview-like viewing experience.
- **Single instance / one window per document** — re-clicking an already-open file must
  **focus the existing window**, never spawn a duplicate (their current pain: duplicate
  browser tabs).
- **Drag-and-drop** page reorder **and** page-level **cut/copy/paste** between documents.
- Merge/splice (append + insert at a position), reorder, delete, move/copy pages, Save/Save As.
- **Undo/redo** for all page edits (Ctrl+Z / Ctrl+Y).
- **Prompt to Save / Discard / Cancel on close** whenever a document has unsaved changes.
- **Must work fully offline at install *and* runtime** — no network access in either phase.
- **Ship as a Windows installer** that bundles **all** dependencies at build time, so a target
  machine needs **no Python and no internet**; the installer performs the registry/file-association
  setup and offers a clean uninstall.
- **Pinned, auditable dependencies:** only **well-known, reputable** libraries, each documented in
  the repo with its **exact version**; versions never change automatically (rebuild or runtime) —
  only by an explicit, reviewed edit (lockfile with hashes + vendored wheels).
- **Preserve the OCR text layer**, **bookmarks/outline**, and **form fields** through all edits.

Intended outcome: a trustworthy, auditable, offline desktop app that replaces Preview's
view + page-editing workflow on Windows.

## Approach (recommended)

A **native Windows desktop app** in **Python**. The repo holds **inspectable source** (the unit
of audit), and we **ship a self-contained Windows installer**: at build time the app is frozen
together with the Python runtime and all libraries, then wrapped in an installer that also writes
the file association. Target machines need **no Python and no internet**. One resident process
manages multiple document windows. (See "Packaging, dependencies & installer" for the full
pinned/offline toolchain.)

**Libraries (all reputable, offline; exact versions live in the lockfile, see packaging):**
- **PySide6** (Qt6, LGPL) — GUI, windows, drag-and-drop, clipboard, and the
  `QLocalServer`/`QLocalSocket` single-instance IPC. `pip install PySide6` includes the
  Addons (QtPdf) automatically.
- **PyMuPDF / `fitz`** (MuPDF by Artifex, **AGPL** — see note), minimum **1.25.5**, pinned to an
  **exact** version in the lockfile — renders pages/thumbnails **and** does lossless object-level
  page editing.
- **pypdf** (BSD, pure Python) — **test-only** since M156: an independent second reader the tests use
  to cross-check what PyMuPDF wrote. It was planned here as an optional fallback edit engine; that
  engine was built in M1, never used outside the tests, and removed (§M156).

**Why PyMuPDF renders the viewer instead of Qt's `QPdfView`:** `QPdfView` renders, scrolls,
zooms, and highlights search hits, but has **no interactive text selection/copy**. The user
wants select-&-copy (the OCR text). Rendering with PyMuPDF and building a selection overlay
from `page.get_text("words")` boxes delivers selection **and** unifies viewing + thumbnails +
editing on one engine. Text selected this way is exactly the preserved OCR layer.

**AGPL note for the user:** PyMuPDF is AGPL. Building the installer for **your own machines** is
private use — fine. If you distribute the installer **publicly**, AGPL requires offering the
corresponding source; the full source now lives in this **public** repo (since 2026-07-17 — see
§Packaging → *Public-release readiness*), so shipping the installer with a pointer to the repo (and
its exact tag/commit) satisfies that. This is no longer hypothetical: the obligation is **live**, and
the About dialog's tagged corresponding-source link (G4) is what discharges it per release. The
remaining alternative is an Artifex commercial license. (A pypdf-only fallback build was the other
one; it was never built, and its engine was removed in M156.)

### Key design idea — Virtual-document / edit-list model

Never mutate the on-disk PDF while editing. Each window holds a `VirtualDocument`: an ordered
list of `PageRef = (source_id, source_page_index, rotation_override)` plus a registry of open
read-only `fitz.Document` sources.

- All edits are **list edits**: reorder = move; delete = remove; merge/insert = splice in refs
  from another source; rotate = set override.
- **Cross-window move/copy is trivial:** dragging/pasting a page from window B into window A
  just splices B's `PageRef`s (registering B's `fitz.Document` in A's sources). Copy keeps B's
  ref; move also removes it from B. Nothing is rewritten until Save.
- **Materialize-on-Save** (the only write) takes **one of two routes**, chosen by
  `VirtualDocument.page_set_unchanged()` — *is the output every page of the origin, in its original
  order?*
  - **Unchanged page set → edit a copy of the origin.** Open a fresh copy and apply the per-page
    edits to it. This is the common case: filling a form, annotating, redacting, rotating, cropping.
    Per-page edits deliberately do **not** count as structural — they act on a page wherever it lives.
  - **Anything structural → graft a new document.** Reorder, delete, insert, or a page from a second
    source: copy contiguous same-source runs via `out.insert_pdf(src, from_page, to_page,
    start_at=-1, links=True, annots=True, widgets=True, final=...)`, then **rebuild the outline**
    (remap old→new page indices, drop bookmarks whose target page was deleted) and the internal
    links, both of which the graft drops.

  Both routes then apply rotation overrides with `page.set_rotation()` (absolute, not additive — set
  the final angle, don't accumulate), the page-edit layer and the form fills, and finish with
  `out.save(path, garbage=4, deflate=True, clean=True)`. Output page `i` is `ordered[i]` either way,
  which is what lets every per-page pass be shared verbatim between the two.

**Why two routes, and what "lossless" actually covers (M93).** `insert_pdf` copies **pages**. A PDF
also keeps a great deal at the **document** level, and none of it rides along with a page: the
accessibility structure tree and `/MarkInfo`, Reader Extensions `/Perms`, the `/Names` tree, and
encryption. Grafting into an empty document dropped every one of them *silently* — the pages came
through perfectly, so the output looked right in every viewer while a tagged, AES-encrypted federal
form came back untagged, unencrypted, with every permission granted, and with its two hyperlinks
rewritten into `/Launch` actions naming local files that do not exist.

The engine had already been patched twice for the same shape of problem: the outline and internal
links are rebuilt (M33) and the metadata stores carried across (M53), each pass added after a save
was caught dropping something `insert_pdf` never copied. The structure tree is the next item on that
list and **the one that cannot have such a pass written for it** — it is a tree of references into
page content, so it can be kept but never reconstructed. Hence the split: the case that needs no new
document no longer gets one.

The guarantee is therefore precise rather than absolute:

| | text layer, annotations, form fields, bookmarks, internal links | structure tree, `/MarkInfo`, `/Perms`, `/Names`, encryption |
|---|---|---|
| **unchanged page set** | preserved | **preserved** |
| **reorder / delete / merge** | preserved (outline + links rebuilt) | **lost** |

Preserving a structure tree across *moved* pages means rewriting it rather than copying it — a
separate project, carried in `PROGRESS.md` §Open follow-ups. Until then the reordering route is
lossless for **content** and not for **document structure**, and the docs say so rather than
rounding up.

Encryption has two cases and M54 only covered one: a document that *needs* a password is decrypted
at open and re-encrypted from the recorded password, while one that opens freely but restricts what
you may do with it (an owner password — the common shape for a published form) has no password to
record and is carried by keeping the encryption the origin copy already holds. A rebuild cannot do
this: reproducing the original's encryption would need the owner password, which we do not have and
must not need.

Keeping the origin means keeping more objects, so the save writes **object streams**
(`use_objstms=1`, PDF 1.5) — which it never did, leaving every object a plain uncompressed
dictionary. Most real PDFs already arrive that way, so this restores how the file was written
rather than compressing it further, and it more than pays for the retained structure: a 9-page
tagged form that saved at 316 KB against a 233 KB input now saves at **151 KB**.

This centralizes outline remapping in one place and makes every editing operation O(list-edit).

### Undo/redo (cheap, because edits are list edits)

Use PySide6's built-in **`QUndoStack` + `QUndoCommand`** (reputable, ships with Qt; wires
directly to Ctrl+Z / Ctrl+Y menu actions and gives free "Undo *reorder*" labels). The stack is
owned by the `MainWindow`; each mutating op (reorder, delete, insert/merge, rotate, paste) is a
command that snapshots and restores `VirtualDocument.ordered[]` — cheap, since it's a list of
small `PageRef` tuples — and updates the dirty flag. `redo()` re-applies; `undo()` restores the
prior snapshot.

- **Cross-window move = two independent commands on two stacks** (remove in B, insert in A).
  This is a known, documented limitation: undoing the paste in window A does **not** restore the
  page in window B. We surface it honestly rather than fake a global history.

### Save-on-close prompt (unsaved-changes guard)

`MainWindow.closeEvent` checks `VirtualDocument.dirty`; if dirty it shows a `QMessageBox` with
**Save / Discard / Cancel**. Save runs the normal save path (Save As if untitled); Cancel calls
`event.ignore()` to abort the close. The app only exits once every window has resolved its
prompt (closing the last window does not bypass an unsaved one). Reuses the dirty tracking
already held in `klarpdf/model/virtual_document.py`.

### Single-instance + one-window-per-document (the duplicate-tab fix)

Every launch is invoked by Explorer as `klarpdf.exe "%1"` (the frozen `launcher.py`; `pythonw
launcher.py "%1"` when running from source in dev):
1. Compute a per-user `QLocalServer` name; try `QLocalSocket.connectToServer`.
2. **Connects** → an instance is running: send the **normalized absolute path**, then exit
   (this process shows no UI).
3. **Fails** → become the server (`removeServer` to clear a stale pipe, then `listen`), open
   our own document window, and keep a `dict[normalized_path -> window]`.
4. On a received path: if it's in the dict → **raise/activate** that window (no duplicate);
   else open a new window.

- **Identity key:** `os.path.normcase(os.path.normpath(os.path.realpath(path)))`
  (case-insensitive on Windows, resolves symlinks/`..`).
- **Windows focus quirk:** background processes can't always steal focus — on activate, restore
  if minimized, `raise_()` + `activateWindow()`, and use a brief
  `WindowStaysOnTopHint` toggle as a reliable fallback (optionally `QApplication.alert`). This
  logic lives behind `platform_integration.activate_window()` (Portability hedge #2).
- Handle stale pipe after a crash and the near-simultaneous double-click race (retry connect once).

### Viewer (PyMuPDF, Option B)

- Continuous vertical scroll in a `QGraphicsView`/`QGraphicsScene`; each page is a
  `QGraphicsPixmapItem` rendered by `page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))`.
- **Lazy/virtualized rendering** (only pages intersecting the viewport + small prefetch),
  bounded LRU pixmap cache keyed by `(page, zoom_bucket, rotation)`.
- Zoom + **fit-width/fit-page** (scalar into the matrix), **rotate view**.
- **Text selection/copy:** cache `page.get_text("words")` — each tuple is
  `(x0,y0,x1,y1, word, block_no, line_no, word_no)`, so reading order comes from the data: index
  words by `(block_no, line_no, word_no)`. Mouse-down hit-tests to an anchor word index,
  drag hit-tests to a cursor index, and the selection is the inclusive range between them in that
  order. Paint highlight rects over the selected boxes; copy the joined words to the clipboard.
  **Search:** `page.search_for(query)` → highlight + next/prev navigation.
- **Thumbnail sidebar** bound to the `ordered[]` list — doubles as jump-to-page (View mode) and
  drag-reorder/delete/cross-window drag (Organize mode). Both views read the same model.
- **Remember last page + rotation per document** in a small local JSON under the QStandardPaths
  app-config dir (`%LOCALAPPDATA%\klarpdf` on Windows, `~/.config/klarpdf` on Linux; auditable, offline),
  keyed by identity path. (Path resolved in `store/settings.py` — Portability hedge #1.)
  **Zoom is saved but deliberately not restored** — a document opens at **Fit Page** (v0.9.1,
  PR #61: a remembered magnification kept reopening documents too large for the window), and there
  is no sub-page scroll offset, only the page. This line used to promise "page/zoom/scroll", which
  is how the shipped behaviour later got mistaken for a wiring bug; the value is kept in the file
  so restoring it stays a one-line decision. Rationale lives with `PdfView.view_state()`.

## Development environment (Hybrid: WSL + Windows)

The owner develops in **WSL2** (Ubuntu, Python 3.12.3, with WSLg so GUIs display on Windows) but
**ships Windows**. Most of this app is cross-platform; only packaging and Windows shell-integration
truly require Windows. So development is **hybrid**, with **git as the bridge** between two
checkouts — neither reaches across the filesystem boundary at runtime.

- **WSL checkout** `/home/<you>/klarpdf` (native Linux fs, fast): canonical dev for all `klarpdf/model/`,
  `viewer/`, `organize/` code, the headless tests, and GUI iteration via **WSLg**.
- **Windows checkout** `C:\Users\<you>\klarpdf` (native NTFS, fast PyInstaller + correct shell
  behavior): `git pull` here for **packaging + Windows-behavior validation only**.
- Push from WSL → pull on Windows. **Do not** edit one checkout with the other OS's tools across
  `\\wsl$` or `/mnt/c` (slow; line-ending/permission churn). A checked-in `.gitattributes`
  (`*.py` LF; `*.ps1`/`*.iss` CRLF) keeps both checkouts clean.

**What runs where** (see Build order for the per-step tags): ~80% is WSL-doable. The GUI-free
`klarpdf/model/` + edit-engine and the **headless pytest suite** run in WSL today; the viewer/selection/
thumbnail GUI iterates via WSLg. Only the **freeze + installer + registry** (step 9) is
Windows-only, and the **single-instance / focus / file-association** behavior (step 2) must be
**validated on Windows** — WSLg runs the *Linux* Qt build, so it smoke-tests logic but is not
authoritative for Windows focus-stealing rules or the Explorer `%1` launch path.

**Windows prerequisite (one-time):** install **Python 3.12.x from python.org** (add to PATH / `py`
launcher). The Windows machine currently has only the **Microsoft Store stub** `python.exe`, which
is not usable for the build. WSL already has 3.12.3.

**Dependency discipline applies to the _shipped Windows build_, not WSL dev:**
- The authoritative `requirements-win.txt` (exact `==` + `--hash=sha256`) and `vendor/wheels/` are the
  **Windows** set (`win_amd64`), produced on Windows — the auditable ship artifact.
- The **WSL dev venv** installs the **same pinned `==` versions** but **by version only, not by
  hash**: the Windows lockfile's `--hash=sha256` lines pin specific *`win_amd64`* wheels, and pip on
  Linux resolves *`manylinux`* wheels with different hashes, so `pip install --require-hashes -r
  requirements-win.txt` would **fail on Linux by design**. Dev therefore installs from a small
  unhashed `requirements-dev.txt` (or `pip install <pkg>==<ver> ...`) carrying the same versions —
  derived from the same `requirements.in`. It is dev tooling, not shipped, so it need not be
  hashed/vendored/offline; the offline+hashed guarantee is the Windows ship build's job.

## Packaging, dependencies & installer (offline, pinned, auditable)

This satisfies the install/offline/auditability requirements. Three layers — **pin → freeze →
install** — each reproducible and offline.

```mermaid
flowchart LR
  A["requirements.in<br/>(human-edited, top-level pins)"] -->|"pip-compile<br/>--generate-hashes"| B["requirements-win.txt<br/>(exact ==, sha256 per wheel)"]
  B -->|"pip download<br/>(once, online)"| C["vendor/wheels/<br/>(committed/released)"]
  C -->|"pip install --no-index<br/>--require-hashes (offline)"| D["build venv<br/>(Win, Python 3.12.x pinned)"]
  D -->|"PyInstaller (pinned)<br/>--onedir --noconsole"| E["dist/klarpdf/<br/>(runtime + Qt + libs)"]
  E -->|"Inno Setup (pinned .iss)"| F["klarpdf-setup-x64.exe<br/>(bundles everything)"]
  F -->|"installs + writes ProgID/.pdf assoc (HKCU)"| G["target machine<br/>no Python, no network"]
```

**1. Dependency pinning & integrity (versions never drift).**
- `requirements.in` lists the few top-level libs (PySide6, PyMuPDF; pypdf until M156). `pip-compile
  --generate-hashes` (from **pip-tools**, itself pinned) produces `requirements-win.txt` with **exact
  `==` versions for the full transitive tree plus a `--hash=sha256:` for every wheel**.
- All installs use `pip install --require-hashes --no-index --find-links vendor/wheels` — pip
  **refuses** anything whose version or hash doesn't match the lockfile, so a rebuild can never
  pull a newer/tampered package. A version bump is an explicit edit to `requirements.in` →
  re-compile → re-vendor → review the diff (a reviewable PR), never automatic.
- The app **never** invokes pip or fetches anything at runtime; the frozen bundle carries fixed
  versions. Also pin **Python (3.12.x exact)**, **PyInstaller**, and the **Inno Setup** version
  used, recorded in `DEPENDENCIES.md`.

**2. Vendored wheels (offline build).** Run `pip download -r requirements-win.txt --only-binary=:all:
-d vendor/wheels` to fetch the `win_amd64` set. The wheels are **not committed** (binary bloat /
GitHub's 100 MB-per-file limit); `vendor/wheels-sources.md` records each wheel's version + sha256 +
source URL so the exact set is reproducible, and each release archives them as assets. Once
fetched, the **build itself is fully offline** (`--no-index --require-hashes`) and reproducible
from the lock alone.

**3. Freeze (bundle Python + Qt + libs).** **PyInstaller** (pinned) with a checked-in
`packaging/app/klarpdf.spec`, built `--onedir --noconsole` on Windows (cannot be cross-built from
WSL). `--onedir` (vs `--onefile`) gives faster startup and a clean tree for the installer to lay
down; a secondary `--onefile` build also ships as a portable, run-anywhere `.exe` (see §5 — it
trades slower per-launch startup and no auto-association for zero-install portability). Output
`dist/klarpdf/` contains the embedded CPython, the PySide6/Qt runtime, and PyMuPDF —
no system Python needed. (Dependency versions are reproducible via the hashes; note honestly that
PyInstaller output is **not byte-identical** across builds due to timestamps — version-repro, not
bit-repro.)

**4. Installer + registry (one self-contained `setup.exe`).** **Inno Setup** (free, mature,
widely used; script-driven) with a checked-in `packaging/app/installer.iss` that:
- bundles the entire `dist/klarpdf/` tree (so the `.exe` carries every dependency — no downloads
  at install time, satisfying offline-install),
- `[Registry]` writes a **per-user ProgID** under `HKCU\Software\Classes` (no admin):
  `klarpdf.Document` with `shell\open\command = "{app}\klarpdf.exe" "%1"`, a `DefaultIcon`, a
  `FriendlyAppName`, and `.pdf\OpenWithProgids` so the app appears in **Open With**,
- installs a Start-Menu shortcut, and registers an **uninstaller** that removes the app, the
  registry keys, **and the per-user config `%LOCALAPPDATA%\klarpdf`** (an `[UninstallDelete]` wipe —
  a clean removal was chosen over leaving the view-state JSON behind).
- **Setting it as *the* default** is the one manual step Windows reserves to the user (the
  `UserChoice` hash is anti-hijack-protected): the installer adds the handler + Open-With entry;
  the user confirms once via the first "Open With → Always" prompt or Settings → Default apps. The
  installer's finish page links straight to that Settings page.

**5. Build & release pipeline (GitHub Actions; manual + tag-triggered).** A checked-in
`.github/workflows/release.yml` runs on a **`windows-latest`** runner, triggered both by
**`workflow_dispatch`** (a "Run workflow" button in the Actions tab, also `gh workflow run`) and by
a **`push` of a `v*` tag**. It drives the same one-command `packaging/app/build.ps1` (also runnable
locally) end-to-end: re-fetch + hash-verify the `win_amd64` wheels from `requirements-win.txt` (not
committed — see §2) → clean build venv (`--require-hashes --no-index`) + pinned PyInstaller → **two
artifacts** from `packaging/app/klarpdf.spec`: the **`--onedir --noconsole`** tree for the installer and
a portable **`--onefile` `klarpdf-portable-x64.exe`** → Inno Setup (`ISCC installer.iss`) →
`klarpdf-setup-x64.exe` → smoke-test (launch + open a PDF). Both artifact names carry an explicit
**`-x64`** suffix — the only architecture built today (§2's `win_amd64`-pinned wheels, built on a
`windows-latest` x64 runner) — so a future `arm64` build has a distinct, non-colliding name.
- **Versioning:** one source of truth (`klarpdf/version.py`) feeds the PyInstaller exe metadata, the Inno
  `AppVersion`, and the git tag; a bump is an explicit edit + a new tag.
- **Release:** on a `v*` tag the workflow publishes a **GitHub Release** attaching
  `klarpdf-setup-x64.exe`, the portable `klarpdf-portable-x64.exe`, a **`SHA256SUMS`** file, and the
  **vendored wheels** (each release archives its exact build inputs and carries the AGPL
  "corresponding source" pointer at that tag). The runner re-fetches wheels from PyPI, so the
  *runner* build is not offline — but the produced installer is fully self-contained, and the
  authoritative **offline** build + clean-machine install stay verified locally (see Verification).
- **Code signing** is a deferred enhancement: an Authenticode sign step (cert from GitHub Secrets)
  slots in just before packaging; until then the unsigned `.exe` shows a one-time SmartScreen
  "unknown publisher" prompt — acceptable for private/own-machine use.

### Public-release readiness (AGPL licensing & compliance)

The repo is **public since 2026-07-17**. That was a deliberate, largely one-way step (published source
is effectively public forever) and it turned the AGPL note above from "private use, fine" into a
**live obligation**, so it carried its own readiness track (checklist + status in `PROGRESS.md`
§Public-Release Readiness; **one PR per item**, ordered, with the email cleanup first and the
flip-to-public last and manual). The pre-public hygiene scan was clean — no secrets in the working
tree or history, `.gitignore` excludes all build artifacts / wheels / `report.json`, CI uses
`${{ secrets.* }}` — so the work was licensing + community files plus a one-time commit-author
cleanup. The sections below record the **design and rationale**; for what is done vs. outstanding, see
`PROGRESS.md`.

- **Branding (name + logo) — decided: KlarPDF.** `pdfproj` was a development codename, not a product
  brand; the name had to be settled **before** the name-dependent artifacts below (license copyright,
  About dialog, community files, README) so they bake in the final identity. The gate is closed — the
  visual system landed first, then the rebrand sweep across `klarpdf/version.py`, `packaging/app/installer.iss`
  (AppName / Publisher / `AppId` / ProgID), the window title, the single-instance + `%LOCALAPPDATA%`
  identifiers, the annotation author tag, the `.ico` + toolbar SVG assets, and the **GitHub repo name**
  (rename while private — old links redirect). Name, casing mapping and the two-part split are tracked
  as G2 in `PROGRESS.md`; the visual system is specified in `assets/brand/BRAND.md`.

  The sweep carries **no backward-compatibility shims**, because the app has never been distributed —
  it has a single user and no installed base. Two values are therefore free to change outright that
  would otherwise be frozen: `KLARPDF_AUTHOR` in `klarpdf/model/page_edits.py`, which is stamped as the PDF
  `/T` field on every annotation the app bakes in and matched on read-back (M31 round-trip, and the
  foreign-annotation boundary noted under §Future enhancements), so changing it would strand
  annotations in already-saved files; and the `%LOCALAPPDATA%` config leaf, whose rename orphans the local
  view-state JSON. Both are accepted losses. The installer additionally takes a **fresh `AppId` GUID**:
  Inno identifies an installation by `AppId`, not `AppName`, so reusing it would make the renamed setup
  an in-place upgrade — silently skipping the old uninstaller's ProgID / `OpenWithProgids` /
  config-dir cleanup and reusing the recorded `pdfproj` install directory.
- **Governance: open source, closed to pull requests.** Issues — bugs, security reports, feature
  requests — are **open to everyone**; they are the cheapest, highest-signal input a public repo gets.
  **Pull requests are restricted to the maintainer and invited collaborators**, and everything else is
  auto-closed by `.github/workflows/close-external-prs.yml`. Review is the scarce resource and the
  roadmap is deliberately narrow; publishing source obliges nothing about accepting changes (AGPL
  requires offering source to *recipients*, not merging patches). Forking is guaranteed by the licence
  and is the intended escape valve. Interaction limits are the wrong tool for this — they would also
  block non-collaborators from opening issues.

  Provenance uses the **DCO 1.1, deemed accepted** by submitting a change: no `Signed-off-by`
  requirement and no sign-off check, because with PRs limited to invited collaborators a per-commit
  certification is ceremony rather than information. **The DCO grants the project no rights** — it
  certifies that the contributor *may* contribute. Contributions are therefore *licensed*
  (inbound = outbound, `AGPL-3.0-or-later`), never assigned.

  **Consequence for the Artifex escape hatch below:** relicensing a work needs the consent of every
  copyright holder in it. While the maintainer is the sole author, that is his alone to give. The
  first merged contribution from *anyone else — a collaborator included* — makes them a copyright
  holder and forecloses commercial relicensing without their consent. A DCO does not change this; only
  a CLA or an explicit relicensing grant does. If that option is ever to be kept, the grant must be
  settled **before** a collaborator's first merge, not after.
- **Project license = `AGPL-3.0-or-later`.** PyMuPDF is AGPL and the app is a derivative of it, so the
  whole project must ship AGPL (it cannot be MIT/BSD); LGPL (PySide6/shiboken6) and BSD-3 (pypdf) are
  then satisfied by the same source release. Add a root `LICENSE` (full AGPL text) + a
  `THIRD_PARTY_LICENSES` bundling the PyMuPDF / PySide6 / shiboken6 / pypdf notices (cross-refs
  `DEPENDENCIES.md`) + a README license section + badge + build-from-source pointer. This closes the
  gap that the repo today has **no `LICENSE` at all** (a public repo with no license is "all rights
  reserved" *and* fails AGPL's obligation to offer source under AGPL terms).
- **In-app About + Open-Source Licenses dialog.** The app has no Help menu today; a proper OSS release
  adds **About** (version + AGPL + no-warranty notice + source-repo link at the matching tag) and an
  **Open-Source Licenses** view (the bundled license texts), shipped offline via
  `packaging/app/klarpdf.spec` `datas` + a freeze-aware `resource_path()` (mirroring `ui/icons.py`).
- **Community-health files:** `SECURITY.md`, `CONTRIBUTING.md` (DCO, deemed-accepted), `CODE_OF_CONDUCT.md`
  (Contributor Covenant), and `.github/` issue/PR templates.
- **Donations (repo + product).** Add a `.github/FUNDING.yml` (repo "Sponsor" button) + a README
  Support section, and a **Help ▸ Donate…** menu entry plus an About-dialog link in the app
  (`QDesktopServices.openUrl` — user-initiated, so the offline / no-telemetry guarantee is preserved;
  the app opens no socket itself). Pick the platform first (GitHub Sponsors / Ko-fi / Liberapay / …).
  Open-source + donations is fully AGPL-compatible (the "open source + voluntary support" model from
  the appendix). Tracked as G6 in `PROGRESS.md`.
- **Commit-author cleanup (runs FIRST, while still private):** the maintainer's personal email is on
  ~162 of 246 commits (the rest already use the GitHub no-reply); a one-time history rewrite maps every
  commit to the canonical `<id>+username@users.noreply.github.com` no-reply and force-pushes, so the
  personal address is never exposed when the repo flips public. **Keeping it true is G7's job**, in
  three layers, each covering what the one before it loses: a local `user.email` = no-reply governs CLI
  commits (per-machine state a fresh clone silently drops); GitHub's *Keep my email addresses private*
  + *Block command line pushes that expose my email* govern web-merge commits and reject an exposing
  push server-side (account-wide, but only for this account); and `.github/workflows/author-email-guard.yml`
  is the backstop that fails a PR carrying a non-no-reply author or committer.
- **Branch rulesets — reviewed at G7, completed at G8.** The `main` ruleset (GitHub *Rulesets*, the
  successor to branch protection) is **id 18233952, "Protect Main"**, and it has been active since
  2026-06-28 — predating this track. G7 believed otherwise: `GET /repos/utyagi24/klarpdf/rulesets`
  returned **403 "Upgrade to GitHub Pro or make this repository public"**, which G7 read as *no
  ruleset can exist* when it only meant *this API is unavailable on a private free repo*. The flip to
  public revealed the two live rulesets, and G8 reconciled them: the deletion + force-push rules were
  **already there**, so G8's real work was adding the required status checks. A **403 on a read is
  not evidence of absence** — the one durable lesson here.

  **The likely full story** (inference from the timeline, not something GitHub documents plainly, so
  hold it loosely): a private free repo can *create* rulesets but does not *enforce* them — which is
  what "enforcement needs a paid plan" always meant. That fits every observation at once: created
  2026-06-28 via the UI; the API refusing to read them; and G1's force-push sailing through weeks
  later despite `non_fast_forward` being listed. If that reading is right, the practical upshot is
  that **`main` acquired real protection at the flip, not on 2026-06-28** — the rules were a promissory
  note until the repo went public.

  | Rule | | Why |
  |---|---|---|
  | Block force pushes | ✅ | The one that matters. G1's scrub + 15 rewritten tags are only permanent if history is. Present in the ruleset since 2026-06-28, yet G1's force-push (weeks later) succeeded — consistent with the reading below that a private free repo's rulesets are *created but not enforced*. It is enforced now, which is what the rule is for. |
  | Restrict deletions | ✅ | `main` should not be deletable by accident. |
  | Require status checks: **`pytest`**, **`emails`** | ✅ | **The only rule G8 actually added.** `pytest` stops a red merge mechanically instead of relying on noticing the ❌ comment `test.yml` posts. `emails` (the G7 author-email guard) is the layer that keeps the G1 scrub true — a personal address merged post-flip is public permanently. Both always report on every PR by construction; see below. Since then `bridge` (M115.1), `windows` (M123), `bridge-windows` (M126) and `lint` (M167) have joined them. The mirror missed the first three until M167. |
  | Require a **PR** (0 approvals) | ✅ | Live since 2026-06-28, and kept at G8 — this is `CLAUDE.md`'s "never leave edits on `main`" convention enforced server-side rather than remembered. It is distinct from requiring a *review*: at `required_approving_review_count: 0` the solo open-then-self-merge flow is untouched, while a direct push to `main` is blocked. G7's table conflated the two under one ❌; only the review half was ever the thing being declined. |
  | Require a **review** (approvals > 0) | ❌ | Dropped while solo (§Governance): it would mean approving your own PRs to be protected from nobody. Re-enable the moment a collaborator is added — that is when it starts doing work. |
  | Require linear history | ❌ | The project merges PRs with merge commits; this would break the existing flow to buy tidiness. |
  | Require signed commits | ❌ | Commits are unsigned today. Needs GPG/SSH signing set up for the no-reply identity first — a prerequisite, not a rule to flip. Revisit separately. |
  | Bypass list | **empty** | Same reasoning as the review rule: a bypass for the repo admin, on a repo whose only pusher *is* the admin, would make the force-push rule protect against nobody. The realistic threat is a fat-fingered `git push --force`, which an empty bypass list is exactly what stops. A genuine history repair remains possible by flipping `enforcement` to `disabled` and back — deliberate, and it leaves a trail. It also avoids an unverifiable magic number: GitHub's REST docs do not publish the numeric `actor_id` of the built-in `RepositoryRole` values. |

  A second ruleset, **Protect Tags** (id 18234032), guards `~ALL` tags with `deletion`,
  `non_fast_forward` and `update`. Also pre-existing; unchanged by G8.

  `.github/rulesets/main.json` (+ a README of the same rationale) is now a **mirror of the live
  ruleset** rather than the from-scratch payload it was authored as, so the rules stay reviewable in a
  diff. Changes go back with a `PUT` to the existing id — a `POST` would add a *second* ruleset on
  `main` and split its rules across two objects:
  `gh api -X PUT repos/utyagi24/klarpdf/rulesets/18233952 --input .github/rulesets/main.json`.

  **Why the required checks are safe** (the trap this design exists to dodge): a required check is a
  *gate* — "was this evaluated?" — not "did tests run". GitHub cannot distinguish a check that was
  **skipped as unnecessary** from one that has **not finished yet**: both are simply an absent check
  run, and the PR waits on *"Expected — waiting for status"* forever. So a required check must report
  on **every** PR. `test.yml` therefore carries **no path filter** on its `pull_request` trigger; the
  docs-only decision lives **inside** the job, which skips the ~2 min install+suite on an all-markdown
  PR and still reports `pytest`. That is also the only place it can be expressed: a path filter cannot
  say *"every changed file is markdown"* — `paths: ["**.md"]` means *any* file is, so a PR touching
  `app.py` **and** `README.md` would match both it and `test.yml`'s `paths-ignore` and race two check
  runs named `pytest`.
- **Flip to public (manual; not a PR):** `gh repo edit --visibility public
  --accept-visibility-change-consequences` (the second flag is required), then — in the same sitting —
  enable private vulnerability reporting, secret scanning + push protection, and Dependabot security
  updates (all four are public-repo-gated and free), and **reconcile the `main` ruleset**. Status of
  each: `PROGRESS.md` G8.

The escape hatch (only if closed-source is ever wanted) is the one in the AGPL note above: an
Artifex commercial PyMuPDF license. The pypdf-only fallback build was never made. M156 removed its
engine.

## Portability (Windows-first ship, Linux-ready seams)

Decision: **ship Windows only** for the first release, but bake in **near-zero-cost seams** so a
future Linux (or macOS) port is small. The architecture is *incidentally* portable — Qt + MuPDF are
the cross-platform engine and the `klarpdf/model/` layer is GUI-free — so the reuse story is strong as long
as OS-specific code stays quarantined.

**Cheap hedges (do now, ≈zero cost):**
1. `store/settings.py` uses `QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)`
   instead of a literal `%LOCALAPPDATA%\klarpdf` — Qt resolves it per-OS (Local AppData on Windows,
   `~/.config` on Linux). No behavior change for the Windows ship, just the portable form.
2. A thin **`platform_integration.py`** seam holds the only OS-specific app behaviors —
   `single_instance_server_name()` and `activate_window(win)` (the `WindowStaysOnTopHint`/`alert`
   focus shims). `app.py`/`launcher.py` call the abstraction; no `WindowStaysOnTopHint` inline.
   Windows impl now; Linux stub later. The Inno Setup installer writes the `.pdf`/ProgID
   association (see Packaging). An empty `register_file_association()` slot for a future Linux
   `xdg-mime` path lived here until M169 removed it: the module is the seam, and the Linux work adds
   the real function when it exists.
3. **All OS coupling stays inside `packaging/` + `platform_integration.py`** — registry/`.iss`/
   `.ps1` never leak into `launcher.py`/`app.py`.
4. `klarpdf/util/paths.py` `normalize_path()` stays the **single identity chokepoint** so the
   case-sensitivity switch (Windows case-fold vs Linux case-sensitive) is one function.

**Reuse / rewrite map (if Linux is targeted later):**
- **Reusable unchanged:** all `klarpdf/model/` (`virtual_document`, `edit_engine`, `toc_remap`,
  materialize), `edit_commands.py`, all `viewer/`, `organize/thumbnail_panel`, `main_window.py`, the
  `QUndoStack` undo/redo, all `tests/`, `requirements.in`.
- **Small platform branches:** `klarpdf/util/paths.py` (case semantics), `store/settings.py` (config path,
  solved by hedge #1), `app.py`/`launcher.py` focus/raise shims (Wayland forbids programmatic
  activation) + the IPC socket path (same Qt API, different underlying transport).
- **Full rewrite per OS:** `packaging/app/installer.iss` → AppImage/Flatpak/.deb; `build.ps1` →
  `build.sh`; HKCU registry association → MIME + `.desktop` (`xdg-mime`); `vendor/wheels/` →
  `manylinux` wheels; `klarpdf.spec` → Linux conditionals.
- **Rule of thumb:** the *application* ports almost for free; the *installer + file-association +
  window-manager glue* is rewritten per OS.

## Critical files to create

```
klarpdf/
  launcher.py                  # entrypoint: single-instance guard, normalize %1, hand-off/become server
  app.py                       # PdfApp(QApplication): path->window dict, page clipboard, QLocalServer; raise/focus via platform_integration
  platform_integration.py      # OS seam (portability hedge): single-instance name, activate_window() focus shims (the installer writes the .pdf association). Windows now / Linux stub later
  main_window.py               # MainWindow: View + Organize modes, toolbar/menu, holds a VirtualDocument;
                               #   owns the QUndoStack (Ctrl+Z/Y) and the closeEvent save-on-close prompt
  edit_commands.py             # QUndoCommand subclasses (reorder/delete/insert/rotate/paste): snapshot+restore ordered[]
  viewer/pdf_view.py           # QGraphicsView continuous-scroll renderer (PyMuPDF pixmaps, lazy, zoom/fit/rotate)
  viewer/text_selection.py     # word-box selection overlay + clipboard copy (feature QPdfView lacks)
  viewer/search.py             # page.search_for highlighting + hit navigation
  organize/thumbnail_panel.py  # grid bound to ordered[]: drag-reorder, cross-window drag (QDrag MIME), cut/copy/paste, delete
  klarpdf/model/virtual_document.py    # VirtualDocument + PageRef; all list-edit ops, dirty tracking
  klarpdf/model/edit_engine.py         # PyMuPDFEngine: materialize-on-save (the pypdf fallback was removed in M156)
  klarpdf/model/toc_remap.py           # outline snapshot + old->new page remap + drop-dangling
  store/settings.py            # per-document last page/zoom/geometry — JSON via QStandardPaths AppConfigLocation (%LOCALAPPDATA% on Windows, ~/.config on Linux)
  klarpdf/util/paths.py                # normalize_path() — SINGLE identity chokepoint (case-fold on Windows; one-line switch for Linux)
  tests/conftest.py            # builds fixtures with fitz: A.pdf (text layer, bookmark, form field), B.pdf (same-name field)
  tests/test_virtual_document.py  # reorder/delete/insert/move/copy + undo/redo restore ordered[]
  tests/test_materialize.py       # materialize preserves OCR text, remaps TOC to new indices, drops dangling, keeps form fields
  requirements.in              # top-level FLOOR pins (e.g. PyMuPDF>=1.25.5); pip-compile makes the exact == lock. Only file edited to bump
  requirements-win.txt             # locked Windows ship: exact == for full tree + sha256 hash per win_amd64 wheel (pip-compile --generate-hashes)
  requirements-dev.txt         # WSL dev: same == versions, NO hashes (Linux manylinux wheels differ); version-only install for iteration + tests
  vendor/wheels/               # pinned win_amd64 wheels (offline build) — NOT committed; re-fetched from the lock, archived as release assets
  vendor/wheels-sources.md     # auditable record: version + sha256 + source URL per wheel (regenerated by vendor/gen-sources.py)
  DEPENDENCIES.md              # each lib: purpose, why reputable, license, exact version; + pinned Python/PyInstaller/Inno versions
  .gitattributes               # *.py eol=lf; *.ps1/*.iss eol=crlf — clean across the WSL + Windows checkouts
  klarpdf/version.py                   # single source of version → PyInstaller exe metadata, Inno AppVersion, git tag
  packaging/app/klarpdf.spec       # PyInstaller spec → --onedir (installer) + --onefile (portable .exe), icon, data files
  packaging/app/installer.iss      # Inno Setup: bundles dist/, [Registry] ProgID + .pdf assoc (HKCU), Start Menu, uninstaller (+ [UninstallDelete] %LOCALAPPDATA%\klarpdf)
  packaging/app/build.ps1          # offline build: --require-hashes --no-index, PyInstaller (onedir+onefile), ISCC — reproducible
  .github/workflows/release.yml # CI: windows-latest; workflow_dispatch + v* tag → build.ps1 → GitHub Release (setup.exe + portable + SHA256SUMS + wheels)
```

Deferred (see Future enhancements): `klarpdf/model/links_remap.py` — generalize `toc_remap` to internal
GoTo link annotations.

## Build order (phased)

Each step is tagged **(WSL)** / **(WSLg)** / **(Windows)** per the Development environment section.
~80% runs in WSL; only step 9 is Windows-only, and step 2 needs a Windows validation pass.

1. **Setup + dependency lock — (split: WSL + Windows).**
   - *WSL (dev):* create a Python 3.12 venv and install the pinned versions (online once) for fast
     iteration + headless tests. Canonical source is the WSL checkout `/home/<you>/klarpdf`.
   - *Windows (ship lock):* install **Python 3.12.x** from python.org (not the Store stub; add to
     PATH). Author `requirements.in`, run `pip-compile --generate-hashes` → the pinned, hashed
     `requirements-win.txt`; `pip download --only-binary=:all:` the `win_amd64` wheels into
     `vendor/wheels/`; write `DEPENDENCIES.md`. The offline build runs from the Windows checkout
     `C:\Users\<you>\klarpdf` (via git): `py -3.12 -m pip install --require-hashes --no-index
     --find-links vendor/wheels -r requirements-win.txt`.
2. **Single-instance launcher + window management — (WSL; validate on Windows).** The duplicate-tab
   fix and resident process. WSLg smoke-tests the `QLocalServer` handoff; Explorer `%1`, named-pipe
   IPC, and the focus quirks are Windows-real → validate on Windows. Focus logic lives behind
   `platform_integration.activate_window()`.
3. **Viewer — (WSL via WSLg).** Render, continuous scroll, zoom/fit, rotate, thumbnail sidebar,
   last-page memory.
4. **Text selection + search — (WSL via WSLg).** Drag-select/copy and find-in-document.
5. **Edit engine + virtual-document model — (WSL).** Merge/insert, reorder, delete, move/copy across
   windows, with OCR/bookmark/form preservation via materialize-on-save.
6. **Undo/redo + unsaved-changes prompt — (WSL via WSLg).** `QUndoStack` commands for every page
   edit; the `closeEvent` Save/Discard/Cancel guard.
7. **Headless model tests — (WSL).** pytest over the GUI-free model/edit-engine layer (can land as
   early as step 5; no Qt display required). Runs in WSL and CI. See Verification.
8. **Save / Save As — (WSL).** Atomic `os.replace` for Save (also atomic on Windows, same volume);
   Save As dialog. The rename goes through `klarpdf/util/atomic.py:atomic_replace` (M38.5), which retries a
   `PermissionError` on a bounded backoff: on Windows the rename needs exclusive access to both
   paths, and an on-access antivirus scanner holding the just-written temp open is enough to fail a
   save that would succeed 200 ms later.
9. **Freeze + installer + release pipeline — (Windows ONLY).** `packaging/app/klarpdf.spec` (PyInstaller
   `--onedir --noconsole` for the installer **plus a `--onefile` portable `.exe`**) →
   `packaging/app/installer.iss` (Inno Setup) bundling `dist/klarpdf/`, writing the `HKCU` ProgID + `.pdf`
   Open-With association, with an uninstaller that **also wipes `%LOCALAPPDATA%\klarpdf`**.
   `packaging/app/build.ps1` ties pin→freeze→install into one offline, reproducible command;
   `.github/workflows/release.yml` runs it on `windows-latest` (`workflow_dispatch` + `v*` tag) and
   publishes the GitHub Release (installer + portable + `SHA256SUMS` + wheels). **Cannot be cross-built
   from WSL.** Setting it as *the* default is the user's one-time confirm. Code signing is deferred.

## Execution (milestones, tracking & Windows handoff)

The Build order above, operationalized: implemented value/risk-first, in shippable **milestones**,
**one PR per milestone**. M0–M5 — the bulk of the effort (~80% of the work) — is built and verified
in WSL before anything touches Windows.

| Milestone | Step(s) | Where | Done when |
|---|---|---|---|
| **M0** Scaffold + dev venv | 1 (WSL) | WSL | repo skeleton, `requirements.in` + `requirements-dev.txt`, WSL venv, `.gitattributes`, `pytest` collects |
| **M1** Correctness core ⭐ | 5 + 7 | WSL | `klarpdf/model/` + headless tests **green** — OCR/TOC/forms/undo preserved |
| **M2** Viewer | 3 | WSLg | open a PDF; scroll, zoom/fit, rotate, thumbnails, last-page memory |
| **M3** Selection + search | 4 | WSLg | drag-select → clipboard copy; find + next/prev |
| **M4** Editing loop | 6 + 8 | WSLg | reorder/delete/merge + cross-window cut/copy/paste (organize panel) + undo/redo; Save/Save As; dirty-close prompt |
| **M5** Single-instance | 2 (logic) | WSL | second launch hands off to first (WSLg smoke test) |
| **M6** Windows ship lock | 1 (Win) | Windows | python.org 3.12; hashed `requirements-win.txt`; vendored `win_amd64` wheels; `DEPENDENCIES.md` |
| **M7** Windows validation | 2 (validate) | Windows | single-instance/focus/Open-With behave on real Windows; GUI fidelity pass |
| **M8** Freeze + installer + CI | 9 | Windows | `build.ps1` (+ `.github/workflows/release.yml`) → PyInstaller (onedir + onefile) → Inno Setup → `klarpdf-setup.exe` + portable `.exe` |
| **M9** Verify + release | Verification § | Windows | offline build + clean-machine install + no-network audit; portable-exe check; uninstall wipes app + keys + `%LOCALAPPDATA%` → tag `v*` → GitHub Release |

⭐ **M1 is the keystone** — most of the correctness risk (lossless edits, TOC remap, dup form-field
handling), GUI-free, fully testable in WSL/CI. The packaging scripts (`build.ps1`, `installer.iss`,
`klarpdf.spec`) are *authored* during M0–M5 but only *executed* on Windows.

**Progress tracking.** `PROGRESS.md` (repo root) is the durable, at-a-glance checklist; each
milestone PR ticks its box and links the PR. `CLAUDE.md` routes any resuming agent: read
`PROGRESS.md` first (current state), then this section + the relevant Build-order step.

**Windows handoff.** git is the only bridge — never edit across `\\wsl$` / `/mnt/c`. Code flows
**WSL → Windows** (push here, pull there); ship artifacts flow **Windows → repo** (the hashed
`requirements-win.txt`, `vendor/wheels/`, `DEPENDENCIES.md`, and `setup.exe` are produced on Windows and
committed back, keeping the repo canonical).
- *One-time Windows setup (M5 → M6):* install **Python 3.12.x from python.org** (Store stub won't
  build); install **git + an SSH key** (or HTTPS + `gh`) and `git clone` to `C:\Users\<you>\klarpdf`;
  install **Inno Setup** (pin its version in `DEPENDENCIES.md`).
- *Per handoff:* `git pull` → `py -3.12 -m pytest -q` (the core passes on Windows Python too) →
  `packaging\app\build.ps1` → validate Windows-only behaviors → commit Windows artifacts back.
- *De-risk early:* do a throwaway handoff right after **M1** (pull, run tests, trial a PyInstaller
  freeze of a stub) to catch "works-in-WSL / breaks-on-Windows" issues long before M8. This needs
  only python.org Python + PyInstaller on Windows (a subset of the one-time setup above) — Inno
  Setup isn't required until M8.
- *Who drives M6–M9:* PyInstaller/Inno + GUI/installer/clean-machine validation are native-Windows —
  either run the authored scripts there, or run Claude Code natively on Windows for that phase.

## Verification (prove every hard constraint)

Fixture: an **OCR'd `A.pdf`** with a text layer, a bookmark, and a form field; plus **`B.pdf`**
with a form field of the **same name** (to test duplicate-name handling). Merge B into A at a
position, reorder, delete a page, Save As `out.pdf`. Then:

- **OCR text survives moved pages:** `fitz` `doc[i].get_text("text")` non-empty/correct, and
  cross-check with Poppler `pdftotext out.pdf -` (different engine than the writer). In-app:
  drag-select a moved page's text and confirm the clipboard.
- **Outline preserved + correct targets:** `doc.get_toc(simple=False)` titles intact, each
  entry's page = its **new** index, bookmarks to the deleted page are gone (no dangling/`-1`);
  click a bookmark in-app and confirm it lands correctly.
- **Form fields preserved + dup-name handled:** `[w.field_name for p in doc for w in p.widgets()]`
  shows both fields; the colliding B field is auto-renamed (e.g. `name [text]`) rather than
  dropped/overwritten. Cross-check with `pypdf` `reader.get_fields()`.
- **Single-instance behavior:** double-click `A.pdf` twice → exactly **one** window (one
  resident `pythonw.exe` in Task Manager); double-click `B.pdf` → a **second** window. Launch
  with a differently-cased path to A → still no duplicate (case-insensitive match).
- **No network:** run under a monitor (`Get-NetTCPConnection -OwningProcess <pid>` shows no
  app-initiated remote connections; or block the process in Windows Firewall and confirm full
  function). Static audit: no `requests`/`urllib`/`socket` outbound calls; libraries limited to
  PySide6, PyMuPDF, pypdf.

### Headless pytest (automated, model/save layer)

The model and edit engine are GUI-free, so they test **headless** (no Qt display) — runnable in
WSL, CI, and web sessions, unlike the GUI/single-instance/focus checks above (those stay manual on
Windows). `tests/conftest.py` builds the fixtures programmatically with `fitz` (no binaries
checked in): `A.pdf` with an inserted text layer, a bookmark (`set_toc`), and a form widget;
`B.pdf` with a form field of the **same name**. Then:

- `test_virtual_document.py` — reorder/delete/insert/move/copy produce the expected `ordered[]`,
  and each op's undo/redo restores the exact prior list.
- `test_materialize.py` — after materialize-on-save: `doc[i].get_text("text")` is non-empty on
  moved pages; `get_toc(simple=False)` entries point at **new** indices and dangling bookmarks
  are dropped; `[w.field_name for p in doc for w in p.widgets()]` retains both fields. The
  duplicate-name outcome is asserted (or `xfail`-documented if the installed PyMuPDF doesn't
  auto-rename), feeding the open item below.

Run with `py -3.12 -m pytest -q` (or `pytest` in the project venv).

### Installer, offline & dependency integrity

- **Version pinning holds:** `pip install --require-hashes --no-index -r requirements-win.txt` into a
  fresh venv succeeds; then flip one hash/version in `requirements-win.txt` and confirm pip **aborts**
  (proves nothing can silently drift). Rebuilding twice yields the same dependency versions.
- **Offline build:** disconnect the network (or build inside a no-egress shell) and run
  `packaging/app/build.ps1` end-to-end from `vendor/wheels/` — produces `klarpdf-setup-x64.exe` with no
  downloads.
- **Offline install on a clean machine:** on a Windows VM with **no Python and networking
  disabled**, run `setup.exe` → installs and launches; the dependency set bundled matches
  `DEPENDENCIES.md`. (**Windows 10 Home has no Windows Sandbox** — use a free VirtualBox VM, a spare
  machine, or a fresh local user account with networking disabled.)
- **Association via installer:** after install, `.pdf` shows **KlarPDF** in Open With with the
  right icon/name; choosing it (and "Always") routes double-clicks through `klarpdf.exe "%1"`
  into the single-instance path. Uninstall removes the app, the `HKCU` ProgID keys, **and
  `%LOCALAPPDATA%\klarpdf`** — nothing left behind.
- **Portable build:** `klarpdf-portable-x64.exe` (the `--onefile` asset) launches from any folder on a
  clean machine and opens a PDF with no install (slower first paint, no auto-association — both
  expected).
- **Offline runtime** (existing "No network" check) holds for the installed `.exe` too.

## Open items / risks to confirm during implementation

- ✅ **Confirmed (M1, PyMuPDF 1.27.2.3):** `insert_pdf(..., widgets=True)` carries form fields,
  and the default (`join_duplicates=0`) **auto-renames** the colliding root field — merging
  `B.pdf`'s `name` field after `A.pdf`'s yields `name` + `name [NN]`, both preserved, neither
  dropped/overwritten. So no `xfail` was needed. Asserted in `tests/test_materialize.py`
  (`test_merge_preserves_both_form_fields_dedup`), cross-checked via pypdf `get_fields()`.
- ✅ **Confirmed (M1):** `insert_pdf` does **not** copy the source TOC — `klarpdf/model/toc_remap.py`
  rebuilds the outline explicitly, handling **multi-level** outlines (level-continuity repair /
  orphan promotion on drop) and explicit destinations (dest-page remap). Covered by
  `tests/test_toc_remap.py` + `tests/test_materialize.py`. Named-destination outlines remain
  untested (the fixture uses page destinations); revisit if a real doc uses them.
- Text-selection overlay across page boundaries in continuous scroll needs care (anchor/cursor
  hit-testing in scene coordinates); this is the most involved viewer piece and can land in a
  follow-up pass after basic view/scroll/zoom works.

## Shipped roadmap (v0.2.0 ✅ → v0.3.0 ✅ → v0.4.0 ✅)

Same discipline as M0–M9 (one PR per milestone, `PROGRESS.md` tracks state). **A key property: none
of it adds a third-party dependency** — annotations/redaction/forms are native PyMuPDF, printing is
`QtPrintSupport`, and the drag-and-drop / interaction work is plain Qt; all already inside the
vendored PySide6 wheel. So `requirements.in` stays unchanged → **no re-compile, no re-vendor; the
hashed offline lock stays exactly as shipped.**

Release sequence:
- **v0.2.0 ✅ shipped** (M10–M15) — Polish, Print & Forms.
- **v0.3.0** (M16–M19) — **Interaction & Drag-and-Drop**: better drag visuals, drop-target
  indicator, drag PDFs in from Explorer, and a grab/select viewer-mode toggle. Small, low-risk UX
  wins, independent of the page-edit layer; sequenced **before** the bigger content-editing release.
- **v0.4.0** (M20–M22) — **Annotate & Redact**: the keystone content-editing work on the M14
  page-edit layer.

### The page-edit layer (the one new architectural concept)

Icons, zoom %, recent docs, and printing bolt onto the existing UI/viewer cleanly. But form-fill,
highlight, text-box, and redaction **edit content *inside* a page** — which the model has never
done. The hard constraint: source `fitz.Document`s are **shared across windows** (cross-window
paste registers another window's source in `klarpdf/model/virtual_document.py`), so we **must never mutate
a source page in place** — it would corrupt the other window and any `VirtualDocument` referencing
that page.

The fix is to treat content edits exactly like the existing list edits — **immutable descriptors
stored in the model, applied only at materialize, on the output copy:**

- **Model (`klarpdf/model/page_edits.py`, new):** frozen descriptors (form-field values, annotations,
  redaction rects) attached per page and snapshotted alongside `ordered[]`, so the existing
  `QUndoStack` snapshot/restore in `edit_commands.py` keeps undo/redo working unchanged.
  Sources stay read-only.
- **Save (`klarpdf/model/edit_engine.py`):** after `insert_pdf` copies each page, a post-copy pass applies
  that page's edits to the **output** page — `add_highlight_annot` / `add_freetext_annot`, set
  `widget.field_value`, and `add_redact_annot` → `apply_redactions`. Materialize remains the only
  write; sources are never touched.
- **Preview (viewer):** highlight / text-box / redaction draw as Qt overlay items — the exact
  pattern already in `viewer/text_selection.py` and `viewer/search.py`. Form-field **values** need
  appearance fidelity, so a page that has edits renders from a throwaway in-memory single-page copy
  (the pixmap cache key in `viewer/pdf_view.py` gains an edit-version component), keeping WYSIWYG
  without reimplementing PDF appearance in Qt.
- **Interaction (`viewer/tools.py`, new):** a mode controller (select / highlight / text-box /
  redact / form) with text-selection as the default tool, so each tool stays quarantined instead of
  bloating `PdfView`.

v0.2.0 built this layer with **form-fill as its first, simplest consumer**; **v0.4.0's** annotations
and redaction slot into the same mechanism. (The v0.3.0 interaction work below doesn't touch this
layer at all — it's drag-and-drop + viewer-mode UX.)

### v0.2.0 — "Polish, Print & Forms"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M10** Icons | App `.ico` (closes the open follow-up) + toolbar icons for undo/redo, zoom-in/out, cut/copy/paste. `QApplication.setWindowIcon`; wire `icon=` into `packaging/app/klarpdf.spec` and `SetupIconFile` into `packaging/app/installer.iss`. | WSLg + **Win** | App has a real icon (taskbar + installed); toolbar buttons are iconographic |
| **M11** Zoom UX | `zoomChanged` signal from `PdfView`; live "150%" indicator (toolbar combo); **Actual Size / 100%** action (Ctrl+0); preset levels. Extends `PdfView.set_zoom`. | WSLg | Magnification % always visible; one click resets to 100% |
| **M12** Printing | `QPrintDialog` + `QPrinter`; render each page via PyMuPDF at printer DPI, paint with `QPainter`; page-range + current-page. Confirm `QtPrintSupport` + plugins survive the freeze. | WSL logic; **Win** print validation | System print dialog prints the open doc correctly |
| **M13** Recent documents | MRU list in `store/settings.py`; dynamic **File ▸ Open Recent** submenu (app-global, refreshed across windows); dedupe via `normalize_path`, drop missing files, "Clear Recent". Reopen routes through `app.open_document` (free single-instance dedupe). | WSL | Recent files listed; reopen in one click |
| **M14** ⭐ Page-edit layer + form fill | The layer above; first consumer fills existing AcroForm fields (text/checkbox/radio/choice). New `klarpdf/model/page_edits.py`, `viewer/tools.py`; click-to-edit field UI; headless materialize tests. | WSL (model+tests) + WSLg | Fill a form's fields, save, reopen with values intact |
| **M15** Verify + release | Headless suite green; Windows validation (print, icon, forms in the frozen build); tag **v0.2.0**. Opportunistically fold in the carried **CI Node-24 action bumps** + **code signing**. | **Win** | Matrix green → v0.2.0 released |

### v0.3.0 — "Interaction & Drag-and-Drop"

UX polish for page-organize + viewer interaction. All plain Qt in the `organize/` + `viewer/`
layers (the "reusable unchanged on Linux" set) — **no new dependency, no OS coupling**. Builds on
the existing thumbnail-panel drag (M4) and the viewer mouse routing; the page-edit layer is untouched.

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M16** Drag visuals | `ThumbnailPanel.startDrag` sets a real **drag pixmap** — a rendered thumbnail of the grabbed page, stacked with an "N pages" count badge for a multi-select, plus a hotspot — so the cursor clearly carries a page. Replaces the weak default indicator with a **custom insertion marker** (a bold caret/line painted at the drop slot from `_drop_before_index`, repainted on `dragMoveEvent`). | WSLg | Dragging shows a page under the cursor; the drop slot is obvious |
| **M17** Explorer file drop | Accept external **local `.pdf`** URLs (`text/uri-list`) in the Pages panel's `dragEnter`/`dragMove`/`drop`; on drop, open each file as a source and `InsertCommand` its pages at the drop slot (new `filesDropped` signal → `MainWindow`). Non-PDF / non-local ignored. Reuses the existing insert plumbing + drop-index logic. | WSL (logic) + WSLg | Drag a PDF from Explorer onto the sidebar → its pages insert at the drop position |
| **M18** Grab / Select mode | A viewer interaction-mode toggle (`viewer/tools.py`): **Select** (default — text selection + form fill) vs **Grab** (`QGraphicsView.ScrollHandDrag` pan; selection/form routing suppressed; hand cursor). Toolbar toggle + View menu; new hand/select SVG icons. | WSLg | Switch to a hand tool to pan; switch back to select text |
| **M19** Verify + release | Headless suite green; Windows validation (drag visuals, Explorer file drop, mode toggle in the frozen build); tag **v0.3.0**. | **Win** | Matrix green → v0.3.0 released |

### v0.4.0 — "Annotate & Redact" (keystone release)

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M20** ⭐ Annotations | Text **highlight** (reuse word-box selection → `add_highlight_annot`) + **text box** (`add_freetext_annot`), on the M14 page-edit layer; reuses the M18 mode controller for the tool palette; headless materialize tests. | WSL + WSLg | Highlight text & drop a text box; both bake into the saved PDF |
| **M21** ⭐ Redaction | Draw rect → mark → `apply_redactions` at save (**true destructive**). **Security verification:** assert the saved output has *no recoverable text/content* under the box (`fitz.get_text` + Poppler `pdftotext` cross-check, a different engine than the writer). Highest-risk milestone. | WSL (model+verify) + WSLg | Redacted content is provably gone, not merely covered |
| **M22** Verify + release | Full annotation/redaction verification matrix + Windows validation; **code signing** (carried from M15, if a cert is available); tag **v0.4.0**. | **Win** | Matrix green → v0.4.0 released |

⭐ = keystone — most correctness/security risk, GUI-free core, fully headless-testable (the role M1
played for v0.1.0).

### Scope decisions (confirmed with the owner)

- **Drag-and-drop visuals (v0.3.0):** the drag carries a page thumbnail + count badge; the drop slot
  is shown by a custom insertion marker.
- **Explorer file drop (v0.3.0):** scoped to the **Pages sidebar, inserting at the drop slot** —
  not the main view (drop-to-open is a possible later extension, see Future enhancements).
- **Grab/Select mode (v0.3.0):** **Select is the default**; Grab is a hand/pan tool for the corner
  cases. Mode is per-window UI state (not persisted across sessions).
- **Form filling (v0.2.0, shipped):** fills **existing** AcroForm fields only — not a new-field designer.
- **Redaction (v0.4.0):** **true destructive** removal (`apply_redactions`), never visual-cover-only
  (which leaves extractable text — a data-leak trap).
- **Annotations (v0.4.0):** **fire-and-forget at save** — they live in the edit-list while the doc is
  open (full undo/redo) and bake into the saved PDF; KlarPDF does not re-parse saved annotations back
  into the model for round-trip re-editing (deferred — see Future enhancements).

### Files (shipped + planned)

**Shipped in v0.2.0:** `klarpdf/model/page_edits.py` (form-value descriptors, snapshotted with `ordered[]`),
`viewer/printing.py` (QPrinter render), `viewer/zoom_widget.py`, `viewer/form_fill.py` (inline
fill), `ui/icons.py` + `ui/icons/*.svg`, `packaging/app/klarpdf.ico` + `packaging/app/make_icon.py`; tests
`test_form_fill.py`, `test_form_fill_ui.py`, `test_zoom.py`, `test_recent.py`, `test_icons.py`,
`test_printing.py`. (Sources are opened from an in-memory stream + `VirtualDocument.fresh_source` so
in-place Save isn't blocked by a file lock and repeated saves keep widgets.)

**Planned — v0.3.0 (Interaction):** mostly edits to `organize/thumbnail_panel.py` (drag pixmap +
insertion marker + Explorer file-drop) and `viewer/pdf_view.py` + new `viewer/tools.py` (grab/select
mode controller); new `ui/icons/` hand + select glyphs; tests `test_drag_drop.py` (drop-index for
file URLs, `filesDropped` signal, drag-pixmap non-null) + `test_interaction_mode.py` (mode switches
drag mode + suppresses selection/form routing).

**Planned — v0.4.0 (Annotate & Redact):** extend `klarpdf/model/page_edits.py` with annotation/redaction
descriptors + `klarpdf/model/edit_engine.py`'s post-copy pass; reuse `viewer/tools.py`; tests
`test_annotations_materialize.py` (highlight + free-text bake in) + `test_redaction.py`
(`apply_redactions` truly removes content — leak check).

**Portability** stays clean throughout: only icon-in-installer touches OS, already quarantined in
`packaging/`; nothing new leaks into `app.py`/`launcher.py`, and all the v0.3.0/v0.4.0 work lives in
the reusable cross-platform layer.

## Next roadmap (v0.5.0 → v0.6.0 → v0.7.0 → v0.8.0 → v0.9.0)

Same discipline as the shipped releases: **one PR per milestone**, `PROGRESS.md` tracks state, ⭐
marks a keystone (most risk, GUI-free core, fully headless-testable). **Still no third-party
dependency** — every item is native PyMuPDF or Qt (already inside the vendored PySide6 / PyMuPDF
wheels), so `requirements.in` stays unchanged and the hashed offline lock + vendored wheels remain
exactly as shipped. (Owner-confirmed sequencing: discard-edits lands in **v0.5.0**; rich text in
**v0.6.0**.)

### v0.5.0 — "File Safety & Output"

Trust/robustness around the on-disk file, plus edits-aware printing. Small and low-risk;
reuses the `VirtualDocument.reload_from_file` plumbing built for the redaction commit and the
existing `render_to_printer` print path.

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M23** Revert / Reopen | A **Revert** action: discard all edits and reload the document from disk (reuse `reload_from_file` + clear the undo stack, behind a dirty-confirm). | WSL + WSLg | Revert returns the doc to its on-disk state; undo history cleared |
| **M24** External-change warning | Detect the open file changed on disk (mtime/size, or a content hash, via `QFileSystemWatcher`); warn before an overwriting Save and/or on window focus, offering **Reload** (→ M23 path) or **Keep**. | WSL (logic) + **Win** (watcher) | Editing a file changed underneath you warns before clobbering it |
| **M25** Edits-aware printing | Print renders from a shared **edits-applied** output (`PyMuPDFEngine.render_output` — materialize without the save), so the printout shows page order, rotation, form values, highlights, text boxes, and (destructive) redactions exactly as a Save would write them — closing a leak where a not-yet-saved redaction printed the *original*. (Preview, a "Save as PDF" destination, and scale modes were scoped out: the native Windows print dialog can't host a custom preview or destination, and a rasterised PDF is strictly worse than the lossless Save As. The page→image render — `render_output` + `_page_image` — is retained as the engine for the planned **image export**, M36.) | WSL (render) + **Win** print validation | Printing a redacted / annotated / filled doc shows the edits in the output |
| **M26** Verify + release | Headless suite green; Windows validation (watcher, revert, edits-aware printing in the frozen build); tag **v0.5.0**. | **Win** | Matrix green → v0.5.0 released |

### v0.6.0 — "Rich Text & Live Preview"

The annotation experience deepens, building directly on v0.4.0's text boxes.

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M27** ⭐ Styled text boxes | Text-box **font family / size / colour** + **box fill** + **box outline** (on/off, black), via a small formatting bar on the inline editor. Extends the `TextBox` descriptor (`fill_color`, `border_width`; the `fontname` hook already existed) + `add_freetext_annot` on the **simple appearance path** (`text_color` / `fill_color` / `border_width`), so the text stays in `/Contents`. **Bold / italic / underline + a coloured outline were descoped** (owner call): on PyMuPDF 1.27's FreeText path a base-14 name selects only the *family* — the bold/italic variant names (`hebo` / `heit` …) collapse to the regular `/Helv` face and render identically, and `border_color` raises unless `richtext=True`; weight / slant / underline / outline-colour would all force the heavier richtext path (text → `/RC`), deferred to keep M27 light. Headless materialize tests assert the styled annot (DA colour/font/size, `/C` fill, `/BS /W` outline). | WSL (model+tests) + WSLg | Style a text box (font / size / colour + fill + outline); it bakes into the saved PDF |
| **M28** Live thumbnails | Thumbnails reflect the page's **current edited state** (annotations / redactions / fills): render each from an edits-applied page copy (shares the per-page edit-render the viewer uses) with cache invalidation on every edit. | WSLg | A redacted/annotated page's thumbnail shows the edit |
| **M29** Dynamic theme icons | Verify + complete the runtime OS **light↔dark** switch: `changeEvent` / `icons.refresh_for_theme` already re-tint toolbar glyphs on `ApplicationPaletteChange`; ensure it fires on a live Windows theme change (and the app/window icon follows). | WSLg + **Win** | Flipping the OS theme re-tints the toolbar without a restart |
| **M30** Verify + release | Headless suite green; Windows validation; tag **v0.6.0**. | **Win** | Matrix green → v0.6.0 released |

### v0.7.0 ✅ — "Round-trip & Export" (shipped)

Re-editing saved annotations + a flatten **Export**. (Encrypted-PDF + internal-link support that
originally sat here were re-scoped by the owner to **v0.9.0**, so the image work ships first.)

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M31** ⭐ Annotation round-trip editing | On open, re-parse **our** annotations (the `KLARPDF_AUTHOR`-tagged highlights / text-boxes) from the source into `klarpdf/model/page_edits.py`; at materialize, **strip-then-re-add** the managed annotations on the copied page so they aren't duplicated. Saved highlights/text-boxes become movable / re-editable / removable after reopening. (Redaction stays a point-of-no-return — not re-editable.) Follow-ups from manual testing: the page render strips our baked marks so the editable overlay is the single source of truth (no double-draw / pinned original), and text selection reads the stripped render page (a box's text isn't drag-selectable, no stale-position copy). | WSL (model+tests) + WSLg | Reopen a saved doc → move / edit / remove its KlarPDF annotations |
| **M31.5** Export → PDF (flatten) | An **Export** action (`File ▸ Export`) whose first format is a **flattened PDF**: bake the managed annotations **and form widgets** into page content via PyMuPDF `Document.bake()` (text layer **preserved**, *not* rasterised) — a locked but still-searchable copy whose marks can't be moved/removed in any tool. The opt-out counterpart to M31's round-trip (Save As stays editable; Export → PDF locks). Built as an **extensible Export path** (`klarpdf/model/export.py`) that **M36** grows to an image format. | WSL (model+tests) + WSLg | Export → PDF writes a flattened, text-preserving copy whose annotations are no longer editable |
| **M34** Verify + release | Headless suite green (317 tests); Windows validation; tag **v0.7.0**. | **Win** | Matrix green → v0.7.0 released |

### v0.8.0 ✅ — "Images" (shipped)

Bring raster images into the page workflow, reusing existing seams — **no new dependency** (PyMuPDF
converts images ↔ PDF pages; the drag/drop + render paths already exist). Shipped with a round of UI
polish: clearer multi-page selection, a vertically-centred fitting page, and centred text-box text.

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M35** Image import | Drag a local image (`.jpg` / `.jpeg` / `.png` / …) from Explorer onto the Pages sidebar → insert as a new page, exactly like a dropped PDF. Reuses **M17**'s `text/uri-list` drop + insert plumbing; the only new bit is converting each image to a one-page PDF source (PyMuPDF `convert_to_pdf`), after which it's just another registered source. | WSL (logic) + WSLg | Drop a PNG/JPEG on the sidebar → it inserts as a page; Save bakes it in |
| **M36** Image export | **Extend the Export feature (M31.5)** to images: export the selected page(s) → image files (`.png` / `.jpeg`) at a chosen DPI, reusing the **M25** edits-aware render (`render_output` + `_page_image`) so each image shows annotations / fills / redactions; one file per page (or the current page). | WSL (render) + WSLg | Export → Image writes PNG/JPEG matching the on-screen (edited) pages |
| **M37** Verify + release | Headless suite green (341 tests); Windows validation (image drop-insert + image export in the frozen build); tag **v0.8.0**. | **Win** | Matrix green → v0.8.0 released |

### v0.9.0 ✅ — "Encrypted & Links" (shipped)

Deeper PDF-format support, **re-scoped out of v0.7.0** by the owner so the image work (v0.8.0) landed
first. **No new dependency** — both are native PyMuPDF.

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M32** Encrypted / password PDFs | On open, detect `doc.needs_pass`, prompt, `doc.authenticate(pw)` before registering the source; the source is then held **decrypted in memory** (re-serialised with `PDF_ENCRYPT_NONE`) so render / materialise / export never need the password and the output stays unencrypted (re-encryption deferred). Cancelling raises `PasswordRequired`; the provider is stored so Revert of an encrypted original re-prompts. | WSL + WSLg | Open a password-protected PDF after entering its password |
| **M33** Internal link remap **+ navigation** | `klarpdf/model/links_remap.py`: at materialize, rebuild internal **GoTo *and* named-destination** links against the new page order (both resolve to a target page, re-emitted as remapped GoTo — `insert_pdf` drops named dests entirely, and cross-run GoTo links). Plus **in-viewer navigation** (`viewer/links.py`): clicking an internal link jumps to its target page (pointing-hand on hover), resolved via the same map so it follows reorder/delete live. | WSL (model+tests) + WSLg | Reordered/deleted pages keep internal links working; clicking a link in the viewer jumps to its target |
| **M38** Verify + release | Headless suite green (369 tests); Windows validation; tag **v0.9.0**. | **Win** | Matrix green → v0.9.0 released |

## MCP / Agent Bridge roadmap (planned; version assigned at tag time)

A new surface, not a GUI feature: expose KlarPDF's PDF engine to **Claude Code, Claude Desktop, and
other agentic clients** as a local **MCP (Model Context Protocol) server**. Same offline/native/
audited principles as the app; the GUI is untouched.

**Revised 2026-08-12** after a premise review: this roadmap was written 2026-06-22, and five releases
shipped past the version it reserved. What changed and the eight decisions the review settled are in
§Revision note (2026-08-12) at the end of this section.

### Why (the niche)

Claude already *reads* PDFs natively (the API renders each page to image + extracts text), so an MCP
server is **not** about "helping Claude read." Native reading has two gaps this fills — and they are
no longer equally weighted:

1. **Transform, not just read — the reason to build, and now the only load-bearing one.** Native PDF
   support is read-only ingestion. KlarPDF is an *editor* — redaction, splice/split/merge, lossless
   save, form fill, encrypted-PDF handling. The differentiator is specifically the
   **destructive-redaction + cross-engine leak-verified** half. The 2026-08 sweep found the niche
   still open and the stakes higher: `redact_mcp`, the one PDF-redaction MCP server in the wild,
   applies a **visual overlay only with no leak verification** — precisely the false-secure output
   M41's `fitz` + Poppler cross-check exists to catch. The gap did not close; it grew a hazard.
2. **Keep big PDFs out of context — real, but materially weaker than when written.** Native reading
   loads the whole file into the context window (bounded at ~600 pages / 32 MB on the API, 100 pages
   on 200k-context models; 20 pages per `Read` in Claude Code). Tool-mediated access still lets an
   agent pull only the pages it needs, and cheap `get_info` / `get_outline` / `search` routing still
   beats loading an 800-page file whole. But **1M-token context is now standard on current models,
   and prompt caching bills a repeated prefix at ~10%**, so the "re-sends it every turn" cost
   argument no longer carries a milestone by itself. This is a supporting benefit, not a
   justification — **do not revive the bridge on this premise alone.**

The leverage: the headless `klarpdf/model/` core already implements every transform. The only new logic is a
thin set of read-only query helpers + the MCP tool layer — and **less of that than first scoped**:
`klarpdf/model/page_text.py` (M78.7) landed after this roadmap was written, is Qt-free, lives in `klarpdf/model/`,
and already produces the page + snippet output `search` needs.

### Architecture (a quarantined seam, like `packaging/`)

- **New `klarpdf/mcp_bridge/` package**, isolated the same way OS code is quarantined behind
  `platform_integration.py` — never inlined into `app.py`/`launcher.py`. The GUI build does not
  import it.
- **It cannot be called `mcp/`** (found at M39, correcting this section's original name). The
  official SDK *is* the top-level module `mcp`, and the repo root is on `sys.path` (pytest's
  `pythonpath = ["."]`, plus a script's own directory), so a local `mcp/` package shadows the
  installed one and `from mcp.server import MCPServer` dies with `ModuleNotFoundError: No module
  named 'mcp.server'` — measured before a line of the server was written, not theorised. The name
  in the milestone table below is `klarpdf/mcp_bridge/` for the same reason.
- **Reuses the GUI-free core only.** Imports `model/virtual_document`, `edit_engine`, `export`,
  `page_edits`, `links_remap`, `toc_remap` — **not** the app's undo commands (`edit_commands.py`,
  which imports `QUndoCommand`, and since M147 lives outside `klarpdf/`); the server calls
  `VirtualDocument` ops directly, so it runs **without PySide6/Qt**. M39 confirms no Qt import
  reaches the server path.
- **Same repo** (decided 2026-08-12). `klarpdf/mcp_bridge/` sits beside `klarpdf/model/` and imports it directly — the same
  quarantined-seam pattern as `packaging/`, not a repo boundary. A sibling repo was rejected because
  the bridge's whole leverage is that `klarpdf/model/` already implements every transform: splitting would
  put a versioning seam through that leverage, and `klarpdf/model/` is not a publishable library today
  (`pyproject.toml` is `0.0.0`, no distribution build). Same-repo also keeps the round-trip tests on
  the existing `A.pdf`/`B.pdf` fixtures and `test_materialize.py` invariants for free.
- **Official `mcp` SDK, 2.x line, stdio transport** (decided 2026-08-12). Not standalone FastMCP:
  that package *wraps* the official SDK (it pins `mcp>=1.24,<2`) and adds ~25 more packages —
  `authlib`, `joserfc`, `websockets`, `watchfiles`, `openapi-pydantic`, `pyperclip` — so it can only
  widen the audit surface, never narrow it. 2.x rather than 1.x because **1.x is maintenance-mode,
  security-fixes-only** on a `v1.x` branch, and because a 2.x server answers *both* the 2026-07-28
  spec's `server/discover` and the legacy `initialize` handshake — the broader client compatibility,
  not the narrower. Note `pip install mcp` now resolves to 2.x, and the SDK renamed its server class
  `FastMCP` → `MCPServer`; the class name in this section's earlier drafts was the 1.x one.
- **Streamable HTTP is an explicit non-goal**, not a deferral (decided 2026-08-12). stdio is the
  universal denominator — Claude Code, Claude Desktop, Codex CLI, Grok Build, Cursor and VS Code all
  take a stdio server as `command` + `args`. HTTP would mean a listening port on a product whose
  selling point is that it never touches the network, breaking the no-outbound-connections
  verification item below; it also attaches AGPL §13 remote-network-interaction obligations that a
  stdio subprocess never triggers. Revisit only on a concrete request.
- **A tool description is a budget, not a place** (M105). Clients truncate it: Claude Code cuts at
  **2,048** characters and appends `… [truncated]` with no error, so a description that outgrows the
  transport loses its tail in silence — and the tail is where the caveats collect. `redact_text`
  lost 69% of 6,573 characters that way. Two rules follow, and they are standing design rather
  than M105 cleanup. **(a)** Every description stays under the **1,900**-character budget that
  `tests/test_mcp_docs.py` enforces over the *live* server, so a tool added later is covered the day
  it is registered; the same budget covers the `instructions` block, which goes through the same
  constant and is **not** the uncapped escape hatch it appears to be. **(b)** When a tool has more
  to say than that, the overflow goes to the resource `klarpdf://docs/{tool}` — a resource read is
  capped at 100,000 characters, not 2,048 — and **never** into a raised budget. Split by *kind*, not
  by importance: what a caller must know **before** calling (that it destroys content, what a flag
  changes, the traps) stays in the description; what they need to interpret the **reply** moves to
  the resource. The resource serves the live registered description plus a disjoint appendix in
  `klarpdf/mcp_bridge/docs.py`, never a second copy, so the two cannot drift. Only `redact_text` and
  `search` need one today; the other fifteen sit comfortably inside the budget, and a resource for
  them would be ceremony.
- **New headless helpers** (the only genuinely new code) — landed in `klarpdf/mcp_bridge/queries.py`:
  `extract_text(path, pages)`, `search(path, query)`, `render_page(path, page, dpi)`,
  `document_info(path)`, `outline(path)`, `form_fields(path)`. `viewer/search.py` is Qt-bound, but
  **`klarpdf/model/page_text.py` is not** and already supplies the per-hit snippet machinery, so `search`
  wraps `page.search_for` + `PageText` rather than being reimplemented from scratch. They hold no
  SDK import, so the PDF behaviour is tested by calling functions rather than by driving a protocol,
  and `server.py` stays a schema adapter.
- **Page numbers are 1-based at the tool boundary** (M39), matching the viewer's page counter and a
  PDF outline's own targets; `klarpdf/model/` stays 0-based and the conversion happens only in
  `klarpdf/mcp_bridge/queries.py`. Out-of-range page numbers are an error, never a silent clamp.
- **List-returning tools return a `{count, items}` dict**, not a bare list (M39). The SDK serialises
  a bare `list` into one content block *per element*, so a 500-hit search would arrive as 500
  blocks; the wrapper makes it one and hands the caller the total before the items — which is the
  number that decides whether to narrow the query, and where M43's size caps report a truncation.

### Tool surface

| Tool | Maps to | Category |
|---|---|---|
| `get_info(path)` → pages, size, encrypted, has-text-layer | new helper + `needs_pass` | query/route |
| `get_outline(path)` | `get_toc` / `remapped_toc` | query/route |
| `search(path, query)` → pages + snippets | new headless helper | query/route |
| `extract_text(path, pages)` | new helper (`page.get_text`) | query |
| `render_page(path, page, dpi)` → image block | `export_page_images` (single page) | query |
| `get_form_fields(path)` | `page_edits.read_form_fields` | query |
| `get_annotations(path, pages)` → every mark, ours and foreign (M101) | raw `page.annots()` + `page_edits.parse_annotation` for the `editable` flag | query |
| `extract_pages(path, pages, out)` | `export.export_selected_pages` (M51) | transform |
| `split` / `merge` / `reorder` / `delete_pages` / `rotate` → out path | `VirtualDocument` ops + `materialize` | transform |
| `fill_form(path, values, out)` | `set_field_value` + `materialize` | transform |
| `flatten(path, out)` | `export.export_flattened_pdf` | transform |
| `export_images(path, pages, dpi, out_dir)` | `export.export_page_images` | transform |
| `annotate(path, marks, out)` → highlights / underlines / strike-throughs + notes (M101) | `page_edits.merge_markup` + `apply_annotations`; palette from `klarpdf/model/markup_palette.py` | transform |
| `redact_regions` / `redact_text(path, …, out)` | `Redaction` + `apply_redactions` + cross-engine leak verify | secure |
| (encrypted input) | `password` param → `from_path(password_provider=…)` | all |

**Two corrections from the first hands-on session with a real client (2026-08-12).** Both were
shipped defects that every test passed over, and both are worth recording because they share a
cause: the tests exercised the code *from inside a working checkout*, which is the one place the
bugs cannot happen.

- **The tool table had no "extract these pages" row, and that is a hole, not a simplification.**
  Asked to pull pages out of a PDF, an agent with the sixteen-tool surface found nothing named for
  it and **shelled out to `pdfunite`** instead. `split(ranges=["10-20"])` produces exactly the right
  file, but it is named for cutting a document up and chooses the output filename itself, so it does
  not read as the answer. The primitive already existed unexposed —
  `klarpdf/model/export.py:export_selected_pages`, the app's own Export ▸ Selected Pages as PDF (M51) — so
  `extract_pages` is a thin wrapper over tested code. **A tool the model cannot find is a tool that
  does not exist**, which is the same lesson `--read-only` encodes from the other direction.
- **`python -m mcp_bridge` cannot be the documented command, and `pyproject.toml` must declare
  dependencies.** `-m` puts the *working directory* on `sys.path`, never the interpreter's location,
  and a client launches its server from its own directory — so the checked-in `.mcp.json` and the
  README both failed with `No module named mcp_bridge` the first time anyone pointed a client at a
  folder of PDFs. Worse, the documented fallback (`pipx install .`) was broken too: M42 left
  `dependencies` out of `pyproject.toml` on the reasoning that the project installs from a lock, so
  the built metadata carried **zero `Requires-Dist`** and produced a `klarpdf-mcp` that died on
  `import mcp`. That reasoning was right for the app and wrong for a package whose entire purpose is
  a console script. Floors now live in `pyproject.toml`, pins stay in `requirements-mcp.txt`, and
  `klarpdf-mcp` is the command everywhere. `tests/test_mcp_packaging.py` builds the real metadata
  through the backend and asserts `Requires-Dist` is populated — the check that was missing.

### Safety model (agent-driven = untrusted caller)

- **Never overwrite the source.** Write tools require an explicit *new* output path; in-place save is
  not exposed. Implemented at M40 in `klarpdf/mcp_bridge/transforms.py:_resolve_out`, and the identity test
  goes through `klarpdf/util/paths.py:normalize_path` — the project's single "are these the same file"
  chokepoint — so a symlink, a `..` segment or a case-different spelling on Windows cannot smuggle
  the input back in as the destination. A string compare would miss all three; there are tests for
  each. Every write tool's schema *requires* `out`/`out_dir`, so no argument shape means "in place".
- **No silent clobbering of anything else either** (added at M40). An output that already exists is
  refused unless the caller passes `overwrite=true`. The roadmap only required protecting the
  *source*; this is the same argument applied consistently, and an agent that meant it says so in
  one word.
- **Writes go to a sibling temp and are renamed into place**, so a failed transform leaves nothing
  half-written for the caller to read back as a corrupt PDF. The rename is M38.5's
  `klarpdf.util.atomic.atomic_replace`, the same helper the GUI's Save and Export use — the
  antivirus-holds-the-temp race applies identically here, and the two write paths deliberately do
  not diverge on it.
- **A mistake is an error, never a quiet partial success.** `fill_form` rejects an unknown field
  name rather than writing nothing and reporting success; `reorder` demands a full permutation so it
  cannot silently drop a page; `delete_pages` refuses to empty a document; an out-of-range page
  number is refused rather than clamped.
- **Redaction stays a point of no return** — destructive `apply_redactions` + the existing
  `fitz`+Poppler leak verification before the tool reports success. Landed at M41 in
  `klarpdf/mcp_bridge/redaction.py`, with two consequences worth stating because they are what separate this
  from `redact_mcp`'s visual overlay:
  - **A failed verification deletes the output and raises.** A caller must never be handed a path to
    a file that looks redacted and is not, so the tool's success is a claim about a file that was
    re-read, not about a function that returned.
  - **Verification counts occurrences, it does not test for presence** — and the difference is not
    pedantry, it was a real bug caught by a test. Redacting the standalone "Smith" out of "Smith and
    Smithsonian" leaves a page that still *contains* the substring "Smith"; a presence check calls
    that a leak and destroys a good output. The rule is
    `occurrences_after <= occurrences_before − boxes_that_covered_it`, per page and per engine,
    which is exact in both directions: unrelated surviving text cannot trip it, and it still catches
    two boxes covering the same word when only one was removed — which a presence check would pass.
  - **The report says which engines actually ran.** Poppler is not installed everywhere, and
    `cross_engine_verified: false` plus an explicit note is the honest answer; the tool never claims
    a cross-engine check it did not perform (§Honesty principle). An **encrypted** output is
    unlocked for verification with the same password — a check that silently cannot read the file
    must not be mistaken for a check that found nothing.
- **Path scoping.** A configurable allowlist of roots the server may read/write (an MCP tool runs with
  the host's file access); refuse paths outside it. Landed at M43 as `--allow-root DIR` (repeatable)
  / `KLARPDF_MCP_ALLOW_ROOTS`, in `klarpdf/mcp_bridge/config.py:PathPolicy`. **Unrestricted by default, and
  that is the honest default rather than a lax one:** a stdio server is a subprocess the user
  launched, running as them, with exactly the file access they already have — a client that can
  start it can already read their disk, so defaulting to some arbitrary root would buy no security
  and break every reasonable call. The allowlist is for wanting a *smaller* blast radius than one's
  own account, which only the user can define. Containment resolves through `normalize_path` before
  comparing, so `..`, a symlink planted inside a root, and a shared-prefix sibling
  (`/data/docs-private` vs `/data/docs`) are all refused; outputs are checked on their parent, so a
  new file inside a root still works. Enforced in the tool layer, not just the policy object —
  tested, because a policy nothing calls is decoration.
- **Return-size caps** so a mis-call (`extract_text` on 800 pages) degrades gracefully, not a context
  blow-out. M43: 200k characters, 500 search hits, 8 MiB per rendered image. Text and hits are
  **truncated with `truncated: true`** plus the real total and a note on how to narrow — the partial
  answer is usually still useful, and silence about the cut is the only unacceptable outcome. An
  oversized render is an *error* instead, because half an image is not a partial answer; the message
  names the fix (lower `dpi`).
- **Read-only mode** — a launch flag exposing only query tools, for users who want zero write risk.
  M43: `--read-only` / `KLARPDF_MCP_READ_ONLY`. It **withholds** the write tools rather than
  refusing them when called — a tool the model can see is a tool it will try, and a server that
  lists sixteen and errors on ten is worse than one that lists six. Conditional registration is why
  the tools are built by `create_server(config)` instead of at import time, and the server's
  instructions say it is read-only so the model is not left hunting for tools it was told about.
  **Writes are on by default** (decided 2026-08-12): no write tool can destroy data by construction —
  every one requires an explicit *new* output path, in-place save is never exposed, and "source left
  byte-identical" is a verification-matrix item — so the flag is the cautious opt-*out*, not the
  default. A read-only-first release was rejected for shipping the half of the justification that
  weakened (§Why, point 2) while withholding the half that did not.

### Dependencies & packaging (keep the shipped app's audit surface tiny)

- **Separate optional component** (decided 2026-08-12) — `klarpdf-setup-x64.exe` is untouched: same
  size, same hashed offline lock, same clean-machine install test. Bundling was rejected on a measured
  footprint plus an audience mismatch (the installer serves Windows users replacing Preview; the
  bridge serves developers running agentic clients).
- The `mcp` SDK goes in a **separate optional lock** (`requirements-mcp.in` → `requirements-mcp.txt`),
  same `pip-compile` discipline, **not** in the GUI ship lock. Budget for it honestly: `mcp` 2.0.0
  carries **14 direct runtime deps** and the HTTP server stack is non-optional even for stdio-only use
  — `starlette`, `uvicorn`, `sse-starlette`, `python-multipart`, `pyjwt[crypto]` (→ `cryptography`),
  plus `opentelemetry-api`, `jsonschema`, `pydantic`, `httpx2`, and an exact pin on
  `mcp-types==2.0.0` that constrains lock resolution. Roughly 25–35 wheels once transitive.
  **Measured at M39: 28 new packages**, and the estimate held — every named dependency above turned
  up, `httpx2`/`truststore` included.
- **The SDK is also a *dev* dependency, added to `requirements-dev.in` at M39** — a different
  artifact for a different audience, not a duplicate of the lock above. `tests/test_mcp_*.py` are
  part of the headless suite and CI installs `requirements-dev.txt` and nothing else, so without it
  those files fail at **collection** and the required `pytest` check goes red. `requirements.in` —
  and therefore the hashed `requirements-win.txt` the installer bundles — is untouched, which is
  what keeps "the shipped app's audit surface is unchanged" true. Recompiling that lock on Linux
  exposed a latent trap worth knowing: pytest's win32-only `colorama` had been present only because
  the committed lock happened to be compiled on Windows, and a Linux compile silently dropped it.
  It is now a direct, **unmarkered** entry in `requirements-dev.in`; a marker does not work, because
  pip-tools evaluates markers against the compiling interpreter and drops the false ones.
- **`audit.yml` gains a fourth `pip-audit` step** for `requirements-mcp.txt`. Its `pull_request`
  trigger already globs `requirements*.txt`, so the new lock joins the **weekly** sweep automatically
  — meaning `cryptography` and `starlette`, both far more CVE-active than `pypdf`, come under the
  same watch that went red on `pypdf` in 2026-08. `audit` is not a required check, so that is signal,
  not a merge blocker. Mirror the step in `tools/audit-deps.ps1`. **Scope caveat:** this audits the
  pip/pipx install path only — the `.mcpb` resolves its own dependencies at install time (see the
  `server.type = "uv"` bullet below), so it is outside the lock's guarantee.
- `klarpdf-mcp` console entry point — the project's **first** `[project.scripts]` (there are none
  today). **No general-purpose CLI** (decided 2026-08-12): it would be a second permanent entry point
  for an audience that has not asked, its testing value is already covered by the headless suite, and
  it is *not* a prerequisite for a Skill — a Skill can wrap the MCP tools directly, which also keeps
  Claude Desktop in reach where a bash-invoked CLI cannot. Deferred, not forbidden: because the tools
  are thin adapters over `klarpdf/model/`, a CLI stays cheap to add if a real request arrives.
- **The server is platform-independent — unlike the app — and the lock must not undo that.** `klarpdf/model/`
  contains no platform-specific code (no `sys.platform` / `os.name` / `win32` / `winreg`), the
  headless suite already proves it on Linux every CI run, PyMuPDF ships manylinux/macOS/Windows
  wheels, and `mcp`'s only Windows dependency (`pywin32`) is platform-marked. Nothing the server
  imports touches `platform_integration.py`, `packaging/`, or the ship lock. **So
  `requirements-mcp.txt` is a cross-platform `==`-pinned lock without hashes** (the
  `requirements-dev.txt` pattern), *not* a `--generate-hashes` `win_amd64` lock: hashed locks are
  platform-specific and `--require-hashes` fails on Linux by design (see CLAUDE.md §Gotchas). Compile
  it from `requirements-mcp.in` with plain `pip-compile`. Getting this wrong makes the bridge
  accidentally Windows-only, which defeats its whole audience.
- Docs: a `.mcp.json` snippet for Claude Code and a `claude_desktop_config.json` block for Desktop,
  **plus a one-click Desktop Extension (`.mcpb`)** — committed at the 2026-08-12 review, no longer
  optional, so Desktop users are not left hand-editing JSON. The format is current and
  Anthropic-maintained (renamed from `.dxt` in 2025-09), and covers **macOS + Windows**, which is
  where Claude Desktop ships.
- **The `.mcpb` is `server.type = "uv"`** (decided 2026-08-12, after the type's consequences were
  spelled out). MCPB manifests take four server types — `node`, `python`, `binary`, `uv` — and the
  guide states plainly that a bundle **cannot portably vendor compiled dependencies**; we have two,
  PyMuPDF (C) and pydantic (Rust, via the `mcp` SDK). The `uv` type has the host resolve Python *and*
  dependencies, so **one bundle serves macOS and Windows** with no user Python install. The bundle
  therefore ships **source plus a `pyproject.toml` only**: the spec requires `pyproject.toml` with
  dependencies and says the bundle **"must NOT include `server/lib/` or `server/venv/`"** — vendoring
  is forbidden by the format for this type, not declined by us.
- **CORRECTION (M42): there is no `uv` server type.** Measured against the real tool rather than the
  guide: `mcpb` **2.1.2** validates `server.type` against exactly `python | node | binary`, and does
  so on every manifest version it accepts (`0.1`/`0.2`/`0.3`; `1.0` and above are rejected outright
  as "unrecognized or unsupported"). The decision above is therefore unbuildable as written, and the
  bullet is kept rather than deleted because the *reasoning* in it is still correct and still what
  the implementation does.
  **What shipped instead:** the manifest declares the supported **`python`** type while its
  `mcp_config.command` **is `uv`** (`uv run --directory ${__dirname}/server klarpdf-mcp`). That
  preserves every property the original decision was made for — resolve-at-install, no vendoring,
  one file for macOS + Windows + Linux — and changes exactly one thing: **`uv` must be on the user's
  PATH**, because the host is no longer the component supplying it. That prerequisite is stated in
  `klarpdf/mcp_bridge/README.md` rather than left to be discovered. `tests/test_mcp_packaging.py` pins the
  command, so a later edit back to a bare interpreter cannot quietly reintroduce the need for
  vendored dependencies the format forbids.
  **How it is built:** `packaging/mcp/mcpb/build_mcpb.py` assembles `server/` from the checkout
  (the whole `klarpdf/` package: `mcp_bridge/`, `model/`, `util/`, `version.py`, none of which
  imports Qt since M147), generates the bundle's `pyproject.toml` **from `requirements-mcp.txt** so the
  two cannot drift, and runs `mcpb pack`. The `.mcpb` is a **release artifact in `dist/`, not a
  committed file** — same treatment as the installer; what is committed is the script, the manifest
  and the generated `pyproject.toml`, so the inputs are reviewable. Measured output: 95 KiB,
  25 files, no `server/lib/`, no `server/venv/`, no PySide6.
- **Consequences of that choice, accepted knowingly — and to be stated in the README, not
  discovered.** The `.mcpb` path **installs online**: at install/first run the host runs `uv`, which
  fetches wheels from PyPI matching that machine's OS, arch and Python. Three follow-ons: (a) it
  needs network, unlike everything else this project ships; (b) dependencies resolve from
  `pyproject.toml`, so **pin with `==`**, not floors, to hold drift down — two users installing a
  month apart should not get different transitive sets; (c) **the audited lock is not what the
  `.mcpb` installs**, so `requirements-mcp.txt` and its `pip-audit` step cover the pip/pipx path and
  *not* the Desktop path. Do not describe the audit as covering "the bridge" without that
  qualification. **M42 must test whether the host honours a `uv.lock`** (and hash verification): the
  spec is silent on both, and if it does, most of gap (c) closes.
  **ANSWERED 2026-08-27 in M129 — the host honours a committed `uv.lock`,** measured with a lock
  doctored to disagree with a fresh resolve. The bundle now ships one, so gap (c) narrows to the
  platform-specific transitive packages a marker-free `requirements-mcp.txt` cannot name; see
  §M129. The M42-era reasoning below is kept because it explains why the question took two
  milestones to become answerable.
  **M42 outcome on the `uv.lock` question: still open, and it is now a different question.** With no
  `uv` server type (see the correction above), there is no host-managed `uv` invocation to honour a
  lock — the bundle runs `uv run --directory server`, and whether *that* consults a committed
  `uv.lock` is answerable only by installing the bundle in Claude Desktop, which is a Windows/macOS
  step. Carried to M44's "lives with a client" item. Gap (c) is therefore **not** closed, and the
  README says so plainly rather than implying the audit covers the Desktop path. What M42 *did*
  narrow: the bundle's `pyproject.toml` is generated from `requirements-mcp.txt` and a test fails if
  it drifts, so the set of versions the bundle asks for is the audited set even though the install
  that fetches them is not itself audited.
- **`binary` was considered and rejected** (2026-08-12) — a PyInstaller-frozen `klarpdf-mcp` shipped
  as a compiled executable would have been offline, hash-verifiable, Python-free on the user's
  machine, and reused `packaging/app/klarpdf.spec`. It loses on reach: it is platform-specific, and the
  bridge — unlike the app — is cross-platform and worth offering to Desktop users who do not run
  KlarPDF at all. Revisit if the online-install requirement ever proves unacceptable. **`python` is
  the worst option for us** and is not on the table: vendored and offline, but it requires a Python
  already installed, which frozen-exe users precisely do not have.

### Milestones (one PR each; ⭐ = keystone, GUI-free, fully headless-testable)

| Milestone | Where | Done when |
|---|---|---|
| **M39 ⭐** MCP scaffold + read-only core | WSL | `klarpdf/mcp_bridge/` stdio server on the official `mcp` 2.x SDK (`MCPServer`); `get_info`/`get_outline`/`search`/`extract_text`/`render_page`/`get_form_fields` wired to headless helpers (`search` reuses `klarpdf/model/page_text.py`, not a fresh implementation); no PySide6 import on the server path — assert it in a test, don't just observe it; headless tests green |
| **M40** Transform tools | WSL | `split`/`merge`/`reorder`/`delete_pages`/`rotate`/`fill_form`/`flatten`/`export_images` → explicit out path; never overwrites source; lossless (OCR/TOC/forms survive); headless tests. Landed in `klarpdf/mcp_bridge/transforms.py`; `split` reuses `klarpdf/util/page_range.py` so an agent types the same range syntax a person does |
| **M41** Redaction + encrypted | WSL | `redact_regions`/`redact_text` (destructive + leak-verified); headless cross-engine leak assertion. **Encrypted input landed early, at M39**: every read tool already takes `password`, because `open_document` had to handle an encrypted source anyway — the model's provider re-prompts on a wrong password and there is no user behind an agent call, so the adapter must decline the retry or the server hangs. Tested there; M41 extends it to the write tools |
| **M42** Dependency lock + packaging | WSL + Windows | `requirements-mcp.{in,txt}` — **cross-platform, `==`-pinned, unhashed**, GUI lock untouched; fourth `pip-audit` step in `audit.yml` + `tools/audit-deps.ps1`; `klarpdf-mcp` entry point; `.mcp.json` + Desktop config docs; **`.mcpb` bundle (committed, `server.type = "uv"`)** |
| **M43** Hardening + docs | WSL | path allowlist, return-size caps, `--read-only` flag (opt-out; writes are on by default), error handling; README usage + example agent workflows |
| **M44** Verify + release | Windows | verification matrix green (below) → tag (**version assigned at tag time**; v0.11.0 is long gone) → GitHub Release |

### Verification (adds to the existing matrix)

- **Tool round-trips (headless pytest):** each transform tool over the `A.pdf`/`B.pdf` fixtures
  asserts the same invariants as `test_materialize.py` — OCR text on moved pages, TOC remapped to new
  indices, both form fields preserved, dup-name handled.
- **Redaction leak-free:** `redact_*` output passes `fitz` + Poppler `pdftotext` cross-engine — the
  secret is gone from the written file.
- **No network:** the server makes no outbound connections and **binds no port** (same audit as the
  app; stdio only — see the HTTP non-goal above).
- **No Qt on the server path:** a test asserts `PySide6` is absent from `sys.modules` after the
  server and every tool have been exercised. Observation is not verification — this is the invariant
  the whole "reuses the GUI-free core" claim rests on.
- **Cross-platform:** the server installs from `requirements-mcp.txt` and passes its tool round-trips
  on **Linux and Windows** — CI already runs the headless suite on `ubuntu-latest`, so this is mostly
  a matter of not regressing it. Unlike the app, the bridge is not Windows-first; a lock that only
  resolves on `win_amd64` is a defect, not a scoping choice.
- **Lives with a client:** the server registers and the tools work from **Claude Code** (`.mcp.json`)
  and **Claude Desktop** (config *and* one-click `.mcpb`). A third-party client — **Codex CLI** via
  `~/.codex/config.toml`, or **Grok Build** — is a spot check, not a gate: stdio is the universal
  denominator, so a failure there is a bug worth knowing about before strangers find it.
- **Source untouched:** every write tool leaves the input file byte-identical.

### Revision note (2026-08-12)

The roadmap was written 2026-06-22 and sat unexecuted while R1–R6 shipped. A premise review before
scheduling it found the **build case intact and sharper**, and the **plan around it stale**.

**Premises that changed.** *Dead:* the v0.11.0 reservation (v0.12.0 → v0.17.1 shipped past it) and
the "assuming the bridge ships first" sequencing note below. *Weakened:* the context-economics
argument (§Why, point 2). *Strengthened:* the transform/redaction argument, by the discovery of a
visual-overlay-only redaction MCP server. *Shrunk:* M39, because `klarpdf/model/page_text.py` landed in the
interim. *Ambiguous, now resolved:* "FastMCP" names two different things since the official SDK
renamed its class and standalone FastMCP 3.x went its own way. *Held up well:* stdio-first, the
`.mcpb` bet, and the Qt-free-core architecture — `klarpdf/model/` is still Qt-free apart from the already
excluded `edit_commands.py`.

**One question the original plan could not have asked:** Agent Skills. A Skill supplies *procedure*,
not *access*, so it must invoke something — bash + a CLI, or MCP tools. Since a fresh Claude Desktop
has no standing filesystem or exec access, the Skill-plus-CLI path is a **strict subset** of what a
stdio MCP server already reaches, and it is the one that loses Desktop. MCP stays the primary
surface. A Skill layered *over* the MCP tools remains attractive after M41 — one markdown file, zero
dependencies — to encode the procedure the tools can't ("search first, present hits for review,
apply, then confirm the leak check passed").

**Decisions settled (all eight, owner-confirmed 2026-08-12):** ship the bridge next rather than
after 1.0, the tracks being independent; fix the `os.replace` flake first as a one-PR prerequisite
and leave `test_single_instance` alone (no reproduction, no fix plan — and neither flake has ever
failed the required `ubuntu-latest` check in 200 recorded runs); official `mcp` 2.x; no CLI;
separate component **plus** `.mcpb`; full tool surface with writes enabled; stdio only with HTTP a
non-goal; this repo. Each is recorded inline above next to the design it governs, so this note is
the index, not the source of truth.

**Two constraints the review surfaced only when the packaging decision was pressed on portability**,
both now written into §Dependencies & packaging because both are the kind that are cheap to honour
up front and expensive to discover at M42: the bridge is **platform-independent while the app is
not**, so its lock must be cross-platform and unhashed or `--require-hashes` silently makes the
server Windows-only; and the `.mcpb` must use `server.type = "uv"`, because MCPB cannot portably
vendor compiled dependencies and we have two (PyMuPDF, pydantic) — which in turn makes the bundle an
**online-installing** path, the one deliberate break from the app's vendored-offline discipline.

### M94 *(unplanned)* — what the bridge would not tell a caller (TC-002, owner-reported 2026-08-13)

Moved to [`docs/milestones/M94.md`](docs/milestones/M94.md).

### M95 *(unplanned)* — a verification that can fail (TC-003, owner-reported 2026-08-15)

Moved to [`docs/milestones/M95.md`](docs/milestones/M95.md).

## GUI feature roadmap — the post-v0.10 tranche R1–R6 (planned; M45–M79)

Owner-decided 2026-07-18 after a feature-exploration session (23 features approved, radio-button
groups rejected — see §Future enhancements); **R6 added 2026-07-22** after a comparison session
against macOS Preview's UI (see its section for the decisions and the decided-against list). Same
discipline as every prior tranche: **one PR per
milestone**, `PROGRESS.md` tracks state, ⭐ marks a keystone (GUI-free core, fully
headless-testable). **Still zero new dependencies** — every item is native PyMuPDF or Qt (already
inside the vendored wheels), so `requirements.in` and the hashed offline lock stay exactly as
shipped. (Releases are numbered **R1–R5** — "R" for release — because **G#** is already taken by the
Public-Release Readiness track's G1–G8 milestones; R# also matches the decision report's bundle names.)

**Sequencing vs the MCP bridge (settled 2026-08-12):** the bridge did *not* ship first. This tranche
did — R1–R6 shipped as v0.12.0 → v0.16.2, and v0.17.x followed, so the v0.11.0 reservation the
roadmap above once held is spent and the "provisionally v0.12.0 → v0.16.0" hedge below resolved
itself. The bridge is now scheduled **next**, after a one-PR `os.replace` flake fix, with its version
assigned at tag time. Kept here as the record of a plan that was overtaken by events — the precedent
being the v0.7.0 → v0.9.0 re-scope.

### Design budgets (binding for every milestone in this tranche)

- **UI budget.** Menus are the complete catalog (inapplicable items *disable* — inside menus, hiding
  breaks spatial memory); the toolbar is **modes-only, ~10 slots**, held by grouped split-buttons
  (Markup ▾ · Draw ▾ · Stamp ▾ · Redact ▾); one-shot commands are menu + dialog, never toolbar.
  Net chrome for the whole tranche: **one new top-level menu (Tools)** plus a few File/Edit/View
  entries. Persistent panels appear **only when applicable** (owner rule — e.g. the Outline tab).
  The bar to hold: *the app at rest — a plain scanned PDF open — looks identical to today.*
- **Lightness budget.** Lazy dialog construction + deferred imports (already the house style);
  nothing new runs on the open-document path unless the document uses it (guard the ~150 ms /
  320-page open with a regression test); installer weight unchanged (zero new deps). **One
  edition** — a "lite" build is rejected: every feature is pure Python on libraries the viewer core
  already loads, so a split buys ~nothing and costs a second packaging/QA pipeline forever.
- **Honesty principle.** Every feature states its guarantee boundary in UI + docs: crop *hides*
  (redact removes); permission flags are advisory; metadata removal clears **both** stores (Info
  dict + XMP); search-&-redact covers the text layer only; an image signature is ink-equivalent,
  not cryptographic; baked-at-save marks are a point of no return, round-trip marks stay editable.

### R1 (prov. v0.12.0) — "Navigate & Polish"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M45** ⭐ Outline sidebar + Go to Page | An **Outline** tab beside Pages in the existing sidebar. **No TOC → no tab and no tab bar** (owner rule): the switcher only materialises for documents with an outline; TOC-less docs keep today's plain Pages panel pixel-identical. Shows the **live** `remapped_toc()` tree during editing (what you see is what Save writes); highlights the visible page's entry on scroll; click → `goto_page`. Plus **Go to Page…** (Ctrl+G). | WSL (glue) + WSLg | TOC'd doc: tree navigates, tracks scroll, reflects edits live; TOC-less doc: sidebar unchanged; Ctrl+G jumps |
| **M46** Context menus everywhere | Grow `pdf_view.contextMenuEvent` (today: annotation-Remove only) by hit-test state — selection → Copy / Highlight / Redact Selection; link → go to target / copy target; empty page → paste object / fit modes / rotate / Go to Page; sidebar adds rotate + extract. Mostly routes existing QActions; later milestones hang their situational verbs here. | WSLg | Right-click offers the state-appropriate verbs on every surface |
| **M47** Search-all results panel | Doc-wide search results list (page + context snippet, click-to-jump) alongside the existing next/prev flow. Builds the reviewable-hit-list UI that M64 (search & redact) reuses with checkboxes. | WSLg | A search lists every hit with context; click jumps to it |
| **M48** Crop pages | `crop_override` riding `PageRef` exactly like `rotation_override` (absolute rect or None; snapshots for undo; follows reorder). Drag a rect → apply to **this page / selected / all pages**; materialise via `set_cropbox`. **UI copy: "hidden, not removed — use Redact to remove permanently."** When CropBox ≠ MediaBox on open, offer adjust/reset (crop is structured geometry — cross-app editable, no author tag needed). Odd/even mirrored book-scan crops deferred. | WSL (model+tests) + WSLg | Crop applies at the chosen scope, undoes, survives save/reopen; reset restores full page |
| **M49** Night reading mode | Invert page pixmaps (view-only; independent of the followed OS theme). | WSLg | Toggle renders pages dark; file + print output unchanged |
| **M50** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

### R2 — "Document Hygiene"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M51** Page ops: extract + blank/duplicate | **Export ▸ Selected Pages as PDF…** (object-level copy via the materialise path, TOC/link remap included) + **Insert ▸ Blank Page / Duplicate Page** (`new_page` / `fullcopy_page`) on the undo stack. | WSL (model+tests) + WSLg | Extracted PDF carries text layer/forms/bookmarks; insert/duplicate undo cleanly |
| **M52** Reduce file size | **File ▸ Export ▸ Reduced Size PDF…** — the *lossy* tier only (normal saves already run `garbage=4, deflate, clean`): image recompression presets named by intent **showing their true values** ("Screen — 150 dpi, JPEG 75" / "Print — 300 dpi, JPEG 85") + a Custom mode exposing the two real knobs (target dpi, quality) + font subsetting. No synthetic "% compression" slider. Reports **actual** before → after sizes; overwriting the original goes through the overwrite guard with permanent-quality-loss wording. | WSL + WSLg | Reduced copy is smaller, renders acceptably at preset intent; original untouched by default |
| **M53** Properties + metadata | One dialog, three verbs: **view** (none exists today) · **edit** · **remove all**. Handles **both stores** — the Info dict *and* the XMP packet (viewers prefer XMP): edit keeps them consistent; remove clears both, or the strip is a false promise. | WSL (model+tests) + WSLg | Removed-metadata file shows clean in Acrobat-class viewers, not just KlarPDF |
| **M54** ⭐ Document encryption | One save-path capability, four verbs: **Set / Change / Remove Password** + carry-through on save (supersedes the old "re-encryption on save" deferral). AES-256 only, user password (real cryptography); optional advisory restriction flags with honest wording ("honored by most viewers; not cryptographically enforced"). Password held in memory only, never persisted; type-twice + unrecoverable-if-lost warning. NB: pypdf can't do AES without a dev-only `cryptography` extra — cross-engine verification is via independent PyMuPDF reopen (fixture pattern exists in `test_encrypted.py`). | WSL (model+tests) + WSLg | Save→reopen round-trips under password on both open paths; Remove requires the current password |
| **M55** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

### R3 — "Markup Tools"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M56** Underline & strikeout | Same text-quad path as Highlight; author-tagged; round-trip read-back. Joins the Markup ▾ split-button. | WSL (model+tests) + WSLg | Underline/strikeout bake, reopen editable, print/flatten correctly |
| **M57** ⭐ Pen & shapes model | `InkStroke` / `Line` / `Shape` descriptors beside `Highlight`; `add_ink_annot`, `add_line_annot` + `set_line_ends` (arrows), `add_rect_annot`, `add_circle_annot`; extend `apply_annotations` + `read_klarpdf_annotations` (style via `annot.colors`/`annot.border` — no DA parsing). Printing, flatten, and thumbnails inherit automatically via `apply_annotations`. | WSL (model+tests) | All four types bake, read back symmetric, survive save→reopen→save without drift |
| **M58** Pen & shapes tools | Draw interactions: pen path capture with live preview; shapes press-drag-release + Shift-constrain (square/circle/45°); move + delete (resize deferred). Toolbar stays in budget via **Draw ▾** split-button. Markup/redlining framing — not CAD editing, no measurement tools. | WSLg | Draw/move/delete each type; fixed-width ink (no pressure — PDF ink can't carry it) |
| **M59** Copy / paste objects | In-process object clipboard (descriptors are frozen value objects); paste with offset on same page, rect-clamp across page sizes; **cross-window free** (single process — same pattern as page paste). Focus-routed Ctrl+C/X/V (text vs pages vs object — the actual design work); copying a text box also sets `text/plain`. Applies to text boxes + R3 types; foreign annotations excluded until M68. | WSL + WSLg | Copy/paste objects within + across windows; keyboard routing unambiguous |
| **M59.5** Markup colour · width · fill | Retire the fixed redline-red/2 pt the markup + draw tools baked (M56/M58 deferred a picker). One sticky `MarkupStyle` (shared **stroke** colour · draw **width** · optional shape **fill**), edited via a single toolbar `MarkupStyleButton` (swatch face + preset/custom menu — one slot, toolbar stays in budget), stamped on the next underline/strikeout + pen/line/arrow/rect/ellipse. **Restyle in place**, mirroring the text-markup "apply to the current selection" rule: with a drawn object (ink/line/shape) selected, a picker change edits *that* mark (one `ReplaceAnnotationCommand`, undoable); selecting a mark loads its style into the picker (the `TextBoxStyle.from_textbox` twin), so a partial tweak leaves its other attributes alone. Text boxes keep their richer format bar. **No model or file-format change** — the descriptors already carry the style and round-trip; highlight (translucent yellow) + redaction (semantic black) stay out of the shared palette. Colour swatches opt out of theme re-tinting. | WSL (model wiring + tests) + WSLg | Pick a colour/width/fill; the next markup/draw mark uses it, a selected drawn object restyles in place, and it bakes + reopens unchanged |
| **M59.6** Multi-object selection | A third interaction mode, **Objects** (beside Select/Grab), for group work on drawn marks: drag empties a marquee that selects the marks it covers; **Ctrl-click** toggles one in/out; dragging a member moves the whole group; the M59.5 picker restyles the group; Delete removes it — each **one undo step** (macro). Selection grows from a single `(page, mark)` to a list (one page per group); the single-object seam (`selected_object`, clipboard copy/cut) still resolves for a lone selection. Copy/paste of a multi-selection is a later pass — **delivered in M59.12**. **Geometry-aware hit-testing** ships with it: a pen stroke / line is grabbed by proximity to the *drawn line*, not its bounding box (shapes keep their interior), so a mark tucked inside a closed pen loop is reachable — a click on the box selects the box, not the loop. **No model change** — reuses `translate_mark` / `restyle_mark`. | WSL (overlay logic + tests) + WSLg | Marquee/Ctrl-click a group; restyle/move/delete it as one undo each; a mark inside a loop is selectable; group stays on one page |
| **M59.7** Object resize | Selection handles + a resize drag over the M59.6 selection: a single mark, or a whole group by its bounding box (every member scaled about it, so the arrangement is preserved). Per-type: shapes + ink get the **eight-handle box** (ink scales all its points); a lone **line** gets **endpoint handles** — an axis-aligned line's box is degenerate, and you re-aim a line by its ends; a lone **text box** gets **no handles** (it hugs its text, so its size is a function of text + font size, which the format bar owns) and in a group it is *repositioned, not stretched*. Shift keeps a corner drag proportional; the result is page-clamped; Esc cancels mid-drag. `scale_mark` is the model twin of `translate_mark`. Handles live in a standalone `viewer/resize_handles.py` — the reusable corner-resize component **M62** (stamp placement) and **M69** (field creation) later consume. | WSL + WSLg | Resize a mark and a group; each type behaves; one undo step |
| **M59.8** Object z-order | **Bring to Front / Forward · Send Backward / to Back** for a selected drawn mark (or group) — `reorder_marks` permutes the page's annotations tuple, which *is* the z-order: later entries paint on top (viewer **and** saved PDF, via `apply_annotations`) and the hit-tests walk it reversed, so paint order and click order move together. `front`/`back` jump the set to an end keeping its relative order; `forward`/`backward` step each past its nearest unselected neighbour, so a contiguous run shifts as a block. One undo step (`SetAnnotationsCommand`). Surfaced in the **object right-click menu** (its discovery path) plus window shortcuts — deliberately *not* a menubar group, which would sit greyed whenever nothing is selected; right-clicking a mark now **selects** it so the verbs have an unambiguous target, and each verb is disabled at the end it is already at. **No model change** — the list order already was the z-order. | WSL (model+tests) + WSLg | Raise/lower a mark or group; paint + hit order follow; bakes in that order |
| **M59.9** Polish & fidelity | The last pass before the R3 tag, from testing. **(a) Text-markup colour palettes** — highlight / underline / strikeout get their own *curated* colours in the **Markup ▾ dropdown** (no extra toolbar slot), because text markup and freehand drawing are different domains: highlight = translucent brights (yellow default, its own remembered colour); underline + strikeout = opaque proofing colours (red default, one shared). Moves them **off** the M59.5 stroke picker, narrowing that button to "pen & shapes only", and gives highlight a colour choice for the first time. **(b) Object opacity** (`/CA`) — the real answer to "my filled shape hides the text": annotations *always* paint above the page content stream, so no z-order can put one behind text; PDF applies `/CA` to outline **and** fill together, so it is whole-mark opacity, in the picker + round-tripped. **(c) Redaction preview z-order fix** — a save applies redactions first (destructively, into the content) then adds annotations on top, so the preview must paint the redaction *below* the marks, not above. **(d) Edits keep your place** — `reload()` preserved the scroll offset only by snapping to the current page's top; it now keeps the exact offset for content-only edits (layout unchanged) and makes the *edited* page current without scrolling. | WSL + WSLg | Curated markup colours bake + reopen; a translucent shape shows the text through it; preview layering matches the saved file; marking up a page doesn't move the view |
| **M59.10** Markup merge | From testing M59.9: re-marking already-marked text **stacked** a second descriptor — two identical-looking highlights that took two Removes to clear (the hit-test returns the topmost only), and a re-colour that merely buried the old colour underneath. Text markup is **paint on text, not stacked objects**, so `merge_markup` folds each pass into what is already there, scoped **per type** (a yellow wash and a red underline on the same words are independent layers and stay so). It resolves at the granularity the marks are already stored in — one unioned bar per text line (`_selection_line_bars`) — so an overlap is 1-D interval arithmetic on x *within* a line band, not 2-D clipping. **Same colour → absorbed**: the old mark is dropped and its bars folded in, so an identical pass is a no-op, an extending pass grows the mark in place, and a bridging pass chains two marks into one. **Different colour → trimmed**: the covered span is cut out and the new colour takes it — full coverage replaces the mark, a cut through the middle *splits* it so the parts you did **not** select keep their colour. Deliberately **no toggle-off** on a same-colour re-mark: the same gesture must not sometimes-add and sometimes-erase (Remove stays the explicit verb). Redaction keeps the plain add path — destructive, colourless, and overlapping rects already union in `apply_redactions`. One `SetAnnotationsCommand` per page inside one macro, so absorb + trim + add across a multi-page selection is a single undo step. | WSL (model+tests) + WSLg | Re-marking never stacks; one Remove clears; a new colour recolours what it covers and splits what it doesn't; one undo step |
| **M59.11** Preview z-order fidelity | From testing M59.10: an opaque filled shape placed over a text box covered the box's **fill** but its **text showed straight through**. The saved file was right (verified: `apply_annotations` writes in tuple order, so the Square hides the FreeText completely) — the *preview* was wrong. It gave each mark a fixed z by **type** (highlight 6, drawn marks + the text-box frame 7, the text-box's text 8), so the text sat permanently above every drawn mark, and the frame merely tied with a shape at 7 where insertion order broke it. Same root cause as M59.9(c), one level up: this also made **M59.8's z-order verbs visually inert across types** — `reorder_marks` permutes the tuple and the hit-test walks that tuple, but paint order ignored it, so Bring to Front on a shape over a text box did nothing on screen while the click order changed underneath. Now z is derived from the mark's **index in the page's tuple**, spread fractionally across `[6, 7)` so the transient chrome above (search 9, selection 10, live gesture 11, handles 12–14) needs no renumbering, and the text-box's text becomes a **child item of its frame** — Qt paints a child directly above its parent and nowhere else, so the text can never out-stack a mark that covers its box. Redactions keep a fixed z *below* the whole band (M59.9): they bake into the page content, not as annotations, so their tuple position is irrelevant. | WSL (model+tests) + WSLg | A filled shape over a text box hides it entirely, text included; z-order verbs restack the preview; preview matches the baked page |
| **M59.12** Group copy / cut / paste | **Reverses M59.6's deferral** ("copy/paste of a multi-selection is a later pass") on owner call from testing: having marquee-selected a group, being unable to duplicate it as a unit is the obvious missing verb — you can already move, restyle, resize, reorder and delete a group. `object_clipboard` becomes a **list** (mirroring `page_clipboard`, empty = nothing to paste) rather than a single descriptor. Copy/Cut act on the whole selection when the clicked mark is part of it, and on just that mark otherwise — the same "right-clicking a member leaves the group intact" rule M59.8 established — with menu labels that count what will move (*Copy 3 Objects*). Paste computes the offset / click-centring / page-clamp **once from the set's union bounds** and applies one delta to every mark, so the arrangement is preserved exactly (the M59.7 group-resize principle); clamping the union rather than each mark is what stops a group collapsing onto itself at a page edge. Cut and a multi-mark paste are each **one undo step** (macro), a lone paste stays a single command, and the pasted set lands **selected** so it is ready to drag (the M59.7 rule). Text boxes in the set still ride the system clipboard as `text/plain`, joined in selection order. **No model change** — `translate_mark` and the existing batch commands already cover it. | WSL + WSLg | Copy/cut/paste a group within + across windows; arrangement preserved; one undo step; labels count the set |
| **M59.13** Dropdown-arrow placement | From testing: the three menu-carrying toolbar buttons disagreed about where the dropdown arrow sits, and all three crowded it against the icon. Cause is Qt placing the indicator **per popup mode**: `MenuButtonPopup` (Markup ▾ / Draw ▾) draws a *raised sub-panel* on the right with the arrow centred in it, while `InstantPopup` (the pen-&-shapes style swatch) tucks a small indicator into the **bottom-right corner**, over the swatch. Measured from the painted pixels, the swatch's arrow sat at **0.745** of the button height against ~0.45 for the split buttons. Fixed in the toolbar stylesheet — reserve a right-hand strip on any menu button (so the arrow never touches the icon), drop the sub-panel frame, and pin the arrow to `center right` for **both** `menu-arrow` (MenuButtonPopup) and `menu-indicator` (InstantPopup). Costs **+13 px per button** (+39 px of toolbar, 1084 → 1123 px), which moves the overflow-chevron threshold by 3.6% — accepted. Popup modes stay as they are: the split buttons genuinely need MenuButtonPopup (click the face = apply the last-used tool), and the swatch has no default action, so a face-click there would be a dead zone. The text-box format bar was checked and needs nothing — its buttons carry *text*, and Qt lays a text button's indicator out inline and centred already. | WSL + WSLg | All three arrows at the same height, vertically centred, clear of the icon |
| **M60** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

### R4 — "Stamp, Sign & Watermark" (+ search & redact)

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M61** ⭐ Unified content-draw engine | **One engine for stamps, signature, and watermark** (owner call: Way 2 — presets are prefilled entries of the custom generator; no dual annotation/content path, no true Stamp annots, no cross-renderer calibration). Custom-text stamp generator via PyMuPDF drawing (rounded-rect border + Helvetica-Bold); image placement; page-range watermark pass (rotation + opacity; `overlay=False` under-content exists in the engine but is **not offered in the UI** — see §M69.6). All **baked at save** — editable/undoable until save, permanent after (redaction-style semantics, said plainly in UI). | WSL (model+tests) | Stamp/watermark descriptors ride PageRefs, render in preview/print/export, bake at materialise |

**M61 as built** — three decisions worth recording, because each is a deviation from the sketch above:

- **Vector, not a raster.** The sketch said "→ transparent high-DPI pixmap". Built instead as a
  throwaway one-page PDF placed with `show_pdf_page`: the artwork stays **vector** (crisp at any
  zoom, no DPI to choose and get wrong), stamp text stays in the **text layer** (searchable, and
  `search_for` is what the tests assert placement with), and `show_pdf_page` takes an **arbitrary
  rotation angle** natively — which a pixmap does not, and which the diagonal watermark needs.
  Images still go through a pixmap, since that is what they are; opacity reaches them by scaling the
  alpha channel, an image having no `/CA` to set.
- **§M69.7 — two use cases, two controls, no third mode.** Owner: *"There are basically two use
  cases. Stamp — only offer the font size, and click on the page where I want it. Watermark — over
  the whole page. So we don't need the third option of dragging to stamp."* Dragging a rectangle was
  only ever a way of **sizing** a text stamp, and once a point size is on the dialog it is a second
  answer to a question already answered — the worse of the two, since a dragged box sets the size
  only indirectly, through whatever padding the auto-fit leaves (the M69.1 complaint). So the drag
  mode is gone for text stamps: `Kind` is *Stamp (click to place)* or *Watermark (whole page)*, the
  size field loses its "Fit to box" position, and a stamp is centred on the **press point** rather
  than the middle of any drag, with no rubber band to advertise a box it will not take. **Signature
  / image placement and M69 field creation keep the drag** — neither has a font size to be sized by,
  so the box is genuinely how you say how big they are; `fontsize=0` therefore remains the engine's
  auto-fit sentinel and the drag path stays under it.

- **§M69.9 — the angle sign was backwards.** ``Stamp.angle`` was **clockwise**-positive: ``-45``
  produced the north-east (bottom-left to top-right) diagonal, while the field's own docstring
  claimed counter-clockwise and the watermark default was written as ``-45`` with the comment
  "bottom-left to top-right". Caught by the owner asking the obvious question — why is north-east
  negative? It should not be: the maths convention is counter-clockwise-positive, so ``+45`` is
  north-east and ``-45`` is south-east. The descriptor was corrected rather than the documentation
  bent to fit it, which cancelled the negation ``apply_content_marks`` had carried since §M69.1 and
  added one in the viewer's preview (Qt's ``setRotation`` is clockwise-positive in a y-down scene).
  Free to fix because R4 has never shipped.

- **§M69.6 — `under` is an engine capability, not a UI control.** A mark drawn with
  `show_pdf_page(overlay=False)` goes beneath *everything the page draws*, including the opaque
  full-page background most real PDFs paint — so it bakes correctly into the text layer and cannot be
  seen (found on a 320-page prospectus: the text was in `get_text()`, nothing was visible). The
  viewer made it worse by previewing an `under` mark with **multiply compositing on top**, which
  shows regardless, so preview and file disagreed. **Opacity already gives the watermark look** —
  a translucent mark over the content with the page's text legible through it — which is what
  `under` was reached for, so the control was dropped (owner). The considered alternative, baking
  `under` as an over-content `/BM /Multiply` draw so the file would match the preview, was
  **rejected**: it does not restore the one thing true under-print uniquely gives (page images
  *covering* the mark), and it means hand-built `/ExtGState` PDF code in the **save path** — exactly
  the cross-renderer variability M61's "no cross-renderer calibration" call exists to avoid. The
  descriptor field and the engine path stay, so a future caller can still use them.

- **§M69.3 — and it is not a second *feature* either.** M62 shipped two dialogs over that one
  descriptor, and the seam cost more than it bought: every new field had to be added twice (the
  sticky-style work at M69.1 wrote `style_state`/`restore` twice), and the two preset lists both
  contained "Draft" and "Confidential" — the same word producing a different mark depending on which
  menu the user opened, with nothing on screen to explain why. Of the seven axes on which the two
  dialogs differed, **six were defaults** (`under`, angle, frame, opacity, scope, preset list) and
  exactly one was structural: **how the mark is placed**. So the merged `ui/mark_dialog.py` surfaces
  that one as a **Place** control — "Where I drag it" / "Over the whole page" — which rewrites the
  style fields visibly and hides Size + Frame for a page-covering mark (no dead chrome). Presets
  became one list of *words*, prefilling text + colour only; whether "Confidential" is a stamp or a
  watermark is now a visible choice rather than a hidden mode. That is this section's own **Way 2**
  rule (a preset is a prefill of the custom generator, never a separate code path) applied one level
  up. Done **before** R4's first release, while it was still free: after M70 ships it would be a
  breaking UI change that release notes must explain.

- **A watermark is not a third type.** It is a `Stamp` or `ImageStamp` with `under=True`, added to
  every page in the range. **The page range lives in the UI loop, not the model** — which is exactly
  what makes "stamp my initials on pages 3–17" and "watermark the document" the same operation, and
  is the concrete form the "one engine" decision takes.
- **Text never wraps.** Auto-fit rejects any size that would break the word, because
  `insert_textbox` left to itself satisfies a narrow box by splitting `DRAFT` into `DR`/`AFT`. A
  *pinned* size that will not fit is shrunk rather than honoured: `insert_textbox` draws **nothing**
  on overflow, and a stamp the user placed but cannot see is the worse failure.

  A trap worth remembering: `insert_textbox` is the only way to ask PyMuPDF "does this fit?", and it
  answers by **drawing** — even at `render_mode=3` (invisible) the glyphs land in the content stream
  and come back out of `get_text`. Measuring on the target page therefore stamps everything twice,
  once invisibly. All measurement runs on a scratch page.

**M62 as built** — the placement mode is **reuse, not a new subsystem**. A content mark is a
free-placed rect, so adding it to the viewer's `_OBJECT_TYPES` gives hit-testing, selection, move,
eight-handle resize, z-order and delete from the M58/M59 object tools already in the file; the
milestone's "drag rect, move, corner-resize until save" then needs only a one-shot `ArmedTool.STAMP`
sharing the existing draw-gesture path. Two consequences worth stating:

- **A stamp stretches; a text box does not.** `scale_mark` repositions a `TextBox` (its size is a
  function of its text) but scales a content mark, because the rect *is* the box the artwork is
  fitted into. The pen-&-shapes style picker correctly ignores a selected stamp — `restyle_mark`
  returns `None` for it — since a stamp's style comes from its own dialog.
- **The preview must reproduce `show_pdf_page`'s fit, and this is easy to get wrong.**
  `show_pdf_page` scales a mark's *rotated* artwork to fit its rect and centres it there, so a 45°
  watermark bakes at ~0.59 of its box. The first implementation baked that factor into the preview
  pixmap's resolution, where `setScale` promptly cancelled it out — the on-screen watermark was
  **~1.8× too large**, and every assertion still passed. It was caught by rendering the preview and
  the materialised page side by side and looking at them. `_rotation_fit` now carries the factor and
  is unit-tested against the geometry, plus a scene-level test that a rotated mark's painted item
  stays inside its rect and concentric with it.

  The one honest gap: an `under=True` watermark bakes *beneath* the page content, and Qt cannot
  paint beneath the page's own pixmap. It is previewed with **multiply** compositing instead —
  equivalent for the translucent marks a watermark actually is, because painting a translucent mark
  under black text and multiplying it over black text both leave the text black.
| **M62** Stamp & watermark UI | Placement mode (drag rect, move, corner-resize until save) — **the same placement UI M69's field creation reuses**; **one** mark dialog (preset · place · text · colour · size · angle · opacity · frame · behind-content · page range) — see §M69.3 for why the stamp and watermark dialogs were merged; apply-to-page-range on any mark (the initials-on-every-page case). | WSLg | Place/move/resize a stamp; watermark a range; both bake on save |
| **M63** Image stamp / signature | The sign-and-return workflow: place a PNG/JPEG (scanned signature, seal, logo) via the M62 placement UI. Transparent-PNG alpha honored + a **"make white background transparent"** threshold toggle (phone-photo signatures just work); **recent-signatures list stores paths only** — KlarPDF never keeps a hidden copy of a signature. Docs: ink-equivalent, **not** a cryptographic signature. | WSL + WSLg | Sign a form offline in two clicks on the second use; baked mark can't be lifted off |
| **M64** Search & redact | Redact every occurrence of a string: `search_for` quads → batched `Redaction` descriptors in **one undo step**; destructive only at the existing confirmed Save. Review flow reuses M47's panel with checkboxes (untick "Smithsonian" when redacting "Smith"); case + whole-word toggles. **Honesty: text-layer only** — detect image-only pages and warn; form-field values are a documented boundary; same-width boxes hint string length (docs note). | WSL (model+tests) + WSLg | Mark-all → review → redact-checked → Save removes them (cross-engine leak check); warnings fire on image-only pages |
| **M65** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

### R5 — "Foreign Annotations & Form Fields"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M66** ⭐ Foreign-annot infrastructure + delete | The shared cost, built once: enumerate/hit-test/select foreign annotations in the viewer + **fingerprint identity** (`/NM` when present, else type + rect + contents hash — xrefs don't survive `insert_pdf`). First verb: **delete** — a `ForeignDeletion` descriptor matched and applied at materialise. Zero fidelity risk, works for **every** annotation type, everything else passes through byte-identical. | WSL (model+tests) + WSLg | Delete any foreign mark; save; remaining annots byte-identical; undo restores |
| **M67** Move foreign marks | Translate `/Rect` in place at materialise — the appearance stream is preserved verbatim, so a rich callout box moves with zero degradation; live drag preview via the annot's pixmap patch. | WSL + WSLg | Move a foreign mark of any type; its appearance survives untouched |
| **M68** Adopt-on-edit | Double-click a foreign mark of a **modeled type** (highlight, FreeText — plus R3's ink/line/rect/ellipse and M56's underline/strikeout) → parse into the model, author-tag, strip-exactly-that-one at materialise. **Detect unsupported features first** (`/RC` rich text, non-base-14 DA font, `/CA` opacity, `/CL` callouts…) and warn "editing will simplify this annotation" with cancel. Unmodeled types stay delete/move only. | WSL (model+tests) + WSLg | Adopt→edit→save round-trips; degrade warning fires exactly when features would be lost |
| **M69** Form-field creation | **Checkbox / text / dropdown** via `page.add_widget` (the API the test fixtures already use); placement UI reused from M62 + a small properties panel (name, type, default, options). Saved fields are ordinary AcroForm — existing fill, lossless value save, edits-aware print, and flatten just work. **Radio-button groups: rejected by owner (2026-07-18)** — see §Future enhancements. | WSL (model+tests) + WSLg | Place the three field types; fill/print/flatten work on them like any AcroForm field |
| **M70** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

### R6 (prov. v0.16.0) — "Simplify & Read" (planned; M71–M79)

Owner-decided **2026-07-22**, from a comparison session against the owner's notes on **macOS
Preview's UI** — the app KlarPDF set out to replace on Windows. Preview is the **inspiration, not
the spec**: its central organizing idea — *the app at rest is a viewer; the markup kit is chrome
you summon on demand* — is adopted, but not its feature cuts (no explicit Save, no page-op
buttons anywhere). The session also reviewed Preview-only features for adoption: four approved
(M75–M78), the rest recorded below so they aren't relitigated.

**Design-budget revision.** R1–R5's UI budget held the bar *"the app at rest — a plain scanned PDF
open — looks identical to today"*. R6 deliberately revises that bar **downward**: after M71 the
app at rest shows a ~10-slot *reading* bar, and the markup kit appears only when asked for. Every
other budget (menus are the complete catalog; one-shot commands are menu + dialog, never toolbar;
lightness; honesty) carries over unchanged and binds every milestone below.

**Decided against / kept as-is (owner, 2026-07-22)** — the non-adoptions, so they stay settled:

- **Zoom cluster unchanged** — the five-slot group (out · % widget · in · Fit Width · Fit Page)
  stays on the resting bar as-is; Preview's two-button zoom was reviewed and declined.
- **Save keeps its toolbar button** — Preview autosaves; KlarPDF has an explicit Save, and the most
  consequential verb in the app keeps its one-click path. **Open and Print leave the bar** (the File
  menu keeps them); whether Open stays beside Save ("Save alone looks odd") is settled by eye at the
  M71 review.
- **No action strip inside the Pages panel** — the page ops leave the toolbar with no replacement
  strip; the Edit menu and the sidebar context menu (M46) are the paths.
- Reviewed from Preview and **not adopted**: Share (against the offline posture); sketch-to-shape
  recognition (heuristics project, explicit shape tools exist); the magnifier shape (niche); a
  separate form-filling toolbar (our form fill is inline — it solves a problem we don't have);
  pressure-sensitive ink (re-affirmed — rejected at M58, PDF ink can't carry it); a toolbar
  Properties/info button (one-shot commands stay off the toolbar); click-to-place shapes
  (drag-to-draw kept — fewer steps for the common case); trackpad/camera signature capture (M63's
  image path covers the everyday tier); **user bookmarks — deferred**, needs an app-side store
  (§Future enhancements' outline-editing entry may serve the same need inside the file).

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M71** Two-tier toolbar | Split the single ~29-slot bar into a **resting bar** — Sidebar · Save (Open beside it if Save alone reads odd — review call) · Undo/Redo · the zoom cluster unchanged · Rotate L/R · a **Markup** toggle · Find — and a **markup bar** the toggle reveals: Select/Grab/Objects · Text Box · Markup ▾ · Draw ▾ · style swatch · Stamp ▾ · Redact (M72). Cut/Copy/Paste/Delete/Insert Pages and Open/Print leave the toolbar — the menus and context menus already carry them (M46); no Pages-panel action strip (owner). Markup-bar visibility is remembered app-wide, like the sidebar. | WSLg | At rest only the reading bar shows; the toggle reveals the kit; every removed button's verb still reachable via menu/context menu; the choice persists across launches |
| **M72** One Redact tool | Preview-style gesture detect: the two Redact slots become **one armed tool** — a drag starting **on text** runs the text-flow redaction, a drag starting elsewhere rubber-bands a block (the press point's hit-test decides, on the existing text-hit path). *Revised 2026-07-24 (PR #189):* the **Tools menu also collapsed** to the single gesture-detecting Redact + Find and Redact — the same Redact action as the bar — rather than keeping the explicit Redact Text / Redact Block verbs; the concrete text/block tools stay (gesture-resolved, context-menu Redact Selection, `_arm_tool`), just not standalone menu entries, and **Ctrl+Shift+R moved onto the combined Redact**. | WSLg | One slot arms both gestures; press-on-text vs press-on-margin choose correctly; the Tools menu shows one Redact (carrying Ctrl+Shift+R) + Find and Redact |
| **M73** Sticky markup arming | Highlight / Underline / Strike Out / Pen stay **armed across gestures** (Preview's HUS behaviour): mark passage after passage on one arm. Three exits — click the armed button again · **Esc** · arm any other tool from the markup bar (owner). Placement and destructive tools (Text Box, shapes/lines, Stamp/Signature, Redact, Crop) stay **one-shot**: repeat use is rare there, and a stuck destructive mode is a trap. | WSLg | Three highlights on one arm; all three exits work; one-shot tools unchanged; the armed state is always visible on the button |
| **M74** ⭐ Arrow ends as style | Preview treats arrowheads as *line style*, and it is right: **Arrow leaves Draw ▾**, and `Line` gains an **ends** attribute (none · start · end · both) on the M59.5 style picker — both-ended arrows for the first time. Existing arrows read back as `Line` + end (round-trip via `set_line_ends`); restyle-in-place covers a selected line's ends like colour/width. | WSL (model+tests) + WSLg | Draw a line, give it ends from the picker; both-ended bakes + reopens; pre-R6 arrows reopen editable and unchanged |
| **M75** Find bar match options | **Match case** + **Whole words** toggles on the FindBar. The filters already exist (`SearchController.search`, built for M64's Find and Redact) — the interactive bar never exposed them; next/prev, List All and the results panel respect them. | WSLg | Both toggles filter interactive search exactly as they do in Find and Redact; both off = today's behaviour |
| **M76** Markup context menu | Right-click on already-marked text offers Preview's change set: the curated **highlight colours** + **Underline** / **Strike Out** toggles + Remove — recolour a mark, or add/remove the other markup layers on the same words, **in place** through the M59.10 merge machinery (recolour = trim/absorb, never stacking). One undo step per action. | WSLg | Recolour an existing highlight in place; add a strikeout over it; remove one layer leaving the other; each is one undo step |
| **M77** Annotations sidebar tab | A third sidebar tab beside Pages \| Outline listing **every mark in the document** — ours and foreign — as "p. N · type · snippet/preview" rows; click selects + jumps (the M47 pattern). **The tab exists only while the document has marks** (owner rule: inapplicable chrome is invisible, not greyed out) and tracks edits/undo live. | WSLg | Marked doc: the tab lists all marks, click jumps + selects; clean doc: sidebar unchanged; the list follows add/remove/undo |
| **M78** View modes | The reading modes Preview offers: **Full Screen** (chrome-free reading, F11) · **Slideshow** (one page per screen at fit-page, arrow/click advance, Esc exits) · **Two-Page view** (facing-pages layout in the ordinary window). Surfaced in the View menu + the bare-page right-click menu (M46). View-only — file, print and export untouched (the M49 principle). | WSLg | Enter/exit each mode cleanly; zoom / night-mode interplay sane; nothing written to the file |
| **M78.2** Nudge objects with arrow keys | Arrow keys move the current object selection — **1 pt/press, Shift = 10 pt**, clamped to the page; nothing selected → arrows scroll as today (gated on `selected_objects`, like Delete). Every movable object, text boxes included (shared `translate_mark`). **Undo keyed to held-vs-tapped**: auto-repeat from *holding* a key coalesces into one undo step (a mergeable `NudgeCommand` — Qt `id`/`mergeWith`, gated on same page + same marks, merging only `event.isAutoRepeat()` events), while each discrete tap is its own step — so the first Undo never throws the object back to its origin. | WSLg | 1/10 pt steps; a group nudges as one step; held sweep = one undo, N taps = N undos; clamps at the page edge; no selection = no-op |
| **M78.3** Resize text-box width (reflow) | A lone text box gains a single **right-edge handle, left side pinned**: dragging sets the wrap width and the text refolds, height auto-fits (font/colour/fill stay the format bar's). `TextBox` gains `auto_width` (default `True` = today's hug-the-longest-line; `False` = rect width authoritative + wrap). Special-cased in `begin/finish_resize` like the lone-`Line` endpoint path, so **group resize still doesn't stretch text boxes** (`scale_mark` unchanged). `_paint_textbox` + the inline editor wrap at the fixed width; round-trip infers `auto_width` from whether the text fits one line in the stored rect (`fitz.get_text_length`). | WSL (model+tests) + WSLg | Lone box shows only the right handle; drag reflows + grows height with the left edge fixed; group resize leaves text boxes unstretched; save+reopen keeps the fold |
| **M78.4** Icon polish — Grab / Text Box / Pen | Redraw three markup-bar glyphs the owner flagged (candidates rendered + chosen at real size, 2026-07-23): **Grab** → an owner-supplied outline hand with separated fingers (the current one's finger-tops merge); **Text Box** → a bold "T" in a box a touch wider than tall (the current flat rectangle read as empty chrome); **Pen** → a pencil on a baseline (the current plain tilted stylus read as "a stick"). Monochrome SVGs, so the M29 theme re-tint is untouched; verified via `ui.icons.icon(...)` offscreen at size. | WSLg | The three glyphs replace the current ones, read correctly light + dark at 16–24 px, and re-tint on theme change |
| **M78.5** Highlight/Underline/Strike arming swatches | The **Markup ▾** dropdown becomes **three swatch rows** — Highlight · Underline · Strike Out — where clicking a colour **both sets that verb's colour and arms the verb** (applying to a live text selection through the existing armed-tool path), collapsing today's pick-then-click. Underline and Strike Out gain **independent colours** (they share one now). Reuses `SwatchRowAction` (`close_on_pick=True`); the split-button face still repeats the last verb. | WSLg | Each row arms its verb in the picked colour in one click; underline vs strike colours are independent; a live selection is marked immediately |
| **M78.6** Split the markup style button | The single pen-&-shapes **style swatch** splits into three markup-bar buttons over the same `MarkupStyle` (no model/file change): **Line Styling** (thickness · dash · arrow ends) · **Colors** (a Border swatch row + a Fill swatch row + custom + No Fill — clubbed under one menu, owner call) · **Opacity** (a slider showing/accepting the exact %, replacing the 25/50/75/100 presets). **+2 markup-bar slots** (the on-demand bar; the resting bar is untouched — a deliberate budget spend, owner-approved). "Border" here is the pen/shape stroke, distinct from M78.5's text-markup colours; selecting an object still loads its style into all three (M59.5). | WSLg | Three buttons drive the shared style; Colors carries Border + Fill; Opacity's slider shows/accepts an exact value; restyling a selected object still works |
| **M79** Verify + release | Headless suite green; Windows validation; tag. | Windows | Matrix green → release |

**M78.2–M78.6 are late additions (owner, 2026-07-23)** — five enhancements approved during owner testing after the M71–M78 build, shipping before the M79 release cut. The decimal numbers are positional (as with M79.1–.3): **M78.1** was already the view-mode-nav fix. Two are object-editing (arrow-key nudge · text-box width), one an icon-polish pass, two markup-UI (H/U/S arming swatches · splitting the shared style button). Zero new dependencies, like the rest of the tranche.

### M80 — Ctrl+wheel pointer zoom, and the input-conventions audit (owner-reported 2026-07-27)

Moved to [`docs/milestones/M80.md`](docs/milestones/M80.md).

### Renumbering note (2026-07-27) — milestone numbers now follow implementation order

The post-R6 milestones were first numbered in the order they were *discovered*, which bore almost
no relation to the order they should be built in. They are **renumbered here so the number is the
build order**: the foreign-annotation bug sweep first (one item is live data loss), then zoom
performance, then the DPI correction that depends on it, then the features.

Two constraints are structural rather than preference, and this ordering preserves both.
**M87's cache work must precede M88's DPI work** — DPI makes every page ~5.4x heavier, so shipping
it against today's count-based cache means ~9.5 GB per window at high zoom, reachable in one
Ctrl+wheel sweep. And **M81's note round-trip precedes M90's notes UI** — the round-trip is what
cures the adoption data loss, so it cannot wait for an interface.

| Old | New | What |
| --- | --- | --- |
| M85.1–.2, M86.3 | **M81** | Note model + `/Contents` round-trip, and the adoption data loss it cures |
| M88 | **M82** | Foreign text markup is not a drag target |
| M86.1–.2 | **M83** | Heterogeneous annotations tuple + geometry chokepoint |
| M87 | **M84** | Highlight rendering fidelity (multiply blend) |
| M84 | **M85** | Current-page tracking for short pages |
| M82 (A, B) | **M86** | Redundant render passes + gesture coalescing |
| M82.1–.2 | **M87** | Adaptive prefetch + global byte-ceiling cache |
| M83 | **M88** | DPI correctness |
| M81 | **M89** | Reading-input conventions |
| M85.3–.6 | **M90** | Notes UI |

The old numbers are kept in this table because [#198](https://github.com/utyagi24/klarpdf/pull/198)
and its commit messages use them. **M80 is unchanged** (shipped). One correction the renumbering
makes visible: **A and B did *not* land in M80's PR** as the earlier text anticipated — #197 merged
without them, so they are M86 and `main` carries the un-coalesced wheel until that lands.

### M81 — Notes: the model, the round-trip, and the data loss it cures

Moved to [`docs/milestones/M81.md`](docs/milestones/M81.md).

### M82 — foreign text markup is draggable, and it steals the press from text selection

Moved to [`docs/milestones/M82.md`](docs/milestones/M82.md).

### M83 — the annotations tuple is heterogeneous, and only four of five hit-tests know it

Moved to [`docs/milestones/M83.md`](docs/milestones/M83.md).

### M84 — highlights render dull because the preview alpha-blends what the file multiplies

Moved to [`docs/milestones/M84.md`](docs/milestones/M84.md).

### M85 — current-page tracking is wrong for short pages (owner-reported 2026-07-27)

Moved to [`docs/milestones/M85.md`](docs/milestones/M85.md).

### M86 — the two cheap zoom fixes (owner-decided 2026-07-27)

Moved to [`docs/milestones/M86.md`](docs/milestones/M86.md).

### M87 — render-resource discipline (owner-decided 2026-07-27)

Moved to [`docs/milestones/M87.md`](docs/milestones/M87.md).

### M88 — DPI correctness: what "100%" means (owner-reported 2026-07-27)

Moved to [`docs/milestones/M88.md`](docs/milestones/M88.md).

### M89 — the rest of the reading-input conventions (owner-decided 2026-07-27)

Moved to [`docs/milestones/M89.md`](docs/milestones/M89.md).

### M90 — Notes: the interface (owner-specified 2026-07-27)

Moved to [`docs/milestones/M90.md`](docs/milestones/M90.md).

### M91 — Whitespace fidelity, glyph legibility, reading position (owner-reported 2026-07-29)

Moved to [`docs/milestones/M91.md`](docs/milestones/M91.md). Also there: M91.4.

### The corner-case document — what it taught us (measured 2026-07-27)

`IAS_CaseStudy.pdf`: 75.6 MB, 18 pages, all 1920×1080 pt, **no text layer at all** (`chars == 0` on
every page), 95 MB of embedded images — 3–13 MB per page. Owner-supplied as a deliberate corner case.
Kept here because it corrects two assumptions the M82/M83 plans were resting on.

**Open + first frame: 11.19 s**, and the profile leaves no ambiguity:

```
10.000 s  fz_run_display_list          (11 calls, ~0.91 s each)
 0.157 s  fz_new_display_list_from_page
```

*Building* the display list is nearly free; **running** it — decoding the embedded imagery — is the
whole cost.

**Correction 1 — this class of document is decode-bound, not fill-bound, so render cost is flat
across scale.** An earlier estimate in this plan predicted M88's DPR work would make such documents
~3× slower. Measured, it will not:

| Scale | Pixels | Page 0 | Page 5 |
| --- | --- | --- | --- |
| 0.625 (today's fit) | 1200×675 | 1231 ms | 2688 ms |
| 1.094 (after DPR 1.75) | 2101×1182 | 1134 ms | 2820 ms |
| 0.833 (after DPR 1.0) | 1600×900 | 1077 ms | 2898 ms |

Three times the pixels, the same time. **M83 costs such documents ~3× the memory and essentially no
extra time.** The "~5.4× heavier" figure elsewhere in §M88 is about *bytes*, and stands; it is not a
time multiplier for image-heavy pages.

**Correction 2 — the cost recurs at every new zoom value.** Re-rendering a page at the *same* zoom
takes 47–126 ms (cached); at a *different* zoom it costs full price again (1976 ms, 4831 ms). So
**every distinct zoom re-decodes every visible page** — which is precisely the reported zoom lag, and
why M80's continuous zoom factor is expensive here beyond the cache-miss argument already recorded.

**What the planned work actually does for it:** **B** is large (each distinct zoom costs 1–3 s/page,
so collapsing a 20-event burst is a 20× saving); **F** is large on open (the ±2 prefetch is two extra
page decodes ≈ 2–6 s of the 11 s); **A** is small (passes 2–3 hit the cache, so it saves scene work,
not decodes); **M87.2's byte ceiling does nothing here** — this document peaks at 13–32 MB, memory was
never its problem.

**And one behaviour to expect rather than treat as a regression:** with no text layer, **M89.6's
Ctrl+A selects nothing and Find never matches** in this document. Edge behaves identically; it is
correct for an image-only PDF.

### Deferred, with the condition for revisiting

- **C — scale existing pixmaps during the gesture, re-rasterise on settle.** Gives 60 fps zoom feel,
  slightly soft mid-gesture. Revisit only if M86 + M87.1 leave zoom sluggish on real hardware. **Worth far
  more than first estimated for decode-bound documents** (see the corner-case section above): their
  render cost is flat across scale yet the cache is keyed *per zoom*, so today they re-decode
  identical imagery at every zoom step. C would let such a document decode **once** and reuse the
  pixels across every zoom — close to a 100× win on zoom for that class, versus the modest polish it
  represents for ordinary text documents.
  **Independent of E** (owner correction, 2026-07-27, and correct): E keeps the *app responsive*, C
  keeps the *page visible*. An earlier note here claiming E supersedes C had it backwards — under
  async rendering every new zoom is a miss whose pixels arrive later, so a gesture would show blank
  placeholders, and C is precisely what fills that gap. **E makes C more valuable.**
  *(Proposed as part of **M152.1** and **M152.2**, 2026-09-23 — §M152. **Built** in both,
  2026-09-24: a slow page's picture is stretched during a resize, and after a zoom, a move to
  another screen or a restore until its pieces arrive — §M152, *M152.1 as built* and *M152.2 as built*.)*
- **D — quantise zoom to a `1.25^n` ladder.** Would restore cache hits cheaply, but buys that by
  giving back the smooth touchpad response M80 exists to deliver. Still recommended against —
  but **one of the two arguments against it has since been measured away**: it also said a ladder
  would make M89.5's pinch ratchety, and M89.5's validation found the touchpad driver **already
  delivers exactly whole `1.25` steps** for a pinch (§M89). So a ladder would cost pinch nothing on
  this hardware; what it would still cost is Ctrl+wheel and a real hi-res wheel. Recorded so it
  isn't re-proposed as a free win *and* isn't rejected for a reason that no longer holds.
- **E — render pages off the UI thread.** All rendering today is synchronous (verified: zero
  threading in `viewer/`, `organize/`, `klarpdf/model/`, `app.py`, `main_window.py`). The white/black page
  rect placeholder already exists in `_build_scene`, but a reader never sees it — the UI *freezes*
  until the batch finishes instead of degrading to placeholders. E is the only item that answers "the
  app must not appear blocked on a heavy document". **Owner call: F now, E only if still needed after
  M86 + M87.1** — threading brings cancellation, ordering and race surface that tests catch less reliably,
  so it needs the measurement to justify it. **That measurement has since arrived** (corner-case
  section above): `IAS_CaseStudy.pdf` spends **1–3 s per page per new zoom** decoding imagery, which
  is irreducible — M86 and M87.1 reduce *how often* we pay it, but nothing except moving the work off the
  UI thread stops the app freezing while we do. On this evidence E is required rather than
  conditional; the "only if still needed" gate has been met, and the open question is now scheduling,
  not justification.
  *(Scheduled as **M152.2**, 2026-09-19 — §The open issues, grouped. Diagnosis, measurements and
  the proposed design: §M152, 2026-09-23. Revised 2026-09-24: slow pages are drawn in pieces on
  the window's thread, and a helper process is kept as the fallback. **Built** as M152.2 on
  2026-09-24 — §M152, *M152.2 as built*. **The owner called the 1.0 gate's Item E done on it**
  the same day, accepting the two pauses pieces do not remove: a page drawn whole the first time
  it is met, and a page's photos unpacked in one step at a new level of detail (§M152, *Its
  limits*; `PROGRESS.md` §Toward 1.0).)*

### M92 — Mouse-wheel scrolling (owner-reported 2026-07-30)

Moved to [`docs/milestones/M92.md`](docs/milestones/M92.md). Also there: M92.3, M92.4, M92.5, M92.6.

### M96 *(unplanned)* — a neighbouring line vetoed a whole-word match (TC-004, owner-reported 2026-08-16)

Moved to [`docs/milestones/M96.md`](docs/milestones/M96.md).

### M97 *(unplanned)* — a region box may cover more than one line (TC-005, owner-reported 2026-08-16)

Moved to [`docs/milestones/M97.md`](docs/milestones/M97.md).

### M98 *(unplanned)* — the two silent failures redaction had no counterweight for (TC-007, 2026-08-16)

Moved to [`docs/milestones/M98.md`](docs/milestones/M98.md).

### M102 *(unplanned)* — the safety net crashed instead of firing (2026-08-17)

Moved to [`docs/milestones/M102.md`](docs/milestones/M102.md).

### M103 *(unplanned)* — what the reply says about what it never looked at (TC-007/008, 2026-08-18)

Moved to [`docs/milestones/M103.md`](docs/milestones/M103.md).

## Planned next — MCP capability milestones (M99–M101, scheduled 2026-08-16)

Three milestones the hands-on sessions asked for, written up so a later session can pick any of them
up cold. None is a defect: TC-001 to TC-007 found the bridge *correct* on these paths and wanting
more of them. They are ordered by cost-to-value, not by the order they were requested. **M99 and
M100 shipped; M101 was re-scoped on 2026-08-20** — see its own section for what changed and why.

### M99 — a region clip on `render_page` and `export_images`

Moved to [`docs/milestones/M99.md`](docs/milestones/M99.md).

### M100 — one redaction call, several queries

Moved to [`docs/milestones/M100.md`](docs/milestones/M100.md).

### M101 — annotation as a capability: marking up a document from the bridge ⭐

Moved to [`docs/milestones/M101.md`](docs/milestones/M101.md).

### M105 *(unplanned)* — the tool descriptions are truncated in transit (TC-007, 2026-08-18)

Moved to [`docs/milestones/M105.md`](docs/milestones/M105.md).

### M106 *(unplanned)* — unknown parameters are dropped in silence (TC-009, 2026-08-18)

Moved to [`docs/milestones/M106.md`](docs/milestones/M106.md).

### M107 *(unplanned)* — a redaction that lands inside a longer word (OPEN-ITEMS, 2026-08-19)

Moved to [`docs/milestones/M107.md`](docs/milestones/M107.md).

### M108 *(unplanned)* — the residual counts said spellings and meant places (TC-011, 2026-08-19)

Moved to [`docs/milestones/M108.md`](docs/milestones/M108.md).

### M109 *(unplanned)* — a redaction that re-encodes an image says so (TC-011, 2026-08-19)

Moved to [`docs/milestones/M109.md`](docs/milestones/M109.md).

### M110 — the save's cleanup level must follow the route, not the file (found 2026-08-21) ⭐

Moved to [`docs/milestones/M110.md`](docs/milestones/M110.md).

### M111 — the export paths never followed M93, and one of them reports a number it does not write (found 2026-08-21)

Moved to [`docs/milestones/M111.md`](docs/milestones/M111.md).

### M112 — the bridge can *edit* an annotation, not only add one (owner-asked 2026-08-21)

Moved to [`docs/milestones/M112.md`](docs/milestones/M112.md).

### M113 — what TC-012 and TC-013 found in M101 (reviewed with the owner, 2026-08-21)

Moved to [`docs/milestones/M113.md`](docs/milestones/M113.md).

### M114 — a mark on one page rewrites all 572 (TC-012 retest, found 2026-08-22)

Moved to [`docs/milestones/M114.md`](docs/milestones/M114.md).

### M115 — the app and the bridge were writing PDFs with different engines (found 2026-08-23)

Moved to [`docs/milestones/M115.md`](docs/milestones/M115.md). Also there: M115.1.

### M116 — adding a highlight appends 1,865 bytes instead of rewriting 8.8 MB (2026-08-24)

Moved to [`docs/milestones/M116.md`](docs/milestones/M116.md).

### M117 — an append should write the mark you added, not the two hundred already there (2026-08-25)

Moved to [`docs/milestones/M117.md`](docs/milestones/M117.md).

### M118 — the boundaries M113 stopped one step short of (TC-015, 2026-08-26)

Moved to [`docs/milestones/M118.md`](docs/milestones/M118.md).

### M119 — the same mistake, a different exception on each platform ([#294](https://github.com/utyagi24/klarpdf/issues/294), 2026-08-26)

Moved to [`docs/milestones/M119.md`](docs/milestones/M119.md).

### M120 — a shape stops resizing itself on every save ([#292](https://github.com/utyagi24/klarpdf/issues/292), 2026-08-26)

Moved to [`docs/milestones/M120.md`](docs/milestones/M120.md).

### M121 — Insert Blank Page leaves you looking at the page you made ([#288](https://github.com/utyagi24/klarpdf/issues/288), 2026-08-26)

Moved to [`docs/milestones/M121.md`](docs/milestones/M121.md).

### M122 — the no-socket guard stops catching asyncio instead of us ([#301](https://github.com/utyagi24/klarpdf/issues/301), 2026-08-27)

Moved to [`docs/milestones/M122.md`](docs/milestones/M122.md).

### M123 — the Windows half of the suite, which nothing had ever run (2026-08-27)

Moved to [`docs/milestones/M123.md`](docs/milestones/M123.md).

### M124 — a sandbox refusal is not a failure to open a file ([#304](https://github.com/utyagi24/klarpdf/issues/304), 2026-08-27)

Moved to [`docs/milestones/M124.md`](docs/milestones/M124.md).

### M125 — a skip says its own name and its own reason (2026-08-27)

Moved to [`docs/milestones/M125.md`](docs/milestones/M125.md).

### M126 — the release check that was written down but never run (2026-08-27)

Moved to [`docs/milestones/M126.md`](docs/milestones/M126.md).

### M127 — the bundle could not be built on the platform that ships it (2026-08-27)

Moved to [`docs/milestones/M127.md`](docs/milestones/M127.md).

### M128 — the bundle declared a Python requirement no Python could satisfy (2026-08-27)

Moved to [`docs/milestones/M128.md`](docs/milestones/M128.md).

### M129 — the bundle ships the lock it installs from (2026-08-27)

Moved to [`docs/milestones/M129.md`](docs/milestones/M129.md).

### M130 — a test that could not be run in the environment it was written for (2026-08-28)

Moved to [`docs/milestones/M130.md`](docs/milestones/M130.md).

### M131 — the release ships the bundle it is named after (2026-08-28)

Moved to [`docs/milestones/M131.md`](docs/milestones/M131.md).

### M132 — the bridge installs on the Python people have *(unplanned)* (2026-08-31)

Moved to [`docs/milestones/M132.md`](docs/milestones/M132.md).

### M133–M136 — how the bridge is installed, and by whom (owner-scoped 2026-09-04)

Moved to [`docs/milestones/M133-M136.md`](docs/milestones/M133-M136.md).

### M137 — the dev lock is compiled off Windows, and the runbook said otherwise *(unplanned)* (2026-09-05)

Moved to [`docs/milestones/M137.md`](docs/milestones/M137.md).

### M138–M140 — structure an agent can navigate: links, outlines, headings (2026-09-07)

Moved to [`docs/milestones/M138-M140.md`](docs/milestones/M138-M140.md).

#### M138 — `get_links`

Moved to [`docs/milestones/M138.md`](docs/milestones/M138.md). Also there: M138.1, M138.2, M138.3, M138.4, M138.5.

#### M139 — `set_outline` *(2026-09-10, the bridge's 21st tool)*

Moved to [`docs/milestones/M139.md`](docs/milestones/M139.md). Also there: M139.1.

#### M140 — heading candidates: the typography fallback, and the route to subsections

Moved to [`docs/milestones/M140.md`](docs/milestones/M140.md).

#### M141 — `get_tables`, rebuilt around checks against the page

Moved to [`docs/milestones/M141.md`](docs/milestones/M141.md).

#### M145 — table titles you can trust, and why there is no chart list *(planned 2026-09-17, built 2026-09-19)*

Moved to [`docs/milestones/M145.md`](docs/milestones/M145.md).

#### M148 — a table no longer starts at a link's underline *(2026-09-19, unplanned)*

Moved to [`docs/milestones/M148.md`](docs/milestones/M148.md).

#### M142 — `extract_markdown`, and why it is ours rather than rented

Moved to [`docs/milestones/M142.md`](docs/milestones/M142.md).

### M143 — the installer stops guessing which scope you meant *(unplanned)* (2026-09-09)

Moved to [`docs/milestones/M143.md`](docs/milestones/M143.md). Also there: M143.1.

### M144 — `httpx2` 2.10.0 → 2.12.0: three advisories in the bridge's HTTP client *(unplanned)* (2026-09-16)

Moved to [`docs/milestones/M144.md`](docs/milestones/M144.md).

### M146 — the shared core is what the bridge loads, and a test pins it *(unplanned)* (2026-09-18)

Moved to [`docs/milestones/M146.md`](docs/milestones/M146.md).

### M147 — the core's folders hold only the core *(unplanned)* (2026-09-18)

Moved to [`docs/milestones/M147.md`](docs/milestones/M147.md).

### M157 — `pyjwt` 2.13.0 → 2.14.0: a malformed key aborts a JWK Set *(unplanned)* (2026-09-29)

Moved to [`docs/milestones/M157.md`](docs/milestones/M157.md).

### M158 — `pyjwt` 2.14.0 → 2.15.1 and `pypdf` 6.17.0 → 6.19.0: crafted-input DoS advisories *(unplanned)* (2026-10-02)

Moved to [`docs/milestones/M158.md`](docs/milestones/M158.md).

### M159 — the dev lock and the bridge's lock pin the same versions *(unplanned)* (2026-10-02)

Moved to [`docs/milestones/M159.md`](docs/milestones/M159.md).

### M162 — the weekly audit passes again, and the bridge keeps its error messages on `mcp` 2.3 *(unplanned)* (2026-11-02)

Moved to [`docs/milestones/M162.md`](docs/milestones/M162.md).

### M149 — small app fixes: a file that will not open, a launch with no window, a window that shrinks to a bar, web links, and a zoom floor that moved (2026-09-19)

Moved to [`docs/milestones/M149.md`](docs/milestones/M149.md).

### M150.1 — links and bookmarks keep, and use, their position on the page (2026-09-20)

Moved to [`docs/milestones/M150.md`](docs/milestones/M150.md). Also there: M150.2.

### M151 — zoom and resize keep the page you are reading (2026-09-21)

Moved to [`docs/milestones/M151.md`](docs/milestones/M151.md).

### M152 — a slow page does not freeze the window: diagnosis and plan (2026-09-23, revised 2026-09-24)

Moved to [`docs/milestones/M152.md`](docs/milestones/M152.md). Also there: M152.1, M152.2.

### M153 — lists and menus leave the desktop on WSL *(unplanned)* (2026-09-24)

Moved to [`docs/milestones/M153.md`](docs/milestones/M153.md).

### M154 — a style change on several shapes changes only that setting *(unplanned)* (2026-09-24)

Moved to [`docs/milestones/M154.md`](docs/milestones/M154.md).

### M155 — shapes without a border *(unplanned)* (2026-09-25)

Moved to [`docs/milestones/M155.md`](docs/milestones/M155.md).

### M156 — pypdf leaves the installer; the fallback engine it served is removed *(unplanned)* (2026-09-26)

Moved to [`docs/milestones/M156.md`](docs/milestones/M156.md).

### M160 — a fit never chases its own scrollbar *(unplanned)* (2026-11-02)

Moved to [`docs/milestones/M160.md`](docs/milestones/M160.md).

### M161 — an encrypted file with no `/ID` still opens after a save *(unplanned)* (2026-11-02)

Moved to [`docs/milestones/M161.md`](docs/milestones/M161.md).

### M163 — a text box another program moved keeps its look *(unplanned)* (2026-11-04)

Moved to [`docs/milestones/M163.md`](docs/milestones/M163.md).

### M164 — a text box looks even, and its text is saved where KlarPDF shows it *(unplanned)* (2026-11-04)

Moved to [`docs/milestones/M164.md`](docs/milestones/M164.md).

### M165 — a drag survives what the page does around it *(unplanned)* (2026-11-06)

Moved to [`docs/milestones/M165.md`](docs/milestones/M165.md).

### M166 — a text box keeps all of its text, in KlarPDF and in the file *(unplanned)* (2026-11-06)

Moved to [`docs/milestones/M166.md`](docs/milestones/M166.md).

### M175 — the pointer changes before a click *(unplanned)* (2026-11-12)

Moved to [`docs/milestones/M175.md`](docs/milestones/M175.md).

### M176 — the bridge says which version it is *(unplanned)* (2026-11-12)

Moved to [`docs/milestones/M176.md`](docs/milestones/M176.md).

### M177 — a SHA256SUMS a user can check *(unplanned)* (2026-11-12)

Moved to [`docs/milestones/M177.md`](docs/milestones/M177.md).

## The open issues, grouped — M149–M152 *(planned 2026-09-19)*

Grouped at the owner's request (2026-09-19: *"plan milestones for all of the issues, except for 352
and 370"*): every open GitHub issue except the two `get_tables` ones, plus
[#373](https://github.com/utyagi24/klarpdf/issues/373) and
[#374](https://github.com/utyagi24/klarpdf/issues/374), found while grouping. **This section is the
grouping and the order only.** Each issue holds its report. The diagnosis and the design are each
milestone's own work, done in the session that builds it, and are added here as that milestone's
entry. **Open questions are settled with the owner in that session** (owner, 2026-09-19), not
decided in advance here.

Issues that share a theme share a milestone. Where a milestone is too large for one PR, it is built
in numbered parts, one PR per part, as M92 was.

| Milestone | Issues | Why these belong together | Surfaces |
|---|---|---|---|
| **M149** Small app fixes | #332, #374, #358, #333 | Four small, independent changes to the app: a file that cannot be opened says so (#332), and a launch that opens no window exits (#374, what #332's fix would run into next at a cold start); the window keeps a usable size (#358); web links open in the browser (#333, which reverses M33/M46's copy-only rule) | app |
| **M150** Links and bookmarks keep and use their position on the page | #362, #373, #361 | All three need the same missing piece: reading where on its page a destination points, and writing it back unchanged. **M150.1** builds it in the core and uses it in the app (#362: a link lands on its page's top; #373: a page move drops or shifts a bookmark's position). **M150.2** is the bridge's side (#361) | .1 core, app, and the bridge's page-move tools through the core; .2 bridge |
| **M151** Zoom and resize keep the page you are reading | #357, #359 | One cause: the reading position is re-read from a view that is stopped at an end of the document | app |
| **M152** A slow page does not freeze the window | #360 | **M152.1**: the two small causes (a page touching the view's edge is drawn in full; every resize step redraws). **M152.2**: drawing pages in the background, the 1.0 gate's Item E (§Deferred E). *Revised in M152's own session: two parts, drawing a slow page in pieces on the window's thread (§M152, 2026-09-24)* | app |

**The order is numeric**, with the parts in order inside each milestone: M150.1 before M150.2,
because the app has to honour a bookmark's position before the bridge writes one (#362's closing
note), and M152.1 before M152.2, so #360's cheap causes are fixed before the large one.

**Found while grouping, for the sessions that build these** (facts that affect a milestone's shape,
not its design):

* **M150:** PyMuPDF's link and outline dictionaries drop or misplace a destination's in-page
  position for several destination forms and rotations; #373 has the measurements.
* **M152.2:** PyMuPDF's documentation says it *"does not support running on multiple threads"*
  (recipes-multiprocessing) and recommends processes, so §Deferred E's "off the UI thread" may mean
  a second process. The milestone decides. *(§M152 first proposed a helper process, 2026-09-23,
  then pieces on the window's thread instead, 2026-09-24, keeping the helper as the fallback.)*

## Linting, dead code and type checking — strategy and roadmap (M167–M173) *(planned 2026-11-08)*

**Where this came from.** The owner asked whether the project uses a linter (2026-11-08). It does
not. They asked for a strategy and a roadmap to review, *"not a quick band-aid"*, with the checks
running routinely in CI, and with [#409](https://github.com/utyagi24/klarpdf/issues/409) (a
dead-code check) folded in. The proposal put three questions to the owner, who answered them the
same day: **no formatter for now, mypy for types, and `lint` and `typecheck` as required checks**.
The answers and their reasons are in §*Decisions* at the end. The numbers M167–M173 are reserved
here so that no other branch takes them (CLAUDE.md, *Every non-trivial change*).

### What the code looks like to a checker (measured 2026-11-08)

Measured on `main` at `2b32906` with Ruff 0.17.4, mypy 2.4.1, Pyright 1.1.414 and ty 0.0.99, run
from a scratch directory on the Windows checkout. Nothing in the repository was changed to get them.

**Nothing is configured.** There is no `[tool.ruff]` or `[tool.mypy]` in `pyproject.toml`, no
linter config file, no linter in `requirements-dev.in`, and no CI step. Two traces show that Ruff
has been run by hand: `.gitignore` lists `.ruff_cache/`, and the code holds 23 `# noqa` comments,
most naming a Ruff code, the best of them with a reason (`# noqa: BLE001 — a malformed /AP is not
worth failing a whole listing over`). Nothing checks them, and it shows. **10 of the 23 are already
stale**: they suppress a finding that no longer occurs. `server.py:287` re-raises, so its `BLE001`
never fires, and `make_icon.py`'s three `E402` and the corpus tools' four suppress nothing. **16 of
the 23 give no reason.**

**The rules that find bugs find almost nothing.** There are 250 tracked Python files and about
83,000 lines. With every Ruff rule on, the counts look large: core 639, bridge 871, app 2,221, tests
22,497, tooling 507. They are style. The tests' total is mostly 5,509 `assert` statements (`S101`),
5,280 unannotated parameters and 1,613 accesses to private members. The pyflakes rules, which are
the closest to "this is a bug", report **28 findings in the whole repository and none in
`klarpdf/`**: 17 unused imports, 4 unused variables, 3 late imports, 2 f-strings without
placeholders, one redefinition and one shadowed name. Every finding that could have been a defect
was read, and none is live:

* `B023` (a closure that captures a loop variable, the classic Qt `connect` bug) at
  `klarpdf/mcp_bridge/tables.py:784`: the closure is called in the same pass of the loop that
  defines it.
* `B033` at `viewer/form_fill.py:28`: `{None, "", "Off", "off", False, 0}` lists `False` and `0`,
  which are one member of a set. Redundant, not wrong.
* `F841` at `packaging/mcp/installer/install.py:419`: `report()` builds a quoted path and never uses
  it. Dead code.
* `S110` (`except Exception: pass`) at three places in the app. Each is deliberate (interpreter
  shutdown, a best-effort fallback), but none says so, where the core's equivalents carry a reason.

**The project's own rules hold today, by habit alone.** No OS-specific call (`sys.platform`,
`os.name`, `ctypes.windll` and the like) appears in the app or the core outside
`platform_integration.py` (CLAUDE.md, *Keep OS-specific code quarantined*). The only `print` in
`klarpdf/` writes to stderr on purpose, because stdout is the bridge's JSON-RPC stream
(`klarpdf/mcp_bridge/server.py:1244`). The whole repository parses as Python 3.11, the wheel's
floor (`requires-python`). No tracked source or document has a BOM or mis-decoded characters.

**Lines are wrapped at 100 by habit.** 1,002 lines are longer than 100 characters, 98 are longer
than 110, and 31 are longer than 120.

**A formatter would rewrite most of the code.** `ruff format` at 100 columns would change 208 of the
250 tracked files, about 10,300 lines. The code already uses double quotes. The churn comes from
two habits: aligned trailing comments, which the formatter collapses to two spaces, and hanging
indents on continuation lines, which it rewraps.

**Type checkers, on the wheel's code** (`klarpdf/`, 32 files):

| Checker | Errors | Of them, PyMuPDF constants missing from its type information |
|---|---|---|
| mypy 2.4.1, default settings, Python 3.11 | 128 | 86 |
| Pyright 1.1.414, `standard` mode | 158 | 82 |
| ty 0.0.99 | 118 | not split out; 98 are `unresolved-attribute` |

The PyMuPDF share is names like `PDF_ANNOT_HIGHLIGHT`, `PDF_WIDGET_TYPE_TEXT` and
`PDF_ENCRYPT_KEEP`. PyMuPDF ships a `py.typed` marker, but those names are not in what it declares.
Of mypy's other 42, the possible `None` dereferences were read, and none is live. Each is an
invariant the code keeps but its types do not state. `klarpdf/model/links_remap.py:198` is guarded by
`keep = dest is not None and …`, and neither mypy nor Pyright follows a guard through a variable.
`klarpdf/mcp_bridge/redaction.py:997` sets `page_invisible` whenever it sets `text`. The app (43
files, Python 3.12) gives mypy 215 errors in 28 files, 75 of them the same constants. That count is
at default settings, which skip the body of any function without annotations. The app has 364
unannotated parameters, so the real number is higher.

**What the numbers say.** There is no hidden backlog of bugs for a checker to clear. Review and a
large test suite have kept the code clean. A checker earns its place here in three other ways:

1. It **keeps** the code this clean, at a cost of seconds per change.
2. It turns rules that exist only as prose in CLAUDE.md into checks that fail.
3. Through types, it states the core's contract with its consumers (CLAUDE.md, *Two consumers share
   one core*). A changed signature in `klarpdf/model/` is then checked against the bridge's calls.
   It is checked against the app's calls only if the app is type-checked too (M173).

### Principles

1. **A rule is switched on only at zero findings, in the PR that gets it there.** No baseline file,
   and no mass `ruff --add-noqa`. So the CI check cannot fail on a PR that did not cause the
   finding, and the set of rules only grows. A baseline would plant hundreds of suppressions that
   look like decisions and are not. The 10 stale comments above are what unchecked suppressions
   become.
2. **Every suppression is a decision with a reason**, in the form the core already uses:
   `# noqa: CODE — reason`. Ruff enforces the first half: `PGH004` refuses a bare `# noqa`, and
   `RUF100` refuses one that suppresses nothing. A small test enforces the reason. The same holds
   for `# type: ignore[code]` and for #409's dead-code whitelist.
3. **The project's rules come before a style guide's.** The first rules switched on are the ones
   CLAUDE.md already states, and each error message names the CLAUDE.md rule it enforces.
4. **One configuration, one pinned version, one answer.** The configuration lives in
   `pyproject.toml`. Each tool is pinned in the dev lock, which `pip-audit` already audits
   (`.github/workflows/audit.yml`), and goes into no shipped artifact. Ruff's `required-version`
   makes a different Ruff (an editor's bundled copy, say) refuse to run rather than give a different
   answer, and a test keeps it equal to the lock's pin.
5. **No rule is checked twice.** The structural tests stay the authority for what they check,
   because they see what a linter cannot: `tests/test_mcp_no_qt.py` (what the bridge loads at run
   time, imports inside tool bodies included), `tests/test_pypdf_is_dev_only.py`,
   `tests/test_mcp_packaging.py` (no Qt in `klarpdf/`) and `tests/test_mcp_docs.py` (the
   tool-description budget). Ruff gets no second copy of them.
6. **Each surface gets the strictness it can bear.** `klarpdf/` is the public wheel and is reached by
   both consumers, so it is the strictest. The app comes next. Tests are meant to poke at internals
   and to assert, so they get the rules that find bugs and not the ones that police style.
7. **A check is not done until it has failed on purpose** (CLAUDE.md, *The thing that verifies the
   code needs verifying too*). Every new CI job and test is seen failing on a planted finding before
   it is kept.

### Tools

**Ruff for linting.** One binary wheel with no plugins, so one pin and one audit entry. It covers
pyflakes, pycodestyle, bugbear, pyupgrade, import sorting and a banned-API list, and the existing
`noqa` comments already use its codes. It checks the whole repository with every rule on in under
half a second, so it never costs what a test run costs.

**vulture for dead code**, as [#409](https://github.com/utyagi24/klarpdf/issues/409) specifies.
Ruff sees dead code only inside one file: an unused import or local variable. It cannot tell that a
function, class or constant nothing calls is dead, and that is #409's gap. #409 already records the
owner's decisions: a static check, not a coverage gate, and code that only tests call counts as dead
unless it is whitelisted with a reason. It also lists the findings to triage. This plan schedules
#409 and does not reopen it.

**mypy for types** (decided, §*Decisions*). It is pure Python, so it sits in the audited dev lock
and gives the same answer in WSL, on Windows and in CI, and VS Code's mypy extension can run the
pinned copy from the project's venv. Pyright was the alternative. Its strength is that Pylance, the
language server VS Code's Python extension installs, is built on Pyright, so the editor and the
check would agree. It has two costs. It is a Node.js program: its PyPI wheel carries the checker but runs it on
whatever Node is on `PATH` (measured: it used a user-installed Node 24.19 here) or downloads one, and
that runtime is outside the lock and outside `pip-audit`. And Pylance bundles its own Pyright, which
agrees with a pinned one only when the versions happen to match. On this code, Pyright's narrowing
gave no advantage: it flags `links_remap.py:198` too. ty is at 0.0.99, before 1.0. It is worth a
look once it reaches 1.0.

**Not adopted:**

* **flake8 with plugins.** Every plugin is a separate pin, audit entry and upgrade, for a subset of
  what Ruff covers.
* **pylint.** It is slow, its useful rules are in Ruff's `PL` set, and the rest is noise on Qt code.
* **The pre-commit framework.** It fetches its hooks from GitHub repositories when it installs them,
  which is a second supply chain beside the audited lock, for a job `invoke lint` does.
* **A coverage gate.** The owner rejected it in #409: a threshold is a tuned number, and code that
  only tests call counts as covered.

### Which rules, where

| Rules | Decision | Why |
|---|---|---|
| `F`, `E4`, `E7`, `E9` (Ruff's default set) | On everywhere (M167) | Unused and undefined names, and syntax errors. 28 findings |
| `PGH004`, `RUF100` | On everywhere (M168) | Suppressions stay decisions (principle 2) |
| `TID251`, banning `sys.platform`, `os.name`, `platform.system`, `winreg`, `ctypes.windll` and `os.startfile` | On, except in `platform_integration.py`, `packaging/`, `tasks.py`, `tools/` and `tests/` (M168) | CLAUDE.md, *Keep OS-specific code quarantined*. Measured with this list: every use is in one of those exempt places |
| `T20` (`print`) | On in `klarpdf/` only (M168) | stdout is the bridge's JSON-RPC stream. The one stderr `print` gets a `noqa` that says so |
| `target-version = "py311"` | The whole repository (M168) | The wheel's floor. For `klarpdf/` this is earlier feedback, since the `bridge-pyver` job already tests 3.11. For the app it is new, and it matters: code moves from the app into the core when the bridge needs it (the markup palette in M101), and 3.12-only syntax would break the wheel the day it moved |
| `B`, `BLE001`, `S110`, `S112`, `PLE`, `PLW`, `RUF007` | On (M170) | Defects that hide: swallowed exceptions, a `zip` that truncates silently, closures over loop variables. 124 findings, each needing a judgement. `RUF007` (`itertools.pairwise`) keeps a neighbour pair from being given `strict=True` |
| `I`, `UP`, `E741`, and `E501` at 120 columns | On (M171), except `E501` in `klarpdf/mcp_bridge/docs.py` | Consistency at a low cost. Most fixes are automatic. `docs.py` is Markdown that the bridge serves as written, and a Markdown table row cannot be split across lines |
| `N` (naming) | Off | Qt's overrides and signals are camelCase by contract. All 41 `N802` and 20 `N815` findings in the app are names like `keyPressEvent`, `sizeHint` and `zoomChanged` |
| `D` (docstring style) | Off | The docstrings here are prose that records measurements and reasons. `D401` alone flags 95 in the core |
| `ANN` (annotations present) | Off | mypy's `disallow_untyped_defs` does this for `klarpdf/` (M172), and checks the types rather than only their presence |
| `S`, apart from `S110` and `S112` | Off | `S101` is pytest's `assert`, and `S603` and `S607` are the installer's and the tools' deliberate subprocess calls |
| `SLF001`, `ARG`, `PLR2004`, `INP001`, `FBT`, `COM812`, `CPY001` and the rest | Off | Measured noise with no class of defect behind it here. Tests reach into internals on purpose (1,613 `SLF001`), and Qt event handlers take arguments they do not use |

**Checks Ruff cannot express** go in small tests beside the existing structural ones (M168). Ruff has
no plugin system, so a project rule that needs more than a banned name is a test that reads the
source with `ast`, the pattern `tests/test_pypdf_is_dev_only.py` set:

* **No call to `get_textbox` in shipped code.** It clips and over-captures (the module docstring of
  `klarpdf/model/page_text.py`, and §M138). Today it appears only in docstrings that explain why not.
* **No `.connect` to a signal of `windowHandle()`** (CLAUDE.md, *Never connect a widget's slot to a
  signal on a QObject that outlives it*). There are none today.
* **No BOM and no mis-decoded text** (`Ã`, `â€`) in tracked source and documents. The code uses `—`,
  `×` and `…` throughout, and a Windows PowerShell 5.1 rewrite once mis-decoded every one of them in
  a file. There are none today.

**Left as prose, on purpose.** Some CLAUDE.md gotchas have no static form that does not guess
(CLAUDE.md, *Compare, don't guess*). `is_encrypted` is right for "does this need a password now" and
wrong for "was this file protected", and only the question tells the two apart. Whether a scene is
rebuilt inside a Qt callback, or a toolbar widget has a fixed size policy, depends on run-time
context. These stay with the tests that cover them.

### How the checks run: CI on every change, and locally before a push

| Check | Where | When | Gate |
|---|---|---|---|
| Ruff | A new `lint` job in `test.yml` on Ubuntu, installing only Ruff at the dev lock's pin. It takes seconds | Every push and every PR, like the rest of `test.yml`, so also on `main` after each merge | Required, once M167 has merged (below) |
| Dead code (vulture) | A test in the suite, as #409 specifies, so it runs in the `pytest` (Linux) and `windows` jobs | Every push and every PR | Already required, through `pytest` and `windows` |
| mypy | A new `typecheck` job on Ubuntu, installing the bridge's lock and mypy | Every push and every PR, from M172 | Required, once M172.1 has merged (below) |
| Locally | `invoke lint` runs Ruff, and mypy from M172. The dead-code test runs with pytest | Before a push, beside the tests for the change (CLAUDE.md, *Run the tests for your change locally*) | None |

* **One Linux job is enough for Ruff.** It reads source, and the operating system it runs on does
  not change its answer. M167 confirms that the Windows checkout gives the same result as CI. mypy
  is different: it treats `if sys.platform == "win32":` branches as dead on Linux. That does not
  matter for `klarpdf/`, which has no such branch, but it does for the app's
  `platform_integration.py`, so M173 has to choose the platform it checks.
* **No matrix.** `test.yml:293` records why: a required check is matched by its exact name, and a
  matrix renames it.
* **Making a check required, in order.** A branch made before a job existed never reports that job,
  and a ruleset cannot tell a check that is not coming from one that has not finished, so the PR
  would wait on *"Expected"* forever (§*Branch rulesets*, *Why the required checks are safe*). So a
  check goes into the ruleset only once its job is on `main`: `lint` after M167 merges, and
  `typecheck` after M172.1. Any branch older than that must merge `main` before it can be merged.
  The change goes through the mirror, `.github/rulesets/main.json`, and the `PUT` that
  §*Branch rulesets* gives. The ruleset is a GitHub setting, so the owner runs the `PUT`, or gives
  the go-ahead for it.
* **The mirror has drifted, and a `PUT` of it would weaken the gate.** It lists `pytest` and
  `emails`, but the live ruleset also requires `bridge`, `windows` and `bridge-windows` (read with
  `gh api` on 2026-11-08). Adding `lint` to the mirror as it stands and running the `PUT` would
  quietly drop those three. So M167 first makes the mirror match the live ruleset, and then adds
  `lint`.
* **No scheduled run.** A pinned linter run again over unchanged code can only repeat its last
  answer. What changes without a commit is the tools themselves, and they change only through a
  bump (below). The weekly `audit` job already covers what does change on its own: new advisories
  against the pins.
* **The editor.** VS Code's Ruff extension uses the project's pinned Ruff when it is set to
  `"ruff.importStrategy": "fromEnvironment"`. Otherwise its bundled copy runs, and
  `required-version` makes that copy report the mismatch rather than give different answers.
* **Considered, not proposed: a Claude Code hook** that runs Ruff after every edit. It would report a
  finding sooner than CI does. But a checked-in `.claude/settings.json` changes every collaborator's
  sessions, and the CLAUDE.md line plus the CI gate catch the same findings before a merge. Worth
  revisiting if `lint` failures in CI turn out to be common.

### Dependencies

* Ruff, vulture and mypy go in `requirements-dev.in` only: not in `requirements.in` (the installer),
  and not in `pyproject.toml`'s `dependencies` (the wheel). They ship nowhere, and the dev lock's
  `pip-audit` step covers them. The dev lock is recompiled off Windows (`invoke lock-dev`; see the
  header of `requirements-dev.in` and M137). That is the one step that needs WSL; the rest runs
  anywhere.
* Ruff is at 0.x and changes its rules between minor releases. A Ruff bump is its own PR (RELEASE.md
  §1), with any new findings fixed in that PR. `required-version` moves with the lock in the same
  commit, and the test fails if the two disagree.
* mypy depends on `typing-extensions`, which the bridge's lock also pins (4.16.0 in both today).
  M159 requires the two locks to pin shared packages to the same version, so M172's lock change has
  to keep them in step.

### The roadmap

| Milestone | What it switches on | Findings to clear (measured 2026-11-08 unless noted) | Surfaces |
|---|---|---|---|
| **M167** Ruff, pinned, on every change | Ruff in the dev lock. `[tool.ruff]` in `pyproject.toml`, with `required-version`. The `lint` job, seen failing on purpose. `invoke lint`. CLAUDE.md's conventions: run Ruff before a push, and the suppression form. The ruleset mirror brought back into line, then `lint` added to it. Rules: `F`, `E4`, `E7` and `E9`, except `E741` | 28: 17 unused imports, 4 unused variables, 3 late imports, 2 empty f-strings, one redefinition, one shadowed name. None in `klarpdf/`; most are in tests | All. No behaviour change |
| **M168** The project's rules, enforced | `TID251` for OS calls, `T20` in `klarpdf/`, `target-version = "py311"`, `PGH004` and `RUF100`. Tests for suppression reasons, `get_textbox`, `windowHandle()` signals and text encoding | The bans: none (the one stderr `print` gets its reason). Suppressions: the 10 stale ones removed, and reasons written for the 8 left without one and for the one `type: ignore` | All. No behaviour change |
| **M169** Dead code: [#409](https://github.com/utyagi24/klarpdf/issues/409) | vulture pinned. `tests/test_no_dead_code.py` over the tracked shipped files, with a whitelist that gives reasons. Triage | #409's list: 46 lines on 2026-09-26, of which `PyPdfEngine` went in M156. Re-measured in M169's own session, not here (CLAUDE.md, *Investigate a defect once*) | Core, app and bridge. A deletion in the core is checked against both consumers |
| **M170** Defects that hide | `B`, `BLE001`, `S110`, `S112`, `PLE` and `PLW` | 124: 45 `zip` calls without `strict=`, 25 blind `except` clauses, 11 `subprocess.run` calls without `check=`, 7 rebound loop variables, 7 needless lambdas, 6 `raise` without `from`, 5 closures over loop variables, 5 unused loop variables, 4 `except: pass`, 4 `global` statements and 5 single findings | Core, bridge, app and tests. **It changes behaviour** wherever `zip(strict=True)` or a narrower `except` is chosen. That policy is settled with the owner in M170's session. M170 also deletes `external = ["BLE001"]` from `pyproject.toml`, which M168 added so that RUF100 would keep the core's six reasoned `noqa: BLE001` comments; once `BLE001` is on, RUF100 sees a stale one again |
| **M171** Consistency | `I`, `UP`, `E741`, and `E501` at 120 columns. No formatter (§*Decisions*) | 71 import blocks, 142 syntax upgrades (111 of them quoted annotations), 50 ambiguous one-letter names such as `l`, and 31 long lines. Mostly automatic | All. No behaviour change |
| **M172** Types for the wheel | mypy in the dev lock, `[tool.mypy]` for `klarpdf/` at Python 3.11, and the `typecheck` job. **M172.1**: the gate at default settings, and `typecheck` added to the ruleset mirror. **M172.2**: `disallow_untyped_defs` | M172.1: 128 errors. 86 are PyMuPDF constants, which one configuration decision handles; 42 to fix. M172.2: about 117 missing annotations (core 88, bridge 29) | Core and bridge. The app is untouched |
| **M173** Types for the app: **go or no-go after M172** | mypy over the app, checked as Windows or on both platforms | 215 errors at default settings (75 PyMuPDF constants), and more once unannotated bodies are checked | App |
| **M174** Lists that say what they hold *(added 2026-11-11, owner)* | mypy's `disallow_any_generics`, over `klarpdf/` and the app: a `tuple`, `dict` or `list` in a type must say what it holds | 445, measured after M173.2: 290 in `klarpdf/` (on M172.2's code, 154 `dict`, 85 `tuple`, 39 `list` and 12 others) and 155 in the app (99 `tuple`, 28 `dict`, 25 `list` and 3 others). Three parts (owner, 2026-11-11): **M174.1** the engine (134), **M174.2** the bridge (156), **M174.3** the app (155) | Core, bridge and app |

**The order, and why.**

* **M167 first.** Everything after it uses its pin, its CI job and its conventions.
* **M168 next.** It gives the most value for the least work: its bans have nothing to fix, and they
  lock in rules that hold today only by habit.
* **M169 (dead code) before M170 and M172.** Those two make a judgement at every finding, and none
  should be spent on code that is about to be deleted. The overlap is real: #409 lists names in
  `page_edits.py` and `virtual_document.py`, which carry 40 and 12 of mypy's 128 errors.
* **M170 before M171.** Defects come before tidiness. M171 is the one milestone that could be
  skipped without losing a guarantee.
* **M172 needs only M167**, and could be built alongside M170 and M171. It comes last because it
  is the largest.
* **M173 is a decision, not a schedule.** After M172, what types cost and what they caught are
  known, and the app has 885 functions to the wheel's 499.
* **M174 comes after M173**, because it states what lists hold in signatures M173 wrote. It was
  not in the 2026-11-08 plan. The owner added it on 2026-11-11 (§*Decisions*).

### Decisions (owner, 2026-11-08)

The proposal asked the owner three questions, and they answered all three the same day. Each answer
keeps the reasoning it was made on, so that a later session does not argue it again (CLAUDE.md,
*Capture the follow-up*).

**1. No formatter, for now.** A formatter removes layout decisions for good and gives outside
contributors one obvious style, which is a real gain for a public repository. Its cost comes all at
once. 208 of the 250 files and about 10,300 lines change in one commit. Every open branch conflicts
with it, so it has to land when no other PR is open. A `.git-blame-ignore-revs` entry, which GitHub
honours, would keep that commit out of `git blame`. And the aligned trailing comments, a deliberate
habit here, would be flattened. Instead, M171 lints the two layout properties that cost something
when they drift: import order and a 120-column ceiling. **Revisit when** outside contributions make
style a review cost. The benefit is mostly in the future and the cost is now, and that is a
judgement, not a measurement.

**2. mypy, not Pyright.** mypy sits inside the audited lock, and Pyright's Node.js runtime does not
(§*Tools*). The cost accepted with it is that VS Code's Pylance, which is built on Pyright, can
disagree with the CI check. VS Code's mypy extension can run the pinned copy from the venv, so the
editor can still show CI's answer. The question also asked whether the app is in scope, and the
answer did not cover that, so it stays M173's go or no-go, decided after M172 when its cost is
known.

**3. `lint` and `typecheck` become required status checks.** A rule is switched on only at zero
findings, so a required check cannot block a PR that did not cause its finding. A check that is not
required can be merged over while it is red. The `audit` job was red at three releases before anyone
noticed (RELEASE.md §3). When and how each check is added is in §*How the checks run*.

**4. Every list says what it holds: M174 (owner, 2026-11-11).** Asked after M173.2, with the count
measured: 445 types say `tuple`, `dict` or `list` without saying what is inside, and mypy checks
nothing done with what is taken out of one. The choices were: the marks lists alone, all 445, or
none, with the reason recorded. M173.2 had just shown the cost of leaving them: a test that tells
mypy a mark's kind did nothing, because the marks list it filters is bare (§M173). The owner
chose all 445, as a new milestone. Most of the work is the bridge's replies, where the honest
type names every key.

Questions that belong to a milestone's own session are settled there, with the owner, as
§*The open issues, grouped* arranged for M149–M152: the `zip(strict=…)` policy (M170), how to treat
PyMuPDF's missing constants (M172), and what goes in #409's whitelist (M169).

### M167 — Ruff, pinned and run on every change (built 2026-11-08)

Moved to [`docs/milestones/M167.md`](docs/milestones/M167.md).

### M168 — the project's rules, enforced (built 2026-11-08)

Moved to [`docs/milestones/M168.md`](docs/milestones/M168.md).

### M169 — dead code, found and kept out (built 2026-11-09, closes [#409](https://github.com/utyagi24/klarpdf/issues/409))

Moved to [`docs/milestones/M169.md`](docs/milestones/M169.md).

### M170 — defects that hide (built 2026-11-10)

Moved to [`docs/milestones/M170.md`](docs/milestones/M170.md).

### M171 — consistency (built 2026-11-11)

Moved to [`docs/milestones/M171.md`](docs/milestones/M171.md).

### M172 — types for the wheel (built 2026-11-11)

Moved to [`docs/milestones/M172.md`](docs/milestones/M172.md).

### M173 — types for the app (built 2026-11-11)

Moved to [`docs/milestones/M173.md`](docs/milestones/M173.md).

### M174 — lists that say what they hold (built 2026-11-11)

Moved to [`docs/milestones/M174.md`](docs/milestones/M174.md).

## Future enhancements (deferred beyond the roadmap)

Captured but not yet scheduled:

- **Showing the document's current image dpi in the Reduce File Size dialog** — **proposed and
  declined 2026-08-22 (owner).** The dialog offers a target dpi without stating what the document
  already is, which makes the choice blind; the owner's call is that the popup's existing wording
  and its actual before → after report are enough. Recorded so the argument is not re-run.

  The finding behind the question stands and is worth keeping, because it sharpens §M111's residue:
  `rewrite_images` is called with `dpi_threshold = dpi + 1` (so that "images *above* the target" is
  exact), which means an image sitting **at** the target is untouched — and measured across the
  corpus, **every image in `spaceX_prospectus.pdf` is already exactly 150 dpi**. The "Screen — 150 dpi"
  preset therefore cannot touch one of them, which is the real reason that file comes back marginally
  larger rather than the vaguer "its images are already efficiently encoded". `f8949.pdf` has no images
  at all, so the lossy tier is wholly inert on it. Others do have headroom: the property brochure runs
  to 442 dpi (median 300), `dhariwal_ipo.pdf` to 246 (median 200).

  Had it been built, the design problem would have been cost rather than wording: effective dpi is a
  property of how large an image is *drawn*, so it needs the placement — `page.get_image_info()` costs
  **0.9–4.5 s** on real documents, too slow to block a dialog, while `page.get_images()` costs
  37–238 ms and knows the count but not the resolution.

- **Touchpad inertia (a fling that decays instead of stopping dead)** — deferred out of M92 by owner
  call (2026-07-30: *"touchpad experience though not perfect I am satisfied with it for now"*). A
  Windows precision touchpad sends `pixelDelta` while the fingers are down and simply **stops** when
  they lift; Qt does not continue the motion, so our scroll ends abruptly where Edge's coasts to
  rest. Edge is not doing anything the OS gives it — Chromium tracks the gesture's velocity and runs
  its own friction decay. Ours would be the same: velocity from the last few `pixelDelta` events, a
  decay animation on gesture end, cancelled by any new input, reusing M92.2's animator.
  **The probe has since run** (`tools/probe_wheel.py`, owner's hardware, 2026-07-31) and settled the
  question it was blocked on: Qt's Windows plugin reports **`NoScrollPhase` for every device,
  touchpad included**, so there is no `ScrollBegin`/`ScrollEnd` to key on and gesture-end must be
  inferred from a quiet gap, exactly as M91.4's coast-mute already does. It also found the touchpad
  sending **376 events with `angleDelta.y` in the -31…-44 range** (1 of them a whole detent) against
  the wheel's uniform ±120 — the fine, continuous stream that inertia would decay.
  **This is also the expensive half of smooth scrolling** (§M92 §Cost): a fling crosses several pages
  in one gesture, and a page rasterise is 4–48 ms synchronously on the UI thread — up to ~120 ms of
  stall inside a ~500 ms fling, ~7 dropped frames, worst at high DPR on image-heavy documents where
  the M87.1 adaptive prefetch has already shrunk the lead to one page. Making a fling stay smooth
  therefore needs *one of*: a velocity-and-direction-aware prefetch (costs one or two extra pages,
  3–40 MB), a cheap scaled placeholder for a page not yet rasterised (deferred item **C**), or
  background rendering (deferred item **E**) — which is the honest reason this is a separate,
  larger piece of work rather than a follow-on constant.
- **Search matches the page's printed text only — the live-model decoupling fix** (owner-reported
  2026-07-24, three symptoms, one cause; **implemented 2026-07-24, PR #190**).
  `SearchController.search` scans the raw source pages (`viewer/pdf_view.py` → `_vdoc.sources[...]`)
  via `page.search_for`, decoupled from the live edit model: our marks are Qt overlays baked to PDF
  only at Save (the render copy even *strips* them), so a newly typed text box isn't found until
  save+reopen; a moved one keeps matching its **old** baked location (the source still holds it there
  until the next Save); and `_on_doc_changed` (`main_window.py`) clears the results list on *every*
  edit to paper over that staleness. PyMuPDF's `search_for` pulls FreeText annotation text **and**
  AcroForm field values into its text layer indistinguishably from body text (verified) — which is
  the only reason our text boxes become findable at all, after save+reopen.

  **Direction A (chosen): search returns content-stream body text only, matching Preview and Edge** —
  neither surfaces annotation or form-field text in search. Behaviour per kind of text:
  - **Excluded:** our text boxes; **foreign FreeText** (Preview/Edge "add text" annotations — excluded
    too, for consistency and to match those apps); and **AcroForm form-field values**. Live, unsaved
    overlays are *already* invisible to search (never in the source), which is the wanted behaviour —
    only *baked* copies need suppressing.
  - **Still findable:** highlighted / underlined / struck-through body text — those annotations add no
    text of their own, they sit over real content, so the hits are genuine content-stream hits.
  - **Redaction:** pending (unsaved) redacted text stays findable (owner call — the redaction is
    reversible until Save, so surfacing it lets the user catch and undo an unintended one); after Save
    it is gone for good (`apply_redactions` is destructive).
  - **Crop:** pending (unsaved) cropped-away text stays findable (owner call, same reversibility logic —
    the source CropBox is unchanged until Save); after save+reopen it is **not** found, because
    `search_for` respects the CropBox (verified) even though crop only *hides* (the text survives in the
    file and returns to search on Remove Crop). This falls out of PyMuPDF and matches Preview — no work
    to keep it.

  **Rejected — Direction B (make text boxes searchable via a materialised search doc):** a cached
  per-source copy that strips stale baked marks and re-bakes the current model annotations so a live
  text box searches at its current rect. Fixes the same three bugs, but costs a **second
  full-document in-memory copy rebuilt on every edit** (it can't share the render copy — that one
  *strips* marks, this one needs them *present*), an O(document) memory/time hit on marked docs, and
  it makes KlarPDF the outlier that finds annotation text no mainstream viewer does. Only worth it if
  typed-text-box searchability is ever wanted as a deliberate differentiator; from owner testing, it
  is not.

  **Implemented as:** (1) the **hit-filter** (chosen over the copy variant for its zero added memory):
  `SearchController.search` drops any hit whose *centre* falls on a source FreeText annotation or a
  form-field widget rect (`viewer/search.py` → `_overlay_text_rects` / `_center_in_any`), computed
  only for the pages that actually produced hits; markup annotations (highlight / underline /
  strikeout) are deliberately absent from the filter, so highlighted body text stays a hit. The rare
  over-exclusion of body text sitting *under* a text box is accepted (the box covers it on screen
  anyway). (2) `_on_doc_changed` no longer clears on every edit — `reload()` now reports whether the
  edit was **structural**, and the search is **kept** across a content-only edit (the body text, and
  therefore every hit, is unmoved) and cleared only when page indices remap. That is cheaper than the
  re-run first sketched: a content edit needs no re-search at all. (`repaint` also guards a stale
  page index, so a delete can never paint a hit off the end of the shrunk document.) Redaction/crop
  needed no code — content-stream text, orthogonal to the filter, behaving exactly as above.
- **New-field form designer (beyond M69):** checkbox / text / dropdown creation is now **scheduled
  (M69)**. What stays deferred is the full designer — field appearance styling, layout tooling, and
  **radio-button groups**, which the owner **rejected (2026-07-18)**: a radio group is several
  widgets sharing one field name with distinct "on" states, PyMuPDF's group creation is historically
  finicky (needs a verification spike before promising), and it's the rarest field type in real
  forms. Revisit only on user demand.
- **Drop-to-open in the main view:** Explorer file-drop is scoped to the Pages sidebar
  (insert-at-slot). A later extension could accept a PDF dropped onto the main page area — insert at
  the current page, or open it as a new window when the view is empty.
- **Outline (bookmark) editing:** M45 *displays* the outline; adding/renaming/deleting entries is a
  natural later verb (entry point via the View menu, since the Outline tab is hidden for TOC-less
  docs — the docs that would need it most). Few free tools offer it.
- **Book-scan crops:** M48 defers odd/even mirrored crops (the gutter alternates sides in two-up
  book scans).
- ~~**Re-encryption on save**~~ → **scheduled**: generalised into **M54 Document encryption** (set /
  change / remove password + carry-through, AES-256) in the R2 release above.
- **Merging Fit Width + Fit Page into one button — considered and rejected (owner, 2026-07-27).**
  Both stay as separate, always-visible buttons. Two alternatives were weighed: a **single toggling
  button** (what Chrome and Edge ship) and a **Fit ▾ split-button** (the idiom §Design budgets already
  sanctions for Markup/Draw/Stamp). The toggle has a structural flaw here — `_fit_mode` is a **three**-
  state value (`None` / `"width"` / `"page"`), and *any* manual zoom sets it to `None`, which M80's
  Ctrl+wheel now does constantly; a two-way toggle cannot express that, and its icon would have to
  answer "what state am I in?" and "what will clicking do?" at once. The split-button avoids that but
  costs two clicks to reach the *other* fit. **Decisive point: neither pays for itself.** The reading
  bar sits at **exactly 10 slots** — at budget, not over — nothing is reported broken, and two labelled
  buttons keep either fit one click away and visible, which is the most discoverable arrangement.
  Revisit only if a later milestone genuinely needs the slot; if it ever is revisited, prefer the
  split-button over the toggle, and land it inside M83 so the zoom cluster changes only once.
- **Consciously rejected (owner, 2026-07 decision session)** — recorded so they aren't relitigated:
  **OCR** (needs a bundled Tesseract — breaks the pinned offline ship-set; if ever revisited, it's
  an optional add-on download, never a core dependency); **cryptographic digital signatures**
  (crypto deps + certificate UX; M63's image signature covers the everyday tier) — **reopened by
  the owner on 2026-09-26** for both the app and the bridge, as
  [#404](https://github.com/utyagi24/klarpdf/issues/404). Checking signatures
  ([#403](https://github.com/utyagi24/klarpdf/issues/403)) brings in a cryptography library
  anyway, which removes most of the first reason;
  **content-stream editing** of baked page content (a different product — redaction removes,
  overlays add); **measurement/dimension tools** (Bluebeam territory — scale-calibration UX is a
  separate large feature); **a "lite" edition** (see §GUI feature roadmap → Design budgets);
  **radio-button groups** (above).
- **Cross-app annotation editing (foreign annotations)** → **scheduled as M66–M68** (R5 above), with
  a staging that supersedes the recommendation at the end of this entry: **delete → move →
  adopt-on-edit**, because the first two verbs are fidelity-safe by construction and cover every
  annotation type, while adoption is confined to modeled types with an explicit degrade warning.
  The analysis below is kept because it documents *why* the boundary exists. Annotation round-trip (M31) re-opens for
  editing **only KlarPDF's own** marks — those stamped with the `/T = "klarpdf"` author tag
  (`KLARPDF_AUTHOR`). A highlight / text-box written by another tool (Preview, Edge, Acrobat, …) is
  **shown** (it stays baked in the page and renders normally) but **not editable**: the read-back in
  `klarpdf/model/page_edits.py:read_klarpdf_annotations` skips any annotation whose title isn't ours, so it
  never enters the editable model, and the viewer overlay / hit-testing only act on the model. This
  is **deliberate**, not a bug: the strip-then-re-add at materialise rewrites a managed annotation
  from our descriptor, and the `TextBox` / `Highlight` model is intentionally narrow (simple
  `/Contents` text, one base-14 family `helv`/`tiro`/`cour`, one size + colour, a fill, a plain black
  border). A foreign box can carry features we don't model — rich text (`/RC` + `/DS`), non-base-14
  or embedded fonts, bold/italic faces, separate border colour, opacity (`/CA`), justification
  (`/Q`), rotation, callout lines (`/IT FreeTextCallout` + `/CL`) — so adopting one into our model
  and re-baking it would **silently drop** whatever we didn't capture. The author tag is exactly
  what lets us strip-and-rewrite *our* marks while passing *theirs* through verbatim (byte-identical),
  which is the safe default. Three options if cross-app editing is wanted later, cheapest first:
  (1) **adopt-on-edit** — keep foreign marks display-only until the user explicitly edits one, then
  tag + manage that single annotation (fidelity loss limited to deliberately-edited boxes; needs a
  per-annotation identity — e.g. record the source xref on the descriptor — so materialise strips
  exactly the adopted one and passes the rest through); (2) **move-only** for foreign boxes (rewrite
  `/Rect` in place, regenerate appearance — no text/style edit); (3) **full model parity** (model
  rich text / arbitrary fonts / opacity / etc. so anything round-trips losslessly — a large effort,
  against the small-audited-model design). *Superseded 2026-07-18:* the scheduled staging (M66–M68)
  leads with **delete** — a fidelity-safe verb this list didn't consider — then (2) move, then (1)
  adopt-on-edit behind a degrade warning; (3) remains rejected.
- **Resolved (no longer open):** duplicate form-field rename + multi-level outline remap are handled
  today — `insert_pdf` auto-renames colliding root fields on merge (confirmed in M1) and
  `klarpdf/model/toc_remap.py` does multi-level remap with orphan repair.

> Note: the view/print/annotate/redact **product features** that earlier sat here all shipped
> (M0–M22); the next tranche is scheduled in §Next roadmap (v0.5.0 → v0.7.0). Only the items above
> remain deferred.
