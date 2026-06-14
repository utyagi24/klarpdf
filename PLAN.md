# Plan: Local, Offline, Native-Windows PDF Viewer + Page Editor (Python)

## Context

On macOS the user relied on **Preview** to view PDFs and to splice/split them — drag one
PDF onto the end of another, rearrange or delete pages, and move/copy individual pages
between documents, then save or save-as. After moving to **Windows** they have no
trustworthy equivalent: third-party utilities and online services exist, but the user does
**not trust them** with their documents.

We will build a **single, self-contained desktop app** the user runs from readable Python
source on their own machine. It will be their **default PDF viewer** (the ~90% use) and also
do the occasional **splice/split** editing. Hard requirements gathered from the user:

- **Default Windows PDF viewer**, full Preview-like viewing experience.
- **Single instance / one window per document** — re-clicking an already-open file must
  **focus the existing window**, never spawn a duplicate (their current pain: duplicate
  browser tabs).
- **Drag-and-drop** page reorder **and** page-level **cut/copy/paste** between documents.
- Merge/splice (append + insert at a position), reorder, delete, move/copy pages, Save/Save As.
- **Must work fully offline**; only **well-known, reputable** open-source libraries.
- **Preserve the OCR text layer**, **bookmarks/outline**, and **form fields** through all edits.

Intended outcome: a trustworthy, auditable, offline desktop app that replaces Preview's
view + page-editing workflow on Windows.

## Approach (recommended)

A **native Windows desktop app** in **Python**, run from inspectable source (a packaged
`.exe` is an optional later convenience). One resident process manages multiple
document windows.

**Libraries (all reputable, offline):**
- **PySide6** (Qt6, LGPL) — GUI, windows, drag-and-drop, clipboard, and the
  `QLocalServer`/`QLocalSocket` single-instance IPC. `pip install PySide6` includes the
  Addons (QtPdf) automatically.
- **PyMuPDF / `fitz`** (MuPDF by Artifex, **AGPL** — fine for personal use; see note) pinned
  **`>=1.25.5`** — renders pages/thumbnails **and** does lossless object-level page editing.
- **pypdf** (BSD, pure Python) — optional fallback edit engine behind a common interface.

**Why PyMuPDF renders the viewer instead of Qt's `QPdfView`:** `QPdfView` renders, scrolls,
zooms, and highlights search hits, but has **no interactive text selection/copy**. The user
wants select-&-copy (the OCR text). Rendering with PyMuPDF and building a selection overlay
from `page.get_text("words")` boxes delivers selection **and** unifies viewing + thumbnails +
editing on one engine. Text selected this way is exactly the preserved OCR layer.

**AGPL note for the user:** PyMuPDF is AGPL — perfectly fine for running their own readable
source privately. Only if they later distribute a packaged `.exe` publicly would they need to
offer source (AGPL), buy an Artifex commercial license, or ship the pypdf-only fallback build.

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
  annots=True, widgets=True, final=...)`, apply rotation overrides, **rebuild the outline**
  (remap old→new page indices, drop bookmarks whose target page was deleted), then
  `out.save(path, garbage=4, deflate=True, clean=True)`. Object-level copies preserve the OCR
  text layer, annotations, and form fields by construction (never rasterize/flatten).

This centralizes outline remapping in one place and makes every editing operation O(list-edit).

### Single-instance + one-window-per-document (the duplicate-tab fix)

Every launch is invoked by Explorer as `pythonw launcher.py "%1"`:
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
- **Text selection/copy:** cache `page.get_text("words")` boxes; on mouse drag, hit-test and
  select the contiguous run in reading order, paint highlight rects, copy joined text to the
  clipboard. **Search:** `page.search_for(query)` → highlight + next/prev navigation.
- **Thumbnail sidebar** bound to the `ordered[]` list — doubles as jump-to-page (View mode) and
  drag-reorder/delete/cross-window drag (Organize mode). Both views read the same model.
- **Remember last page/zoom/scroll per document** in a small local JSON under
  `%APPDATA%\pdfproj\state.json` (auditable, offline), keyed by identity path.

## Critical files to create

```
pdfproj/
  launcher.py                  # entrypoint: single-instance guard, normalize %1, hand-off/become server
  app.py                       # PdfApp(QApplication): path->window dict, raise/focus, page clipboard, QLocalServer
  main_window.py               # MainWindow: View + Organize modes, toolbar/menu, holds a VirtualDocument
  viewer/pdf_view.py           # QGraphicsView continuous-scroll renderer (PyMuPDF pixmaps, lazy, zoom/fit/rotate)
  viewer/text_selection.py     # word-box selection overlay + clipboard copy (feature QPdfView lacks)
  viewer/search.py             # page.search_for highlighting + hit navigation
  organize/thumbnail_panel.py  # grid bound to ordered[]: drag-reorder, cross-window drag (QDrag MIME), cut/copy/paste, delete
  model/virtual_document.py    # VirtualDocument + PageRef; all list-edit ops, dirty tracking
  model/edit_engine.py         # EditEngine interface; PyMuPDFEngine (default) + PyPdfEngine (fallback); materialize-on-save
  model/toc_remap.py           # outline snapshot + old->new page remap + drop-dangling
  store/settings.py            # per-document last page/zoom/geometry (JSON in %APPDATA%)
  util/paths.py                # normalize_path() shared by launcher and app
```

## Build order (phased)

1. **Setup (on Windows):** install Python 3.12 from python.org (not the Store stub; add to
   PATH); `py -3.12 -m pip install "PySide6" "PyMuPDF>=1.25.5" pypdf`. Place the project on the
   **Windows filesystem** (e.g. `C:\Users\<you>\pdfproj`, visible in WSL as
   `/mnt/c/Users/<you>/pdfproj`) for fast native startup — confirm the Windows user folder.
2. **Single-instance launcher + window management** — the duplicate-tab fix and resident process.
3. **Viewer** — render, continuous scroll, zoom/fit, rotate, thumbnail sidebar, last-page memory.
4. **Text selection + search** — drag-select/copy and find-in-document.
5. **Edit engine + virtual-document model** — merge/insert, reorder, delete, move/copy across
   windows, with OCR/bookmark/form preservation via materialize-on-save.
6. **Save / Save As + default-app association** — atomic `os.replace` for Save; set the app as
   the default `.pdf` handler (Settings → Default apps), association target a small `.bat`/shim
   running `pythonw launcher.py "%1"`. (Optional later: `pyinstaller --noconsole --onefile` —
   **must be built on Windows**, cannot be cross-built from WSL.)

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

## Open items / risks to confirm during implementation

- Verify, on the installed PyMuPDF version, that `insert_pdf(..., widgets=True)` carries form
  fields and renames duplicate root field names (test with fixture `B.pdf`).
- `insert_pdf` does **not** copy the source TOC — outline is rebuilt explicitly; confirm the
  remap handles **multi-level** outlines and named/explicit destinations, not just simple links.
- Text-selection overlay across page boundaries in continuous scroll needs care (anchor/cursor
  hit-testing in scene coordinates); this is the most involved viewer piece and can land in a
  follow-up pass after basic view/scroll/zoom works.
