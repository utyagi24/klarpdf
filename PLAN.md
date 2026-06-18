# Plan: Local, Offline, Native-Windows PDF Viewer + Page Editor (Python)

> **Shipped (2026-06-17): `v0.2.0` released** — milestones M0–M15 complete (v0.1.0 = M0–M9; v0.2.0 =
> M10–M15: icons, zoom %, printing, recent docs, form filling on the page-edit layer). Releases:
> [v0.2.0](https://github.com/utyagi24/pdfproj/releases/tag/v0.2.0) ·
> [v0.1.0](https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0). This plan stays the
> spec/source-of-truth. **Next:** the **v0.3.0** "Annotate & Redact" milestones (M16–M18) in
> §Next-release roadmap below; anything beyond lives in §Future enhancements.

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
- **pypdf** (BSD, pure Python) — optional fallback edit engine behind a common interface.

**Why PyMuPDF renders the viewer instead of Qt's `QPdfView`:** `QPdfView` renders, scrolls,
zooms, and highlights search hits, but has **no interactive text selection/copy**. The user
wants select-&-copy (the OCR text). Rendering with PyMuPDF and building a selection overlay
from `page.get_text("words")` boxes delivers selection **and** unifies viewing + thumbnails +
editing on one engine. Text selected this way is exactly the preserved OCR layer.

**AGPL note for the user:** PyMuPDF is AGPL. Building the installer for **your own machines** is
private use — fine. If you ever distribute the installer **publicly**, AGPL requires offering the
corresponding source; since the full source already lives in this public repo, shipping the
installer with a pointer to the repo (and its exact tag/commit) satisfies that. The alternatives
remain: an Artifex commercial license, or a pypdf-only fallback build.

### Key design idea — Virtual-document / edit-list model (lossless)

Never mutate the on-disk PDF while editing. Each window holds a `VirtualDocument`: an ordered
list of `PageRef = (source_id, source_page_index, rotation_override)` plus a registry of open
read-only `fitz.Document` sources.

- All edits are **list edits**: reorder = move; delete = remove; merge/insert = splice in refs
  from another source; rotate = set override.
- **Cross-window move/copy is trivial:** dragging/pasting a page from window B into window A
  just splices B's `PageRef`s (registering B's `fitz.Document` in A's sources). Copy keeps B's
  ref; move also removes it from B. Nothing is rewritten until Save.
- **Materialize-on-Save** (the only write): iterate the ordered list and copy contiguous
  same-source runs via `out.insert_pdf(src, from_page, to_page, start_at=-1, links=True,
  annots=True, widgets=True, final=...)`, apply rotation overrides with `page.set_rotation()`
  (absolute, not additive — set the final angle, don't accumulate), **rebuild the outline**
  (remap old→new page indices, drop bookmarks whose target page was deleted), then
  `out.save(path, garbage=4, deflate=True, clean=True)`. Object-level copies preserve the OCR
  text layer, annotations, and form fields by construction (never rasterize/flatten).

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
already held in `model/virtual_document.py`.

### Single-instance + one-window-per-document (the duplicate-tab fix)

Every launch is invoked by Explorer as `pdfproj.exe "%1"` (the frozen `launcher.py`; `pythonw
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
- **Remember last page/zoom/scroll per document** in a small local JSON under the QStandardPaths
  app-config dir (`%APPDATA%\pdfproj` on Windows, `~/.config/pdfproj` on Linux; auditable, offline),
  keyed by identity path. (Path resolved in `store/settings.py` — Portability hedge #1.)

## Development environment (Hybrid: WSL + Windows)

The owner develops in **WSL2** (Ubuntu, Python 3.12.3, with WSLg so GUIs display on Windows) but
**ships Windows**. Most of this app is cross-platform; only packaging and Windows shell-integration
truly require Windows. So development is **hybrid**, with **git as the bridge** between two
checkouts — neither reaches across the filesystem boundary at runtime.

- **WSL checkout** `/home/<you>/pdfproj` (native Linux fs, fast): canonical dev for all `model/`,
  `viewer/`, `organize/` code, the headless tests, and GUI iteration via **WSLg**.
- **Windows checkout** `C:\Users\<you>\pdfproj` (native NTFS, fast PyInstaller + correct shell
  behavior): `git pull` here for **packaging + Windows-behavior validation only**.
- Push from WSL → pull on Windows. **Do not** edit one checkout with the other OS's tools across
  `\\wsl$` or `/mnt/c` (slow; line-ending/permission churn). A checked-in `.gitattributes`
  (`*.py` LF; `*.ps1`/`*.iss` CRLF) keeps both checkouts clean.

**What runs where** (see Build order for the per-step tags): ~80% is WSL-doable. The GUI-free
`model/` + edit-engine and the **headless pytest suite** run in WSL today; the viewer/selection/
thumbnail GUI iterates via WSLg. Only the **freeze + installer + registry** (step 9) is
Windows-only, and the **single-instance / focus / file-association** behavior (step 2) must be
**validated on Windows** — WSLg runs the *Linux* Qt build, so it smoke-tests logic but is not
authoritative for Windows focus-stealing rules or the Explorer `%1` launch path.

**Windows prerequisite (one-time):** install **Python 3.12.x from python.org** (add to PATH / `py`
launcher). The Windows machine currently has only the **Microsoft Store stub** `python.exe`, which
is not usable for the build. WSL already has 3.12.3.

**Dependency discipline applies to the _shipped Windows build_, not WSL dev:**
- The authoritative `requirements.txt` (exact `==` + `--hash=sha256`) and `vendor/wheels/` are the
  **Windows** set (`win_amd64`), produced on Windows — the auditable ship artifact.
- The **WSL dev venv** installs the **same pinned `==` versions** but **by version only, not by
  hash**: the Windows lockfile's `--hash=sha256` lines pin specific *`win_amd64`* wheels, and pip on
  Linux resolves *`manylinux`* wheels with different hashes, so `pip install --require-hashes -r
  requirements.txt` would **fail on Linux by design**. Dev therefore installs from a small
  unhashed `requirements-dev.txt` (or `pip install <pkg>==<ver> ...`) carrying the same versions —
  derived from the same `requirements.in`. It is dev tooling, not shipped, so it need not be
  hashed/vendored/offline; the offline+hashed guarantee is the Windows ship build's job.

## Packaging, dependencies & installer (offline, pinned, auditable)

This satisfies the install/offline/auditability requirements. Three layers — **pin → freeze →
install** — each reproducible and offline.

```mermaid
flowchart LR
  A["requirements.in<br/>(human-edited, top-level pins)"] -->|"pip-compile<br/>--generate-hashes"| B["requirements.txt<br/>(exact ==, sha256 per wheel)"]
  B -->|"pip download<br/>(once, online)"| C["vendor/wheels/<br/>(committed/released)"]
  C -->|"pip install --no-index<br/>--require-hashes (offline)"| D["build venv<br/>(Win, Python 3.12.x pinned)"]
  D -->|"PyInstaller (pinned)<br/>--onedir --noconsole"| E["dist/pdfproj/<br/>(runtime + Qt + libs)"]
  E -->|"Inno Setup (pinned .iss)"| F["pdfproj-setup.exe<br/>(bundles everything)"]
  F -->|"installs + writes ProgID/.pdf assoc (HKCU)"| G["target machine<br/>no Python, no network"]
```

**1. Dependency pinning & integrity (versions never drift).**
- `requirements.in` lists the few top-level libs (PySide6, PyMuPDF, pypdf). `pip-compile
  --generate-hashes` (from **pip-tools**, itself pinned) produces `requirements.txt` with **exact
  `==` versions for the full transitive tree plus a `--hash=sha256:` for every wheel**.
- All installs use `pip install --require-hashes --no-index --find-links vendor/wheels` — pip
  **refuses** anything whose version or hash doesn't match the lockfile, so a rebuild can never
  pull a newer/tampered package. A version bump is an explicit edit to `requirements.in` →
  re-compile → re-vendor → review the diff (a reviewable PR), never automatic.
- The app **never** invokes pip or fetches anything at runtime; the frozen bundle carries fixed
  versions. Also pin **Python (3.12.x exact)**, **PyInstaller**, and the **Inno Setup** version
  used, recorded in `DEPENDENCIES.md`.

**2. Vendored wheels (offline build).** Run `pip download -r requirements.txt --only-binary=:all:
-d vendor/wheels` to fetch the `win_amd64` set. The wheels are **not committed** (binary bloat /
GitHub's 100 MB-per-file limit); `vendor/wheels-sources.md` records each wheel's version + sha256 +
source URL so the exact set is reproducible, and each release archives them as assets. Once
fetched, the **build itself is fully offline** (`--no-index --require-hashes`) and reproducible
from the lock alone.

**3. Freeze (bundle Python + Qt + libs).** **PyInstaller** (pinned) with a checked-in
`packaging/pdfproj.spec`, built `--onedir --noconsole` on Windows (cannot be cross-built from
WSL). `--onedir` (vs `--onefile`) gives faster startup and a clean tree for the installer to lay
down; a secondary `--onefile` build also ships as a portable, run-anywhere `.exe` (see §5 — it
trades slower per-launch startup and no auto-association for zero-install portability). Output
`dist/pdfproj/` contains the embedded CPython, the PySide6/Qt runtime, and PyMuPDF —
no system Python needed. (Dependency versions are reproducible via the hashes; note honestly that
PyInstaller output is **not byte-identical** across builds due to timestamps — version-repro, not
bit-repro.)

**4. Installer + registry (one self-contained `setup.exe`).** **Inno Setup** (free, mature,
widely used; script-driven) with a checked-in `packaging/installer.iss` that:
- bundles the entire `dist/pdfproj/` tree (so the `.exe` carries every dependency — no downloads
  at install time, satisfying offline-install),
- `[Registry]` writes a **per-user ProgID** under `HKCU\Software\Classes` (no admin):
  `pdfproj.Document` with `shell\open\command = "{app}\pdfproj.exe" "%1"`, a `DefaultIcon`, a
  `FriendlyAppName`, and `.pdf\OpenWithProgids` so the app appears in **Open With**,
- installs a Start-Menu shortcut, and registers an **uninstaller** that removes the app, the
  registry keys, **and the per-user config `%APPDATA%\pdfproj`** (an `[UninstallDelete]` wipe —
  a clean removal was chosen over leaving the view-state JSON behind).
- **Setting it as *the* default** is the one manual step Windows reserves to the user (the
  `UserChoice` hash is anti-hijack-protected): the installer adds the handler + Open-With entry;
  the user confirms once via the first "Open With → Always" prompt or Settings → Default apps. The
  installer's finish page links straight to that Settings page.

**5. Build & release pipeline (GitHub Actions; manual + tag-triggered).** A checked-in
`.github/workflows/release.yml` runs on a **`windows-latest`** runner, triggered both by
**`workflow_dispatch`** (a "Run workflow" button in the Actions tab, also `gh workflow run`) and by
a **`push` of a `v*` tag**. It drives the same one-command `packaging/build.ps1` (also runnable
locally) end-to-end: re-fetch + hash-verify the `win_amd64` wheels from `requirements.txt` (not
committed — see §2) → clean build venv (`--require-hashes --no-index`) + pinned PyInstaller → **two
artifacts** from `packaging/pdfproj.spec`: the **`--onedir --noconsole`** tree for the installer and
a portable **`--onefile` `pdfproj-portable.exe`** → Inno Setup (`ISCC installer.iss`) →
`pdfproj-setup.exe` → smoke-test (launch + open a PDF).
- **Versioning:** one source of truth (`version.py`) feeds the PyInstaller exe metadata, the Inno
  `AppVersion`, and the git tag; a bump is an explicit edit + a new tag.
- **Release:** on a `v*` tag the workflow publishes a **GitHub Release** attaching
  `pdfproj-setup.exe`, the portable `pdfproj-portable.exe`, a **`SHA256SUMS`** file, and the
  **vendored wheels** (each release archives its exact build inputs and carries the AGPL
  "corresponding source" pointer at that tag). The runner re-fetches wheels from PyPI, so the
  *runner* build is not offline — but the produced installer is fully self-contained, and the
  authoritative **offline** build + clean-machine install stay verified locally (see Verification).
- **Code signing** is a deferred enhancement: an Authenticode sign step (cert from GitHub Secrets)
  slots in just before packaging; until then the unsigned `.exe` shows a one-time SmartScreen
  "unknown publisher" prompt — acceptable for private/own-machine use.

## Portability (Windows-first ship, Linux-ready seams)

Decision: **ship Windows only** for the first release, but bake in **near-zero-cost seams** so a
future Linux (or macOS) port is small. The architecture is *incidentally* portable — Qt + MuPDF are
the cross-platform engine and the `model/` layer is GUI-free — so the reuse story is strong as long
as OS-specific code stays quarantined.

**Cheap hedges (do now, ≈zero cost):**
1. `store/settings.py` uses `QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)`
   instead of a literal `%APPDATA%\pdfproj` — Qt resolves it per-OS (AppData on Windows,
   `~/.config` on Linux). No behavior change for the Windows ship, just the portable form.
2. A thin **`platform_integration.py`** seam holds the only OS-specific app behaviors —
   `single_instance_server_name()` and `activate_window(win)` (the `WindowStaysOnTopHint`/`alert`
   focus shims). `app.py`/`launcher.py` call the abstraction; no `WindowStaysOnTopHint` inline.
   Windows impl now; Linux stub later. A `register_file_association()` slot also lives here, but on
   **Windows it is effectively unused** — the Inno Setup installer writes the `.pdf`/ProgID
   association (see Packaging); the function exists mainly for a future Linux `xdg-mime` path and an
   optional dev/source-run convenience, so the two sections don't contradict.
3. **All OS coupling stays inside `packaging/` + `platform_integration.py`** — registry/`.iss`/
   `.ps1` never leak into `launcher.py`/`app.py`.
4. `util/paths.py` `normalize_path()` stays the **single identity chokepoint** so the
   case-sensitivity switch (Windows case-fold vs Linux case-sensitive) is one function.

**Reuse / rewrite map (if Linux is targeted later):**
- **Reusable unchanged:** all `model/` (`virtual_document`, `edit_commands`, `edit_engine`,
  `toc_remap`, materialize), all `viewer/`, `organize/thumbnail_panel`, `main_window.py`, the
  `QUndoStack` undo/redo, all `tests/`, `requirements.in`.
- **Small platform branches:** `util/paths.py` (case semantics), `store/settings.py` (config path,
  solved by hedge #1), `app.py`/`launcher.py` focus/raise shims (Wayland forbids programmatic
  activation) + the IPC socket path (same Qt API, different underlying transport).
- **Full rewrite per OS:** `packaging/installer.iss` → AppImage/Flatpak/.deb; `build.ps1` →
  `build.sh`; HKCU registry association → MIME + `.desktop` (`xdg-mime`); `vendor/wheels/` →
  `manylinux` wheels; `pdfproj.spec` → Linux conditionals.
- **Rule of thumb:** the *application* ports almost for free; the *installer + file-association +
  window-manager glue* is rewritten per OS.

## Critical files to create

```
pdfproj/
  launcher.py                  # entrypoint: single-instance guard, normalize %1, hand-off/become server
  app.py                       # PdfApp(QApplication): path->window dict, page clipboard, QLocalServer; raise/focus via platform_integration
  platform_integration.py      # OS seam (portability hedge): single-instance name, activate_window() focus shims; register_file_association() slot (Windows uses the installer, so unused there; for future Linux xdg-mime). Windows now / Linux stub later
  main_window.py               # MainWindow: View + Organize modes, toolbar/menu, holds a VirtualDocument;
                               #   owns the QUndoStack (Ctrl+Z/Y) and the closeEvent save-on-close prompt
  viewer/pdf_view.py           # QGraphicsView continuous-scroll renderer (PyMuPDF pixmaps, lazy, zoom/fit/rotate)
  viewer/text_selection.py     # word-box selection overlay + clipboard copy (feature QPdfView lacks)
  viewer/search.py             # page.search_for highlighting + hit navigation
  organize/thumbnail_panel.py  # grid bound to ordered[]: drag-reorder, cross-window drag (QDrag MIME), cut/copy/paste, delete
  model/virtual_document.py    # VirtualDocument + PageRef; all list-edit ops, dirty tracking
  model/edit_commands.py       # QUndoCommand subclasses (reorder/delete/insert/rotate/paste): snapshot+restore ordered[]
  model/edit_engine.py         # EditEngine interface; PyMuPDFEngine (default) + PyPdfEngine (fallback); materialize-on-save
  model/toc_remap.py           # outline snapshot + old->new page remap + drop-dangling
  store/settings.py            # per-document last page/zoom/geometry — JSON via QStandardPaths AppConfigLocation (%APPDATA% on Windows, ~/.config on Linux)
  util/paths.py                # normalize_path() — SINGLE identity chokepoint (case-fold on Windows; one-line switch for Linux)
  tests/conftest.py            # builds fixtures with fitz: A.pdf (text layer, bookmark, form field), B.pdf (same-name field)
  tests/test_virtual_document.py  # reorder/delete/insert/move/copy + undo/redo restore ordered[]
  tests/test_materialize.py       # materialize preserves OCR text, remaps TOC to new indices, drops dangling, keeps form fields
  requirements.in              # top-level FLOOR pins (e.g. PyMuPDF>=1.25.5); pip-compile makes the exact == lock. Only file edited to bump
  requirements.txt             # locked Windows ship: exact == for full tree + sha256 hash per win_amd64 wheel (pip-compile --generate-hashes)
  requirements-dev.txt         # WSL dev: same == versions, NO hashes (Linux manylinux wheels differ); version-only install for iteration + tests
  vendor/wheels/               # pinned win_amd64 wheels (offline build) — NOT committed; re-fetched from the lock, archived as release assets
  vendor/wheels-sources.md     # auditable record: version + sha256 + source URL per wheel (regenerated by vendor/gen-sources.py)
  DEPENDENCIES.md              # each lib: purpose, why reputable, license, exact version; + pinned Python/PyInstaller/Inno versions
  .gitattributes               # *.py eol=lf; *.ps1/*.iss eol=crlf — clean across the WSL + Windows checkouts
  version.py                   # single source of version → PyInstaller exe metadata, Inno AppVersion, git tag
  packaging/pdfproj.spec       # PyInstaller spec → --onedir (installer) + --onefile (portable .exe), icon, data files
  packaging/installer.iss      # Inno Setup: bundles dist/, [Registry] ProgID + .pdf assoc (HKCU), Start Menu, uninstaller (+ [UninstallDelete] %APPDATA%\pdfproj)
  packaging/build.ps1          # offline build: --require-hashes --no-index, PyInstaller (onedir+onefile), ISCC — reproducible
  .github/workflows/release.yml # CI: windows-latest; workflow_dispatch + v* tag → build.ps1 → GitHub Release (setup.exe + portable + SHA256SUMS + wheels)
```

Deferred (see Future enhancements): `model/links_remap.py` — generalize `toc_remap` to internal
GoTo link annotations.

## Build order (phased)

Each step is tagged **(WSL)** / **(WSLg)** / **(Windows)** per the Development environment section.
~80% runs in WSL; only step 9 is Windows-only, and step 2 needs a Windows validation pass.

1. **Setup + dependency lock — (split: WSL + Windows).**
   - *WSL (dev):* create a Python 3.12 venv and install the pinned versions (online once) for fast
     iteration + headless tests. Canonical source is the WSL checkout `/home/<you>/pdfproj`.
   - *Windows (ship lock):* install **Python 3.12.x** from python.org (not the Store stub; add to
     PATH). Author `requirements.in`, run `pip-compile --generate-hashes` → the pinned, hashed
     `requirements.txt`; `pip download --only-binary=:all:` the `win_amd64` wheels into
     `vendor/wheels/`; write `DEPENDENCIES.md`. The offline build runs from the Windows checkout
     `C:\Users\<you>\pdfproj` (via git): `py -3.12 -m pip install --require-hashes --no-index
     --find-links vendor/wheels -r requirements.txt`.
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
   Save As dialog.
9. **Freeze + installer + release pipeline — (Windows ONLY).** `packaging/pdfproj.spec` (PyInstaller
   `--onedir --noconsole` for the installer **plus a `--onefile` portable `.exe`**) →
   `packaging/installer.iss` (Inno Setup) bundling `dist/pdfproj/`, writing the `HKCU` ProgID + `.pdf`
   Open-With association, with an uninstaller that **also wipes `%APPDATA%\pdfproj`**.
   `packaging/build.ps1` ties pin→freeze→install into one offline, reproducible command;
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
| **M1** Correctness core ⭐ | 5 + 7 | WSL | `model/` + headless tests **green** — OCR/TOC/forms/undo preserved |
| **M2** Viewer | 3 | WSLg | open a PDF; scroll, zoom/fit, rotate, thumbnails, last-page memory |
| **M3** Selection + search | 4 | WSLg | drag-select → clipboard copy; find + next/prev |
| **M4** Editing loop | 6 + 8 | WSLg | reorder/delete/merge + cross-window cut/copy/paste (organize panel) + undo/redo; Save/Save As; dirty-close prompt |
| **M5** Single-instance | 2 (logic) | WSL | second launch hands off to first (WSLg smoke test) |
| **M6** Windows ship lock | 1 (Win) | Windows | python.org 3.12; hashed `requirements.txt`; vendored `win_amd64` wheels; `DEPENDENCIES.md` |
| **M7** Windows validation | 2 (validate) | Windows | single-instance/focus/Open-With behave on real Windows; GUI fidelity pass |
| **M8** Freeze + installer + CI | 9 | Windows | `build.ps1` (+ `.github/workflows/release.yml`) → PyInstaller (onedir + onefile) → Inno Setup → `pdfproj-setup.exe` + portable `.exe` |
| **M9** Verify + release | Verification § | Windows | offline build + clean-machine install + no-network audit; portable-exe check; uninstall wipes app + keys + `%APPDATA%` → tag `v*` → GitHub Release |

⭐ **M1 is the keystone** — most of the correctness risk (lossless edits, TOC remap, dup form-field
handling), GUI-free, fully testable in WSL/CI. The packaging scripts (`build.ps1`, `installer.iss`,
`pdfproj.spec`) are *authored* during M0–M5 but only *executed* on Windows.

**Progress tracking.** `PROGRESS.md` (repo root) is the durable, at-a-glance checklist; each
milestone PR ticks its box and links the PR. `CLAUDE.md` routes any resuming agent: read
`PROGRESS.md` first (current state), then this section + the relevant Build-order step.

**Windows handoff.** git is the only bridge — never edit across `\\wsl$` / `/mnt/c`. Code flows
**WSL → Windows** (push here, pull there); ship artifacts flow **Windows → repo** (the hashed
`requirements.txt`, `vendor/wheels/`, `DEPENDENCIES.md`, and `setup.exe` are produced on Windows and
committed back, keeping the repo canonical).
- *One-time Windows setup (M5 → M6):* install **Python 3.12.x from python.org** (Store stub won't
  build); install **git + an SSH key** (or HTTPS + `gh`) and `git clone` to `C:\Users\<you>\pdfproj`;
  install **Inno Setup** (pin its version in `DEPENDENCIES.md`).
- *Per handoff:* `git pull` → `py -3.12 -m pytest -q` (the core passes on Windows Python too) →
  `packaging\build.ps1` → validate Windows-only behaviors → commit Windows artifacts back.
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

- **Version pinning holds:** `pip install --require-hashes --no-index -r requirements.txt` into a
  fresh venv succeeds; then flip one hash/version in `requirements.txt` and confirm pip **aborts**
  (proves nothing can silently drift). Rebuilding twice yields the same dependency versions.
- **Offline build:** disconnect the network (or build inside a no-egress shell) and run
  `packaging/build.ps1` end-to-end from `vendor/wheels/` — produces `pdfproj-setup.exe` with no
  downloads.
- **Offline install on a clean machine:** on a Windows VM with **no Python and networking
  disabled**, run `setup.exe` → installs and launches; the dependency set bundled matches
  `DEPENDENCIES.md`. (**Windows 10 Home has no Windows Sandbox** — use a free VirtualBox VM, a spare
  machine, or a fresh local user account with networking disabled.)
- **Association via installer:** after install, `.pdf` shows **pdfproj** in Open With with the
  right icon/name; choosing it (and "Always") routes double-clicks through `pdfproj.exe "%1"`
  into the single-instance path. Uninstall removes the app, the `HKCU` ProgID keys, **and
  `%APPDATA%\pdfproj`** — nothing left behind.
- **Portable build:** `pdfproj-portable.exe` (the `--onefile` asset) launches from any folder on a
  clean machine and opens a PDF with no install (slower first paint, no auto-association — both
  expected).
- **Offline runtime** (existing "No network" check) holds for the installed `.exe` too.

## Open items / risks to confirm during implementation

- ✅ **Confirmed (M1, PyMuPDF 1.27.2.3):** `insert_pdf(..., widgets=True)` carries form fields,
  and the default (`join_duplicates=0`) **auto-renames** the colliding root field — merging
  `B.pdf`'s `name` field after `A.pdf`'s yields `name` + `name [NN]`, both preserved, neither
  dropped/overwritten. So no `xfail` was needed. Asserted in `tests/test_materialize.py`
  (`test_merge_preserves_both_form_fields_dedup`), cross-checked via pypdf `get_fields()`.
- ✅ **Confirmed (M1):** `insert_pdf` does **not** copy the source TOC — `model/toc_remap.py`
  rebuilds the outline explicitly, handling **multi-level** outlines (level-continuity repair /
  orphan promotion on drop) and explicit destinations (dest-page remap). Covered by
  `tests/test_toc_remap.py` + `tests/test_materialize.py`. Named-destination outlines remain
  untested (the fixture uses page destinations); revisit if a real doc uses them.
- Text-selection overlay across page boundaries in continuous scroll needs care (anchor/cursor
  hit-testing in scene coordinates); this is the most involved viewer piece and can land in a
  follow-up pass after basic view/scroll/zoom works.

## Next-release roadmap (v0.2.0 → v0.3.0)

Planned after v0.1.0. Same discipline as M0–M9 (one PR per milestone, `PROGRESS.md` tracks state).
The work splits into a low-risk polish release and a keystone content-editing release. **A key
property: none of it adds a third-party dependency** — annotations/redaction/forms are native
PyMuPDF, printing is `QtPrintSupport` (already inside the vendored PySide6 wheel). So
`requirements.in` is unchanged → **no re-compile, no re-vendor; the hashed offline lock stays
exactly as shipped.** The only freeze change to verify is that `QtPrintSupport` + its print plugins
survive PyInstaller (it is not in the spec `excludes`, so it ships today — confirm during M12).

### The page-edit layer (the one new architectural concept)

Icons, zoom %, recent docs, and printing bolt onto the existing UI/viewer cleanly. But form-fill,
highlight, text-box, and redaction **edit content *inside* a page** — which the model has never
done. The hard constraint: source `fitz.Document`s are **shared across windows** (cross-window
paste registers another window's source in `model/virtual_document.py`), so we **must never mutate
a source page in place** — it would corrupt the other window and any `VirtualDocument` referencing
that page.

The fix is to treat content edits exactly like the existing list edits — **immutable descriptors
stored in the model, applied only at materialize, on the output copy:**

- **Model (`model/page_edits.py`, new):** frozen descriptors (form-field values, annotations,
  redaction rects) attached per page and snapshotted alongside `ordered[]`, so the existing
  `QUndoStack` snapshot/restore in `model/edit_commands.py` keeps undo/redo working unchanged.
  Sources stay read-only.
- **Save (`model/edit_engine.py`):** after `insert_pdf` copies each page, a post-copy pass applies
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

v0.2.0 builds this layer with **form-fill as its first, simplest consumer**; v0.3.0's annotations
and redaction slot into the same mechanism.

### v0.2.0 — "Polish, Print & Forms"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M10** Icons | App `.ico` (closes the open follow-up) + toolbar icons for undo/redo, zoom-in/out, cut/copy/paste. `QApplication.setWindowIcon`; wire `icon=` into `packaging/pdfproj.spec` and `SetupIconFile` into `packaging/installer.iss`. | WSLg + **Win** | App has a real icon (taskbar + installed); toolbar buttons are iconographic |
| **M11** Zoom UX | `zoomChanged` signal from `PdfView`; live "150%" indicator (toolbar combo); **Actual Size / 100%** action (Ctrl+0); preset levels. Extends `PdfView.set_zoom`. | WSLg | Magnification % always visible; one click resets to 100% |
| **M12** Printing | `QPrintDialog` + `QPrinter`; render each page via PyMuPDF at printer DPI, paint with `QPainter`; page-range + current-page. Confirm `QtPrintSupport` + plugins survive the freeze. | WSL logic; **Win** print validation | System print dialog prints the open doc correctly |
| **M13** Recent documents | MRU list in `store/settings.py`; dynamic **File ▸ Open Recent** submenu (app-global, refreshed across windows); dedupe via `normalize_path`, drop missing files, "Clear Recent". Reopen routes through `app.open_document` (free single-instance dedupe). | WSL | Recent files listed; reopen in one click |
| **M14** ⭐ Page-edit layer + form fill | The layer above; first consumer fills existing AcroForm fields (text/checkbox/radio/choice). New `model/page_edits.py`, `viewer/tools.py`; click-to-edit field UI; headless materialize tests. | WSL (model+tests) + WSLg | Fill a form's fields, save, reopen with values intact |
| **M15** Verify + release | Headless suite green; Windows validation (print, icon, forms in the frozen build); tag **v0.2.0**. Opportunistically fold in the carried **CI Node-24 action bumps** + **code signing**. | **Win** | Matrix green → v0.2.0 released |

### v0.3.0 — "Annotate & Redact" (keystone release)

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M16** ⭐ Annotations | Text **highlight** (reuse word-box selection → `add_highlight_annot`) + **text box** (`add_freetext_annot`), on the M14 layer; tool palette + interaction modes; headless materialize tests. | WSL + WSLg | Highlight text & drop a text box; both bake into the saved PDF |
| **M17** ⭐ Redaction | Draw rect → mark → `apply_redactions` at save (**true destructive**). **Security verification:** assert the saved output has *no recoverable text/content* under the box (`fitz.get_text` + Poppler `pdftotext` cross-check, a different engine than the writer). Highest-risk milestone. | WSL (model+verify) + WSLg | Redacted content is provably gone, not merely covered |
| **M18** Verify + release | Full annotation/redaction verification matrix + Windows validation; tag **v0.3.0**. | **Win** | Matrix green → v0.3.0 released |

⭐ = keystone — most correctness/security risk, GUI-free core, fully headless-testable (the role M1
played for v0.1.0).

### Scope decisions (confirmed with the owner)

- **Form filling:** fill **existing** AcroForm fields only — not a new-field designer.
- **Redaction:** **true destructive** removal (`apply_redactions`), never visual-cover-only (which
  leaves extractable text — a data-leak trap).
- **Annotations:** **fire-and-forget at save** — they live in the edit-list while the doc is open
  (full undo/redo) and bake into the saved PDF; pdfproj does not re-parse saved annotations back
  into the model for round-trip re-editing (deferred — see Future enhancements).

### New files (extends the Critical-files map)

```
model/page_edits.py            # frozen per-page edit descriptors (form values, annots, redactions); snapshotted with ordered[]
viewer/tools.py                # interaction-mode controller: select / highlight / text-box / redact / form (select = default)
viewer/printing.py             # QPrinter render: PyMuPDF pixmap per page at printer DPI → QPainter
ui/icons/ + ui/icons.py        # bundled toolbar icon assets + a resolver
packaging/pdfproj.ico          # app icon, referenced by pdfproj.spec (icon=) + installer.iss
tests/test_page_edits.py       # descriptor snapshot/restore + undo/redo
tests/test_form_fill_materialize.py   # filled field values survive materialize
tests/test_annotations_materialize.py # highlight + free-text bake into output
tests/test_redaction.py        # apply_redactions truly removes content (leak check)
tests/test_recent.py           # MRU dedupe / drop-missing / cap
```
Extended in place: `model/edit_commands.py` (new commands), `model/edit_engine.py` (post-copy edit
pass), `store/settings.py` (MRU), `main_window.py` (menus/toolbar/print), `viewer/pdf_view.py`
(zoom signal + edit-aware render cache). **Portability** stays clean: only icon-in-installer touches
OS, and that is already quarantined in `packaging/`; nothing new leaks into `app.py`/`launcher.py`.

## Future enhancements (deferred)

Out of scope for the first build, captured so they can be picked up cleanly later:

- **Encrypted / password-protected PDFs:** on open, detect `doc.needs_pass`, prompt the user,
  and call `doc.authenticate(pw)` before registering the source. Materialize-on-save already
  writes a fresh document, so output is unencrypted unless we later add re-encryption.
- **Internal GoTo-link remap:** `LINK` annotations that jump to another page break on
  reorder/delete exactly like the outline does. Generalize `model/toc_remap.py` into
  `model/links_remap.py` — same old→new index map, drop links whose target page was deleted —
  and apply it during materialize. (Today `insert_pdf(links=True)` copies links but does not
  fix cross-run targets.)
- **Duplicate form-field rename + multi-level outline:** promote the two TOC/forms items from
  "Open items" above into real handling once the fixture tests confirm the installed PyMuPDF's
  behavior (auto-rename colliding root field names; remap named/explicit destinations across
  multi-level outlines).
- **Annotation round-trip editing:** v0.3.0 annotations are fire-and-forget (bake in at save). A
  later pass could re-parse existing annotations from a source on open into `model/page_edits.py`
  so saved highlights/text-boxes/redactions can be moved, re-edited, or removed in-app.
- **New-field form designer:** v0.2.0 fills *existing* AcroForm fields only; adding brand-new
  fields (layout, types, appearance streams) is a larger, separate effort.

> Note: the view/print/annotate **product features** that earlier sat here are now scheduled in
> §Next-release roadmap (v0.2.0 → v0.3.0); only the items above remain deferred.
