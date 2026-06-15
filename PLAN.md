# Plan: Local, Offline, Native-Windows PDF Viewer + Page Editor (Python)

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
  `WindowStaysOnTopHint` toggle as a reliable fallback (optionally `QApplication.alert`).
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
- **Remember last page/zoom/scroll per document** in a small local JSON under
  `%APPDATA%\pdfproj\state.json` (auditable, offline), keyed by identity path.

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
-d vendor/wheels` once on a connected machine; commit the wheels (or attach to a tagged release).
After that the **build itself is fully offline** and reproducible from the repo alone.

**3. Freeze (bundle Python + Qt + libs).** **PyInstaller** (pinned) with a checked-in
`packaging/pdfproj.spec`, built `--onedir --noconsole` on Windows (cannot be cross-built from
WSL). `--onedir` (vs `--onefile`) gives faster startup and a clean tree for the installer to lay
down. Output `dist/pdfproj/` contains the embedded CPython, the PySide6/Qt runtime, and PyMuPDF —
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
- installs a Start-Menu shortcut, and registers an **uninstaller** that removes the app **and**
  the registry keys.
- **Setting it as *the* default** is the one manual step Windows reserves to the user (the
  `UserChoice` hash is anti-hijack-protected): the installer adds the handler + Open-With entry;
  the user confirms once via the first "Open With → Always" prompt or Settings → Default apps. The
  installer's finish page links straight to that Settings page.

## Critical files to create

```
pdfproj/
  launcher.py                  # entrypoint: single-instance guard, normalize %1, hand-off/become server
  app.py                       # PdfApp(QApplication): path->window dict, raise/focus, page clipboard, QLocalServer
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
  store/settings.py            # per-document last page/zoom/geometry (JSON in %APPDATA%)
  util/paths.py                # normalize_path() shared by launcher and app
  tests/conftest.py            # builds fixtures with fitz: A.pdf (text layer, bookmark, form field), B.pdf (same-name field)
  tests/test_virtual_document.py  # reorder/delete/insert/move/copy + undo/redo restore ordered[]
  tests/test_materialize.py       # materialize preserves OCR text, remaps TOC to new indices, drops dangling, keeps form fields
  requirements.in              # top-level pins (PySide6, PyMuPDF, pypdf) — the only file edited to change versions
  requirements.txt             # locked: exact == for full tree + sha256 hash per wheel (pip-compile --generate-hashes)
  vendor/wheels/               # downloaded pinned wheels (offline build); committed or attached to a release
  DEPENDENCIES.md              # each lib: purpose, why reputable, license, exact version; + pinned Python/PyInstaller/Inno versions
  packaging/pdfproj.spec       # PyInstaller spec (--onedir --noconsole), icon, data files
  packaging/installer.iss      # Inno Setup: bundles dist/, [Registry] ProgID + .pdf assoc (HKCU), Start Menu, uninstaller
  packaging/build.ps1          # offline build: pip install --require-hashes --no-index, PyInstaller, ISCC — reproducible
```

Deferred (see Future enhancements): `model/links_remap.py` — generalize `toc_remap` to internal
GoTo link annotations.

## Build order (phased)

1. **Setup + dependency lock (on Windows):** install **Python 3.12.x** from python.org (not the
   Store stub; add to PATH). Author `requirements.in`, run `pip-compile --generate-hashes` to
   produce the pinned, hashed `requirements.txt`, `pip download` the wheels into `vendor/wheels/`,
   and write `DEPENDENCIES.md`. Create the dev venv from the lockfile offline:
   `py -3.12 -m pip install --require-hashes --no-index --find-links vendor/wheels -r
   requirements.txt`. Place the project on the **Windows filesystem** (e.g.
   `C:\Users\<you>\pdfproj`) for fast native startup.
2. **Single-instance launcher + window management** — the duplicate-tab fix and resident process.
3. **Viewer** — render, continuous scroll, zoom/fit, rotate, thumbnail sidebar, last-page memory.
4. **Text selection + search** — drag-select/copy and find-in-document.
5. **Edit engine + virtual-document model** — merge/insert, reorder, delete, move/copy across
   windows, with OCR/bookmark/form preservation via materialize-on-save.
6. **Undo/redo + unsaved-changes prompt** — `QUndoStack` commands for every page edit; the
   `closeEvent` Save/Discard/Cancel guard.
7. **Headless model tests** — pytest over the GUI-free model/edit-engine layer (can land as early
   as step 5; no Qt display required). See Verification.
8. **Save / Save As** — atomic `os.replace` for Save; Save As dialog.
9. **Freeze + installer + registry** — `packaging/pdfproj.spec` (PyInstaller `--onedir
   --noconsole`) → `packaging/installer.iss` (Inno Setup) bundling `dist/pdfproj/` and writing the
   `HKCU` ProgID + `.pdf` Open-With association, with a working uninstaller. `packaging/build.ps1`
   ties pin→freeze→install into one offline, reproducible command. **Must be built on Windows**
   (cannot be cross-built from WSL). Setting it as *the* default is the user's one-time confirm.

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
CI and web sessions, unlike the GUI/single-instance/focus checks above (those stay manual on
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
  `DEPENDENCIES.md`.
- **Association via installer:** after install, `.pdf` shows **pdfproj** in Open With with the
  right icon/name; choosing it (and "Always") routes double-clicks through `pdfproj.exe "%1"`
  into the single-instance path. Uninstall removes the app **and** the `HKCU` ProgID keys.
- **Offline runtime** (existing "No network" check) holds for the installed `.exe` too.

## Open items / risks to confirm during implementation

- Verify, on the installed PyMuPDF version, that `insert_pdf(..., widgets=True)` carries form
  fields and renames duplicate root field names (test with fixture `B.pdf`).
- `insert_pdf` does **not** copy the source TOC — outline is rebuilt explicitly; confirm the
  remap handles **multi-level** outlines and named/explicit destinations, not just simple links.
- Text-selection overlay across page boundaries in continuous scroll needs care (anchor/cursor
  hit-testing in scene coordinates); this is the most involved viewer piece and can land in a
  follow-up pass after basic view/scroll/zoom works.

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
