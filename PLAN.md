# Plan: Local, Offline, Native-Windows PDF Viewer + Page Editor (Python)

> **This document is the design / spec source-of-truth** — architecture, packaging, verification, and
> the roadmap of *planned* work (the what & why). It deliberately carries **no live status**: for
> shipped versions, per-release notes, release links, milestone ticks, and open follow-ups, see
> **`PROGRESS.md`** (the single source of status). How-we-work conventions live in **`CLAUDE.md`**.

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
private use — fine. If you distribute the installer **publicly**, AGPL requires offering the
corresponding source; the full source now lives in this **public** repo (since 2026-07-17 — see
§Packaging → *Public-release readiness*), so shipping the installer with a pointer to the repo (and
its exact tag/commit) satisfies that. This is no longer hypothetical: the obligation is **live**, and
the About dialog's tagged corresponding-source link (G4) is what discharges it per release. The
alternatives remain: an Artifex commercial license, or a pypdf-only fallback build.

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
already held in `model/virtual_document.py`.

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

- **WSL checkout** `/home/<you>/klarpdf` (native Linux fs, fast): canonical dev for all `model/`,
  `viewer/`, `organize/` code, the headless tests, and GUI iteration via **WSLg**.
- **Windows checkout** `C:\Users\<you>\klarpdf` (native NTFS, fast PyInstaller + correct shell
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
- `requirements.in` lists the few top-level libs (PySide6, PyMuPDF, pypdf). `pip-compile
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
`packaging/klarpdf.spec`, built `--onedir --noconsole` on Windows (cannot be cross-built from
WSL). `--onedir` (vs `--onefile`) gives faster startup and a clean tree for the installer to lay
down; a secondary `--onefile` build also ships as a portable, run-anywhere `.exe` (see §5 — it
trades slower per-launch startup and no auto-association for zero-install portability). Output
`dist/klarpdf/` contains the embedded CPython, the PySide6/Qt runtime, and PyMuPDF —
no system Python needed. (Dependency versions are reproducible via the hashes; note honestly that
PyInstaller output is **not byte-identical** across builds due to timestamps — version-repro, not
bit-repro.)

**4. Installer + registry (one self-contained `setup.exe`).** **Inno Setup** (free, mature,
widely used; script-driven) with a checked-in `packaging/installer.iss` that:
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
a **`push` of a `v*` tag**. It drives the same one-command `packaging/build.ps1` (also runnable
locally) end-to-end: re-fetch + hash-verify the `win_amd64` wheels from `requirements-win.txt` (not
committed — see §2) → clean build venv (`--require-hashes --no-index`) + pinned PyInstaller → **two
artifacts** from `packaging/klarpdf.spec`: the **`--onedir --noconsole`** tree for the installer and
a portable **`--onefile` `klarpdf-portable-x64.exe`** → Inno Setup (`ISCC installer.iss`) →
`klarpdf-setup-x64.exe` → smoke-test (launch + open a PDF). Both artifact names carry an explicit
**`-x64`** suffix — the only architecture built today (§2's `win_amd64`-pinned wheels, built on a
`windows-latest` x64 runner) — so a future `arm64` build has a distinct, non-colliding name.
- **Versioning:** one source of truth (`version.py`) feeds the PyInstaller exe metadata, the Inno
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
  visual system landed first, then the rebrand sweep across `version.py`, `packaging/installer.iss`
  (AppName / Publisher / `AppId` / ProgID), the window title, the single-instance + `%LOCALAPPDATA%`
  identifiers, the annotation author tag, the `.ico` + toolbar SVG assets, and the **GitHub repo name**
  (rename while private — old links redirect). Name, casing mapping and the two-part split are tracked
  as G2 in `PROGRESS.md`; the visual system is specified in `assets/brand/BRAND.md`.

  The sweep carries **no backward-compatibility shims**, because the app has never been distributed —
  it has a single user and no installed base. Two values are therefore free to change outright that
  would otherwise be frozen: `KLARPDF_AUTHOR` in `model/page_edits.py`, which is stamped as the PDF
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
  `packaging/klarpdf.spec` `datas` + a freeze-aware `resource_path()` (mirroring `ui/icons.py`).
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
  | Require status checks: **`pytest`**, **`emails`** | ✅ | **The only rule G8 actually added.** `pytest` stops a red merge mechanically instead of relying on noticing the ❌ comment `test.yml` posts. `emails` (the G7 author-email guard) is the layer that keeps the G1 scrub true — a personal address merged post-flip is public permanently. Both always report on every PR by construction; see below. |
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

Escape hatches (only if closed-source is ever wanted) remain as in the AGPL note above: an Artifex
commercial PyMuPDF license, or a pypdf-only fallback build.

## Portability (Windows-first ship, Linux-ready seams)

Decision: **ship Windows only** for the first release, but bake in **near-zero-cost seams** so a
future Linux (or macOS) port is small. The architecture is *incidentally* portable — Qt + MuPDF are
the cross-platform engine and the `model/` layer is GUI-free — so the reuse story is strong as long
as OS-specific code stays quarantined.

**Cheap hedges (do now, ≈zero cost):**
1. `store/settings.py` uses `QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)`
   instead of a literal `%LOCALAPPDATA%\klarpdf` — Qt resolves it per-OS (Local AppData on Windows,
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
  `manylinux` wheels; `klarpdf.spec` → Linux conditionals.
- **Rule of thumb:** the *application* ports almost for free; the *installer + file-association +
  window-manager glue* is rewritten per OS.

## Critical files to create

```
klarpdf/
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
  store/settings.py            # per-document last page/zoom/geometry — JSON via QStandardPaths AppConfigLocation (%LOCALAPPDATA% on Windows, ~/.config on Linux)
  util/paths.py                # normalize_path() — SINGLE identity chokepoint (case-fold on Windows; one-line switch for Linux)
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
  version.py                   # single source of version → PyInstaller exe metadata, Inno AppVersion, git tag
  packaging/klarpdf.spec       # PyInstaller spec → --onedir (installer) + --onefile (portable .exe), icon, data files
  packaging/installer.iss      # Inno Setup: bundles dist/, [Registry] ProgID + .pdf assoc (HKCU), Start Menu, uninstaller (+ [UninstallDelete] %LOCALAPPDATA%\klarpdf)
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
   Save As dialog. The rename goes through `util/atomic.py:atomic_replace` (M38.5), which retries a
   `PermissionError` on a bounded backoff: on Windows the rename needs exclusive access to both
   paths, and an on-access antivirus scanner holding the just-written temp open is enough to fail a
   save that would succeed 200 ms later.
9. **Freeze + installer + release pipeline — (Windows ONLY).** `packaging/klarpdf.spec` (PyInstaller
   `--onedir --noconsole` for the installer **plus a `--onefile` portable `.exe`**) →
   `packaging/installer.iss` (Inno Setup) bundling `dist/klarpdf/`, writing the `HKCU` ProgID + `.pdf`
   Open-With association, with an uninstaller that **also wipes `%LOCALAPPDATA%\klarpdf`**.
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

- **Version pinning holds:** `pip install --require-hashes --no-index -r requirements-win.txt` into a
  fresh venv succeeds; then flip one hash/version in `requirements-win.txt` and confirm pip **aborts**
  (proves nothing can silently drift). Rebuilding twice yields the same dependency versions.
- **Offline build:** disconnect the network (or build inside a no-egress shell) and run
  `packaging/build.ps1` end-to-end from `vendor/wheels/` — produces `klarpdf-setup-x64.exe` with no
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
- ✅ **Confirmed (M1):** `insert_pdf` does **not** copy the source TOC — `model/toc_remap.py`
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

v0.2.0 built this layer with **form-fill as its first, simplest consumer**; **v0.4.0's** annotations
and redaction slot into the same mechanism. (The v0.3.0 interaction work below doesn't touch this
layer at all — it's drag-and-drop + viewer-mode UX.)

### v0.2.0 — "Polish, Print & Forms"

| Milestone | Feature | Where | Done when |
|---|---|---|---|
| **M10** Icons | App `.ico` (closes the open follow-up) + toolbar icons for undo/redo, zoom-in/out, cut/copy/paste. `QApplication.setWindowIcon`; wire `icon=` into `packaging/klarpdf.spec` and `SetupIconFile` into `packaging/installer.iss`. | WSLg + **Win** | App has a real icon (taskbar + installed); toolbar buttons are iconographic |
| **M11** Zoom UX | `zoomChanged` signal from `PdfView`; live "150%" indicator (toolbar combo); **Actual Size / 100%** action (Ctrl+0); preset levels. Extends `PdfView.set_zoom`. | WSLg | Magnification % always visible; one click resets to 100% |
| **M12** Printing | `QPrintDialog` + `QPrinter`; render each page via PyMuPDF at printer DPI, paint with `QPainter`; page-range + current-page. Confirm `QtPrintSupport` + plugins survive the freeze. | WSL logic; **Win** print validation | System print dialog prints the open doc correctly |
| **M13** Recent documents | MRU list in `store/settings.py`; dynamic **File ▸ Open Recent** submenu (app-global, refreshed across windows); dedupe via `normalize_path`, drop missing files, "Clear Recent". Reopen routes through `app.open_document` (free single-instance dedupe). | WSL | Recent files listed; reopen in one click |
| **M14** ⭐ Page-edit layer + form fill | The layer above; first consumer fills existing AcroForm fields (text/checkbox/radio/choice). New `model/page_edits.py`, `viewer/tools.py`; click-to-edit field UI; headless materialize tests. | WSL (model+tests) + WSLg | Fill a form's fields, save, reopen with values intact |
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

**Shipped in v0.2.0:** `model/page_edits.py` (form-value descriptors, snapshotted with `ordered[]`),
`viewer/printing.py` (QPrinter render), `viewer/zoom_widget.py`, `viewer/form_fill.py` (inline
fill), `ui/icons.py` + `ui/icons/*.svg`, `packaging/klarpdf.ico` + `packaging/make_icon.py`; tests
`test_form_fill.py`, `test_form_fill_ui.py`, `test_zoom.py`, `test_recent.py`, `test_icons.py`,
`test_printing.py`. (Sources are opened from an in-memory stream + `VirtualDocument.fresh_source` so
in-place Save isn't blocked by a file lock and repeated saves keep widgets.)

**Planned — v0.3.0 (Interaction):** mostly edits to `organize/thumbnail_panel.py` (drag pixmap +
insertion marker + Explorer file-drop) and `viewer/pdf_view.py` + new `viewer/tools.py` (grab/select
mode controller); new `ui/icons/` hand + select glyphs; tests `test_drag_drop.py` (drop-index for
file URLs, `filesDropped` signal, drag-pixmap non-null) + `test_interaction_mode.py` (mode switches
drag mode + suppresses selection/form routing).

**Planned — v0.4.0 (Annotate & Redact):** extend `model/page_edits.py` with annotation/redaction
descriptors + `model/edit_engine.py`'s post-copy pass; reuse `viewer/tools.py`; tests
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
| **M31** ⭐ Annotation round-trip editing | On open, re-parse **our** annotations (the `KLARPDF_AUTHOR`-tagged highlights / text-boxes) from the source into `model/page_edits.py`; at materialize, **strip-then-re-add** the managed annotations on the copied page so they aren't duplicated. Saved highlights/text-boxes become movable / re-editable / removable after reopening. (Redaction stays a point-of-no-return — not re-editable.) Follow-ups from manual testing: the page render strips our baked marks so the editable overlay is the single source of truth (no double-draw / pinned original), and text selection reads the stripped render page (a box's text isn't drag-selectable, no stale-position copy). | WSL (model+tests) + WSLg | Reopen a saved doc → move / edit / remove its KlarPDF annotations |
| **M31.5** Export → PDF (flatten) | An **Export** action (`File ▸ Export`) whose first format is a **flattened PDF**: bake the managed annotations **and form widgets** into page content via PyMuPDF `Document.bake()` (text layer **preserved**, *not* rasterised) — a locked but still-searchable copy whose marks can't be moved/removed in any tool. The opt-out counterpart to M31's round-trip (Save As stays editable; Export → PDF locks). Built as an **extensible Export path** (`model/export.py`) that **M36** grows to an image format. | WSL (model+tests) + WSLg | Export → PDF writes a flattened, text-preserving copy whose annotations are no longer editable |
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
| **M33** Internal link remap **+ navigation** | `model/links_remap.py`: at materialize, rebuild internal **GoTo *and* named-destination** links against the new page order (both resolve to a target page, re-emitted as remapped GoTo — `insert_pdf` drops named dests entirely, and cross-run GoTo links). Plus **in-viewer navigation** (`viewer/links.py`): clicking an internal link jumps to its target page (pointing-hand on hover), resolved via the same map so it follows reorder/delete live. | WSL (model+tests) + WSLg | Reordered/deleted pages keep internal links working; clicking a link in the viewer jumps to its target |
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

The leverage: the headless `model/` core already implements every transform. The only new logic is a
thin set of read-only query helpers + the MCP tool layer — and **less of that than first scoped**:
`model/page_text.py` (M78.7) landed after this roadmap was written, is Qt-free, lives in `model/`,
and already produces the page + snippet output `search` needs.

### Architecture (a quarantined seam, like `packaging/`)

- **New `mcp_bridge/` package**, isolated the same way OS code is quarantined behind
  `platform_integration.py` — never inlined into `app.py`/`launcher.py`. The GUI build does not
  import it.
- **It cannot be called `mcp/`** (found at M39, correcting this section's original name). The
  official SDK *is* the top-level module `mcp`, and the repo root is on `sys.path` (pytest's
  `pythonpath = ["."]`, plus a script's own directory), so a local `mcp/` package shadows the
  installed one and `from mcp.server import MCPServer` dies with `ModuleNotFoundError: No module
  named 'mcp.server'` — measured before a line of the server was written, not theorised. The name
  in the milestone table below is `mcp_bridge/` for the same reason.
- **Reuses the GUI-free core only.** Imports `model/virtual_document`, `edit_engine`, `export`,
  `page_edits`, `links_remap`, `toc_remap` — **not** `model/edit_commands.py` (it imports
  `QUndoCommand`); the server calls `VirtualDocument` ops directly, so it runs **without PySide6/Qt**.
  M39 confirms no Qt import reaches the server path.
- **Same repo** (decided 2026-08-12). `mcp_bridge/` sits beside `model/` and imports it directly — the same
  quarantined-seam pattern as `packaging/`, not a repo boundary. A sibling repo was rejected because
  the bridge's whole leverage is that `model/` already implements every transform: splitting would
  put a versioning seam through that leverage, and `model/` is not a publishable library today
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
  `mcp_bridge/docs.py`, never a second copy, so the two cannot drift. Only `redact_text` and
  `search` need one today; the other fifteen sit comfortably inside the budget, and a resource for
  them would be ceremony.
- **New headless helpers** (the only genuinely new code) — landed in `mcp_bridge/queries.py`:
  `extract_text(path, pages)`, `search(path, query)`, `render_page(path, page, dpi)`,
  `document_info(path)`, `outline(path)`, `form_fields(path)`. `viewer/search.py` is Qt-bound, but
  **`model/page_text.py` is not** and already supplies the per-hit snippet machinery, so `search`
  wraps `page.search_for` + `PageText` rather than being reimplemented from scratch. They hold no
  SDK import, so the PDF behaviour is tested by calling functions rather than by driving a protocol,
  and `server.py` stays a schema adapter.
- **Page numbers are 1-based at the tool boundary** (M39), matching the viewer's page counter and a
  PDF outline's own targets; `model/` stays 0-based and the conversion happens only in
  `mcp_bridge/queries.py`. Out-of-range page numbers are an error, never a silent clamp.
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
| `annotate(path, marks, out)` → highlights / underlines / strike-throughs + notes (M101) | `page_edits.merge_markup` + `apply_annotations`; palette from `model/markup_palette.py` | transform |
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
  `model/export.py:export_selected_pages`, the app's own Export ▸ Selected Pages as PDF (M51) — so
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
  not exposed. Implemented at M40 in `mcp_bridge/transforms.py:_resolve_out`, and the identity test
  goes through `util/paths.py:normalize_path` — the project's single "are these the same file"
  chokepoint — so a symlink, a `..` segment or a case-different spelling on Windows cannot smuggle
  the input back in as the destination. A string compare would miss all three; there are tests for
  each. Every write tool's schema *requires* `out`/`out_dir`, so no argument shape means "in place".
- **No silent clobbering of anything else either** (added at M40). An output that already exists is
  refused unless the caller passes `overwrite=true`. The roadmap only required protecting the
  *source*; this is the same argument applied consistently, and an agent that meant it says so in
  one word.
- **Writes go to a sibling temp and are renamed into place**, so a failed transform leaves nothing
  half-written for the caller to read back as a corrupt PDF. The rename is M38.5's
  `util.atomic.atomic_replace`, the same helper the GUI's Save and Export use — the
  antivirus-holds-the-temp race applies identically here, and the two write paths deliberately do
  not diverge on it.
- **A mistake is an error, never a quiet partial success.** `fill_form` rejects an unknown field
  name rather than writing nothing and reporting success; `reorder` demands a full permutation so it
  cannot silently drop a page; `delete_pages` refuses to empty a document; an out-of-range page
  number is refused rather than clamped.
- **Redaction stays a point of no return** — destructive `apply_redactions` + the existing
  `fitz`+Poppler leak verification before the tool reports success. Landed at M41 in
  `mcp_bridge/redaction.py`, with two consequences worth stating because they are what separate this
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
  / `KLARPDF_MCP_ALLOW_ROOTS`, in `mcp_bridge/config.py:PathPolicy`. **Unrestricted by default, and
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
  are thin adapters over `model/`, a CLI stays cheap to add if a real request arrives.
- **The server is platform-independent — unlike the app — and the lock must not undo that.** `model/`
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
  `mcp_bridge/README.md` rather than left to be discovered. `tests/test_mcp_packaging.py` pins the
  command, so a later edit back to a bare interpreter cannot quietly reintroduce the need for
  vendored dependencies the format forbids.
  **How it is built:** `packaging/mcpb/build_mcpb.py` assembles `server/` from the checkout
  (`mcp_bridge/`, `model/`, `util/`, `version.py`, minus `model/edit_commands.py` — the one
  Qt-importing file), generates the bundle's `pyproject.toml` **from `requirements-mcp.txt** so the
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
  machine, and reused `packaging/klarpdf.spec`. It loses on reach: it is platform-specific, and the
  bridge — unlike the app — is cross-platform and worth offering to Desktop users who do not run
  KlarPDF at all. Revisit if the online-install requirement ever proves unacceptable. **`python` is
  the worst option for us** and is not on the table: vendored and offline, but it requires a Python
  already installed, which frozen-exe users precisely do not have.

### Milestones (one PR each; ⭐ = keystone, GUI-free, fully headless-testable)

| Milestone | Where | Done when |
|---|---|---|
| **M39 ⭐** MCP scaffold + read-only core | WSL | `mcp_bridge/` stdio server on the official `mcp` 2.x SDK (`MCPServer`); `get_info`/`get_outline`/`search`/`extract_text`/`render_page`/`get_form_fields` wired to headless helpers (`search` reuses `model/page_text.py`, not a fresh implementation); no PySide6 import on the server path — assert it in a test, don't just observe it; headless tests green |
| **M40** Transform tools | WSL | `split`/`merge`/`reorder`/`delete_pages`/`rotate`/`fill_form`/`flatten`/`export_images` → explicit out path; never overwrites source; lossless (OCR/TOC/forms survive); headless tests. Landed in `mcp_bridge/transforms.py`; `split` reuses `util/page_range.py` so an agent types the same range syntax a person does |
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
visual-overlay-only redaction MCP server. *Shrunk:* M39, because `model/page_text.py` landed in the
interim. *Ambiguous, now resolved:* "FastMCP" names two different things since the official SDK
renamed its class and standalone FastMCP 3.x went its own way. *Held up well:* stdio-first, the
`.mcpb` bet, and the Qt-free-core architecture — `model/` is still Qt-free apart from the already
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

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M94** The three bridge-side defects TC-002 left open, plus the encryption case M93 did not reach | `get_info` reports the file's own encryption + named permissions; `get_form_fields` reports each button's on-state and the read-only / required / multiline / max-length flags; `fill_form` warns on an XFA form; `VirtualDocument` captures a source's protection *before* decrypting it | WSL | An owner-password document reads as encrypted with its restrictions named; a checkbox's `"2"` on-state is discoverable and a boolean still ticks it; an XFA fill carries a warning naming static vs dynamic and leaves `datasets` byte-identical; a **user**-password document saves back restricted |

**Where these came from.** The second hands-on session ([TC-002](https://github.com/utyagi24/klarpdf),
report held with the owner's test harness) filled an SSA-3 through `fill_form` and found the values
correct and the document degraded seven ways. Four were the *app's* save engine and were fixed at
M93. The three below are the bridge's own, and they share a shape worth naming: **each is a place
where the server knew something and did not say it.** None is a wrong result; all three are a
caller left to guess.

**`get_info` answered a different question than it was asked.** `encrypted` was
`vdoc.password is not None` — "did the caller hand me a password?" — so the SSA-3, AES-128 with
copying and modification forbidden, came back `encrypted: false`. The tool documented as *the*
routing call, the one that "answers the question that changes everything else", was the single place
that hid it. Two protections have to read as encryption here and only one involves a password: a
**user** password, without which the file will not open, and an **owner** password, which is how
essentially every published form arrives. `is_encrypted` is not the fix — it is `False` the moment a
document opens (CLAUDE.md §Gotchas) — and neither is asking the in-memory source, which for a
user-password document is a decrypted copy. `permissions` rides along, named rather than as a
bitfield, because "what does this document forbid" is the follow-up question in every case where the
answer to the first one is yes.

**The same decrypt was quietly discarding restrictions on the way out, too.** Reading the source
after `open_source` cannot work, and that is not only a reporting problem: `from_path` seeded
`_permissions` from exactly there, so a user-password document that forbade copying, modification
and assembly *saved back permitting all three* — measured `-1052` in, `-4` out, with the password
itself carrying through fine, which is what made it invisible. It is M93's defect one door further
along, and it is fixed the only way it can be: `_authenticate_and_decrypt` reads the algorithm and
the flags **between the authenticate and the decrypt**, the one moment both are legible, and
`open_source` records them per source. `reload_from_file` re-baselines from the same record, closing
a third door where a redaction commit on an owner-password document left `permissions` reading
"everything allowed".

**`get_form_fields` could not tell a caller how to tick a box.** A checkbox's ticked value is the
name of an appearance state — `"1"` on one SSA-3 widget, `"2"` on the next, `"Yes"` on neither —
and `choices` cannot carry it, because PyMuPDF populates `choice_values` for combo and list boxes
only. `fill_form` does accept a plain `true` and resolve the on-state itself, which is why TC-002
passed; a convenience nothing documents is not a contract, and the natural guess is `"Yes"`. Both
routes now work and both are stated. The field flags are the same defect in a quieter register: the
form carries three read-only 3–5 pt slivers named `P2_PAReadOnly_FLD` and friends, indistinguishable
in the listing from the fields a person is meant to fill.

**XFA is reported, not resolved — an owner decision (2026-08-15).** An XFA form keeps a second copy
of itself as XML, and `fill_form` updates the AcroForm widgets while leaving the `datasets` packet
byte-identical, so the file asserts two things at once. The three available answers were: write the
values into `datasets` too (highest fidelity, and a wrong name-to-node mapping is worse than no
write); drop `/XFA` so the document degrades to a plain AcroForm everyone agrees on (conventional,
and it removes the only thing that renders a *dynamic* form); or say so. The owner chose to say so.
The result carries `xfa: {present, dynamic, datasets_updated: false}` and a warning that names which
case it is, because the two differ in what the caller must do: a static form is correct on screen
and wrong only to a machine reading the XFA data, while a dynamic one — Acrobat builds its pages
from the template — can look empty as well. The discriminator is `<dynamicRender>` in the `config`
packet and it must be read **by value**: the SSA-3 contains the element set to `forbidden`, so a
presence check calls the one form measured static dynamic instead. Both stronger options stay open
in `PROGRESS.md` §Open follow-ups, and a dynamic-XFA fixture now exists to test them against.

**The retest found three more, all in the surface this milestone had just added** (2026-08-15), and
they are fixed here rather than filed: `on_state`, `states` and `read_only` did not exist before
this PR, so they are its own loose ends and the review is the moment to close them.

The load-bearing one is the same defect one argument along. `fill_form` refuses an unknown field
**name** because "a typo that writes nothing and reports success is the worst outcome here" — and
then accepted any **value** at all, resolving whatever it did not recognise as falsy. So `"3"`, the
obvious slip on a form whose states are `"1"` and `"2"`, *cleared* the box and listed the field
under `filled`. That is strictly worse than the no-op the name check exists to prevent: a no-op
leaves the form unanswered, this writes the wrong answer and calls it success. The guarantee now
covers both arguments — a button value must be a real export state or a boolean, and anything else
is an error naming the states the widget accepts, with nothing written.

`"Of"` is rejected with it, though it lands on `Off` today and that is what the caller meant. It
gets there down the same unvalidated path that mishandles `"3"`, and an input that is wrong but
happens to work is precisely what keeps the broken case invisible (owner decision, 2026-08-16).

The other two are smaller and follow the same principle — say what you know. A **read-only** field
can still be filled, because stamping a signature line may be exactly what the caller wants, but the
names come back in `warnings` now that the server reads the flag at all. And `states` is ordered
`on_state` first then sorted, rather than in `/AP/N` key order, which a write rebuilds: the same
checkbox reported `["2", "Off"]` before a fill and `["Off", "2"]` after one, breaking equality in a
caller's regression test for no reason.

**"Lossless" was narrowed to what M93 established.** The tool docs promised that "the text layer,
form fields and bookmarks survive", which is true and was being read as more than it says — TC-002
quoted it back against a write that dropped the structure tree, `/Perms`, `/Names` and encryption.
The server instructions, `mcp_bridge/README.md` and the per-tool docstrings now draw the line where
§Key design idea draws it: **a tool that leaves the page set alone keeps everything; a tool that
moves pages keeps the content and not the document structure.** The claim is smaller and, for the
first time, the same in all four places it is made.

### M95 *(unplanned)* — a verification that can fail (TC-003, owner-reported 2026-08-15)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M95** The redaction safety net stops sharing the matcher's blind spot; invisible text is reported | `_literal_residuals` scans the written output for the query as a plain substring — no boundary rule, nothing borrowed — and reports `residual_literal` + `warnings`; `PageText.is_invisible` render-confirms text that is in the file but not on the page; `whole_words` documented as whole **token** | WSL | The TC-003 call that leaked now names both survivors; Smith/Smithsonian still succeeds; a deliberately broken matcher fails verification instead of passing it; a white-on-dark header is not flagged |

**The defect.** TC-003 redacted an account number out of a utility bill with `whole_words: true` —
the documented, natural choice for a single token — and got `matches: 2`, `residual_matches: 0`,
`cross_engine_verified: true`, over a file that still contained the account number **twice**. It
survived inside `<AccountNumber:220885-1063303>`, a machine-readable tag with no spaces in it.
Reproduced here through the real code path before anything was changed.

**Why the existing check did not catch it, which is not where the report looked.** The report
diagnosed the verification as "circular — it re-runs the same query through the same matcher". That
is true of pass 1 and misses what is actually there: `_no_residual_match` already ran a *second*
pass, `_residual_in_text`, over both engines' extracted text through an entirely separate code path,
written precisely against this criticism and carrying a docstring that said so — *"A textual scan
owes the matcher nothing."*

**That sentence was false, and it is the whole defect.** The textual pass calls `_word_bounded`,
which is documented as *"deliberately the same rule"* as `PageText.is_whole_word` — on the reasoning
that *"two checks that disagreed about what 'whole word' means would be worse than one."* For
deciding **what to redact** that reasoning is right. For a **safety net** it is exactly inverted: a
check that agrees with the thing it is checking cannot fail when that thing is wrong. So the second
engine, the second extraction and the second code path all faithfully reproduced the first pass's
blind spot, and the tool pronounced the file clean twice.

The fix is therefore small and specific rather than a rebuild: a third pass that owes the matcher
nothing **and can be held to it** — the query as a literal substring, no `whole_words`, no term
splitting.

**It warns; it never deletes.** A literal scan cannot be a leak test, because redacting whole-word
`Smith` correctly leaves `Smithsonian`, which literally contains the query —
`test_a_legitimate_survivor_is_not_mistaken_for_a_leak` pins that, and wiring the literal scan to
the delete would destroy correct output. So it reports the **surrounding token**, which is what
separates the two cases at a glance without the tool having to judge: `'Smithsonian'` reads as
obviously fine, `'<AccountNumber:220885-1063303>'` reads as obviously not. A check that cannot be
trusted to fail must not be wired to a delete; a check that cannot fail at all is decoration.

**Invisible ink, and why colour cannot answer it.** The two surviving occurrences were 10 pt white
text on white ground at the extreme margins — live to `get_text`, copy-paste and any indexer, absent
from every render. The report proposed flagging a hit whose "fill colour equals the background".
**Measured, that rule fires 21 times on this page and is right twice**: 19 of the white spans are
ordinary table headers — *Customer Name*, *Bill Date* — painted on dark banners and perfectly
legible. A flag that fires on every table header of every bill is one a reader learns to ignore,
which leaves the two that matter worse off than before.

So colour is a **pre-filter** and the pixels decide: render the box and ask whether anything was
drawn in it. Measured separation on that page — contrast **1** for the two invisible tags, **163–215**
for all 19 legible headers. It also catches, without special-casing any of them, zero-alpha text and
text painted over by an opaque image. What it cannot see is dark text on an equally dark ground,
because the pre-filter is for pale text; `False` therefore means "not invisible in the way this can
see", and it is a flag, never a guarantee (§Honesty principle).

Cost, measured on the 320-page `spaceX_prospectus.pdf`: **+7–12%** on `search` — one extra
`get_text("dict")` per page that has a hit (2.15 ms/page, cheaper than the `words` extraction
`PageText` already does), and a small pixmap only for pale candidates. The pathological one-letter
query goes 4.60 s → 5.04 s.

**`whole_words: true` means whole *token*, and the docs now say so.** The behaviour is deliberate —
M64 treats `ALPHA-zero-A0` as one word and the find bar is tested on it — and was documented in
`model/page_text.py` while the tool docs said only "matched whole", then steered the caller toward
that mode for "one phrase". Facing an account number, a careful reader picks `true` and silently
loses half the occurrences. The tool docs now state the limitation and name the shape it bites on
(`<tag:VALUE>`, filenames, URLs, `key:value`), and point single-token queries at `whole_words: false`,
which cannot over-match a query with no second word in it.

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

**The report:** "many applications support Ctrl+mouse-scroll for zooming; ours does not — and are
there other common shortcuts we're missing?" Both halves are recorded here: the fix, and the audit
that answers the second question so it isn't re-derived later.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M80** Ctrl+wheel pointer zoom | `PdfView.wheelEvent` intercepts a Ctrl-modified wheel and zooms instead of scrolling. **Anchored on the pointer**, not the viewport centre: `_center_anchor`/`_restore_center_anchor` generalise to `_anchor_at(view_pos=None)`/`_restore_anchor(anchor, view_pos=None)`, and `set_zoom` grows an `anchor_pos` argument — `None` keeps the existing centre behaviour for every zoom with no pointer behind it (menu, toolbar, typed %, Ctrl+±). The zoom factor is **continuous** — `_ZOOM_STEP ** (delta / _WHEEL_NOTCH)` — so one detent equals exactly one Ctrl+± step while a precision touchpad's fractional deltas stay smooth instead of being swallowed. The event is accepted unconditionally (limits included) so the gesture can never fall through to a scroll. **Not** active in the slideshow: that mode's contract is one page per screen at Fit Page (M78), so the wheel there keeps stepping slides. No model, file or dependency change — view-only, like every other zoom. | WSLg / Windows (offscreen GUI) | Ctrl+wheel zooms one step per detent; the point under the cursor stays under the cursor; four quarter-detents equal one detent; a plain wheel still scrolls; the sticky fit is cancelled; the limits don't leak into a scroll; the slideshow still steps |

**The audit (measured, not assumed — every line below was exercised against a running offscreen
window on 2026-07-27, not read off the source).** What the app already has: `Ctrl+O/S/Shift+S/P/W`,
`Ctrl+Z/Y`, `Ctrl+X/C/V`, `Ctrl+F` + `F3`/`Shift+F3`, `Ctrl+D`, `Ctrl+L`/`Ctrl+R`, `Ctrl+G`,
`Ctrl+0/1/2`, `Ctrl+±`, `Ctrl+H`/`Ctrl+U`, `Ctrl+Shift+R`, `Ctrl+Shift+M`, `F11`, `F5`,
`Ctrl+[`/`]` (± Shift), arrow-key nudge, `Esc`, and `PgUp`/`PgDn` + arrows for scrolling (inherited
from `QAbstractScrollArea`). What is **missing**, in the order the audit ranks them:

1. **Ctrl+wheel zoom** — fixed above (M80). It did not merely do nothing: it *scrolled*, the worst
   outcome, since the reader asks for zoom and gets motion.
2. **`Home` / `End` / `Ctrl+Home` / `Ctrl+End` — verified dead.** `QAbstractScrollArea` handles
   `PgUp`/`PgDn`/arrows but not these, so "jump to the start / end of the document" — universal in
   Preview, Acrobat, Edge and every browser — has no binding at all outside the slideshow (where
   M78 does bind `Home`/`End`).
3. **`Space` / `Shift+Space` — verified dead.** Page-down / page-up by spacebar is how most people
   actually read a long PDF (Preview, Acrobat, Edge, Chrome). Bound in the slideshow (M78) and
   nowhere else.
4. **`Shift+wheel` horizontal scroll — verified absent**: with the h-scrollbar at full range,
   `Shift+wheel` scrolls *vertically* (Qt's default), so a zoomed-in wide page has no wheel gesture
   for panning across it.
5. **Temporary hand-pan** — hold `Space` (Acrobat) or middle-drag (near-universal) to pan without
   leaving the current tool. The Grab **mode** exists (M46); what is missing is the *momentary*
   form, which is what makes it usable mid-markup.
6. **`Ctrl+A` select-all text** — `QKeySequence.SelectAll` is unbound and `TextSelection` has no
   select-all entry point, so "select the page's text and copy it" needs a manual drag.
7. **`Ctrl+=` as a `Zoom In` alias.** Qt's `StandardKey.ZoomIn` resolves to `Ctrl++` on Windows —
   which on a US layout means `Ctrl+Shift+=`. Browsers all bind bare `Ctrl+=` (and the numpad `+`)
   for exactly this reason; we bind only Qt's default.
8. **Pinch-zoom on a precision touchpad** (`QNativeGestureEvent` / `Qt::ZoomNativeGesture`) —
   Windows delivers it, nothing consumes it. The natural companion to M80, and the same
   `set_zoom(..., anchor_pos=…)` seam serves it.

Items 2–8 went to the owner as a menu. **Scheduled as M81 below** (2026-07-27): all of them except
item 5, the momentary hand-pan, which was **dropped** — and dropping it is what removes the
`Space` tap-versus-hold ambiguity that item would otherwise have forced.

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

Closes the gap §Future enhancements never recorded: KlarPDF can *display, move and delete* a foreign
sticky note but has never been able to make one, and M77's own wording flagged it — foreign notes are
"'notes' arriving from another tool **ahead of our own**".

**The owner's specification.** A note is **attached to exactly one Highlight / Underline / Strikeout**
(HUS) mark, never free-floating:

1. A note can be attached to **any** existing HUS mark.
2. Removing the host mark removes its note.
3. The note's background is its host's colour.
4. Applying a note to a **text selection** resolves its host by what is already there: if the
   selection **already carries an HUS mark, the note attaches to that mark** — no new mark is
   created; only if the selection is **plain text with no pre-existing HUS** does it **create a
   Highlight** in the current highlight colour and attach the note to that. (Owner clarification,
   2026-07-27: attaching is the primary act; creating a highlight is the fallback when there is
   nothing to attach to.)
5. Adding further HUS marks over the same text does not move or copy the note — it dies with **its
   own** host, not with any mark covering that passage.
6. **Host resolution when several marks qualify** (owner, 2026-07-27): the app deliberately allows
   layered HUS — M59.10 scopes merging *per type*, so a yellow highlight and a red underline on the
   same words are independent marks. When a selection carries more than one, **a Highlight wins;
   failing that, the topmost** of the underline/strikeout marks. Deterministic, and it keeps a note's
   background usually the highlight colour a reader already associates with the passage (rule 3).

**Why this shape is cheap: the note is a *field of the host*, not an object.** `Highlight`,
`Underline` and `Strikeout` are frozen dataclasses carrying only `rects` and `color`; adding
`note: str = ""` makes rules 2 and 5 **fall out with no code** — there is no second object, no parent
pointer, and no referential integrity to keep. Deleting the mark deletes the note because the note
*is* part of the mark.

**And the PDF representation already exists.** A markup annotation's `/Contents` **is** a comment on
that highlight — what Acrobat, Preview and Edge all read and write. We already call
`annot.set_info(title=KLARPDF_AUTHOR)` when baking, so this becomes
`set_info(title=…, content=note)`; read-back mirrors what `TextBox` already does with
`info["content"]`. **Verified 2026-07-27** on the pinned PyMuPDF: `/Contents` round-trips on a
Highlight alongside our `/T` tag, and — importantly — the note text does **not** appear in
`search_for` or `get_text()`, so notes stay invisible to Find with **no change to the PR #190 search
filter**, exactly as that decision intends (annotation text is not body text). PyMuPDF writes no
`/Popup` object; `/Contents` alone is valid and other viewers display it.

**The one collision the specification did not cover — `merge_markup`.** Re-marking absorbs same-colour
marks and rebuilds the survivor from scratch: `merged = mark_type(_union_bars(absorbed), color=color)`
— bars and colour only. A note on an absorbed mark would be **silently destroyed**, with the user
having deleted nothing: they highlighted adjacent text and their typed note vanished. (The
different-colour *trim* path one line above uses `dataclasses.replace` and already preserves extra
fields, so only the absorb path is unsafe.) **Owner decision: the merged mark keeps the notes** —
inherit, and where several absorbed marks carry notes, join them with a separator. Nothing typed is
ever lost, and undo restores the prior state.

**And the same code is losing data today.** Found while investigating M83's traceback, on the
owner's Edge-annotated file: `parse_annotation` builds `Highlight(rects, color)` / `Underline` /
`Strikeout` and **never reads `/Contents`**, while `degradations()` checks `/RC`,
`/IT /FreeTextCallout`, `/IRT`, `/BS/D` and opacity but **not `/Contents`**. So adopting a
commented foreign highlight (M68 double-click = strip the original, re-add ours) **drops the
comment with no warning**, contradicting that function's own contract — *Empty means adoption is
lossless.* Reachable in two clicks on any Acrobat/Preview/Edge-reviewed PDF, and **live in
v0.16.2**. This is why the note *model* leads the whole tranche: once HUS marks carry `note`, the
comment survives adoption and the loss disappears rather than being papered over with a warning.
Only adoption is affected — a foreign mark displayed, moved (M67) or deleted (M66) never has its
`/Contents` rewritten.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M81.1** Model + round-trip | `note: str = ""` on `Highlight` / `Underline` / `Strikeout`; baked via `set_info(content=…)`; parsed back in `parse_annotation` beside the existing `TextBox` content read. No new mark type, no new PDF construct. | WSL (model+tests) | A noted mark saves, reopens and still carries its note; a note survives save→reopen→save unchanged; Find never matches note text |
| **M81.2** Merge preserves notes | The absorb path in `merge_markup` carries notes onto the survivor, joining multiples with a separator; the trim path already preserves them via `replace`. | WSL (model+tests) | Extending a noted highlight keeps the note; sweeping across two noted marks yields both notes joined; a trimmed mark keeps its own note; one undo step restores everything |
| **M81.3** Adoption carries the comment | Found while investigating the above, on the same file. `parse_annotation` builds `Highlight(rects, color)` / `Underline` / `Strikeout` and **never reads `/Contents`**, while `degradations()` checks `/RC`, `/IT /FreeTextCallout`, `/IRT`, `/BS/D` and opacity but **not `/Contents`**. So double-clicking a commented foreign highlight to edit it (M68 adoption = strip the original + re-add ours) **drops the comment with no warning**, contradicting that function's own contract — "Empty means adoption is lossless." Reachable in two clicks on any Acrobat/Preview/Edge-reviewed PDF. **M81.1 cures it outright** (once HUS marks carry `note`, the comment survives adoption); until then `degradations()` must at least *report* it. Only adoption is affected — a foreign mark merely displayed, moved (M67) or deleted (M66) never has its `/Contents` rewritten. | WSL (model+tests) | Adopting a commented foreign highlight either preserves the comment (post-M81.1) or warns before dropping it; a comment-free mark warns about nothing |

**The interface is M90.** This milestone lands only the model, the round-trip and the adoption
fix — all headless, all testable without a pixel. Marks will briefly carry a `note` with no way to
show it, which is deliberate: it stops the data loss now and lets the interface be designed with no
bug outstanding. The owner's six rules above govern M90; rules 2 and 5 are already satisfied here by
construction.

### M82 — foreign text markup is draggable, and it steals the press from text selection

**Owner-reported 2026-07-27**, testing the Edge-annotated file: *"our app lets me grab the text
highlight added by Edge and drag it around like normal drawing objects and place it arbitrarily. We
should **not** be able to drag the highlights."*

**It is an asymmetry, not a general drag problem.** Our **own** `Highlight` / `Underline` /
`Strikeout` appear in neither `OBJECT_TYPES` (`viewer/annotations.py`) nor `PLACEABLE_TYPES`
(`model/page_edits.py`) — deliberately not draggable, because a text markup's quads *describe text*:
move it and it marks nothing. The **foreign** path has no equivalent gate —
`foreign_annotation_at()` hit-tests every foreign annotation by its rect with no type filter, so
M67's move applies to text markup too. Edge's highlights get a capability our own identical marks are
denied, and the resulting `ForeignMove` is applied at materialise, so the displacement becomes
permanent in the saved file.

**The more serious consequence is that it pre-empts text selection.** `begin_foreign_move` is tried in
the **SELECT mode** press path — the default mode — in this order:

```
selected object → form field → our own marks → foreign annotation → text selection
```

So on any document reviewed in Edge or Acrobat, **dragging across a highlighted passage to select the
text drags the highlight instead**. The user cannot select or copy the very text a reviewer marked for
their attention — the worst possible passage to lose selection on.

**This codebase has already met this failure mode once.** `covers_page()` exists precisely because a
grabbable full-page watermark meant "text selection stopped working entirely" (its docstring). Same
symptom, different cause; the lesson was fixed locally and never generalised into a rule about what
may claim a press ahead of text selection.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M82.1** Text markup is not a drag target | Gate the foreign hit-test/move on **free-placed** types, excluding text markup (Highlight, Underline, StrikeOut, Squiggly) — mirroring the `OBJECT_TYPES` / `PLACEABLE_TYPES` rule our own marks already follow. Free-placed foreign marks (sticky notes, stamps, drawings) stay draggable exactly as M67 intended, and **delete (M66) stays available for every type**. | WSL + WSLg | Dragging a foreign highlight selects the text under it instead of moving the mark; a foreign sticky note still drags; a foreign highlight still deletes |
| **M82.2** Selecting text over foreign markup works | Regression test on the owner's file: press-drag across an Edge highlight yields a text selection. | WSL (headless) | The selection matches the same drag on unhighlighted text; Ctrl+C copies it |

**Two side benefits.** Fewer `ForeignMove` descriptors reach the annotations tuple, which narrows
exposure to **M83.1** (the dead context menu) — though M83.1 must still be fixed on its own terms,
since deletion and adoption also produce non-geometric descriptors. And it removes a silent
file-modifying action a reader can trigger by accident while merely trying to read.

### M83 — the annotations tuple is heterogeneous, and only four of five hit-tests know it

**Owner-reported 2026-07-27** while testing interop with a PDF annotated in Edge — a console
traceback, deliberately filed as "expose any unknown gap" rather than as a fix request:

```
AttributeError: 'ForeignDeletion' object has no attribute 'rect'
  viewer/annotations.py:826 in annotation_at
```

**What the user sees is not a crash.** Qt swallows exceptions raised from a Python override of one of
its virtuals, so the app survives and the **context menu silently never appears**. Once a document is
in this state every right-click in the page view is dead, with no error surfaced — which is why it
reached the console instead of a failure dialog.

**Cause.** `PageRef.annotations` is a **heterogeneous tuple**: real marks *plus* non-geometric
bookkeeping descriptors — `ForeignDeletion` (`fingerprint`, `label`) and `ForeignMove`
(`fingerprint`, `dx`, `dy`, `label`). Riding the same tuple is deliberate (M66/M67): it is how they
snapshot for undo and follow their page through a reorder. But `annotation_at` resolves geometry with
`annot.rects if hasattr(annot, "rects") else (annot.rect,)`, which assumes every entry has some.

**Trigger, matching the report exactly.** An Edge **sticky note is an unmodeled type**, so M68 leaves
it delete/move-only — and deleting one is the obvious thing to try when testing interop. That puts a
`ForeignDeletion` in the tuple. Adopting an Edge *highlight* does the same, since M68 is implemented
as a deletion plus a parsed descriptor in one macro.

**Scope: one site.** All five iterations over the tuple were checked — the other four guard with
`isinstance` first, and `covers_page` guards before touching geometry. Only line 826 uses the
`hasattr` fallback.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M83.1** Fix the hit-test | `annotation_at` skips descriptors that carry no geometry. | WSL (headless) | Right-click works after deleting, moving or adopting a foreign annotation — the regression test is exactly the owner's repro |
| **M83.2** Make it structural, not conventional | One predicate — `is_geometric(mark)` / `rects_of(mark)` — that every hit-test routes through, so "which descriptors have geometry" is answered in a single place. | WSL (model+tests) | Every hit-test uses it; a new non-geometric descriptor type cannot silently break a hit-test |

**The gap this exposes, which is the reason it is written up rather than just patched.** The tuple's
heterogeneity has **no enforced contract**. There is no "does this have geometry?" predicate anywhere;
`mark_bounds()` looks like the chokepoint but assumes `bounding_rect()` and is safe only because its
callers happen to guard first. It works **by convention, not construction** — four of five sites got
it right, one did not, and nothing would have caught it. M85 is safe (it adds a *field* to existing
marks), but the next non-geometric descriptor lands the same trap. M83.2 is the same
"single chokepoint" discipline §How we work already applies to path identity.

**The test file is also a direct validation of M81's design.** `ClientStatements_5752_043026.pdf`
(owner-supplied, annotated in Edge) holds three Highlight annotations, **two of which carry
`/Contents`** — "Comment to yello highlight" and "Comment to Pink highlight" — with an empty `/T`, so
they read as foreign. That is M81's model exactly: a note *is* a comment on a highlight, stored in
`/Contents`. The design was chosen from the PDF spec before this file existed; the file confirms Edge
implements it the same way. Two consequences: M90.4 would surface two comments that are **completely
invisible in KlarPDF today**, and the same file is the natural regression fixture for M81.1, M90.4 and
M81.3.

### M84 — highlights render dull because the preview alpha-blends what the file multiplies

**Owner-reported 2026-07-27:** *"our highlight color appear very dull compared to the color used by
Edge. can we revisit our palette?"* **Investigated: the palette is not the problem and needs no
change.** `viewer/annotations.py` paints a highlight with `fill.setAlpha(110)` — plain source-over at
43% — which washes every colour toward the white page. Measured, over white:

| Colour | Mode | Renders as | Saturation | Black text under it becomes |
| --- | --- | --- | --- | --- |
| Yellow | alpha 110 (today) | (255, 240, 156) | 0.39 | (110, 95, 11) — olive |
| Yellow | **multiply** | (255, 219, 26) | **0.90** | **(0, 0, 0)** — stays black |
| Green | alpha 110 (today) | (206, 246, 194) | 0.21 | (61, 101, 50) |
| Green | **multiply** | (140, 235, 115) | **0.51** | **(0, 0, 0)** |
| Pink | alpha 110 (today) | (255, 216, 238) | 0.15 | (110, 72, 94) |
| Pink | **multiply** | (255, 166, 217) | **0.35** | **(0, 0, 0)** |

**Two defects, not one.** Saturation is **2.3×–2.4× lower** than it should be — that is the reported
dullness. And the *text* under a highlight is washed out with it: black becomes olive, so our
highlight actively reduces legibility, which is the opposite of a highlighter's purpose.

**Our viewer contradicts our own saved file.** PyMuPDF writes highlight annotations with
`/BM /Multiply` (verified on our pinned version), so a passage highlighted in KlarPDF, saved, and
reopened in Edge looks **more vivid than it did in KlarPDF**. This is a preview-fidelity bug, not a
taste question.

**The idiom already exists in the same module.** `_MultiplyPixmapItem` was written for the
under-content watermark preview, and its docstring states this exact principle — *"the text stays
black and the mark shows everywhere else. The saved file is unaffected either way; this is purely so
the preview does not lie about legibility."* Highlights simply never got it.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M84.1** Multiply-blend the committed highlight | A `_MultiplyRectItem` sibling of the existing `_MultiplyPixmapItem`, replacing the alpha-110 fill. **Palette unchanged.** | WSLg (offscreen grab) | A rendered highlight matches the saved PDF's appearance; black text under it stays black; measured saturation matches the multiply column above |
| **M84.2** The live selection preview must match | `TextSelection._armed_brush` uses alpha 120 for the drag-over-text preview. Fixing only the committed mark would make arming look pale and the committed mark jump vivid on release — the M73 sticky-markup flow shows that preview constantly, so the two must change together. | WSLg | Preview and committed mark are the same colour; no flip on release |

**Palette: keep as is.** Rendered with multiply, our colours are comparable to — and in the case of
yellow, richer than — the five Edge uses (`#FFF066` yellow, `#EB4949` red, `#F799D1` pink, `#7DF066`
green, `#8FDEF9` blue, read from the owner's test file). The only substantive difference is that Edge
offers **Red** where we offer **Orange**; adding red is a separate, optional palette question and not
part of this fix.

### M85 — current-page tracking is wrong for short pages (owner-reported 2026-07-27)

**The report**, on `IAS_CaseStudy.pdf` (18 slides, every page 1920×1080 pt): *"I clicked on slide 1
thumbnail and it resulted in showing both Slide 1 and 2 as selected. Then as I grabbed the right edge
and made the window wider, the current slide changed to 4 and then to 5 without me clicking on any
thumbnail."* **Reproduced headlessly** with a tall/narrow window and the same page geometry:

```
click Slide 3 (prior state)     thumb.current=3  selected=[3]  view.page=3
click Slide 1  <-- the action   thumb.current=1  selected=[0]  view.page=1   <-- TWO MARKED
```

**One root cause, two symptoms.** `_update_current()` decides the current page by asking **what sits
under the viewport centre**. That holds for portrait pages taller than the viewport and breaks for
short ones: a 16:9 slide at fit-width in a tall window was 403 px tall in a 966 px viewport, so the
centre landed **1.2 pages down** — jump to page 0 and the centre is already inside page 1. The trigger
is purely geometric — **page height < half the viewport height**, i.e. for 16:9 pages any viewport
taller than ~1.125× its width. Ordinary A4 documents never reach it, which is why this survived to
M84.

* **Two marked thumbnails.** Click Slide 1 → selection = row 0; the view jumps to page 0; the tracker
  reports page **1**; `currentPageChanged(1)` → `ThumbnailPanel.set_current(1)` moves the current row.
  But `setCurrentRow` under `ExtendedSelection` **leaves the selection untouched**, so row 0 stays
  selected while row 1 becomes current — and the panel paints *both* (a 2 px border on every selected
  row, a 3 px ring on the current one), which reads as "it selected two".
* **The current page drifts while resizing.** Each resize re-applies the sticky Fit Width →
  `goto_page(current)` puts page N at the viewport top → the tracker re-derives from the centre →
  returns N+1. The next resize starts from N+1 and yields N+2: **one page per resize step, no click
  involved.** It self-limits as the window widens, because a wider window raises the fit zoom until
  pages are tall enough to contain the centre again.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M85.1** Track the page by **largest visible area** | Replace the viewport-centre test in `_update_current` with "the page occupying the most of the viewport". Correct for short pages, tall pages and the facing layout alike, and what other viewers do. Removes both symptoms on its own. | WSL (headless) | The repro above yields `current == clicked row`; resizing no longer advances the page; existing current-page tests stay green |
| **M85.2** Keep current and selection in step | When the **view** drives the current row and the selection is a *single* row, move the selection with it, so the two markers can never disagree. **Multi-row selections are deliberately left alone** — a Ctrl-click selection of pages 3–7 staged for a page operation must survive scrolling. | WSL (headless) | A view-driven page change moves a single-row selection; a multi-row selection is preserved across scrolling |

**Independent of the performance work** — none of A, B, F, E or M83 touch this. It is a correctness
bug in page tracking that `IAS_CaseStudy.pdf` merely exposed, by being the first content opened with
pages wider than they are tall.

### M86 — the two cheap zoom fixes (owner-decided 2026-07-27)

Came out of profiling M80: Ctrl+wheel can drive `set_zoom` 10–60× per second where a toolbar click
drove it once, which exposed costs that were always there and never mattered. **M80 did not make a
zoom step slower — it made steps frequent.** Measured on a 60-page text document at 1200×900,
offscreen:

| Gesture | Cost |
| --- | --- |
| Notched mouse, 10 detents | 1.24 s total — 124 ms/event |
| Precision touchpad, 40 fine deltas | 3.16 s total — 79 ms/event |
| Pixmap cache after one sweep | saturated 48/48 — every fractional zoom evicts |
| `_build_scene()` alone | 3 ms |
| `set_zoom()` cold / warm | 23 ms / 7 ms |

Two of those need naming precisely. **`set_zoom` profiles at 21 ms of our own work but 79–124 ms
end-to-end**; the gap is Qt paint inside `processEvents()` on the *offscreen* platform, which has no
GPU path, so the end-to-end figures may overstate real Windows. The 21 ms is solid. And **M80's
continuous zoom factor guarantees a cache miss** — the key is `(index, round(zoom, 4), rotation)`, so
every event is a zoom value nothing has rendered before. The feature that makes touchpad zoom smooth
is exactly what defeats the cache; that tension is inherent, not a bug to fix.

**A and B were meant to ride M80's PR; #197 merged without them**, so they are this milestone — and
`main` carries Ctrl+wheel at the cost measured above until they land. Adaptive prefetch and the
cache rework follow as M87.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M86.1** Collapse redundant render passes (**A**) | **Collapse the 3 redundant `_render_visible()` passes to 1.** One `set_zoom` runs it three times — `_build_scene`'s own call, `_restore_anchor`'s explicit call, and `_on_scroll` fired by the scrollbar write (profiler: `ncalls=3`). **Pre-existing, not M80's** — the old `centerOn` path did the same — so this speeds up every zoom, fit, rotate and two-page toggle, and is worth doing even if M80 were dropped. **Corrected twice when built (see PROGRESS §M86 for the numbers).** The row originally called the extra passes "two-thirds of the most expensive work". They are not: they never rasterise anything — measured across five regimes, the **cache-miss count is identical with and without the fix**, because all three passes of one `set_zoom` run at the *same* zoom value, so the first populates the cache and the rest hit it. That holds even at 8 s of rasterising per sweep, so cache pressure never converts them into real work. But the first correction then under-sold it by measuring only a 60-page document: `_render_visible` walks **every page in the document** twice (`_visible_range`, then the drop-offscreen loop), so a pass costs **O(document length)** regardless of how few pages are on screen. On 60 pages that is a 1–5% saving; on **320 pages it is 40–47% of rasterising time — ~15 ms per geometry change**, about one frame, on exactly the documents where zoom already feels worst. The saving scales with page count, not pixel work. | WSL (headless) | One `_render_visible` per zoom; no visual change; existing zoom/fit/rotate tests still green |
| **M86.2** Coalesce the gesture (**B**) | **Coalesce the gesture**: accumulate wheel deltas and apply once per frame (~16 ms timer) so a burst becomes one rebuild instead of N. Fits the `_WHEEL_QUIET_MS` idiom M78 already established. | WSLg | A burst of N wheel events produces ~1 rebuild per frame; the final zoom matches applying them individually; single detents still feel immediate |

### M87 — render-resource discipline (owner-decided 2026-07-27)

The other half of the zoom work: what the app *keeps*, rather than how often it rebuilds. Sized
against **post-M88** numbers, because the DPI correction makes every page ~5.4x heavier in memory —
tuning this against today's figures would bake in an assumption that breaks the day M88 lands.

**Premise check (2026-07-28, measured before building — offscreen, 60-page Letter at 1200×900).**
Every number this milestone rested on was a projection. Three held, one was wrong, and one changes
the milestone's priority:

* **The byte figures below were ~27–33% low.** `QPixmap` is **32 bpp**, not the 24 bpp (`w × h × 3`)
  assumed throughout — Qt stores it in the display format. Measured 1.85 MB for a Letter page at
  100% where the tables said 1.5. The **5.4× ratio is unaffected** (it is geometric); every absolute
  MB is not. The §M88 table below is corrected.
* **The cache blowup is already live on `main` — it is not a post-M88 risk.** One 40-step Ctrl+wheel
  sweep to max zoom on an ordinary 60-page Letter document peaks at **4.3 GB**, confirmed against
  process working set (RSS 127 MB → **4431 MB**, peak 4669 MB), and the cache sits at exactly **48
  entries** the whole way — the count-based limit does not bound bytes even slightly. Sweep ceilings:
  → 2.0× = 286 MB, → 4.0× = 1100 MB, → 8.0× = 4317 MB. This makes **M87.2 a user-facing defect fix
  today**, not preparation for M88, and is why it should ship first.
* **M87.1's premise holds and was understated.** At zoom ≥ 2 the visible band is 2 pages while the
  render band is 6, so **67% of rendered bytes are prefetch** — at 8× that is 237 MB visible against
  **473 MB prefetched**. Confirmed for every zoom ≥ 1 (57% at 1.0×, 67% from 2.0× up).
* **The five-window projection was accurate.** Five documents at post-M88 "100% physical" on the
  1.75× panel: **199.6 MB** total (the estimate said ~198 MB), of which the focused window is
  39.9 MB and the four background windows **159.7 MB** — so M87.2's background-drop target of
  ~40 MB is exactly right.
* **Ruled out, so nobody re-opens it: there is no leak on close.** `closeEvent` never clears
  `_cache`, which looks like one, but destroying the view releases everything — 2124 MB → 90.6 MB
  once the last reference goes. A `_cache.clear()` in `closeEvent` would be redundant.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M87.1** Adaptive prefetch (**F**) — ✅ **shipped** (A/B numbers in PROGRESS) | `_PREFETCH = 2` is a **fixed constant** — sound when a page was 1.85 MB, actively harmful once it is 264 MB (see M88). Scale the prefetch band down as pixmap bytes grow. **Measured 2026-07-28**: at zoom ≥ 2 the visible band is 2 pages and the render band 6, so **67% of rendered bytes are prefetch** — 237 MB visible against **473 MB prefetched** at 8×, and 57% waste even at 1.0×. The "~800 MB at 500%" estimate was the right shape; it is 473 MB at today's 8× ceiling and larger after M88. **Built as a byte allowance, not a zoom curve**: 48 MB per direction, scaled by the *heaviest page in view*, so the trigger is page size rather than magnification — an A0 sheet is already down to 1 page of prefetch at 100%, and a Letter sheet keeps the full band until it passes 48 MB. | WSL (model+tests) | Prefetch shrinks as zoom/page size grows; the visible band is never starved; normal-zoom behaviour unchanged |
| **M87.2** Cache: count → **global byte ceiling** — ✅ **shipped** (see PROGRESS for the before/after) | **Measured 2026-07-28**: one Ctrl+wheel sweep to max zoom on an ordinary 60-page Letter document takes the process from 127 MB to **4431 MB** of working set, with the cache pinned at exactly 48 entries throughout. That is on `main` **today**, with no DPI change involved — so this row is not preparation for M88, it is a defect a user can hit now, and it should lead the milestone. Four changes, each answering a specific defect. (1) **Global, not per-window** — `self._cache` is a `PdfView` instance attribute, one `PdfView` per `MainWindow`, one window per document, so **N open documents = N independent caches**; every figure quoted before this was silently per-window. (2) **Byte ceiling, not entry count** — 48 entries is 89 MB of A4 but **4.3 GB of zoomed Letter, measured**, because a rendered page costs `w × h × 4` and nothing else. (3) **Visible pages pinned, never evicted** — a better guarantee than a byte floor: thrashing becomes impossible by construction, and a single page larger than the whole budget (A0 at 500% ≈ 600 MB) still displays, temporarily exceeding the nominal ceiling. That is the graceful behaviour, not a leak. (4) **Background windows drop their pixmaps** — only one window is ever in front; re-render on focus is ~6 ms/page for text. **Built as two tiers, not one** (deviation, owner call to revisit): losing *focus* drops the scrollback and keeps the band on screen, and only a **minimised** window drops the band as well. The row's premise — "only one window is ever in front" — does not hold on Windows, where windows tile; blanking a window the reader can still see is a visible defect traded for memory nobody asked to trade. The measured split is 1184 → 491 MB on deactivation and → 118 MB on minimise. | WSL (model+tests) | Ceiling honoured across windows; visible pages survive any eviction pass; a single over-budget page still renders; a backgrounded window releases; no thrash while scrolling |
| **M87.3** A render pass costs the band, not the document — ✅ **shipped** (carried M86.1 follow-up, assigned here by the premise check) | `_render_visible` walked every page **twice** per pass — `_visible_range()` scanning all pages for the intersecting range, then the body looping over all pages again to drop offscreen pixmaps — so a pass cost O(document length) however few pages were on screen. **A third walk found while fixing it made the pass quadratic**: `AnnotationOverlay._paint_visible_content` asked every page in the document whether it was in the band, re-deriving the band (a viewport map plus a full page scan) each time. The range becomes a **binary search** over a y-sorted index of page tops — non-decreasing by construction, with a step back for the two pages of a facing row that share a y — the drop pass reads a **tracked set** of what is actually painted, and the overlay derives the band once. | WSL (model+tests) | The binary search agrees with the old scan at every scroll position, zoom, layout and page-size mix; only the band holds a pixmap; the pass does not scale with page count |
**The sizing policy (owner, 2026-07-27) — two numbers, not one.** "I am okay to go up to 1 GB
(global, 3.125% on a 32 GB machine) **only if we are dealing with exceptionally heavy documents** …
just because resources are available should not imply that we stop being stingy." So retention is
driven by **what responsiveness needs** (the visible band + a bounded scrollback, expressed in pages),
and the byte ceiling is a **backstop that only binds when pages are genuinely enormous** — never a
target to fill. Concretely: ordinary documents settle in the tens of MB; a 500%-zoomed large-format
document may climb toward the ceiling, because there it must.

**Why the document's file size and richness are irrelevant to cache sizing** — the owner asked whether
the cache should scale with document properties. Measured, and it settles the question:

| Document | File size | Pixmap @200% | Render time |
| --- | --- | --- | --- |
| A4, text only | ~0 MB | **5.8 MB** | 6 ms |
| A4, 4,000 vector strokes | 0.7 MB | **5.8 MB** | 88 ms |
| A4, full-page scan image | ~0 MB | **5.8 MB** | 82 ms |
| A0 poster, text only | 0.1 MB | **96.4 MB** | 29 ms |

Three wildly different A4s produce **byte-identical** pixmaps: by cache time the content that made
those pixels is gone. The property that drives memory is **page dimensions** (the A0 is 16.6× an A4
from a 0.1 MB file); the property complexity drives is **render time** (a 14× spread), which changes
how *valuable* a hit is, not how *large*. A byte ceiling is therefore already the document-adaptive
mechanism — it holds ~88 A4s or ~5 A0s automatically. The count-based limit is the one that ignores
the document.

### M88 — DPI correctness: what "100%" means (owner-reported 2026-07-27)

**The report:** "why does the document appear smaller in our app compared to Edge and Brave at the
same zoom percentage?" Because `actual_size` is documented as "1 PDF point per pixel", a PDF point is
1/72", and the display is 96 logical DPI — so a 612 pt (8.5") Letter page renders 612 logical px =
**6.375 inches**. We show **75% of physical size and call it 100%**. Browsers and Acrobat define 100%
as true physical size (×96/72 = 1.333), so Edge's 100% is our 133%.

Investigating it surfaced a **second, worse defect**: `devicePixelRatio` is handled **nowhere** in the
codebase. The owner's machine has a 1.75× laptop panel and a 1.0× external Dell, both at 96 logical
DPI. We render at logical-pixel resolution and hand Qt a pixmap with no DPR set, so on the laptop the
page is **upscaled 1.75× and the text is blurry** — on the higher-resolution screen of the two. Wrong
size is a preference you can zoom around; blurry text is the thing this app exists to get right.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M88.1** 100% means physical size — ✅ **shipped** | Map 1 pt → 1/72" on screen (×`logicalDpi/72`). Owner-decided: match Edge/Brave/Chrome/Acrobat. **Built as a scale split, not a multiplier at the render call**: `zoom` is what the reader asks for, **`scale`** = `zoom × logicalDpi/72` is scene units per point and drives every piece of *geometry* (layout, hit-tests, the annotation overlay), **`device_scale`** = `scale × dpr` drives only the rasteriser and the cache key. Routing all three through one definition is what made M88.4 fall out for free and what keeps a future DPI change a one-line edit. | WSLg / Windows | A Letter page measures 8.5" at 100%; matches Edge side by side |
| **M88.2** Honour `devicePixelRatio` — ✅ **shipped** | Render at `zoom × dpr` and `setDevicePixelRatio(dpr)` on the pixmap, so logical layout is unchanged and pixels are native. **Measured on the panel**: 816×1056 logical / 1428×1848 device at 100%, exactly 1.75 device px per logical px. Set the ratio on the **`QImage`**, which `QPixmap.fromImage` inherits and `transformed()` (the rotation path) preserves. Two consequences the spec missed and this row now carries: the **cache key must be `device_scale`** — the store is process-global since M87.2, so a `zoom` key serves the 1.0× screen's pixmap to the 1.75× one — and **`_page_bytes` needs a `dpr²` term**, or M87.1's prefetch allowance under-counts by 3.06× on the owner's panel. | Windows (**hands-on**, both screens) | Text is crisp on the 1.75× panel; layout/geometry unchanged |
| **M88.3** DPR changes at runtime — ✅ **shipped** | **Not polish — required.** With a 1.75× and a 1.0× screen, dragging the window between them changes DPR live; a naive fix is correct only on the screen the window opened on. Hook the screen-change signal and re-render. Bound in `showEvent` (there is no window handle before that) with `UniqueConnection`, since Full Screen and the slideshow both re-show the window. A metrics read that finds nothing changed returns early — `screenChanged` also fires between two identical screens, and rebuilding for a no-op would stutter the drag. | Windows (**hands-on**) | Dragging between the two screens keeps the page sharp and the same physical size |
| **M88.4** Re-base "Actual Size" — ✅ **shipped** | Ctrl+0 becomes **true physical size**. Owner-decided: otherwise the menu item's name is a lie. Free once M88.1 lands: `actual_size` still sets zoom 1.0, and 1.0 now *means* physical. | WSLg | Ctrl+0 gives a physically-correct page |
| **M88.5** Migrate saved zooms — ⚠️ **premise does not hold; re-scope before building** | `view_state()` persists `{page, zoom, rotation}` per document and `apply_state()` range-checks it, so changing the semantics silently redefines every remembered zoom. Scale stored values on read, versioned. Owner-decided: migrate — "remembers where I was" is exactly the kind of trust that silent state changes destroy. **Found while investigating the M86 verification pass (2026-07-28): nothing reopens at a saved zoom.** A document opens at **Fit Page** by the v0.9.1 decision (PR #61), and `apply_state()` — the only reader of a saved zoom — has **no production caller**. So there is no remembered magnification for M88.1 to silently redefine and nothing to migrate; the saved value is kept only as a seam (see `view_state()`). This row therefore reduces to **either nothing, or a versioning stamp** written *now* so that a future "restore my zoom" can tell pre- from post-M88 values — which is the only part with any real content. **Decided 2026-07-28 (owner): nothing.** The feature may never be built, and if it is, it can start clean rather than migrate values of unknown era — which is cheaper than carrying a schema field forever on the chance. **The trade being accepted, recorded so it is not rediscovered as a bug:** a stored `1.0` written before M88.1 meant 6.375″ and now means 8.5″, so a future restore that honours pre-existing values reopens those documents ~33% larger than the reader left them. The information needed to tell the two eras apart is not recoverable once mixed-era values are on disk, so a later "restore my zoom" should **ignore** values it cannot date rather than trust them. Row closed as no work required. | WSL (model+tests) | A pre-change saved zoom reopens at the same *apparent* size; an out-of-range value falls back cleanly |
| **M88.6** Zoom range → **25–500%** — ✅ **shipped** | Owner-decided, and deliberately sequenced **after** M88.1: correcting the DPI shifts every number, so deciding earlier means deciding twice. Also drops a 10% view that rendered a page at 62×80 px — illegible. **One thing this row did not anticipate: a hard 25% floor breaks Fit Page.** An A0 sheet in a 1100×850 window fits at 17%, and clamping that to 25% overshoots the viewport in both orientations (measured). So the floor drops to the **Fit Page zoom** whenever that is smaller — you can always zoom out until the whole page fits, and no further. Deriving it from Fit Page rather than from the current zoom is what keeps it a floor and not a trap: `min(_MIN_ZOOM, current)` reads as "no step may zoom you in" and is true, but the floor then follows a reader who zooms in off a 17% fit, stranding them above it. Fit Page is the smallest fit (Fit Width is never smaller) and is computable at any moment, so one bound serves fits and manual steps alike. | WSLg | Clamps at the new bounds; the preset list and the % widget agree |

**Print, export and thumbnails are unaffected** — each computes its own scale from a real DPI target
(`printer.resolution()/72`, `dpi/72`), so M83 is **view-only**, consistent with the M49 principle.

**The interaction that must be respected: this correction makes every page ~5.4× heavier**, so M87's sizing has to
be decided against post-M88 numbers, not today's:

Corrected 2026-07-28 to **measured** bytes. The original column assumed `w × h × 3`; `QPixmap` is
32 bpp, so every figure was ~33% low. Pixel counts were right, and the ~5.4× ratio is unchanged.

| Case | Pixels | MB (was) | **MB (measured)** |
| --- | --- | --- | --- |
| Today, 100%, either screen | 612×792 | 1.5 | **1.85** |
| After, 100% physical, Dell (DPR 1.0) | 816×1056 | 2.6 | **3.29** |
| After, 100% physical, laptop (DPR 1.75) | 1428×1848 | 7.9 | **10.07** |
| After, 200% physical, laptop | 2856×3696 | 31.7 | **40.27** |
| After, 500% physical (new max), laptop | 7140×9240 | 197.9 | **~264** |

(The last row is extrapolated — today's 8× ceiling measures 118.34 MB at 4896×6336, and the ratio
holds exactly.)

**Confirmed on the hardware once M88.1–.4 shipped**: a Letter page on the 1.75× panel measures
**10.07 MiB** at 100% and **40.27 MiB** at 200% — the two middle rows above, to the decimal. The
five-window projection and M87.1's post-M88 band curve (2 → 1 → 0 across 1×/2×/4×) both hold as
written.

Correct rendering is not free: sharp text at true size on a 1.75× panel genuinely needs 5.4× the
pixels. The owner's stated common case — **several small PDFs open and juggled** — costs ~198 MB of
*visible* pixmaps across five windows after M88, which is what makes M87.2's background-window drop
(→ ~40 MB) and M87.1's adaptive prefetch load-bearing rather than optional.

### M89 — the rest of the reading-input conventions (owner-decided 2026-07-27)

The M80 audit's remaining items, scoped in a review pass with the owner. All view-only; no model,
file-format or dependency change. M80 has **shipped to `main`** (#197), so these branch from `main`
normally — the earlier stacking note is obsolete. They still *build on* M80: M89.3 edits the same
`wheelEvent`, and M89.5 calls the `set_zoom(..., anchor_pos=…)` seam M80 introduced.

**Two PRs in the end, and one closure.** The original split was **M89.1–M89.4** together (all small,
and the first two edit the *same* `keyPressEvent`, so separate PRs would mean rebasing four times
against one function for no review benefit); **M89.4 shipped early** on its own, leaving
**M89.1–M89.3** to ride together. **M89.6** went alone, being much the largest, touching
`TextSelection` rather than `PdfView`, and carrying a visual change reviewed on a rendered grab.
**M89.5** also went alone — being the one part the headless suite cannot certify — and that is
exactly what it turned out to need: it was **closed unmerged** once hands-on validation showed the
gesture never reaches the handler (below).

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M89.1** Document-end navigation | `Home` / `End` / `Ctrl+Home` / `Ctrl+End` scroll to the **start / end of the document** — all four the same verb (owner call: to a reader the Ctrl'd and bare forms are one gesture, and Preview/Edge/Chrome bind them alike in a PDF). Implemented as scrollbar minimum / maximum, which is the literal reading and lets `_on_scroll` update the current page for free. Today all four are **verified dead** outside the slideshow, where M78 already binds `Home`/`End` to the first/last slide — that stays. | WSLg / Windows (offscreen GUI) | Each of the four jumps to the corresponding end; the page indicator follows; the slideshow's own binding is untouched |
| **M89.2** Spacebar paging | `Space` scrolls down one screenful, `Shift+Space` up one — the same `SliderPageStepAdd`/`Sub` as `PgDn`/`PgUp`, on the key most readers actually reach for. Verified dead today outside the slideshow. | WSLg | Space/Shift+Space page down/up; typing a space into a text box or form field still types a space |
| **M89.3** Shift+wheel horizontal pan | `Shift+wheel` pans **horizontally**. This is an *override*, not a gap: Qt's own `Shift+wheel` scrolls vertically here (measured with the h-bar at full range), so a page zoomed wider than the viewport has no wheel gesture to cross it. A wheel with a genuine horizontal component (tilt wheels, most touchpads) is left to `super()`, which already routes it correctly. | WSLg | Shift+wheel moves the h-scrollbar; inert with no h-range; a plain wheel still scrolls vertically; Ctrl+wheel still zooms (M80) |
| **M89.4** `Ctrl+=` zoom-in alias — ✅ **shipped early** (see PROGRESS) | Add `Ctrl+=` to the existing **Zoom In** action's shortcut list. Qt's `StandardKey.ZoomIn` resolves to `Ctrl++`, which on a US layout physically means `Ctrl+Shift+=`; browsers all bind bare `Ctrl+=` for exactly that reason. `Ctrl+-` already works, so Zoom Out needs nothing. Pure binding — no behaviour change, still centre-anchored. **Pulled out of M89 and shipped on its own**: the owner hit it by hand during the M86 verification pass ("Zoom with Ctrl+- is working but not with Ctrl++"), which turned a predicted papercut into a reported one. Waiting for the rest of M89 would have meant knowingly shipping a dead accelerator. | WSLg | `Ctrl+=` zooms in one step; `Ctrl++` and `Ctrl+-` keep working; the menu still shows the standard accelerator |
| ~~**M89.5** Pinch-zoom~~ — **closed unmerged, 2026-07-29 (owner call): the handler is unreachable on Windows.** The premise — *"Windows delivers the event today and nothing consumes it"* — was **wrong**, and hands-on validation is what caught it (see the measurement below). The code was written and unit-tested; PR [#215] was closed rather than merged, because shipping a handler no machine can reach is worse than not shipping it. **Nothing user-facing is lost: pinch already zooms** through M80's Ctrl+wheel path. | Windows (**hands-on**) | — |
| **M89.6** `Ctrl+A` select all text | Select every word in the **whole document**, not the current page (owner call: "both Edge and Brave select text from all the document — why should we be different?"). Nearly free in the model, which has always carried the selection as a `(page_index, word_index)` anchor/cursor pair spanning pages — `select_all` just pins it to the two ends. **Paired with the repaint rework below, which is not optional.** | WSL (model+tests) + WSLg | Ctrl+A selects across every page; Ctrl+C copies the document's text; an image-only document selects nothing rather than erroring |

**The repaint rework M89.6 depends on — and the measurement that forces it.** `TextSelection.repaint`
puts one `QGraphicsRectItem` in the scene **per selected word**, and `_build_scene` calls
`scene.clear()` on **every zoom step**. Measured offscreen on 2026-07-27:

| Selected words | Add to scene | `scene.clear()` |
| --- | --- | --- |
| 25,000 (~50 pages) | 0.08 s | 0.24 s |
| 100,000 (~200 pages) | 0.39 s | 2.96 s |
| 247,500 (~500 pages) | 0.91 s | **20.64 s** |

So `Ctrl+A` on a 500-page document followed by one zoom step would freeze the app for twenty
seconds. Two changes, owner-chosen from three options:

1. **Clip painting to the visible pages** (plus the existing `_PREFETCH` margin), repainting as the
   viewport moves. The *model* still holds the whole selection — that is what `Ctrl+C` copies — but
   the *scene* only ever holds what is on screen, which bounds the item count by viewport size
   instead of document length.
2. **Coalesce each line's run into one rect.** Words are already sorted `(block, line, word-no)`, so
   a selected run on one line is a contiguous index range whose rects union cleanly. Cuts the
   remaining count by roughly another order of magnitude.

Note this is a **pre-existing latent bug, not one Ctrl+A introduces** — a drag-selection carried
across several hundred pages reaches the same state today. Ctrl+A only makes it a single keystroke.
(2) additionally changes how **every** selection looks — a merged passage highlight rather than a row
of per-word boxes — so it is reviewed on a rendered offscreen grab before it ships, not merged on
description.

**Two design decisions worth not relitigating:**

- **All the navigation keys live in `PdfView.keyPressEvent`, never as window-level `QAction`
  shortcuts.** A window shortcut fires wherever focus is, so `Home` / `Space` / `Ctrl+A` bound that
  way would hijack those keys from the inline text-box (`_TextBoxEditor`, a `QPlainTextEdit` child of
  the viewport) and form-field editors, where they mean line-start, a literal space, and select-all-
  in-this-field. Routed through the view, a focused editor consumes them first and never sees ours —
  the same reasoning that already put the clipboard verbs behind `_edit_copy`'s focus router (M59).
- **Momentary hand-pan is dropped** (owner, 2026-07-27). It was the only audit item that would have
  needed `Space` to distinguish a tap from a hold — a timing heuristic that is fiddly and
  occasionally wrong — and the Grab *mode* (M46) already covers the deliberate case. Revisit only if
  panning mid-markup proves annoying in practice, and then bind it to middle-drag alone.

**M89.5 — what hands-on validation found, and why it was closed (2026-07-29).** The milestone was
flagged from the start as the one part the headless suite could not certify: a constructed
`QNativeGestureEvent` exercises the handler, but *"Windows actually delivers the gesture to this
widget"* is only demonstrable on real hardware. It was validated on a Synaptics Precision Touchpad
+ HID touchscreen machine, with the two zoom paths instrumented so the console named which one
fired. **Neither input route reaches the handler:**

| Route | What actually arrives | Reaches `_pinch_zoom`? |
| --- | --- | --- |
| Precision touchpad | **Ctrl+wheel**, `delta = ±120` — one whole detent per pinch step; the driver translates the gesture before Qt sees it. (A native gesture reports a *fractional* `value`; ±120 is the mouse-wheel unit.) | No |
| Touchscreen | Synthesised **mouse** events — the app never sets `WA_AcceptTouchEvents` (grep: no `grabGesture` / `PinchGesture` / `QTouchEvent` anywhere), so Qt converts touch to mouse and the second finger is discarded before any recogniser sees it. This is also why one finger drags a text selection and a long press opens the context menu. | No |

Three conclusions worth keeping:

- **Pinch-to-zoom already works** — through M80's Ctrl+wheel handler, and *pointer-anchored*, since
  the cursor sits where the fingers are. The milestone's user-visible goal was already met before it
  was written; what M89.5 would have added is continuity.
- **That continuity is not ours to give.** The driver quantises to whole detents, so M80's
  continuous factor is fed exactly one `_ZOOM_STEP` per step. Recovering it would need raw
  Precision-Touchpad HID input, which Qt does not surface — well outside this milestone.
- **The touchscreen could be reached, but the price is wrong.** It would take
  `WA_AcceptTouchEvents` on the viewport plus a pinch recogniser, which changes Qt's mouse synthesis
  for *all* touch input and so risks the finger-drag selection and long-press menu that work today —
  for a gesture the touchpad already performs. Revisit only if raw touch input is wanted for its own
  sake.

**The generalisable lesson: "the platform delivers this event" is a claim, not a given.** It sat in
this plan as settled fact and was false. A milestone resting on one is worth a ten-minute
instrumented check *before* it is built, not after.

### M90 — Notes: the interface (owner-specified 2026-07-27)

The visible half of the feature whose model M81 lands. The six rules governing behaviour are in
§M81; this milestone builds the surfaces that apply them.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M90.1** Create + edit | **Note** verb on the **Markup ▾** dropdown (no new toolbar slot — it is a text-markup verb, and §Design budgets holds the bar at ~10) plus **Add / Edit / Remove Note** on the existing M76 markup context menu. Applying to a selection **resolves the host first** (rule 4): an existing HUS under the selection receives the note as-is; only plain text with no HUS creates a Highlight, and that creation plus the attach is **one undo macro**. One-shot, not sticky like M73's HUS quartet — writing a note is a deliberate single act. Clearing a note's text removes the note and **leaves the mark**. | WSLg | A selection already carrying a highlight gets the note on *that* mark with no second mark created and its geometry unchanged; a plain selection makes a highlight in the current colour with the note attached, one undo; a selection carrying both a highlight and an underline attaches to the **highlight** (rule 6); emptying the text drops the note, not the highlight |
| **M90.2** On-page affordance | A small note glyph on the marked passage — without one a note is invisible until the exact mark is right-clicked. Click/hover opens it. The editor reuses the `_TextBoxEditor` idiom (a `QPlainTextEdit` child of the viewport), so focus, clipboard routing and the Space/Home key guards of M81 behave as they already do. | WSLg | The glyph appears only on noted marks, is legible at low zoom, does not obscure the marked text, and re-tints with the theme |
| **M90.3** Annotations sidebar | Noted marks show their note in the M77 panel — the panel that already exists as "a reading of the document's margin" — and are editable there. | WSLg | A note shows in the sidebar; editing there and on the page agree; deleting the host removes the row |
| **M90.4** Foreign notes | A foreign markup's `/Contents` displays as a **read-only** note and lists in the sidebar, consistent with M68 (foreign marks are not editable until adopted). **Adopting** one (M68 double-click) carries the note across into the editable KlarPDF mark. M66/M67 already parse and move these annotations, so the read side is largely built. | WSL + WSLg | An Acrobat/Preview commented highlight shows its note read-only; adopting it makes the note editable and it round-trips as ours |


**Owner decisions, recorded so they are not relitigated** (all 2026-07-27): merged marks **keep and
join** notes rather than dropping them or refusing to merge; a note takes **its host's** colour, so a
note on a red underline is red (not always highlight-yellow); foreign comments are **shown read-only
and adopted on edit** rather than ignored; and the affordance is **both** an on-page glyph and the
sidebar, because a sidebar-only note is undiscoverable when the sidebar is closed.

**Interactions to respect.** Notes ride the HUS marks, which are exactly the types M77 already lists
(`is_listed`), so no panel-existence logic changes. `Ctrl+A` (M89.6) and Find remain body-text-only, so
neither reaches note text. And a document whose *only* marks are notes-on-highlights already has
listed markup, so the Annotations tab's existence check needs nothing new.

### M91 — Whitespace fidelity, glyph legibility, reading position (owner-reported 2026-07-29)

Three defects from the owner's post-M90 testing pass. Independent of one another and grouped only
because they arrived together, each is small, and all three are **view-layer**: no model change, no
save path, no round-trip. One PR per part.

**The numbering is the build order** (owner request, 2026-07-29), which is *not* the order the three
were reported in. Three reasons, in decreasing weight. **Fidelity bugs before features:** M91.1 is the
only part where what the app shows and what it saves disagree, and a viewer that renders its own
annotations wrongly is a worse thing to be carrying than a missing indicator. **The owner-gated part
sits in the middle:** M91.2 cannot be finished without a pick from rendered candidates, so it wants to
be in flight while something else is reviewable, not first (where it blocks) or last (where it delays
the release on a round-trip). **The part that adds surface goes last:** M91.3 is the only one that adds
a widget, a toolbar slot and a new binding rather than correcting something that exists, so it is the
one whose review is worth having the other two already settled — and the only one that spends the
slot the §Design budgets note argues over, which is a decision better made against a finished bar.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M91.1** A text box paints its leading spaces | `_paint_textbox` draws **one `QGraphicsSimpleTextItem` per line**, each line's leading-whitespace run removed from the string and paid for as an **x offset** (`QFontMetricsF.horizontalAdvance(indent)`); vertical centring switches to `len(lines) * lineSpacing()` since no single item spans the box any more. Separately, `_wrap_textbox_lines` stops eating the indent of every line it wraps. Two other surfaces strip under the same rule and are fixed with it. | WSL (model) + WSLg | A box typed `"    hello"` paints its ink indented with no slack left on the right; a two-line box paints each line at its own indent; a width-dragged box keeps the indents when it wraps; an all-whitespace box is still dropped; the round-trip tests do not move — this is a paint fix, so the saved bytes are unchanged |
| **M91.2** Rotate stops reading as Undo | Redraw `rotate-left.svg` — and `rotate-right.svg`, which shares the Edit menu and the sidebar context menu — as **a page with an arrow arcing over one corner**, replacing the bare circular arrow. Three candidates rendered at 16/20/24 px for the owner to pick from, as M78.4 did for Grab / Text Box / Pen; both names join `POLISHED_ICONS` in `tests/test_icons.py` so the QtSvg-safety, not-blank and centring assertions cover them. | WSLg (rendered grabs) | At 20 px the glyph is unmistakably a page being turned; `test_icons.py`'s polish assertions pass for both names; both re-tint on a theme change (they already carry `iconName`, so `_retint_icons` covers them) |
| **M91.3** Page counter | An **editable page field on the reading bar** — `[ 10 ] of 320` — two-way bound to `PdfView.currentPageChanged` exactly as `ZoomWidget` is bound to `zoomChanged`: the view is the one source of truth, typing a number + Enter calls `goto_page`, out-of-range clamps and echoes the clamped value back, garbage restores the live one. New `viewer/page_widget.py`, mirroring `viewer/zoom_widget.py`. The **total** refreshes from `_on_doc_changed` — there is no `pageCountChanged` signal, and insert / delete / undo change the count without moving the current page. Its own group **between the fit buttons and rotate** (owner placement 2026-07-30 — the counter belongs with the view controls it reads against, not beside Save). | WSLg / Windows (offscreen GUI) | Opens showing `1 of N`; scrolling updates it; typing a page jumps; out-of-range clamps; deleting pages updates the total and undo restores it; a wheel scroll over the bar never steals focus from the view |

**M91.1 — the fault is not where it looks (measured 2026-07-29).** The owner reported a text box
truncating leading spaces, then re-tested and refined it: the spaces **are** saved, but the box **paints**
without them, and they reappear only on double-click into edit mode. That is exactly right, and it
narrows to one Qt behaviour:

- **The model, the save and the round-trip are all correct.** A box committed as `"    hello"` stores
  `"    hello"`, bakes an appearance stream containing `(    hello) Tj`, rasterises with its ink offset
  by 13 px, and reopens verbatim. `test_textbox_preserves_whitespace_and_newlines` has pinned this since
  M27, which is why the bug survived: the tests assert the *stored* text, and the stored text was never
  the problem.
- **`QGraphicsSimpleTextItem` reserves leading whitespace in `boundingRect()` but paints the visible
  glyphs flush left.** Measured at 24 px: the bounding rect is **288 px** wide for `"    hello"` against
  **160 px** for `"hello"` — 128 px duly reserved — yet the first inked column is **x = 2 in both cases**.
  So today the box grows wider by the indent *and* the text does not move, which puts the indent on the
  **right** as slack. `QGraphicsTextItem` behaves identically, and a non-breaking space does not help.
- **The consequence is that the overlay and the saved file disagree**, inside a method whose docstring
  claims WYSIWYG. Edit mode is right (a `QPlainTextEdit` holds a real text document) and the file is
  right; only the resting view is wrong.
- **`_wrap_textbox_lines` has a second, independent bug** on the fixed-width path: `para.split(" ")`
  turns every leading space into an empty token that the `if current and …` guard discards, so
  `"    indented first"` wraps to `["indented", "first"]` — the indent is destroyed on *every* line, not
  just the first. Reached after a width-handle drag (M78.3) and for any reopened box whose text does not
  fit one line.

**The approach is verified, not assumed:** with the indent stripped and re-applied as an x offset, the
ink lands at x = 98 for a 96 px indent (the 2 px is the item's own left bearing) — where it belongs.

**The governing rule, stated once:** *whitespace decides whether the text exists, never how it looks.*
The all-blank drop stays exactly as it is — `_commit_textbox` already keeps `raw` and strips only to
decide empty-vs-not, and that is the shape the other sites should copy. Two of them do not:

- **`_commit_note`** — `text.strip()` turns `"    indented note"` into `"indented note"` and drops
  trailing space too. An all-whitespace note must keep dropping the note and leaving the mark (M90.1's
  rule), so the fix is the two-variable form: `stripped = text.strip()`, store `text if stripped else ""`.
- **`ui/field_dialog.py`** — a new form field's initial **value** is stripped. Its **name** keeps its
  strip: a field name is an identifier, and a whitespace-only name must go on disabling OK.

`FormFiller` needs no change — it already stores `editor.text()` verbatim.

**A test-design trap this uncovered, worth recording for every future text measurement.** The headless
platform resolves **no font at all**: `QFontInfo(qt_font("helv", 24)).family()` is `''`, `exactMatch()`
is `False`, and every glyph *including the space* measures exactly one em (24.00 px) against Helvetica's
real 6.67 px. So **no headless test may assert an absolute pixel offset for text** — it would encode the
tofu fallback's metrics and pass for the wrong reason. Assert the relative invariant instead: an indented
line's item starts further right than the same line unindented, by that font's own measured advance for
that indent. (This is also why the zoom combo renders as `□□□` in offscreen grabs — a rendering artifact
of the test platform, not a defect.)

**M91.2 — why v0.16.2 did not already fix this.** `rotate-left.svg` is Feather's `rotate-ccw`: a ~340°
circle with an arrowhead and **nothing being rotated**. That is the universal *undo / reload* mark, so
the button reads as Undo **on its own merits**, which is why removing its neighbouring curved arrows in
v0.16.2 left the misreading intact — the owner still reports having to be reminded not to click it to
undo. The same bar already establishes *rounded rect = the page* in `fit-width` and `fit-page`; rotate is
the one page operation whose glyph contains no page. **Owner call 2026-07-29:** redraw, and keep the
single direction (Rotate Left, as Preview's own toolbar does). Rejected with reasons: *dropping* the
button (a frequent verb for sideways scans would lose its one-click path), and *restoring both
directions* (a mirrored pair is exactly what v0.16.2 set out to remove).

**The corner and the direction are coupled — measured 2026-07-30, and it constrains any future
redraw.** Asked to see the corner sweep on the **top-right** instead of the top-left, we drew it, and
it fails for a reason worth keeping. Rotate Left is counter-clockwise, so a sweep ending at the
top-right must point **back to the left, over the page**; an arrowhead's arms open backwards from its
tip, which puts one arm along the inside of the arc it just travelled. With a 12-unit page, a
6.5-unit arc and 2-unit strokes inside a 24-unit box, that arm lands **1.3 units** from its own arc —
less than a stroke width once both are inked — and at 20 px the head and the arc merge into a blob.
Only two resolutions exist: let the sweep **start** at the right corner and cross the whole top (the
head then lands in open space at the left), or keep the sweep at the right corner and accept that it
reads **clockwise**, i.e. Rotate *Right*. So: **a compact corner sweep can only face the direction
its corner allows** — top-left for counter-clockwise, top-right for clockwise. **Owner call
2026-07-30:** the top-left corner sweep, keeping Rotate Left on the bar.

**The shipped drawing came from Claude Design, and its construction is the part worth keeping.** Two
in-house rounds were rejected by the owner, so the constraints above were written up as a brief and
the glyph was designed in the project *Sheaf PDF application branding*
(`Rotate glyph candidates.dc.html`), then imported over the design MCP. The chosen candidate,
**"Corner gutter"**, does something our hand-drawn arcs did not: the sweep is an **offset curve of the
page outline** — top rail, corner arc of r 7.2 (the page's own r 2.2 **+ 5**), left rail — so the
clearance between arrow and page is uniform *by construction* instead of tuned per candidate, and the
two shapes read as belonging to each other. That is the rule to reuse for any future glyph pairing a
mark with the page keyshape. The page also stays a **full portrait document** rather than shrinking to
make room, which is what kept it optically level with `fit-page`'s rect.

**Imported artwork is re-measured, never accepted on the design doc's own numbers.** The doc stated
its own span and clearances; we re-ran them through the code `tests/test_icons.py` uses and through
Qt's rasteriser (a browser's flatters every candidate). Measured on the shipped file: parses under
QtSvg, no banned construct, ink span **62%** of the canvas, centre **(11.5, 11.5)**, nothing across
the 2 px margin, and the mirror **0** differing pixels at 48 px. The two numbers that disagreed with
the doc were both in our favour; the gate is ours either way.

**M91.3 — why it is needed, and what it costs.** `sidebar_visible` defaults to **`False`**, so out of
the box the app gives a reader **no** position indication whatsoever: the sidebar's current-thumbnail
highlight is the only one that exists today, and on a 320-page document that is the difference between
reading and guessing. The field makes the reading bar **11 slots** against §Design budgets' "modes-only,
~10" — **owner call 2026-07-29: taken**, on three grounds. A live indicator is not a mode; the bar
already carries one (zoom), so this is the established pattern rather than a new kind of thing; and the
field *replaces* a dialog trip (Ctrl+G) for the common case instead of adding a verb. Measured, the bar
uses 436 px of an 1100 px window, so space was never the constraint — the budget was. (Built: **555 px**,
the counter costing 119 px including its separator.)

**A `QWidget` in a `QToolBar` will eat the bar — measured 2026-07-30.** `QToolBar.addWidget` leaves a
plain widget on the default **Preferred** size policy, and the toolbar's layout hands every spare pixel
to whatever will take it: `PageWidget` stretched to **627 px** in an 1100 px window and pushed the entire
zoom cluster off the right-hand end. `ZoomWidget` never showed the problem because it calls
`setFixedWidth` on itself — it is the only other widget on either bar, which is why the trap went
unmet until now, and why it will be waiting for the next one. The fix is one line —
`setSizePolicy(Fixed, Preferred)` — and `test_page_counter.py` pins it, because the failure mode is
*invisible chrome*, which no assertion about the widget itself would have caught.

**Recorded non-goals**, so they are not re-proposed as free wins: no ◀ ▶ prev/next buttons (two more
slots for what the wheel, PgUp/PgDn and M89.1's Home/End already do); no counter in Full Screen or
Slideshow, which M78 made deliberately chrome-free; and in Two-Page mode the field shows the **current**
page as the rest of the app already defines it (M85 — largest visible area, ties to the earlier page)
rather than a `10–11` span, so the field, the sidebar highlight and the outline tab cannot disagree.

#### M91.4 — Space pages by a page, and the sidebar hands it over (owner-reported 2026-07-30)

Three reports from the owner's testing pass on the new page counter, all against **M89.2's** `Space`
paging. Two are the same defect seen from different sides; the third is a question, answered "no".

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M91.4** Space pages by a page | `Space` / `Shift+Space` / `PgDn` / `PgUp` step to a **reading stop** (`PdfView._reading_stops`) instead of the scrollbar's `SliderPageStep`: the top of each page, plus a tall page cut into the fewest **equal** steps that each fit a screenful. The sidebar panels leave `Space` unaccepted so it propagates to `MainWindow.keyPressEvent`, which hands it to `PdfView.reading_key`. A thumbnail **click** jumps even when it lands on the already-current row. | Windows (offscreen GUI) | Twelve presses from page 1 land on twelve page tops, each equal to `goto_page`'s offset; `PgDn` matches `Space`; a page taller than the screen takes equal steps ending on the next page's top; zoomed out, one press advances every page that fits; `Space` with focus in the Pages / Outline tab pages the document and leaves a staged multi-row selection alone; clicking the current thumbnail re-seats the view |

**The drift is one `_PAGE_GAP` per press, and it is arithmetic, not a race (measured 2026-07-30).**
`SliderPageStepAdd` advances by the scrollbar's `pageStep`, which `QGraphicsView` sets to the
**viewport height**. The strip advances by the **page pitch**. At Fit Page those are not the same
number and cannot be: `_fit_zoom` reserves `2 * _PAGE_GAP` of margin, so the page is `viewport −
2·gap` tall, and the layout puts one gap back between consecutive pages — pitch `= viewport − gap`.
Measured in an 1100×800 window: viewport 746, page 718, pitch 732, `pageStep` 746. **Every press
overshoots by exactly 14 px**, and nothing ever resets it:

| press | scroll offset | page 1's own top | slip | what fills the window |
| --- | --- | --- | --- | --- |
| 1 | 746 | 732 | 14 | page 2 (100%), 2% of page 3 |
| 9 | 6714 | 6588 | 126 | page 10 (84%), 18% of page 11 |
| ~27 | — | — | ~380 | **the counter says 28 while page 27 fills the top half** |

That last row is the owner's report verbatim — "if I am on page 10, what I see on screen is bottom
half of page 9 and top half of page 10". The counter is not lying: M85 resolves the current page by
largest visible area, so once the slip passes half a screen the *next* page wins the count while the
*previous* one still fills the top of the window. Both readings are correct; the scroll offset is
what is wrong.

**Why a reading stop, and why the subdivision is equal.** One rule covers three cases that would
otherwise each need their own: a page that fits advances exactly one page; several pages that fit at
once (zoomed out) advance as many whole pages as the screen holds, still aligned — hence *furthest*
stop within reach, not nearest; and a page taller than the screen takes equal steps. Equal, rather
than "a screenful, then whatever is left", because the remainder can be a handful of pixels — a press
that visibly does nothing — and since every page of a document is usually the same height, it would
do nothing **once per page, for the whole document**. Consecutive stops are at most a screenful apart
by construction, so a press always finds one. `PgDn`/`PgUp` are taken off Qt for the same reason:
M89.2's promise is that they and `Space` are one verb, and left to the base class they would have
drifted from the page *and* from `Space`.

**The sidebar was not inert — it was eating the key.** `QAbstractItemView::keyPressEvent` **accepts**
`Space`, so Qt's key propagation stopped at the panel and the view never saw it: with focus in the
sidebar the document could not be paged at all, and the only way back was to click the page. Worse,
what Qt does with the key is `selectionCommand` → `Select`, which **adds the current row to the
selection** — the selection Delete Pages and Rotate act on. So the answer to "is that expected?" is
no twice over: the key was dead *and* it was quietly staging a page the reader never picked.

The fallback lives in `MainWindow.keyPressEvent`, which is the same decision M89.2 recorded, read the
other way round. A **`QAction` shortcut** fires *before* the focused widget sees the key, which is why
`Space` must never be one — it would be stolen from the inline text-box and form-field editors. A
**window `keyPressEvent`** runs only after every widget in the chain has declined it, so an editor
still types its space and a reader in the sidebar still turns the page. Only `Space` is handed over:
the arrows, `PgUp`/`PgDn` and `Home`/`End` all mean something in a page list, and each jumps the view
through `pageActivated` anyway.

**And a thumbnail click must always jump.** `ThumbnailPanel` announced clicks through
`currentRowChanged`, which fires only when the row **changes** — but the view drags the highlight
along as the reader scrolls, so scrolling away from page 1 and clicking page 1's thumbnail to get
back did *nothing*: the row was already 0. That is the second half of the owner's "clicking on page 1
again … requires multiple attempts" — the click was a no-op and the `Space` presses that followed
were being eaten by the panel. `OutlinePanel` and `AnnotationsPanel` already jump from `itemClicked`;
this makes the three agree.

**The page counter was fighting the reader too (owner re-test, 2026-07-30).** "Press spacebar, the
first page flickers but stays at 1" reproduced only once focus was accounted for, and the answer was
the counter M91.3 had just added — two independent faults in the same 44 px box, both measured:

- **`Space` was eaten.** The field is integer-validated, so a space can never be valid input — but
  `QLineEdit` accepts the key regardless and the validator silently drops the character. A reader who
  clicked the field once found the commonest gesture in the app dead for the rest of the session,
  with nothing on screen to say why. (`PgUp`/`PgDn` already worked from there, because `QLineEdit`
  *declines* those — which is the shape of the fix: `PageField` declines `Space` too.)
- **An unedited focus-out re-applied the field.** `editingFinished` fires on Enter *and* on every
  focus-out with acceptable input — Qt does **not** require the text to have changed (measured; the
  documented "contents have changed" wording does not match `QLineEdit::focusOutEvent`). So clicking
  the field and clicking back onto the page re-ran `goto_page` with the displayed number, and
  `goto_page` re-seats the view on that page's **top**. That is the flicker: the reader was pulled
  back to where the field last said they were. `isModified` is the exact question — Qt sets it when
  the *user* edits and clears it on `setText`, which is how `show_page` marks a value as the view's
  rather than the reader's.

Recorded so it is not "fixed" in the wrong place: **`ZoomWidget` has the identical wiring and is not
wrong.** Re-applying a stale zoom is a genuine no-op because `set_zoom` returns early on an unchanged
value; `goto_page` has no such early-out **and must not**, since jumping to the page you are already
on is exactly what a reader means by clicking its thumbnail. Enter also hands the keyboard back to
the page: a page field is a one-shot instruction, not somewhere to leave the focus.

**And the one that made it look intermittent: a coasting wheel undoes a deliberate step — in every
mode, not just the slideshow (owner re-test, 2026-07-30).** With focus never leaving the page and no
click anywhere, spinning the wheel **hard** back to page 1 and pressing `Space` makes the page
"flicker and stay on page 1", and the next press "moves only half a page" — **100% reproducible on a
fast spin, never on a slow one.**

That is M78's bug, met a second time. A flywheel wheel (and Windows' smooth scrolling) keeps emitting
long after the hand has left it, so the coast walks the view back out of the step the key just made;
a harder flick coasts longer, which is why the count of dead presses tracked how hard the owner spun
— and why the first round's "growing count" report was real and the first round's repro, which fired
keys directly with no wheel in flight, could not see it. The reason it hides so well: **scrolling up
at offset 0 is a no-op**, so the coast is invisible until a paging key gives it somewhere to go, at
which point the *key* looks broken rather than the wheel still running.

M78 diagnosed and fixed exactly this, then scoped the guard inside `if self.slideshow` — so ordinary
reading, where the same wheel drives the same view, never got it. M91.4 hoists the mute to the top of
`wheelEvent` and arms it from every deliberate navigation: the paging keys, Home/End, the slideshow's
`_deliberate_step`, and `goto_page`, which is where the thumbnail, the outline, the counter, Ctrl+G
and internal links all arrive. Two things the generalisation needed:

- **A wheel-driven move must not park the wheel that drove it.** `step_slide` lands by calling
  `goto_page`, so arming there made the wheel mute *itself* after one detent and a four-detent flick
  moved one slide (caught by M78's own tests). `_wheel_driving` is set only inside `wheelEvent`.
- **The quiet test must fail open on a backwards clock.** The elapsed check gained a `0 <=` lower
  bound: `event.timestamp()` and the `time.monotonic()` fallback are different clocks, and before
  this only the slideshow kept the timestamp, so the mixed-source case could not arise. Now that
  every wheel event updates it, one unstamped event followed by a stamped one would have left the
  wheel muted for ever. A mute that cannot lift is a dead wheel.

**`open_at` must announce the page it restored.** Reopening a document closed on page 10 showed page
10 with the counter reading 1. `open_at` assigns `_current` **directly**, because the Fit Page zoom
has to be sized against that page's row before there is a scene to derive it from — so when
`goto_page` then scrolls there, `_update_current` finds the page it already holds and stays silent.
The sidebar never showed the bug because `MainWindow.showEvent` carried a private workaround
(`mark_open_page`) for exactly this; the counter had none, and neither would the next indicator
bound to that signal. The emit belongs at the source, so no consumer has to know.

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
- **D — quantise zoom to a `1.25^n` ladder.** Would restore cache hits cheaply, but buys that by
  giving back the smooth touchpad response M80 exists to deliver. Still recommended against —
  but **one of the two arguments against it has since been measured away**: it also said a ladder
  would make M89.5's pinch ratchety, and M89.5's validation found the touchpad driver **already
  delivers exactly whole `1.25` steps** for a pinch (§M89). So a ladder would cost pinch nothing on
  this hardware; what it would still cost is Ctrl+wheel and a real hi-res wheel. Recorded so it
  isn't re-proposed as a free win *and* isn't rejected for a reason that no longer holds.
- **E — render pages off the UI thread.** All rendering today is synchronous (verified: zero
  threading in `viewer/`, `organize/`, `model/`, `app.py`, `main_window.py`). The white/black page
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

### M92 — Mouse-wheel scrolling (owner-reported 2026-07-30)

One defect, one polish, in that order of weight. The owner reported that **one detent of the mouse
wheel moves too much of the page**, and separately that scrolling is less fluid than Edge. Measured
on the owner's display, the first is a real defect with a specific cause; the second is a much
smaller effect once the first is fixed. Touchpad scrolling is explicitly **out of scope** (owner,
2026-07-30: *"though not perfect I am satisfied with it for now"*) — the inertia/fling work that
would need is recorded under §Future enhancements, not here.

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M92.1** A wheel detent moves a defined distance | `PdfView.wheelEvent` stops delegating the plain (unmodified) wheel to `super()` and computes its own step: **`wheelScrollLines × _WHEEL_LINE_PX × zoom`**, applied to the vertical scrollbar. `_WHEEL_LINE_PX = 32` logical px, **set from the owner's side-by-side against Edge** (see below), so Windows' *lines to scroll* setting finally means to us what it means to every other app. Proportional to the raw `angleDelta` (`delta/120`), so a hi-res wheel's fragments accumulate rather than quantise. | WSL + WSLg | One detent moves exactly `wheelScrollLines × _WHEEL_LINE_PX × zoom` px; the distance is **unchanged by window height** (the defect) and **scales with zoom**; N detents accumulate to N × step with no lost delta; hi-res fragments summing to 120 move exactly one step; `Ctrl`/`Shift`/slideshow paths unchanged |
| **M92.2** The step is eased, not teleported | A clock-driven scroll animator on `PdfView`: a wheel tick moves a **target**, a `QTimer` (parented to the view, interval from `QScreen.refreshRate()`) walks the scrollbar to it on an **ease-out** curve over **`_WHEEL_EASE_MS = 200`** — the owner's pick, made with the wheel in hand against a live toggle. A tick arriving mid-animation **extends the target and re-times the curve** from the current position rather than restarting from rest. `_glide_tick` ends **on the pixels, not on the clock**. A **View ▸ Smooth Scrolling** preference turns it off, restoring M92.1's direct write. | WSLg / Windows | A detent's motion is spread over ~200 ms and lands on **exactly** the M92.1 pixel; held spinning reads as continuous motion, not a train of lurches; a reversal collapses the target instead of unwinding it; a deliberate nav (`Space`, `goto_page`, Home/End) cancels the animation; a stalled frame does not stretch the glide; with the pref off, behaviour is byte-for-byte M92.1 |

**The defect, measured on the owner's display (2026-07-30).** Qt's `QGraphicsView` sets the vertical
scrollbar's `singleStep` to **`viewportHeight / 20`** — confirmed directly: viewport 846 px →
`singleStep` 42, viewport 832 px → 41. A wheel detent is `wheelScrollLines × singleStep`, so **our
step is 15% of the window height and nothing else** — unrelated to the document, the text, or the
zoom, and it gets *worse* the more screen the window is given. `_place_window` opens the window at
the **full available screen height** by design (§M84), which puts that derivation at its maximum:

```
screen   2560x1440 @ 100%    window 1000x1353    viewport 770x1246    zoom 91% (Fit Page)
wheelScrollLines 3   singleStep 61 (= 1246/20)
ONE DETENT = 183 logical px = 19.1% of a page = 10.1 lines of body text
```

Ten lines of text on one click of the wheel. The replacement rule is invariant under window size and
moves the same amount of *document* at every zoom; what remained was to fix its one constant.

**The constant was borrowed, then measured (2026-07-30 → 07-31).** It shipped at
**`_WHEEL_LINE_PX = 40`** — the figure Chromium and Gecko share, giving `40 × 3 = 120 px` at 100%
zoom and **109 px** at the owner's Fit Page. The owner's side-by-side then settled it: in the same
document a detent moved **10 lines against Edge's 8**, so `40 × 0.8 = ` **32**, and one detent is
**87 px at Fit Page — 2.09× smaller than Qt's 183**. Two independent observations agree on the
target: *"Edge moves about half"* of 183 px is ~91 px, and 8/10 of 109 px is ~87 px. The likely
reason the web constant was the wrong one to borrow is that **Edge renders PDFs through PDFium**, not
the generic web scroll path, so its viewer never used 40 px/line at all. Worth keeping in view on any
future tune: the "standard" number is a standard for *web pages*, and this is a PDF viewer — the
owner's side-by-side is the better authority, and the reader's own control is the Windows
lines-to-scroll slider.

**A third candidate, recorded because it is the one a purist would reach for:** 3 *real* text lines
(`wheelScrollLines × 15pt-line × scale`) measures **55 px** on the same setup — well past Edge in the
other direction. The semantically pure rule and the comfortable rule are not the same number, and the
owner's judgement of feel is the tiebreak.

**Why the step, not the animation, is the headline** — and a correction to this plan's own first
draft. The initial proposal led with smooth animation on the strength of a *recalled* Chromium
constant compared against a detent measured in a 900 px-tall bench window, which put us at 126 px and
Edge at 120 px — "comparable", and therefore the difference had to be the easing. Both halves were
wrong: the real window is 1353 px tall (183 px per detent, not 126), and the owner's direct
observation on real hardware is the better evidence. The lesson is the repo's own rule read back at
itself — **measure on the machine that has the problem, in the window it actually opens at**, before
attributing a felt difference to a mechanism.

**Cost, measured (2026-07-30, this machine, offscreen harness).** M92.2 is the only part with any
runtime cost, and it is small:

* **Per animation frame: ~0.11–0.15 ms of `_on_scroll` plus ~0.7–1.2 ms of viewport repaint** — about
  1 ms of a 16.7 ms frame, ~6% of one core, and **only while an animation runs** (~130 ms per
  detent; zero at rest). The handler cost is **flat** across zoom (1.0×–2.0×), DPR (1.0/1.75) and
  document content: measured **0.148 ms with no marks and 0.143 ms with 880 marks over 40 pages**,
  because `_on_scroll` is band-bounded by M87.3's binary search and the overlay work early-returns.
* **Memory: no change.** The band comes from `_visible_range` + the M87.1 adaptive prefetch and is
  identical whether a distance is crossed in one jump or thirty frames. Resting cache on a 60-page
  Letter document: **79 MB @1.0×, 140 MB @1.5×, 197 MB @2.0×**, bounded by `RETAIN_PAGES` and the
  1 GB backstop. The animator itself is a few floats and a timer.
* **The one real risk is a rasterise landing mid-glide.** Rendering a page is synchronous on the UI
  thread inside `_on_scroll`: **4.1 ms** (text, DPR 1.0, zoom 1.0) rising to **40.8 ms** (text, DPR
  1.75, zoom 2.0) and **48.3 ms** (full-page scanned image, DPR 1.75, zoom 2.0). Today that lands
  inside a discrete jump where nobody perceives it; inside a glide it is a visible hitch. **Two
  things keep it small here**: a detent is ~110 px, so a single step rarely crosses a page boundary
  at all (this is the risk that made the touchpad-fling case expensive, and dropping that scope drops
  most of it), and the animator is driven from the **wall clock, not a per-frame increment** — a
  blocked frame costs smoothness for two frames but the motion still lands on the right pixel at the
  right time. An increment-driven animator would stretch and drift instead.

**What M92.2 cannot fix, and the measurement that settles it (2026-07-31).** The owner asked why a
finger drag on the touchpad looks *continuous* where the wheel looks *quantised*, and the answer is
not frame rate, transit smoothness, or anything easing touches. A finger drag has **no minimum
unit**; a detented wheel does, so after M92.1 the positions the page can come to **rest** at form a
**lattice of 87 px**. There is no wheel gesture that moves the document 30 px. M92.2 smooths the
*transit* between lattice points — the worst single-frame jump falls from **87 px to 20 px** at 200 ms
ease-out, and held spinning becomes one motion instead of a train — but the lattice is untouched.

The obvious escape was **hi-res wheel reporting** (a mouse sending ~15 units per notch instead of 120,
which would dissolve the lattice), and the probe closed it: this mouse's **free-spin mode is purely
mechanical**, emitting 160 whole ±120 detents where discrete emitted 50, with no change in encoder
resolution. So the lattice is a property of the hardware, and glide is the only improvement available
to us. Recorded so the option is not re-proposed: it was checked, on this mouse, and it is not there.
Per-frame motion at 200 ms, for reference — ease-out `20.0 16.7 13.6 10.9 8.5 6.4 4.6 3.1 1.9 1.0 0.3`,
linear a uniform `7.3`, no glide a single `87.0`.

**Choosing the duration (owner, 2026-07-31), and the bound that stopped binding.** The pick was made
against a **throwaway toggle demo** — the real app with easing on a live key, plus `[`/`]` to walk the
duration and a key to cycle the curve — rather than from the benchmark alone, because the only
question that mattered was whether it *feels* better. Two bounds framed the range: **lag** (how far
the page trails the hand during a 5-detent/second spin: 60 px at 130 ms, 66 px at 170 ms, 88 px at
200 ms, against a detent of 87 px) and **duty cycle** (70% / 86% / 100%). 200 ms sat on the edge of
both.

Then the implementation moved one of them. `_glide_tick` **ends when the rounded position reaches the
rounded target**, not when the clock runs out — an ease-out asymptotes, so the tail moves under half
a pixel a frame and the reader sees nothing. Measured on the owner's display, a detent's motion is
complete at **t = 0.80, i.e. 160 ms of the 200**, and stopping there cuts duty at 5 detents/second
from **100% to 80%** (3/second: 60% → 48%) for exactly the same landing pixel. The lag bound still
argues for less, and **170 ms remains the largest value inside both bounds as first drawn**, recorded
in case this ever wants walking back.

**Why the timer interval comes from `QScreen.refreshRate()`.** Not for phase — a plain `QTimer`
cannot lock to vblank, and Qt does not vsync-lock raster `QWidget` painting on Windows in any case
(measured: this display refreshes at **59.95 Hz**, every 16.68 ms, and no integer millisecond divides
it). It is for **rate**: a hardcoded 16 ms would produce 62 updates a second on a 144 Hz panel, needlessly
coarser than the panel can show. **Truncated, not rounded**, so we always produce at least one update
per display frame — 17 ms would give 58.8 Hz, under-sampling, where the display shows a position
twice and the motion micro-stalls; over-sampling merely computes a frame nobody sees, at ~1 ms.
A related hypothesis was **disproven**: `QTimer`'s default `CoarseTimer` was expected to be too loose
on Windows, but measured over 2 s at 16 ms it is indistinguishable from `PreciseTimer` (mean 16.01 vs
16.00 ms, sd 0.21 both), so the default stays and `PreciseTimer`'s power cost is not incurred.

**Verified end to end on the owner's display**, real clock rather than the tests' injected one: one
detent traces `19 36 48 59 67 74 79 83 85 86 87` px across 12 distinct positions and lands on the
M92.1 pixel — **worst single-frame jump 19 px against 87 px unglided**.

### M92.3 — the coast-mute is bounded (owner-reported 2026-07-31)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M92.3** The wheel can never be dead for long | `wheelEvent`'s mute test moves into `_mute_still_applies`, which keeps M91.4's quiet-gap escape and adds two the mute **cannot renew**: a **ceiling** of `_WHEEL_MUTE_MAX_MS = 800` measured from when it was armed, and a **direction reversal**. | WSL + WSLg | Four seconds of continuous events after `Space` recover inside the ceiling; a continuous stream cannot push the ceiling back; a coast *inside* the window is still swallowed (M91.4 must not regress); the quiet gap still lifts it; a reversal lifts it at once; re-arming forgets the previous coast's direction |

**The defect.** Owner, 2026-07-31: *"if I scroll with mouse wheel really fast and press space bar
while pages are scrolling, the scrolling stops but the mouse wheel becomes unavailable to resume
scrolling again for a long duration; I have to click around before it becomes responsive."*

M91.4's mute lifts once the wheel has been quiet for `_WHEEL_QUIET_MS`, but **a swallowed event still
refreshed `_last_wheel_ts`** — so the quiet window could never elapse while events kept arriving, and
the mute was **indefinitely renewable**. Reproduced: **200 consecutive events over 4 seconds, every
one swallowed**, recovering only after a 300 ms pause. The cruelty is the feedback loop — the
instinctive response to "scrolling stopped working" is to scroll *more*, which is exactly what holds
the mute open, and "clicking around" works only because it is time spent *not* touching the wheel.

**Pre-existing (M91.4), surfaced by M92.** The mute block was untouched by M92.1/M92.2. What changed
is the exposure: M92.1 cut the step 2.09×, so covering a document takes about twice the spinning —
the coast probe caught 589 events in a single discrete-mode burst, ~51 000 px, nearly a whole 60-page
file — and M92.2 gave "the pages are moving" a visible duration that invites pressing `Space` into it.

**800 ms is measured.** A coast probe on the owner's hardware recorded the decelerating tail after a
hard spin at **~660 ms in discrete mode and ~720 ms in free-spin**, with **no inter-event gap
reaching 250 ms until the very end** — which is why the quiet-gap test essentially never fires
*during* a coast and the trap was so easy to fall into. The ceiling must cover that tail or M91.4's
defect returns, so 800 ms clears both with a little room while bounding the worst case from
*unbounded* to a hiccup. **It is the one number that trades the owner's two reports against each
other** and is deliberately easy to move.

**A hypothesis disproven on the way:** discrete/ratchet mode was expected not to coast at all — which
would have meant the mute was swallowing deliberate input on a false premise. The probe shows it
coasts much like free-spin (660 vs 720 ms). The mute's premise holds; only its renewability was wrong.

**Two clocks, never compared.** The ceiling is timed on `PdfView._now_ms` (monotonic, shared with
M92.2's glide — hence the rename from `_glide_now_ms`), while the gap test keeps using
`QWheelEvent.timestamp()` from the platform message. Each test is internally consistent, which is
what keeps the mixed-clock trap `wheelEvent` documents from reappearing in the ceiling.

### M92.4 — prefetch off the scroll's critical path (owner-reported 2026-08-01)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M92.4** The glide stops stalling on image pages | `_render_visible` rasterises **only the visible pages**; the prefetch margin goes to `_queue_prefetch`, drained by a view-owned timer **one page per tick** and **never while a glide is running**. The queue is ordered direction-of-travel first, nearest first. | WSL + WSLg | Only visible pages are painted synchronously; the queue holds exactly the margin, ordered towards travel; the drain paints one page per tick and stops when empty; a running glide defers it and settling resumes it; a rebuild drops stale indices; `release_pixmaps` stops it; a visible page is never left to the queue |

**The defect.** Owner, 2026-08-01: *"with smooth scrolling on, scrolling tends to stall on pages with
images, while the pages with texts glide past smoothly."*

**It was entirely prefetch, and that is not what anyone would have guessed.** Measured on a 40-page
document alternating text and full-page images, scrolling one glide-frame at a time across six pages:

| zoom | frames over 60 Hz | worst frame | **visible**-page render | **prefetch** render |
| --- | --- | --- | --- | --- |
| 0.91 | 0 | 14.7 ms | **0 ms** | 48 ms |
| 1.50 | 3 | 26.8 ms | **0 ms** | 101 ms |
| 2.00 | 6 | 45.9 ms | **0 ms** | 166 ms |
| 3.00 | 6 | 91.1 ms | **0 ms** | 356 ms |

The reader **never waits for a page they are looking at** — visible-page rendering is 0 ms at every
zoom, because prefetch had already cached it. **100% of the stall is speculative work for pages one
or two ahead that are not on screen yet**, paid synchronously in the scroll handler: on the thread
that also has to animate, at the moment that can least afford it. The stall therefore lands one or
two pages *before* the image page the reader blames. Prefetch was doing its job and destroying the
thing it exists to protect.

**After**, A/B under the real animator (60 detents at 5/s, counting only frames where the page is
actually **in motion** — work that lands in the idle gap between detents cannot be seen):

| zoom | inline (before) | deferred (after) |
| --- | --- | --- |
| 1.5 | 3 frames over budget, worst 27.4 ms | **1**, worst 26.5 ms |
| 2.0 | 3 frames over budget, worst 41.9 ms | **0**, worst **0.8 ms** |
| 3.0 | 5 frames over budget, worst 91.4 ms | **0**, worst **1.0 ms** |

The work did not vanish — idle-gap work rises from ~99 ms to ~474 ms at 3x, which is the same ~6 page
renders at ~85 ms each, now paid where nothing is moving on screen.

**Measuring this needed the right metric, twice.** A first A/B counted *every* frame and made the fix
look **worse** (7 over budget against 3), because deferred work landing in the idle gap was still
being counted as a frame. Only frames during motion can be perceived as a stutter. Recorded because
the wrong metric was actively misleading, not merely uninformative.

**What is deliberately not done.** The band stays **symmetric**: trimming the margin behind would
halve the work, but it would blank the page above on every small reversal, and since the work is now
off the critical path its *total* matters much less than its *timing*. And **the honest limit**: a
reader who outruns the queue reaches a page it has not rendered yet, which `_render_visible` must
then rasterise synchronously — a stall like the old one, but only on genuinely outpacing prefetch
rather than on every image page. Removing that case needs rendering off the UI thread (§Deferred,
item **E**), whose gate this milestone does not attempt to meet.

### M92.5 — landing at the ends of the document (owner-reported 2026-08-01)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M92.5** Page 1 and the last page arrive smoothly | `_scroll_by` returns without touching `_glide_origin` / `_glide_start` when the new target **equals the one already in flight** — which happens only at the ends, where the clamp pins it. The curve in progress is left to land. | WSL + WSLg | A clamped detent does not restart the curve; from peak speed onwards the arrival at the top and at the bottom only decelerates; the last pixels do not crawl; mid-document target extension (M92.2) is unchanged |

**The defect.** Owner, 2026-08-01: *"starting from page 3 or 4, if I scroll back using free spin, we
scroll back quickly to first page and when about 70% or 80% of the first page is visible there is an
abrupt jerky stop before the rest of the page shows up… this might be related to how we have
implemented the ease-out."* Correct on both counts, and the fix is the anticipation asked for.

Replaying the owner's own probe gap pattern into the top of a document, the frames arriving at page 1
were:

```
offset 426  moved 129   p1 74% visible
offset 360  moved  66   p1 78%
offset 276  moved  84   <-- speeds UP again
offset 233  moved  43   p1 86%
offset 179  moved  54   <-- and again
...
offset  23  moved   6
offset   0  moved   1   <-- 240 ms to crawl the last 23 px
```

**Two symptoms, one cause.** Every detent set `_glide_origin = current` and `_glide_start = now`, so
each one re-entered the ease-out's **fast opening**: velocity snapped up, decayed, snapped up — a
sawtooth, landing squarely in the 74–90% band the owner named. Mid-document it is invisible because
the target keeps advancing and the run-up is *supposed* to accelerate; against a target **pinned by
the clamp**, the same shrinking distance is re-traversed and the sawtooth is all that is left. The
second symptom is the same arithmetic at the other end of the scale: restarting takes 23% of the
remainder, so once the remainder is small it moves **a pixel at a time**.

**After** — the arrival is monotone and bounded by the glide itself:

```
673 562 462 370 290 219 158 107 ... 0        reaches the top at t=496 ms (was t=944 ms)
```

**Not a new animation, a removed one.** The fix is to stop *re-issuing* the curve when nothing was
asked for. The clamp already decided the destination; the curve already knows how to decelerate into
it. Mid-document behaviour (M92.2's target extension) is untouched, because there the target always
moves.

**Two harness mistakes worth recording**, both of which produced confident, wrong pictures before the
real one:

* **Firing a detent and ticking the glide at the same instant cannot move anything** — `_scroll_by`
  sets `_glide_start` to now, so the tick sees zero elapsed. A first replay therefore showed a
  motionless spin followed by one 12-frame rush, and would have sent the fix after the wrong thing.
  Detents and frames must be merged onto a **timeline** and processed in time order.
* **Asserting the whole spin decelerates is wrong** — the run-up genuinely accelerates while detents
  keep arriving, because the reader is still asking for more. The property is that everything **after
  peak speed** never speeds up again.

**Interactions to preserve.** `Ctrl+wheel` zoom (already coalesced per frame, §M86.2), `Shift+wheel`
horizontal pan (§M89.3), and the slideshow's whole-slide stepping (§M78) all sit **before** the
scroll path and must be untouched — animating slideshow steps would break its one-page-per-screen
contract. One genuine coupling: **`_park_coasting_wheel` must also stop the animator**, or a `Space`
pressed mid-glide is undone by our own animation — M91.4's defect wearing a new hat, except that this
time we own the coast and can end it deterministically instead of inferring it from a 250 ms quiet
gap.

**Considered and rejected.** `QScroller` — built for kinetic touch-drag, grabs gestures at the widget
level, and would contend with the wheel handling already there for zoom, pan and slideshow. **Wheel
acceleration** (a bigger step for faster spinning) — Chromium does not do it for the wheel, and it is
a reliable source of "the scroll feels unpredictable"; M92.2's target extension already makes fast
spinning cover ground fast without it.

### M92.6 — the Pages sidebar rolls continuously (owner-reported 2026-08-01)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M92.6** The thumbnail sidebar scrolls a fraction of a page per detent | `ThumbnailPanel.wheelEvent` replaces Qt's `wheelScrollLines × singleStep` with `angleDelta / notch × pitch / _NOTCH_PER_THUMB` — a third of a thumbnail per detent, measured from the real row pitch, and not inheriting Windows' *lines to scroll at a time*. | WSL + WSLg | One detent moves a third of a thumbnail and not a viewport; identical at `wheelScrollLines` 1 / 3 / 10; successive detents land mid-thumbnail; the third holds at every sidebar width; a sub-notch delta moves proportionally; a tilt wheel and an unscrollable document still reach `super()` |

**The defect.** Owner, 2026-08-01: *"scrolling on the thumbnails sidebar jumps three thumbnails at a
time. Cant this be improved to have a continuous rolling of thumbnails?"* — then, having tested the
Windows slider: *"changing mouse setting in Windows 'Lines to scroll at a time' to 1 changed our app
behavior also"*.

**Two wrong factors, multiplied.** Measured on a 30-page document in a 210 × 700 sidebar:

```
item height 245 + spacing 8   ->  row pitch          253 px
Qt singleStep                                        253 px   (one whole thumbnail)
x wheelScrollLines                                       3    (the Windows default)
= 759 px asked for, clamped by Qt to pageStep        698 px   = 2.76 thumbnails per detent
```

Qt sets an `IconMode` list's `singleStep` to a whole item, so *every* line of the Windows setting was
already a whole page here; the clamp to `pageStep` then turned the request into "scroll one entire
viewport". Setting the slider to 1 changing the app's behaviour is the direct confirmation of the
second factor — and the reason the fix cannot simply be a smaller `singleStep`.

**The rule.** A detent moves `pitch / 3`:

* **Continuous, not stepped.** The reference is Edge, named by the owner: *"in Edge even Thumbnails
  move continuously, no in step of 1. So I can scroll thumbnail such that only half or a fraction of
  it is visible on the top."* A whole-thumbnail step would land on a boundary every time and re-frame
  the strip identically at each click; a third lands on the two intermediate fractions. The page stays
  the legible unit — three clicks to the next one.
* **Independent of `wheelScrollLines`**, by owner request. It is a *lines of text* preference, the
  sidebar has no text, and inheriting it is what let a reasonable "3" mean three whole pages. The
  document view still honours it (§M92.1), where it means what it is for.
* **Scaled by the pitch, not a pixel constant.** Thumbnails scale with the bar width
  (`_apply_thumb_size`, 110–240 px), so a fixed constant would drift from a third of a thumbnail to a
  half across that range. Measured after: **0.331 / 0.332 / 0.332** thumbnails per detent at bar
  widths 150 / 210 / 276. This is the sidebar's analogue of §M92.1 scaling the document view's detent
  by zoom — *a detent moves the same amount of document wherever you are*.
* **Proportional to the raw delta**, not quantised to whole detents, so a free-spin or hi-res wheel
  reporting fractions of a notch moves a matching fraction.

At the default bar width a detent lands on **84 px**, within a few pixels of the **87 px** the
document view moves at Fit Page (§M92.1) — the distance already tuned against Edge, so the two
surfaces agree without either being tuned to the other.

**Not done here: easing.** The step is a jump, as M92.1's was before M92.2 eased it. Whether the
sidebar should glide under **View ▸ Smooth Scrolling** too is a separate question with a separate
cost (the animator is `PdfView`-owned), left open rather than folded in.

### M96 *(unplanned)* — a neighbouring line vetoed a whole-word match (TC-004, owner-reported 2026-08-16)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M96** Whole-word search stops rejecting matches against words on an adjacent line | `PageText.struck` requires :func:`shares_line` as well as `boxes_touch` — a word belongs to a hit box only when one of the two vertical midlines falls inside the other | WSL | `search "Security"` on the SSA-3 returns 5, not 1; the find bar agrees; `Smith`/`Smithsonian`, `ALPHA-zero-A0` and `expression.` all still resolve as before |

**The defect.** `search` with `whole_words: true` on `ssa-3.pdf` page 1 found **1 of 5** occurrences
of `Security`, 3 of 5 `Social`, 2 of 4 `DATE`. Every missed occurrence is a free-standing word with
a space on each side — there is no reading of "whole word" under which they should be dropped, so
this is unrelated to the token semantics of TC-003 §3, which are deliberate.

**A word box is not the ink.** `get_text("words")` reports each word's box spanning the font's full
ascender-to-descender height, so on a tightly-leaded page consecutive lines' boxes **overlap
vertically**. `boxes_touch` is a plain 2-D intersection and cannot tell that apart from a word the
hit genuinely covers, so `struck` was returning words from the line above and below:

```
box for 'Security'   y=[45.2, 58.8]   x=[ 77.8, 114.0]
struck               ['Discontinue', 'Prior', 'Security']
'Discontinue'        y=[35.2, 48.8]   ← the previous line, overlapping by 3.6 pt
```

`is_whole_word` reads `struck[0]` and `struck[-1]` as the words at the hit's two edges. With a
neighbour from the line above sitting at index 0, its letters of course run to the left of the hit,
the left edge was judged to be inside a longer word, and the match was thrown away.

**One cause, not two.** The report separated a "first match per line only" symptom from the
under-count, reasonably — the second `DATE` on a line was consistently the one lost. It is the same
defect: that `DATE` sits under `DECEASED` from the next line while the first `DATE` has nothing
above it, so which occurrence survives is a fact about the neighbours, not about position. Worth
recording because "first per line" points at `group_matches` and the dedup, which are innocent. The
same cause also explains the inversion the report flagged as the signature to chase — a longer query
spans a wider box, so its edge words are different neighbours, and `Social Security Number` found
what `Social` had missed.

**The rule.** A word counts as struck only if it :func:`shares_line` with the box: **either**
vertical midline inside the other's span. One direction alone is not enough — testing only the
word's midline fails a hit box shorter than its word, testing only the box's fails a box spanning a
line of shorter words — while requiring either still puts a whole line's leading between a word and
its neighbour's midline. Precision is unaffected: `Smith` inside `Smithsonian`, `ALPHA` inside
`ALPHA-zero-A0` (M64) and the trailing period of `expression.` (TC-001) all resolve exactly as
before, because those turn on characters *within* the struck word, not on which words are struck.

**It is a shipped-app defect, not only a bridge one.** `viewer/search.py` routes through the same
`PageText.is_whole_word`, so **Find ▸ Whole words** under-reported identically — measured 1 of 5 on
the same document before the fix. That is the larger half of the impact and is what makes this
worth a milestone rather than a footnote.

**Found by the M95 verifier, on a defect nobody knew existed when it was written.** `redact_text`
did not leak here: the literal residual scan refused the write and deleted its own output, with
counts exactly right (2 missed for `Social Security`, 4 for `Security`). That is the independent
-check design paying for itself, and the strongest argument on record for keeping the safety net's
predicate separate from the matcher's. `redact_regions` has no query and therefore no such net —
with the matcher fixed the upstream under-count is gone, but the asymmetry is real and stated in its
tool docs.

### M97 *(unplanned)* — a region box may cover more than one line (TC-005, owner-reported 2026-08-16)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M97** `redact_regions` stops failing, and deleting its own correct output, whenever a single `box` spans two or more text lines | `PageText.text_under` separates lines instead of concatenating them; the verification's shortfall message reports the real budget instead of clamping it | WSL | A 1-, 2- and 3-line region all succeed; one tall box and one box per line produce identical `verified_text`; a single-line box gains no separator |

**The defect.** A single `box` covering one text line worked; the same box extended to touch a
second always failed, deleted its output, and reported:

```
page 1: 'TYAGI1703' still appears 0 time(s) … (at most 0 expected after redaction) — PyMuPDF
```

`TYAGI1703` is the tail of `UMESH TYAGI` welded to the head of `1703 PORCELLANO WAY`. The threshold
was exactly one line, and the case it broke is the one the tool's own docs recommend region
redaction for: *"a signature block, a letterhead, a photo, a table cell."*

**The cause is one `join`.** `text_under` answered with `"".join(...)` over every character whose
centre fell in the box — correct within a line, and a fabrication across two, because the end of one
line is not adjacent to the start of the next. `_tokens_under` then split that on whitespace and
produced a token the document never contained, so the budget was
`occurrences_in_source(0) − boxes_covering_it(1) = −1`, and `found(0) > −1` failed a check no output
could ever pass.

**Two things the report got wrong, both worth recording because each points at a different fix.**

* It read the message as self-contradictory — *"reports `0 ≤ 0`, which is satisfied, and fails
  anyway"* — and concluded that **either** fix alone would resolve the symptom. The comparison was
  never `0 ≤ 0`: the budget was −1 and the arithmetic was right. The message printed
  `max(allowed, 0)`, which renders an impossible budget as a satisfied one. Fixing only the message
  would print *"at most −1 expected"* and still fail. It is fixed here anyway, because a message
  that hides the real fault one layer down is what cost the reporter the time.
* Its "cheapest correct framing" — *treat a single `box` as `boxes: [box]` internally* — is a
  **no-op**. `_apply` already processes one box at a time; the plural form works because each
  rectangle happens to be a single line, not because it is plural. Routing a tall box through it
  produces the identical welded token.

**Blast radius, checked rather than assumed:** every other `text_under` caller passes a single-line
box. The annotations panel reads one rect per line bar, and `matches_case` / `group_matches` only
ever see `search_for` rectangles, which MuPDF already returns one per line. So this was
`redact_regions` alone, and the app is unaffected — unlike M96, which was shared with the find bar.

**Part 2, considered and declined: no `elsewhere_in_document` warning.** The report proposed that a
region redaction also scan the rest of the document for the strings it removed, as the exact analogue
of M95's `residual_literal`. The analogy does not hold, and the difference is the one that decides
whether a warning earns its place:

* `residual_literal` and the `invisible` flag disclose things the caller **cannot** discover — a
  matcher's blind spot is invisible by construction, and white-on-white text appears in no render.
* This would disclose something **one obvious call away**. `verified_text` already reports every
  string that came out of the boxes, so a caller who has just been told the box contained `UMESH`
  can run `search "UMESH"` themselves.

It would also be noisy in a way the others are not: a region over a table cell removes `CA` or `1`,
and scanning the document for those warns on nearly every call — which is how a warning stops being
read. And the contract is deliberate: `redact_text` removes what you *named* and proves coverage;
`redact_regions` removes what is *there* and proves those boxes are empty. Collapsing that
distinction makes two tools into one blurred one.

What ships instead is a sentence in the tool doc pointing at `verified_text` — it lists what actually
came out, often more than you aimed at — and telling a caller removing PII rather than blanking an
area to search those strings or use `redact_text`. Zero runtime cost, no noise, same gap closed.
**Revisit if a real session shows an agent doing visual region redaction on PII and missing
occurrences it had no reason to look for**; the harness that produced TC-001–TC-005 is exactly what
would surface that, and the feature stays cheap to add.

### M98 *(unplanned)* — the two silent failures redaction had no counterweight for (TC-007, 2026-08-16)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M98** `redact_text` reports separator-variant spellings it left behind, and over-redaction by a split query | `_variant_residuals` scans the output for the query's alphanumerics ignoring separators and reports each surviving **spelling**; `_term_report` breaks the count down per term and compares it against what the phrase alone would have matched | WSL | The unspaced policy number is named; a line-wrapped identifier is named; a degenerate query scans nothing; a deliberate word list is not warned about |

TC-007 found **no defects** — the delivery was correct with zero residuals. It found two failure
modes the tool is silent about, and both are silent in the direction that matters.

**Variants: the same value written two ways.** `607347469 203 1` and `6073474692031` are one policy
number; a literal scan sees neither in the other, so redacting one form reported the file clean
while the other was still in it. Dropping every non-alphanumeric character collapses the whole
family at once — separator *substitution* and separator *removal* normalise identically, so
`08-24-1970` / `08/24/1970` and `999 99 9999` / `999-99-9999` come along for free.

**Reported, never matched.** Whitespace-insensitive *matching* in a destructive tool would be
dangerous (`12345` would begin matching across table columns) and the judgement that two spellings
denote one value is document semantics the caller owns. So the scan runs after the write, on the
output, and its whole output is a sentence. This is the same move as `residual_literal` (M95): change
no matching behaviour, report what the matcher cannot see.

**Both guards were set by measurement, not taste** — 49 documents, 270 identifier-shaped queries
drawn from the documents themselves:

| | |
|---|---|
| queries that would warn | **12 of 220** (5%) — not the every-call noise the report feared |
| extra hits reported | **41**, and on inspection **every one a real variant** |
| false positives before the query floor | all of them from degenerate queries — `000000` matching across `708.000 0.00`, digits welded from two unrelated numbers |
| effect of the boundary rule | 9 of 53 candidate hits suppressed |

Two design corrections came out of that measurement:

* **The boundary test must read the *source*, not the normalised stream.** TC-007 proposed requiring
  that a match "not sit inside a longer alphanumeric run", which is vacuous once applied to the
  normalised form: stripping separators makes the whole stream alphanumeric, so *every* interior
  match is inside a longer run. It has to be judged against the original text, which is why
  :func:`_normalise` returns an offset map alongside the stripped string.
* **A floor on the query is load-bearing**, and it is what makes the precision above hold: seven
  normalised characters and three distinct ones. Every false positive in the corpus was a query
  below it.

The measurement also turned up a leak class **nobody had identified** — neither the report nor the
plan for it. An identifier broken by a line wrap (`526-\n5999`) is invisible to any literal check,
because the newline is a character the query does not contain. The variant scan sees it for free.

**Over-redaction, the failure with no check at all.** The default word-list mode split
`607347469 203 1` into three terms, one of them `1`, and destroyed every standalone digit in a
22-page document — reporting 240 boxes redacted, zero residuals, cross-engine verified, and nothing
else. The asymmetry is structural rather than an oversight: a missed occurrence survives in the
output and can be searched for, so it is checkable after the fact, while destroyed content leaves no
trace in the output at all. The only record it was ever there is the input, which this tool never
modifies — so the moment of the write is the only moment the warning can be given.

**The signal is a comparison, not a share.** "One term dominates" is a bad test: an ordinary
two-word query whose second word is simply commoner reaches any share threshold with nothing wrong.
Asking instead *how much more did this remove than the phrase you appear to have typed* answers the
real question and stays quiet in the two cases that must not warn — a query whose phrase never
occurs is a deliberate word list, and one whose phrase accounts for most of the hits is behaving as
expected. TC-007 removed 240 against a phrase occurring 9 times.

**M98.1 — the floor was blunter than the risk it was guarding against** (TC-007 retest, same day).
The retest confirmed both findings implemented and found that the variant scan silently skipped
three obviously structured identifiers: `999 99 9999` and `4444 5555` for repeating a character, and
`AB 12 CD` for normalising to six. It filed this as a bug of unknown mechanism, having ruled the
guards out — reasonably, but wrongly: `1111 2222 3333` was read as disproof of the entropy floor
while sitting exactly *on* it (3 distinct), and `AB 12 CD` fails the *length* floor rather than the
entropy one, so two guards were being tested as if they were one. Every one of the eleven reported
cases is predicted by the two thresholds.

So the mechanism was working as designed and the design was wrong, which is the more useful finding.
**Separators are the caller declaring the value structured.** `999 99 9999` has already said what it
is; `000000` could be anything and still has to earn the scan by being long and varied. The floor
now applies only to unpunctuated queries. Re-measured over the same 49 documents: scanning **36 more
queries produced exactly the same 41 hits**, so the relaxation costs no precision — the original
probe could not have caught this, because it generated candidates with a digit-run regex and so
never asked what a *person* would type.

**And absence is no longer an answer.** `residual_normalized` is always present: `[]` means the scan
ran and found nothing, `null` means it did not run, and the `null` carries a warning saying why. The
retest's own argument is the right one — a feature whose purpose is closing an *invisible* failure
must not go quiet in a way that reads as reassurance, and a caller who sees a variant reported for
one identifier and silence for the next will reasonably conclude the second is clean.

**Not built: multi-query, and region clip.** Both are wanted and neither is a silent failure — see
`PROGRESS.md` §Open follow-ups, including the interaction that makes multi-query less thin than it
looks: overlapping terms produce overlapping boxes, and `_apply` counts box-hits per token, so two
boxes covering one token drive the budget negative and trip M97's impossible-budget path.

### M102 *(unplanned)* — the safety net crashed instead of firing (2026-08-17)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M102** The coverage-gap leak report is built from a hit's `boxes`, so it raises `RedactionLeak` rather than `KeyError` | One key in `_no_residual_match`'s message | WSL | Pass 1 finding a residual match raises `RedactionLeak` and the message carries the coordinates; both tests fail on the old key |

**One character wide and squarely on the invariant.** `_no_residual_match` pass 1 is the check that
catches a *matching* bug — an occurrence the matcher never boxed, which is the failure mode with
teeth and the shape of TC-001. Building its message read `hit['box']`; a hit has carried **`boxes`**,
one rectangle per line, since [#250](https://github.com/utyagi24/klarpdf/pull/250). So the line
raised `KeyError` **before** it could raise `RedactionLeak`.

**The consequence is not a bad error message.** `_finish` catches `RedactionLeak` and nothing else:

```python
except RedactionLeak:
    if os.path.exists(target):
        os.remove(target)   # never leave a false-secure file behind
    raise
```

A `KeyError` walks straight past that `except`, so the output of a redaction that had **just failed
verification** stayed on disk, and the caller got an exception naming a missing dict key rather than
a leak. The invariant this module is built around was broken by its own error handler, on the exact
path that exists for the most dangerous failure it can have.

**Why no test caught it.** Every redaction test drives a redaction that *works*, and pass 1 is
silent on those — the leak branch only runs when the matcher has already gone wrong, which no
fixture arranged. The regression tests call `_no_residual_match` against an **un-redacted** file so
pass 1 genuinely finds something, rather than monkeypatching the function under test. Both assert
the exception **type**, because the type is what deletes the file.

Found while reading this function for §M100, not by a report. It is the same class as M93's
`insert_pdf` catalog loss: an interface changed under a call site that still parsed, and the failure
lives in a branch nothing exercises.

### M103 *(unplanned)* — what the reply says about what it never looked at (TC-007/008, 2026-08-18)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M103** Five reporting defects from the multi-query retest: a check that ran on nothing reporting clean, a warning repeated until it buried the one that mattered, and two counts nobody had to reconcile before | `_covered_tokens`, `_no_residual_match`, `_query_reports`, tool docs | WSL | A 1-char redaction is verified and *can fail*; ≥4 misses collapse to one warning while 2 stay named; `residual_scope` appears and an unscoped call is unchanged; a `pages` miss blames the page filter, an unscoped one blames spelling |

Five findings, none a leak, all the same family — **the tool reporting a clean-looking result about
something it never examined**. That is M98's principle (`[]` and `null` must not be spelled the same
way) applied to four more places. Two were introduced by M100; three predate it.

**B — a single-character redaction was verified by nothing.** `_covered_tokens` dropped tokens
shorter than two characters, from M41 onward, with no design note and no test — the only trace of
intent is the phrase "tokens worth checking for individually". Since that dict *is* what `_verify`
checks, redacting `1` returned `verified_text: {}` beside `boxes_redacted: 2` in one reply, and the
box-level cross-engine check ran **zero assertions**. Not a leak (`_no_residual_match` still runs),
but the strongest check silently no-opped. On the TC-007 over-redaction path 216 of 240 boxes came
from the term `1`, and the field that exists to say "here is what I deleted" never mentioned it.
Both defences of the filter were tested and neither held: the *noise* theory fails because
`PageText` answers by character centre, tight enough that a box over `Smith` yields exactly `Smith`;
the *vacuous check* theory fails because on `Item 1 and item 1 again, ref 2031` the budget is
`before 3 − covered 2 = 1`, which requires the `1` inside `2031` to survive. Removed. This is the one
change here that touches the destructive path's arithmetic — the code that produced M97's bug and
needed M98.1 within hours — so it lands with a test that the budget can still **fail**, driven
through `_verify` directly. Monkeypatching `_count` was tried first and proves nothing: it inflates
the before and the after equally and the assertion passes exactly as it did.

**A — the warning that buried the warning.** 60 queries with 59 misses produced 59 near-identical
~330-character warnings. The cost is not the ~20 KB, it is that a genuine over-redaction warning
among them would have been line 37 of 59. M100 multiplied a once-per-call message by N. Misses now
aggregate above three, using the `(+N more)` idiom `residual_literal` already established. Only this
class aggregates: the literal and variant warnings carry per-query *content* (the actual surviving
tokens), so they are informative rather than repetitive.

**D — the advisory scans never said which pages they read.** With `pages=[1,3]`,
`residual_literal: 0` and `residual_normalized: []` came back about a document the scans had read
two pages of, while their documented contract is "the scan ran and found nothing".
**The scoping itself is correct and stays** — the owner's rule, settled 2026-08-18, is that a reply
never mixes page-scoped and document-wide results, so widening the two advisory scans was rejected
even though it costs only ~0.6 ms/page. What was missing was the disclosure: `residual_scope` now
names the pages read, with a warning when `pages` narrowed them. `pages_redacted` was not a
substitute — it lists where boxes *landed*, a strictly smaller set (`[1]` for a call that scanned
`[1, 2, 3]`). Worth recording that the severity shrank twice under examination: these fields cannot
be misread as *success*, because success is signalled by returning at all rather than by any field,
so the real defect is an under-informative advisory rather than a false all-clear.

**E — the zero-match warning blamed the caller's spelling** ("it may be spelled in a way this mode
cannot see") when the cause was the caller's own `pages`, which the response already knew about. It
now leads with the page restriction when there is one.

**C — `matches` and `boxes_redacted` diverge** (468 against 240): the first sums per-query hits and
double-counts text two queries both matched, the second counts distinct rectangles. Both right, and
always equal before M100, so the difference is documented rather than changed.

## Planned next — MCP capability milestones (M99–M101, scheduled 2026-08-16)

Three milestones the hands-on sessions asked for, written up so a later session can pick any of them
up cold. None is a defect: TC-001 to TC-007 found the bridge *correct* on these paths and wanting
more of them. They are ordered by cost-to-value, not by the order they were requested. **M99 and
M100 shipped; M101 was re-scoped on 2026-08-20** — see its own section for what changed and why.

### M99 — a region clip on `render_page` and `export_images`

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M99** Both imaging tools take an optional `clip` box, in the page-point space everything else uses | `queries.render_page` passes `clip` to `get_pixmap`; `model/export.py:export_page_images` threads one through to the same call | WSL | A clip returns only that region at the right pixel size; an out-of-page or inverted clip is refused; omitting it is byte-identical to today |

**The gap.** Neither tool takes a region, so "extract this ID card as a PNG" cannot be finished inside
the server — TC-007 exported a whole page at 200 dpi and cropped it outside. Asked for twice
(TC-007, and TC-003-old before it).

**Why it is worth more than the request it came from.** Boxes are already this server's native
currency: `search` hands them back and `redact_regions` consumes them, so region→image is the same
"I know *where*" workflow the bridge already serves, minus the destruction. The composition is the
real prize — `search` → `render_page(clip=hit box)` lets an agent show a person **the actual pixels**
of what it is about to delete. Every safety mechanism built across M95–M98 is a variation on
*preview before you destroy*, and all of them are textual; this is the one that makes the preview
visual, on a tool whose docs already say **"Always run `search` before `redact_text`."**

**Cost is one keyword argument at each of the two call sites** (`get_pixmap(clip=fitz.Rect(...))`),
plus validation and docs. And unlike most of this bridge's work it **cannot fail silently**: a wrong
clip produces a visibly wrong image. That is rare enough here to be worth saying out loud.

Take the `dpi` interaction seriously in the tests: the returned pixel size must follow the clip, not
the page, or a caller sizing an image from `width_px` gets it wrong.

**Built as scheduled, with three things the plan above got wrong** *(2026-08-17)*:

1. **"A wrong clip produces a visibly wrong image" is true of `export_images` and false of
   `render_page`** — and the difference decided the validation rule. `render_page` returns an MCP
   *image block*, not JSON, so its reply has **nowhere to carry a note**. PyMuPDF intersects an
   overhanging clip with the page and returns a smaller pixmap; the caller, having sized a layout
   from the clip it asked for, would get different pixels with nothing saying so. So the refusal is
   not a taste for strictness — the error message is the only channel the tool has, and it names the
   page rect so a caller can correct in one step. `export_images` returns JSON and *could* have
   reported an adjustment, but two imaging tools disagreeing about what a clip means is worse than
   one strict rule.
2. **A `search` hit has `boxes`, not a `box`** — one per line since
   [#250](https://github.com/utyagi24/klarpdf/pull/250), so the composition this
   milestone is named for does not typecheck as written above. `clip` stays a **single rectangle**
   and the caller unions a wrapped hit's boxes. Keeping the list, as `redact_regions` does, was
   considered and rejected: the union of two lines' boxes covers whatever sits between them, which
   is *helpful* when looking and is *data loss* when deleting. The two tools taking different shapes
   is the honest encoding of that.
3. **`export_images` needs per-page validation, which "one keyword argument at each of the two call
   sites" does not cover.** Page sizes vary within a document, so a region that sits comfortably on
   page 1 can overhang page 3; validating once would export that page silently short. It is checked
   for the whole set **before the first file is written**, so a clip that fails on page 7 of 10 does
   not leave six files behind for the caller to clean up after handling the error.

**M99.1 — the clip was on the wrong side of the rotation** *(TC-008 Finding 3, 2026-08-18)*. The
correction above chose to validate against "the *rendered* page rather than the stored one, because
that is the rect `get_pixmap` will clip against once rotation has been applied". Internally
consistent, and the wrong space: `search_for` reports **unrotated** coordinates — byte-identical at
`/Rotate 0` and `/Rotate 90` — and `redact_regions` consumes them there, while `page.rect` is the
*displayed* rect. So `clip` sat on the opposite side of the rotation from every box a caller has,
and the documented promise ("pass a `search` hit straight back") was false on any rotated page.

It failed two ways, neither safe. A `search` box fits inside the displayed rect, so it **rendered
blank with no error** — measured 671 dark pixels unrotated against **0** at `/Rotate 90`. And a box
beyond the displayed width was **refused as off-page** although `search` had returned it for that
same page one call earlier. The destructive tool was correct throughout, which is what makes it
worse: on a turned page `redact_regions` deleted the right region while `clip` previewed the wrong
one — and `clip`'s stated purpose is to show a person what is about to be deleted. Nothing was
destroyed wrongly; the human check meant to catch a mistake was silently disabled.

The fix is to read `clip` in the space the caller's numbers are actually in: bounds-check against
`page.rect * page.derotation_matrix`, then map the result through `page.rotation_matrix` for the
rasteriser. Both are the identity when the page is not turned, so unrotated behaviour is untouched
(the rotation-0 case of the new parametrised test passes on the old code; the other three do not).
Two details fall out. The reply echoes the caller's **own** quadruple rather than the mapped one,
since telling them their clip had been changed is its own lie. And the refusal names the unrotated
rect plus the rotation, because a caller told their box is outside `[0, 0, 792, 612]` while the
viewer shows 612×792 otherwise has no way to tell which of the two they are being measured against.

**M104 — the naming was at odds with the feature** *(TC-008 Findings 1 and 2, 2026-08-18)*.
`export_images` wrote `<stem>.png` for a single page and `<stem>-3.png` only when there were
several. Non-uniform, and — the reason it mattered — two *clips* of one page therefore wanted the
same filename, so the second call hit the no-clobber refusal. Cutting several regions out of one
page is the use `clip` exists for, which made this a naming scheme arguing with its own feature; the
refusal itself was correct and clear, so nothing was destroyed. The workarounds (a directory per
region, or `overwrite: true`, which eats the first card) were both worse than a rename.

Every file now carries its page number, and a new `name` chooses the stem — `name="card_front"` →
`card_front-3.png`. The stem is the caller's because only the caller knows what a region *is*; the
server never can. Two consequences worth stating. `name` is a filename **component**, so
`_safe_stem` refuses separators, `..` and an extension rather than sanitising them: joined onto
`out_dir` unchecked it would walk out of the directory and around `--allow-root`, and quietly
rewriting it would hand back a file under a different name than was asked for. And `number_all`
defaults **off** in `model/export.py`, because that function is shared with the app's Export ▸
Images, where the filename comes from a save dialog — turning the user's `report.png` into
`report-1.png` behind them would be its own small betrayal. Only the bridge passes it.

Finding 2 needed no code. The clipped pixel size is `ceil(x1·dpi/72) − floor(x0·dpi/72)`, expanded
outward to whole device pixels, so a 100 pt square at 150 dpi is **209 px** rather than the 208.33
the naive formula gives. That is the right policy — no partial pixel of the requested region is
dropped — but it was unstated, so it is now documented and pinned by a test, since an undocumented
rounding rule is one a later change can break silently.

`resolve_clip` therefore lives in `model/export.py` beside the rasterisation it constrains, not in
the bridge — the app's own Export shares the path, and two validators with two answers is the trap
`_word_bounded` already documents. Tolerance is 0.01 pt, sub-pixel at any dpi either tool renders,
so a computed box landing a ten-thousandth past the page edge is not an error while a real overhang
still is.

### M100 — one redaction call, several queries

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M100** `redact_text` accepts `queries: [...]` and removes all of them in one verified pass | Coalesce overlapping boxes **before** `_apply`, then the API on top; per-query `matches` in the result | WSL | Overlapping terms (`607347469 203 1` + `607347469`) verify cleanly; one call equals the chained calls byte-for-byte in what it removes; a single `query` behaves exactly as today |

**The argument is data hygiene, not ergonomics.** TC-007 needed four chained calls for six
identifiers, which left **three intermediate files, each a partially-redacted copy holding live
PII**, all of which had to be remembered and deleted. That sprawl is caused by our own design — every
write demands a fresh `out`, so chaining necessarily strews copies — which makes it ours to fix
rather than the caller's to remember. It also removes an ordering hazard the same session hit: terms
must currently be removed longest-first, or the shorter query leaves ` 203 1` fragments behind.

**Do the arithmetic first; the API is the easy half.** `_apply` counts *box-hits* per token, so two
overlapping terms give `covered = 2` against `before = 1`, the budget goes to −1, and the call trips
the impossible-budget path M97 added. Overlap is not an edge case here — it is the motivating example
— so **rejecting overlapping terms would fail the very use this exists for**. Coalescing the boxes
(union any that intersect, before `_apply` reads tokens under them) is the contained fix and is where
this milestone should start.

**Treat that code as the sharp edge it is.** The coverage arithmetic is what every safety guarantee in
the bridge rests on, and it is also where the last two defects were: M97 *introduced* the
impossible-budget path, and M98 needed M98.1 within hours. Any change here wants the same
measure-first discipline the variant scan got, and a test that a multi-query call and the equivalent
chain produce identical output.

**Not urgent.** TC-007's own ranking put it second and called multi-query without variant reporting
*"a faster way to be confidently wrong"* — M98 shipped the variant reporting, so the thing that made
deferring it risky is already handled.

**Built 2026-08-17. The diagnosis above was right; the prescribed fix was wrong.**

The measurement first, because it decided everything else. A probe over the TC-007 shape
(`607347469 203 1` on one line, `607347469` on the next) reproduced the failure exactly as
predicted — `covered=3` against `before=2`, budget `-1`, M97's impossible-budget path — and then
showed **why**, which the plan had not asked. The double count is **textual, not geometric**: three
*boxes* produced three counts of one token because `_tokens_under` was called per box and the same
characters sat under two of them. Overlapping rectangles were never the problem; overlapping
*characters* were.

That distinction inverts the prescription. **Coalescing the boxes was the wrong fix**, and would
have been a bad one: `fitz.Rect` unions to a *bounding* rect, so two boxes overlapping across a line
break — the wrapped-identifier case M97 exists for — would union into a block covering everything
between them and **delete text neither query matched**. Silently, because destroyed content leaves
no trace in the output; the exact failure M98's `query_terms` was built to counterweight. The fix
instead is to count each character once (`PageText.text_under_all`) and leave every rectangle alone:

* **No boxes are merged**, so no redaction is widened by a milestone about counting.
* It repairs `redact_regions` for free — a caller passing overlapping rectangles hit the same wall.
* It corrects a **pre-existing** mispairing nobody had noticed: `fitz_before` counts *occurrences*
  (`str.count`), while `_tokens_under` returned a `set` and so counted *distinct tokens per box*.
  One box over `203 1 203` claimed a single removal against a before of two, and the surviving copy
  was permitted by the budget. Occurrences on both sides is the arithmetic `_verify` documents.

**One rule generalised, one trap re-sprung.** Concatenating a line's covered characters recreated
M97's `TYAGI1703` bug *within* a line: redacting `Smith` and `Jones` from one line produced the
token `SmithJones`, which the source contains zero times, so the budget went negative and a correct
redaction deleted its own output — the identical failure by the identical mechanism, one axis over.
Caught by M98's existing tests, not by a new one. A covered run therefore ends wherever an uncovered
character interrupts it, on either axis.

**The API half, as the plan said, was the easy one**, with two decisions it did not anticipate:

1. **The reply shape follows which parameter was used, not how many queries survived.** `query`
   returns exactly today's flat shape; `queries` always returns the list form, including for one
   element and for a list whose duplicates collapsed to one. Branching on the *count* — which the
   first implementation did, and a test caught — hands a caller iterating a variable-length list a
   different shape on the days its list has one element in it.
2. **A query matching nothing warns rather than failing the call.** The single-query rule ("a
   redaction reporting success over a file it did not change is how a secret ships") does not
   survive translation: failing the whole call would delete a verified output that correctly removed
   the other five. The rule becomes *none* of them matched, and the individual miss is a
   `matches: 0` plus a warning naming it.

**Ordering is now provably irrelevant**, which was the second half of the argument for this
milestone: every box is computed against the intact source before anything is applied, so the
shortest-first and longest-first orders produce byte-identical output — asserted, alongside the
chained equivalent, which still fails shortest-first.

**Cost, measured rather than assumed.** Counting over the union initially dropped `_band`'s
narrowing and tested every character against every box: **4.5x slower** on the hot path (90 boxes
over a 540-word page — 16.0 ms against 3.5 ms), which a 22-page TC-007-scale redaction pays once per
page. Applying the same band filter per line, read the other way round — only boxes whose vertical
extent meets this line can hold any of its characters — brings it to **1.07x**, inside noise, and
the whole 1 980-box redaction from 1.96 s to 1.64 s. Worth recording because the check is cheap and
the previous performance claim on this file was wrong in the other direction (§M98: a "regression"
that turned out to be my own probes loading the machine).

### M101 — annotation as a capability: marking up a document from the bridge ⭐

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M101** The bridge can *write* highlights / underlines / strike-throughs, each carrying a note, and *read back* every annotation a document holds | New `mcp_bridge/annotations.py` over `model/page_edits.py`; two tools — `annotate`, `get_annotations` | WSL | A written highlight reopens in the app as an editable mark, with its note in the note editor and its badge on the page; a foreign (Acrobat / Edge) annotation is read back too, and says it is not ours; a second `annotate` over the same span merges instead of stacking; `get_annotations` boxes go into `redact_regions` unchanged |

**Re-scoped 2026-08-20, on the owner's reading of the milestone below.** The first draft of M101
described annotation entirely as the front half of a redaction workflow, and that was wrong twice
over. It was wrong about *what the feature is* — marking up a document is a thing people want done,
full stop, and a milestone that only justifies it as a redaction pre-step gets built with no verify
criterion for the ordinary case. And it was wrong about *how much of it belongs in the bridge*: it
proposed three tools where two do the job. What follows is the corrected scope; the rejection at the
end records the third tool and why it is not built, so a later session does not re-derive it.

**What the tools are for.** `annotate` marks passages the caller has already located — "underline
every clause that mentions termination", "highlight these figures so I can check them", "strike the
paragraphs we agreed to drop, and note why on each one". `get_annotations` reads a document's marks
back, whoever wrote them, so an agent can summarise a colleague's review comments or check its own
work. Both stand on their own; neither mentions redaction.

**The bridge does mechanics; the caller does semantics** (owner, 2026-08-20 — the decision that
shaped everything else here). `annotate` takes **boxes, not queries**. It does not find PII, it does
not decide what a termination clause is, and it does not search: the caller locates what it cares
about with `search` / `extract_text`, and hands over coordinates. This is the same seam M98 drew
when it made the variant scan *report* rather than *match* — "whether two spellings denote one
value is the caller's fact" — and M100's multi-query is deliberately **not** mirrored here. It also
keeps the tool small, which the M105 description budget rewards.

**Almost all of the model work is already done**, which is what makes this a milestone rather than a
project:

* `Highlight`, `Underline`, `Strikeout` already carry `rects`, an RGB `color` and a `note` (their
  PDF `/Contents`, M81), and `apply_annotations` bakes them tagged with `KLARPDF_AUTHOR`.
* `read_klarpdf_annotations` reads *our* marks back into editable descriptors (M31), and
  `parse_annotation` was deliberately split out so a **foreign** annotation parses identically
  (M68's adopt-on-edit).
* `VirtualDocument.from_path` seeds every page from `read_klarpdf_annotations`, so although
  materialise strips our marks from the output before re-applying them (`edit_engine`), a *later*
  write tool re-applies them from descriptors. A bridge-written highlight therefore survives a
  subsequent `fill_form`, `flatten` or `delete_pages` rather than being silently stripped. Worth a
  regression test, since nothing states it today.

**Six things to get right:**

1. **One tool with a `type`, not three.** `annotate(path, marks, out)` where a mark is
   `{type, page, boxes, color?, note?}` and `type` is `highlight` / `underline` / `strikeout`. Three
   near-identical descriptions would spend the M105 budget three times over for no gain. The other
   descriptor types the model supports — ink, line, shape, text box — are **out of scope and the
   description must say so**, or the model will try them and get a validation error instead of an
   answer.
2. **A list of marks per call, because a call is a file.** Every write goes to a fresh `out`
   (`_resolve_out` refuses `out == path` whatever `overwrite` says), so highlight-then-underline-
   then-note as three calls is three files — exactly the intermediate sprawl M100 exists to stop.
   One call takes every mark for the document.
3. **A repeat call merges; it does not stack.** Route through `merge_markup` (the same function
   `MainWindow` uses), so a bridge-written mark and an app-drawn one are indistinguishable: same
   type and colour over the same span absorb into one, a different colour takes over the span, and
   M81.2 carries the notes onto the survivor. Without this an agent that retries leaves two
   highlights where the reader sees one, and `get_annotations` reports both.
4. **`get_annotations` must not be built on `parse_annotation` alone.** That parser returns `None`
   for every type the model cannot draw — **sticky notes included** (§M83: "an Edge sticky note is
   an unmodeled type"). A colleague who left their comments as sticky notes rather than
   notes-on-highlights would be *invisible* to the agent, which is the one failure this tool cannot
   have. Read the raw annotations — type, page, rect, colour, `/Contents`, author — and flag
   `editable: true/false` from whether `parse_annotation` models it, because only ours round-trip.
5. **Boxes in, boxes out, in the same space** — unrotated page points, which is what `search`,
   `redact_regions` and (since M99.1) `clip` all use. `get_annotations` must report each mark in
   `redact_regions`' own region shape (`page` + `boxes`) so its output composes with the write tools
   *without reshaping*, and its docs must name the space. (`get_info` was the one dissenter here;
   M107.1 settled it by reporting each page's `rotation` and grouping on it.)
6. **Colour: RGB always, names from the app's own palette.** Accept a raw `[r, g, b]`, and accept
   a name — but only a name in that mark type's palette (`viewer/markup_style.py`: highlights are
   Yellow / Green / Blue / Pink / **Orange**; lines are Red / Blue / Green / Black — there is no
   orange line and no red highlight), erroring on anything else per M106's rule that an unrecognised
   input is an error rather than a silent default. The point is not tidiness: colour is how a person
   sorts marks, and if the bridge's orange is not *byte-identical* to the picker's orange, the same
   colour splits into two values the moment a human re-marks a passage in the app. On the **read**
   side report the raw RGB **and** a nearest palette name within a tolerance, since a mark made in
   Acrobat will not carry a palette value at all.

**Notes behave exactly as the app's do** (owner, 2026-08-20). A note is not an object: it is the
`/Contents` of its host mark (M81), which is why it needs no tool of its own and why a bridge-written
note opens in the M90 note editor, draws the M90.2 badge, and survives save → reopen → save. Two
consequences the implementation must honour — a `note` with no `type` creates a **Highlight** to
carry it, following `resolve_note_host`'s rule for a note dropped on unmarked text; and clearing a
note leaves its mark standing.

**Deliberately out of scope: editing an annotation that already exists** — attaching a note to a
mark already in the file, recolouring one, or deleting one. For our own marks this is easy; for a
*foreign* one it is M68's adopt-on-edit (strip the original, re-add as ours, with the degradation
warning), which is a different milestone's worth of care. Writing marks and reading them back is the
capability; mutating someone else's is not part of it.

**Considered and rejected: a third tool, `redact_annotated`** (owner, 2026-08-20). The first draft
proposed it for the workflow it was written around — *"highlight all PII in this document"* → a
person reviews in KlarPDF → *"redact everything highlighted in orange"*. That workflow is still the
one this milestone serves best, and the shape of it is still right: the agent **proposes** in a form
that deletes nothing, a human **disposes** in the app's own Annotations sidebar (M79) — a better
review surface than any tool response can be — and the agent then **executes** only what was
approved, with the colour as the reviewer's verdict channel. What was wrong is the assumption that
the last step needs a tool. **The caller composes it**: `get_annotations` → filter on colour →
`redact_regions`.

*Nothing is lost by that.* The draft's argument for the tool was that it would inherit
`verified_text`, the cross-engine check and the delete-the-output-on-failure rule "for free" —
but the composition inherits all three **identically**, because it *is* `redact_regions`. The tool
would have added colour filtering, which is a few lines in the caller and better placed there:
`redact_annotated` "must never decide that orange means delete, only that the caller said so", and
the surest way to keep that promise is not to have a tool that could break it. Two further costs it
would have carried on its own: a narrower promise than its name implies (no query, so no
`residual_literal` and no coverage claim beyond the marks themselves — the M97 Part 2 asymmetry),
and a fourth tool in a list the model must choose from, which is the same objection the draft
already raised against editing annotations. Point 5 above is what pays for the rejection: as long as
`get_annotations` hands back boxes in `redact_regions`' own shape and space, the composition is a
filter and nothing more.

**Built as scoped** *(2026-08-20)*, `mcp_bridge/annotations.py` + `model/markup_palette.py`, 36 new
tests. Four things the build settled that the design above could only assert:

1. **The palette had to move, and that is the interesting part.** Point 6 wanted the bridge and the
   picker to share one definition of "Orange". They could not: the swatches lived in
   `viewer/markup_style.py`, which imports Qt on line 41, and `tests/test_mcp_no_qt.py` asserts in a
   fresh interpreter that the bridge imports none. The choice was to duplicate the tuples or lift
   them, and duplication would have been a correctness bug on a slow fuse — colour is the review
   loop's verdict channel, so two copies agree until someone edits one, and then "orange" names two
   RGB values and a filter returns half the document's orange marks. They now live in
   `model/markup_palette.py`; `viewer/markup_style.py` re-exports both names, so no caller changed.
   The invariant is structural rather than tested-for.
2. **The rotation question answered itself, and is now pinned.** Point 5 required annotation boxes
   to be in the same unrotated space `redact_regions` consumes. Measured across `/Rotate` 0 / 90 /
   180 / 270: PyMuPDF stores *and reports* annotation geometry unrotated at every one of them, and
   so does `search_for`. The hand-off therefore needs **no arithmetic at all** — which is a happy
   accident of the library rather than anything this code does, so it is a parametrised test rather
   than a comment (M99.1 was this exact class of bug on `clip`).
3. **`/Rect` is not the mark.** Text markup stores quad points per line and a `/Rect` padded ~5 pt
   wider on every side — measured. Reporting the rect would have handed callers boxes visibly too
   big and, fed to `redact_regions`, would have deleted a strip of whatever sat alongside every
   highlight. `get_annotations` reads quads for the markup types and falls back to the rect only for
   types that have none.
4. **`merge_markup` builds its survivor with an empty note**, since it only ever inherits from marks
   it absorbed — so a note passed *in the same call* needed applying afterwards, to the mark the
   merge had just produced, and identity cannot find it (the survivor is a new frozen object at a
   slot the merge chose). `_attach_note` locates it by geometry and **joins** rather than replaces,
   which is M81's rule that only deleting a mark may delete its note.

**Two traps worth recording for the next session**, both PyMuPDF lifetime rules that surface as a
*segfault* rather than an exception, and both hit while writing the tests rather than the code:
`next(doc[0].annots()).rect` frees the page and the generator before the annotation is read, and
`doc[0].add_highlight_annot(…).update()` frees the page before the annot is used ("annotation not
bound to any page" when it is merciful, a crash when it is not). Hold the page in a local.

### M105 *(unplanned)* — the tool descriptions are truncated in transit (TC-007, 2026-08-18)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M105** Restructure the oversized tool descriptions so the safety-critical half arrives, publish the reference half where it cannot be cut, and pin a ceiling so they cannot grow past it again | `mcp_bridge/server.py` docstrings + `mcp_bridge/docs.py` behind `klarpdf://docs/{tool}`; `tests/test_mcp_docs.py` enumerating the live server | WSL | Every description is under the budget; `redact_text` still names what it destroys, what `whole_words` *means*, and that the reply must be read; the field catalogue is readable from the resource; the test fails if any *future* tool grows past the budget |

**The finding.** The testing agent reported it could not see the Finding-C documentation "even
though the MCP was reinstalled", and that `redact_text`'s description was **truncated in its tool
listing, ending mid-sentence in the `whole_words` bullet list**. Three checks ruled out a stale
install: the serving process runs from a checkout on the same commit and *does* contain the text;
`config.py` caps text, search hits and image bytes but **not** descriptions, so the server sends all
6,573 characters; and cutting the description at **2048** reproduces the reported symptom to the
character, because the two `whole_words` bullets sit at offsets 1844 and 2040 and the cut lands
between them. **The client truncates at ~2 KB.**

**What that costs.** 69% of `redact_text`'s description never reaches the agent, and it is the wrong
69% — everything after offset 2048, which is nearly all of the last three rounds' work:

| Content | Offset | Delivered |
| --- | --- | --- |
| what it destroys; `whole_words` semantics | < 2048 | yes |
| the `queries: [...]` contract (M100) | > 2048 | **no** |
| the residual-field catalogue, `invisible_matches` | > 2048 | **no** |
| `matches` vs `boxes_redacted` (M103/C) | 4965 | **no** |
| `residual_scope` (M103/D) | 5436 | **no** |

Only `redact_text` (6,573) and `search` (2,241, losing 9%) exceed the cap; the other fifteen tools
are unaffected. This is a fat-tool problem, not a server-wide one.

**Why it went unnoticed for three milestones.** Nothing errors. The tool works, the reply is
correct, and the guidance explaining it is dropped in transit — the same silent-failure shape as
every defect M95–M103 closed, this time in the documentation channel rather than the data one. It
surfaced only because a tester said "I cannot see this" instead of assuming they had misread.

**The fix is editing *and* relocating — the plan's "editing, not relocating" did not survive
measurement.** Front-loading alone cannot carry this contract: the content a caller needs *before*
calling `redact_text` came to ~2,374 characters even before the residual catalogue, so ordering
would only have chosen which safety-critical paragraph got cut. So the description keeps what must
be read **before** the call (that it destroys content, the `query`/`queries` contract, run `search`
first, what `whole_words` changes, the word-boundary trap) and the reference material a caller needs
**while reading the reply** moves to `klarpdf://docs/{tool}`.

**Adopted, having been rejected: the MCP resource.** The earlier objection was that resources are
*application-controlled* and so not guaranteed to reach the model. That still holds, and it is why
the resource carries only the reference half — an agent that never reads it still gets every
safety-critical sentence. What settled it was measuring the channel: a resource read is capped at
**100,000** characters (`ReadMcpResourceTool`'s `maxResultSizeChars: 1e5`) against the description
channel's 2,048, so the depth has somewhere to go that is not merely 49× larger but *uncapped in
practice*. Assembly is what keeps it honest: the resource returns the **live registered
description** plus a disjoint appendix, never a second copy, so drift is impossible rather than
unlikely, and a test asserts the resource still contains the description verbatim.

**The cap is settled, and it is wider than the report knew.** No probe tool was needed: the constant
is visible in the client binary as `yfe = 2048`, and the same constant truncates an MCP server's
**`instructions` block** — a path ENV-001 believed uncapped because ours arrived whole at 1,765
characters, which is simply under the cap. The `--read-only` build appends to it and reaches 1,853,
leaving 195 characters of headroom, so the instructions are tested to the same budget as the
descriptions. The enforced budget is **1,900**: under the real number with margin, because the cap
was read out of a minified bundle belonging to a client we do not ship.

**A tenth of the budget was leading whitespace.** The SDK sends `fn.__doc__` **verbatim**
(`tools/base.py`: `func_doc = description or fn.__doc__`) — no `inspect.getdoc` — so every
continuation line arrived carrying the eight spaces that indent it inside `create_server`. Across
the seventeen tools that was **5,972 characters, 29% of all description bytes**, spent to say
nothing and counted against a 2,048 cap. `guarded` now runs `inspect.cleandoc`, which fixes every
tool at once; it is the same class of defect as the `__signature__` copy that wrapper already
carries, and the same lesson — the SDK introspects the callable, so what it reads has to be
normalised deliberately.

**Found and deliberately not used: `anthropic/alwaysLoad`.** A tool carrying
`_meta: {"anthropic/alwaysLoad": true}` is excluded from the deferred set (`isDeferredTool` returns
false for it), and a non-deferred tool renders through `description()`, which returns the string
raw — so the flag removes the truncation outright. It is not used because it is not needed once the
budget is met, and it is not free: an always-loaded description is resident in every session that
connects the server, whether or not the tool is used, and it is a vendor key that does nothing on
any other MCP client. Recorded as the fallback if a future tool genuinely cannot be said in 1,900
characters — with the note that reaching for it should be evidence the material belonged in the
resource.

### M106 *(unplanned)* — unknown parameters are dropped in silence (TC-009, 2026-08-18)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M106** An unrecognised argument is an **error**, not a silent no-op — on every tool, with a did-you-mean when the name is close | `mcp_bridge/strict_args.py` (the message, SDK-free) behind `StrictArguments`, an `Extension.intercept_tool_call` in `mcp_bridge/server.py` | WSL | `querys=[…]` on `redact_text` raises naming the parameter and suggesting `queries`, and **writes nothing**; a correctly-spelled call is byte-identical to today; the check covers all 17 tools, not just the redactors |

**The worst defect this series has found, and it arrives through a door none of the others watch.**
Reproduced directly: a one-character typo left PII in a file the tool certified clean.

```jsonc
redact_text { "query": "08-24-1970", "querys": ["12-25-1972"], "whole_words": true }
→ { "matches": 1, "residual_matches": 0, "residual_literal": 0, "residual_normalized": [],
    "cross_engine_verified": true, "source_unchanged": true }        // unqualified success
```

`08-24-1970` was removed; `12-25-1972` is **still in the output**, and nothing in the reply mentions
`querys`. TC-009 tested four more plausible typos with the same result: `wholewords` silently
switched a phrase redaction into word-list mode and destroyed 240 boxes where 9 were wanted; `page`
silently expanded a one-page request to five pages; and an invented **`dry_run: true`** performed a
real destructive write and reported success — the most alarming shape, because the parameter's whole
purpose is to prevent the thing it fails to prevent.

**Why nothing already built can catch it.** Every check this bridge performs is *downstream of
parameter binding*: `residual_matches`, `residual_literal`, `residual_normalized`,
`cross_engine_verified` and `verified_text` all describe what the server **did**, and none can
describe what it was **asked** to do, because that information was discarded before any of them ran.
The verification is sound and the report is honest; the input simply was not what the caller sent.
Every safety signal M95–M103 added reads clean here, correctly, which is what makes it dangerous.

**Root cause is one missing pydantic setting in the SDK**, not in our code: `ArgModelBase` in
`mcp/server/mcpserver/utilities/func_metadata.py` declares
`model_config = ConfigDict(arbitrary_types_allowed=True)` with no `extra=`, so pydantic's default
`extra="ignore"` applies and `arg_model.model_validate()` drops unknown keys before the tool
function is ever called. `guarded` cannot see them — it wraps the function, which runs after
validation. So the fix has to sit **upstream of validation**.

**The seam turned out to be `Extension.intercept_tool_call`, not `middleware`.** Both were probed
end-to-end against a real in-memory client session before choosing. `middleware` works and sees the
raw params, but the SDK marks it `TODO(L54): provisional — signature and semantics change with the
Context/middleware rework before v2 final`, and a short-circuit there has to hand back its own wire
envelope, so the rejection would arrive as a JSON-RPC protocol error rather than in the shape every
other bridge error uses. The interceptor avoids both costs. It wraps the **handler**, which sits
*below* the runner's `CallToolRequestParams` validation but still *above* the per-tool argument
model — and `CallToolRequestParams.arguments` is a plain `dict[str, Any]`, so the unknown keys are
all still present (measured: `['path', 'query', 'querys']`). That is the same visibility from a
documented, non-provisional API, and a short-circuit return is "sieved and stamped exactly like the
wrapped handler's", so the rejection reaches the agent as an ordinary tool error. The price is one
entry in `capabilities.extensions` (`io.klarpdf/strict-arguments`), which is an honest description
of a server that does check its arguments strictly.

**Two things the build settled that the plan had guessed at.**

*The suggestion is plural, and the cutoff is deliberately high.* `difflib` ranks `querys` closer to
`query` than to `queries` — the shorter word is the smaller edit — so the single best match is not
the one the caller wanted. Offering every match above the cutoff (`n=3`) answers with both and
costs nothing. The cutoff is **0.7**, not `difflib`'s default 0.6, because at 0.6 an `out_path` is
answered with `path`: nudging a caller who meant the **output** towards the **input** file is worse
than staying quiet, and the accepted list is printed either way.

*The suggestion is matched case-insensitively; the check is not.* The TC-009 **retest** found the
one gap the build left: a shouted-but-otherwise-correct `PAGES` was rejected with no hint at all,
because case-sensitive edit distance is dominated by the case difference (`PAGES` → nothing,
`Query` → `query`, the cutoff falling between them at two differing characters). Case-folding both
sides before the comparison fixes `PAGES`, `OUT` and `MATCH_CASE`, and can only *add* a hint —
every accepted name is already lowercase, so folding cannot pull a lowercase probe towards a
different answer, and the semantic aliases that should stay quiet (`case_sensitive`, `out_path`)
still do. Matches map back to the tool's own spelling, since the caller needs the name to type
rather than the one they typed. The **rejection** stays case-sensitive: accepting `PAGES` as
`pages` would be the same species of leniency M106 exists to remove.

*Nothing was added to the tool descriptions or to `INSTRUCTIONS`.* The error is self-teaching — it
names the parameter, suggests the near miss, lists what the tool accepts, and states that nothing
ran — and it arrives exactly when it is needed. `INSTRUCTIONS` is already at 1,765 characters
against the ~2,048 the client truncates at (§M105), so spending that headroom on a message the
agent will be handed anyway is the wrong trade. The bridge README carries the human-facing note.

**Reject rather than warn.** For a tool that deletes content an unrecognised key is far more likely
to be a mistake than something safe to ignore, and rejection *fails closed*: it costs the caller one
corrected call instead of a file they may already have shipped. The accepted names are known and the
observed typos are all edit-distance 1, so a suggestion is nearly free:

```
Error: unknown parameter 'querys'. Did you mean 'queries'? Accepted: path, out, query, queries,
       match_case, whole_words, pages, password, overwrite. Nothing was written.
```

**Mitigating factor, real but narrow:** `source_unchanged: true` held in every case — the input is
never touched, so each of these is recoverable by discarding the output. The harm is in *trusting*
the output, which is exactly what the `querys` case invites.

**Scope is framework-wide, not per-tool.** Confirmed on `redact_text` (destructive) and
`render_page` (read-only), so the other fifteen tools will behave identically; the fix should be one
check over all of them rather than an argument list per tool. Worth re-testing the remaining write
tools afterwards to confirm.

**Built and retested 2026-08-19.** The guard reads each tool's own published input schema rather
than a list kept beside it, so a tool that gains an argument cannot fall out of step with it, and
it holds no per-tool knowledge at all — one loop covers all 17. Because the check runs above
argument validation, a tool needs no valid arguments to be probed, which is what makes a test over
the whole surface possible; `tests/test_mcp_strict_args.py` walks every registered tool and asserts
the roster against `test_mcp_server.py`'s rather than restating it. Those tests drive a **real
client session**, not `MCPServer.call_tool` — the interceptor is on the `tools/call` handler and
`call_tool` goes straight to the tool manager, so a test written the way the rest of the MCP suite
is written would pass against a server with the guard deleted. That is also what pins the SDK seam:
if a bump moves `intercept_tool_call` below argument validation, these fail loudly instead of
quietly reverting to the TC-009 behaviour.

One trap found in the building: `create_server` has **two** exits — the `--read-only` path returns
early after the six query tools — and a bind on the way out missed it, leaving the guard unarmed in
exactly the configuration chosen for caution. It binds immediately after construction instead,
which is safe because binding stores a reference and the schemas are not read until the first call.
A withheld tool must still report as an *unknown tool* rather than being answered with an argument
list it does not have; that is tested.

**The retest closed it.** All five original cases fail closed including the PII leak, the coverage
was confirmed by hand across read-only (`search`, `get_info`), destructive (`redact_regions`) and
page-set (`rotate`) tools, and ten typo shapes deliberately chosen to differ *in kind* from the
reported ones all behaved — including the three that should draw no suggestion at all. Fifteen
calls, fourteen rejections, exactly one file on disk. The single finding was the case-sensitive
matcher above.

### M107 *(unplanned)* — a redaction that lands inside a longer word (OPEN-ITEMS, 2026-08-19)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M107** Disclose a redaction that fell **inside** a longer word, instead of reporting plain success | `_partial_word_report` in `mcp_bridge/redaction.py`, fed from the write loop | WSL | `redact_text {"query": "Male"}` over a page holding `Female` returns `partial_word_matches` and a warning naming `'Male' inside 'Female'` and what it now reads; an ordinary whole-word redaction stays silent |
| **M107.1** `get_info` reports each page's `rotation` | `_page_sizes` in `mcp_bridge/queries.py` | WSL | A portrait page turned 90° and a native landscape page no longer share a `page_sizes` row |
| **M107.2** The docs resource says where the reader is | `_documentation` in `mcp_bridge/server.py` | WSL | `klarpdf://docs/redact_text` opens by naming itself; the description is still contained verbatim |

**The last of the family.** Every other "reported clean but was not" path this series found is
closed. This is the remaining one, and it had been filed three times under three names — TC-003b #7
("substring hazard is un-previewable"), TC-003 #2, and TC-007's addendum — without being recognised
as one defect. Reproduced on the current build:

```jsonc
redact_text { "query": "Male" }        // whole_words omitted, the default
→ { "matches": 3, "boxes_redacted": 3, "residual_matches": 0, "residual_literal": 0,
    "cross_engine_verified": true }                       // and the driver table now reads "Fe"
```

**Why nothing already built could see it.** Two guards cover the two ways this tool destroys more
than intended, and neither could see the other's case. Every residual field is scoped to *the
query*, and the query was removed exactly as asked — the damage is to a word the caller never
mentioned, so nothing that measures the query can find it. And `_term_report`, the over-redaction
guard TC-007 drove, returns on its **first line** when `len(terms) < 2`: a one-word query never
reached it at all. The tell that did exist was `verified_text` listing a lowercase `"male"` beside a
capitalised query, which is real and far too subtle to rely on.

**The data was already being computed.** The write loop calls `text.is_whole_word(box)` — but only
when `whole_words` is on, to *filter*. Calling it when the flag is off, to *record*, is the whole
fix; `PageText.struck(box)` then names the enclosing word. So this is a reporting gap, not a
matching one, which is why it is a small change rather than a matcher rewrite.

**Reject the temptation to filter instead.** A partial match is being redacted *because* the caller
left `whole_words` off, which is a legitimate and often correct request — `search`'s own
documentation tells them it is the right mode for an embedded identifier. Suppressing those matches
would break every caller redacting an account number inside a machine tag (M96/TC-004). The defect
is the silence, not the behaviour.

**`leaves` is computed rather than described**, and deliberately: a caller reading "1 match fell
inside a longer word" may shrug, and the same caller reading that a name now says `Fe` will not.

**The false-positive surface is the risk**, since this module's warnings are read and a warning on
every ordinary redaction would train callers to skip them. Four shapes must stay silent and are
tested: an exact whole-word match; `whole_words: true` (where a partial match is filtered before it
is redacted, so there is nothing to report); a phrase spanning two page words (a hit *between* words
is not a fragment eaten out of one); and a whole word abutting punctuation — `expression.` is a
single page word including the period, the exact shape that made whole-word search drop matches in
M64/TC-001.

**M107.1** is the same class of defect one tool over: `page_sizes` reports *displayed* dimensions
while `clip` and `redact_regions` take *unrotated* ones, so a natively-landscape page and a portrait
page turned 90° were the same row and a caller computing a box by hand could not tell which
convention they were in (TC-008). `rotation` joins the grouping key, not just the payload — as a
bare field the two geometries would still have merged into one row. Boxes from `search` are already
in the right space, which is why the normal workflow never tripped on this and it stayed low.

**M107.2** is an M105 by-product: the description ends by pointing at the docs resource, so the
resource contained a pointer to itself and a reader who followed it once could follow it again.
Stripping the sentence was the obvious fix and the wrong one — it would break the verbatim
containment that makes the two halves unable to drift. A preamble naming the page costs nothing and
keeps the guarantee.

### M108 *(unplanned)* — the residual counts said spellings and meant places (TC-011, 2026-08-19)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M108** `residual_literal` counts **occurrences**, and names each spelling with its own count and pages | `_no_residual_match` + `_literal_residuals` / `_variant_residuals` in `mcp_bridge/redaction.py` | WSL | Two spellings, three occurrences each, reports `6` and a `residual_literal_forms` list; the same number Poppler and PyMuPDF between them actually leave |
| **M108.1** `export_images` caps its file listing like every other bulk tool | `mcp_bridge/server.py` + `Limits.max_listed_files` | WSL | A 40-page export lists 25 paths with `truncated: true`, `out_dir` and the naming pattern — and still writes 40 files |
| **M108.2** The `search` cap note stops advising a flag that is already set | `mcp_bridge/server.py` | WSL | A truncated `whole_words: true` search no longer says "or set `whole_words`" |
| **M108.3** Exported filenames pad to the document's page count on the bridge path | `model/export.py` | WSL | `pages: [1,2,3]` and `pages: [5,72,150]` from one 200-page document produce the same width; the app's Export is unchanged |

**Found only at scale.** On a 320-page document `redact_text` reported `residual_literal: 2` where
**12** residual occurrences remained. The field counted distinct *spellings* and the warning called
them "place(s)". Small documents hid it perfectly: with one occurrence per spelling, spellings and
places are the same number, which is why nine testcases passed over it.

It matters because of *which* field it is. `residual_literal` is what TC-003 added to break circular
verification — the check that owes the matcher nothing — and the docs single it out as "the one
worth reading". A caller sizing their follow-up work against 2 rather than 12 is exactly the
understated-safety-number shape this series has spent its length closing.

**The reported fix would have propagated a second bug.** It proposed adopting the shape
`residual_normalized` already used — and that field's `count` was `len(pages)`, so three variants on
one page reported as `1`. Copying it would have carried a subtler undercount into the field being
repaired. Both now count occurrences; the *structure* was worth copying, the implementation was not.

**And the obvious repair is wrong in the other direction.** The literal scan reads text extracted by
**both** PyMuPDF and Poppler, so counting every occurrence across `extracted` double-reports — 12
where 6 remain. Occurrences are therefore **maxed per page across engines, never summed**, which
also keeps a spelling that only one extractor can see. `residual_normalized` sidesteps this by
reading one engine (`if engine != "PyMuPDF": continue`); the literal scan cannot, because reading
both is the point of it.

The dedup was happening at three levels — inside each scanner, in the caller's accumulator, and
across engines — so both scanners now return **one entry per occurrence** and the caller aggregates.
That is the change that makes the count mean what its label says.

**M108.1** is the server's own convention, applied to the one tool that never followed it:
`extract_text` caps at a character budget and `search` at 500 hits with `total_matches`, while
`export_images` returned N paths for any N — 320 near-identical absolute paths, ~35 KB, no
`truncated`. The files are all written either way; this caps the *listing*, and the reply now
carries `out_dir` and the naming pattern, which is what a caller cannot reconstruct for themselves.

**M108.3** came out of the retest: padding was derived from the highest page number *in the
request*, so two exports from one document into one directory disagreed — `pages: [1..60]` gave
`-01` and `pages: [5, 72, 500]` gave `-005`, and `-005` sorts before `-01`. The document's page
count is the stable basis. It is confined to the **bridge** path because `export_page_images` also
serves the app's Export, where the user typed the filename and picked the pages, and a width derived
from a page count they never mentioned would be the surprising choice; `number_all` is already the
derived-vs-chosen discriminator for exactly this kind of question.

**M108.2** is the same defect as TC-007 item E, one tool over: advice that does not apply to the
call that was made. A caller told to set a flag they already set reads the note as boilerplate, and
the next note they skip may be one that mattered.
### M109 *(unplanned)* — a redaction that re-encodes an image says so (TC-011, 2026-08-19)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M109** Disclose the images a redaction had to re-encode, and what it cost | `_images_under` / `_recode_report` in `mcp_bridge/redaction.py`, read in `_apply` and reported in `_finish` | WSL | A redaction over a photo returns `images_recoded` with before/after sizes and a warning; a text-only redaction, and one whose box misses the image, say nothing |

**Correct behaviour, undisclosed.** Erasing pixels inside an image means decoding it, and
re-compressing lossily would degrade exactly the area a redaction was asked to destroy — so the
engine stores it losslessly. That is the right trade. But a photograph held losslessly is far larger
than the same photograph as JPEG, so a redaction touching a few images multiplies the file size:
7.4 MB → 10.0 MB from nine images on a real 320-page document, and 61 KB → 1.3 MB for a single
synthetic page.

**The cost of the silence was three investigations, one of them ours.** The size was already visible
as `bytes`; the reason was not, and an unexplained jump reads as a bug. It was filed as one twice —
"redacted pages gain duplicated image XObjects" (TC-003 #5, re-chased as TC-010) was this mechanism
seen from outside, pursued to a duplication that never existed. The 2026-08-19 review concluded
"does not reproduce", correctly for the document tested, because that document had no image under a
redaction box. Disclosure is what stops a correct behaviour generating bug reports.

**Keyed by placement, not by xref.** A page that draws one image twice holds a single xref; erasing
pixels under one placement forces the engine to split them, so the output has two xrefs where the
source had one and no xref-to-xref mapping survives. The placement rectangle does not move, so it is
the stable identity — measured, and it is also what makes "only the placement under the box was
re-encoded" reportable, which is the fact that distinguishes this from duplication.

**Read in `_apply`, reported in `_finish`**, for the reason that function already gives for the text
counts: after materialise the source encoding is gone, so the write is the last moment it can be
seen.

**The warning states the measured direction rather than asserting growth.** Lossless is much larger
for a photograph and can be *smaller* for a flat graphic — a negative test caught the first draft
claiming growth on a case that shrank. A caller who catches a warning being wrong stops reading
warnings, which is the opposite of what this milestone is for.

**Only a changed filter is reported.** An image the write left alone, or re-stored in the encoding
it already had, cost the caller nothing, and a field on every redaction would be noise.

**The encodings and sizes are read from the object, not through `extract_image`** — corrected after
the TC-011 retest caught the first implementation reporting neither one. That helper returns a
*portable*
copy: for anything not already JPEG it synthesises a PNG, so the field named an encoding the file
does not contain (PDF has no PNG image filter; every re-encoded stream is `/FlateDecode`) and a
length that was not the embedded stream. It was exact on the JPEG "before" side, which is what hid
it — the numbers looked plausible until reconciled against the file, and then the total overstated
real growth by 129 KB with small images running 67–80% high. Reading `Filter` and `xref_stream_raw`
gives both honestly, and a caller who opens the output now sees the filter they were told to expect.
The lesson generalises: a field whose whole purpose is to account for something must reconcile
against the thing it accounts for, and a test now asserts `bytes_after` against the stream in the
file rather than against itself.

### M110 — the save's cleanup level must follow the route, not the file (found 2026-08-21) ⭐

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M110** Pick the object-cleanup level from the save *route*: the graft keeps `garbage=4`, the unchanged-page-set copy drops to `garbage=2` | `PyMuPDFEngine.materialize` in `model/edit_engine.py` — the level becomes a function of `page_set_unchanged()`, beside the encryption choice that already is | WSL + Windows | A 572-page 48,877-object document saves in ~2 s instead of ~200 s; a duplicated image-heavy page still writes 1.9 MB, not 39 MB; output size across the corpus is unchanged or smaller on every file; a redaction still leaves no orphaned image object |

**A performance regression M93 introduced and nobody measured.** `annotate` on a 572-page document
took over 120 s (TC-012 FINDING 1), which the report attributed to "cost scales with document size".
It does not: a 320-page 7 MB prospectus saves in 2.4 s. The trigger is the document's **object
count**, and the mechanism is a route change.

| | Time | Objects surviving |
| --- | --- | --- |
| **M93 route** — copy the origin, edit it (page set unchanged) | **202.17 s** | 44,860 |
| **Pre-M93 route** — graft every page into a fresh document | **3.13 s** | 2,176 |

Before M93 every save rebuilt the document, and `insert_pdf` collapsed the object graph as a side
effect — 48,877 objects in, 2,176 out. `garbage=4` then had almost nothing left to search. M93
stopped rebuilding, for good reasons (the graft silently dropped the structure tree, `/Perms`,
`/Names` and encryption), and the full object graph now survives into the save, where `garbage=4`
hunts duplicates across all 48,877 of them and finds none. **M93 did not add a slow step; it removed
a fast one nobody knew was load-bearing.** The regression is **unreleased** — v0.17.1 was tagged
2026-08-11, M93 merged 2026-08-15 — which is why an owner running the shipped build could not
reproduce it and a WSL checkout of `main` could.

**What each level actually buys, measured** (same document, five levels):

| Level | Adds | Size | Objects |
| --- | --- | --- | --- |
| 0 | nothing | 8,795,392 | 1,502 |
| **1** | **drops unreferenced objects** | **7,220,823** | 1,502 |
| **2** | **compacts the xref** | 7,220,117 | **1,182** |
| 3 | merges identical objects | 7,220,117 | 1,182 |
| 4 | merges identical streams | 7,220,117 | 1,182 |

Levels 1 and 2 do all the work and cost nothing. Levels 3 and 4 changed **nothing** on this file,
and on the pathological one they cost 195 s to change nothing — `garbage=2` there is 121× faster and
18 KB *smaller* (merging objects perturbs how they pack into object streams, and it does not always
win). `clean=True` is a different parameter and is **not** implicated: measured at ~1.9 s, and
dropping it makes `garbage=4` *slower* (533 s). It stays.

**Levels 3 and 4 are not useless — they clean up after our own copying, and the split is exactly
where the routes already divide.** Duplicating an image-heavy page is the case that proves it:

| Level | page + 5 duplicates | page + 20 duplicates |
| --- | --- | --- |
| 1–3 | 11,291,676 | 39,517,349 |
| **4** | **1,883,185** | **1,883,359** |

Level 4 saves 95% of that file **and is 4× faster** while doing it, because detecting that twenty
images are identical costs less than compressing twenty copies of them. Note level **3** does not
help and level **4** does: images are *streams*, and only level 4 merges streams. Duplicating,
deleting, reordering and merging all change the page set, so all take the graft — which is where
the duplicates come from and where level 4 must stay. The rule is therefore not about the document
at all: **use the expensive level exactly when we were the ones who did the copying.**

**Level 1 is security-critical and is never in question.** A redaction that removes an image detaches
it from the page but leaves it in the file; level 1 is what deletes it. Measured on a page whose text
sat on an image: at `garbage=0` the orphaned image object is still in the file and recoverable by
anything that walks objects rather than pages; at level 1 it is gone. The proposed floor of 2 keeps
this comfortably, but the **verification cannot see it** — `redact_regions` re-reads the output and
checks the *text* with two engines, and an orphaned picture of a secret is not text to either. So
M110 must also pin the floor with a test: redact an image-backed page, assert no orphaned image
object survives. One test, closing a gap the current checks structurally cannot cover.

**Do not generalise from five documents.** The counter-case exists — a file that arrives *already*
full of duplicate streams (built by another tool, or by someone else's duplicate-pages save) would
no longer be shrunk by an ordinary Save. That is arguably correct (a save that was not asked to
optimise should leave a file the size it found it, and **Reduced-Size PDF** exists for when you do
ask), but it is a behaviour change and the corpus comparison must look for it rather than only
confirming the wins.

#### Verified when built (2026-08-21) — the route rule stands, and the counter-case is real but small

The corpus comparison this section asked for was run over ten documents, and it found the
counter-case: at `garbage=2` four of them are larger than the same file written at `garbage=4` —
`ssa-1-bk.pdf` by 31% (224,075 B against 154,902), `ssa-3.pdf` by 8%, `dhariwal_ipo.pdf` by 5%,
`Policy_home_….pdf` by 0.1%. Level 4 is the only level that merges *streams*, and real documents do
arrive carrying duplicate ones, so the claim that 3–4 buy nothing on the copy route is false in
general; what stands is that **it is not a Save's job to collect that saving**.

**The comparison that matters is against the file the user has, not against our previous output**,
and by that measure the rule holds: at `garbage=2` every corpus document still saves *smaller than
its input*.

| Document | input | `garbage=2` save | vs input |
| --- | --- | --- | --- |
| `ssa-1-bk.pdf` | 233,320 | 224,075 | **−9,245** |
| `ssa-3.pdf` | 72,997 | 70,112 | **−2,885** |
| `f8949.pdf` | 150,240 | 81,352 | **−68,888** |
| `spaceX_prospectus.pdf` | 7,363,360 | 7,220,185 | **−143,175** |
| `Policy_home_….pdf` | 284,270 | 281,990 | **−2,280** |
| `dhariwal_ipo.pdf` | 9,015,879 | 9,311,232 | +295,353 |

Only the pathological file ends larger than its input, by 3.3%, and that is the file that cost
**289 s** to save and now takes **1.87 s**. **Nothing ratchets**: re-saving a `garbage=2` output
reproduces its size exactly (224,075 → 224,075), so a document does not creep upward across
successive saves.

**The graft keeping level 4 costs nothing, for a structural reason.** `insert_pdf` collapses the
object graph on the way through, so the hunt runs against an already-small document: measured on the
572-page, 48,877-object prospectus with one page deleted, the graft writes 2,178 objects in
**2.08 s**. The expensive level is therefore safe exactly where it is used.

**An object-count budget was built and withdrawn.** Choosing the level from the output document's
object count (deduplicate under ~5,000 objects, compact above) keeps the 31% on `ssa-1-bk.pdf` and
still fixes the regression. It was rejected on the owner's call, and the reason is the one this
section already gives: a Save that was not asked to optimise should leave the file as it found it,
`Export ▸ Reduced Size PDF` is the place that asks, and a magic threshold buys size the user never
requested at the cost of a cliff — two similar documents behaving differently with nothing to
explain why.

### M111 — the export paths never followed M93, and one of them reports a number it does not write (found 2026-08-21)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M111** Give the save options one owner, so a change cannot update one call site and leave three behind: add `use_objstms=1` to the three export writes, and fix the `before` baseline `export_reduced_pdf` reports | `model/export.py` (`export_flattened_pdf`, `export_reduced_pdf` ×2) against the option set `model/edit_engine.py` owns | WSL | Reduced-Size never returns a file larger than a plain Save; the reported `before` equals what a Save actually writes; flatten output is unchanged or smaller |

**The same drift as M110, from the same commit.** M93 added `use_objstms=1` to `materialize` and to
nothing else. Four call sites write a PDF; one was updated. The consequences land on the feature
whose entire purpose is making files smaller:

| Document | Reduced-Size today | with `use_objstms` | left on the table |
| --- | --- | --- | --- |
| Property brochure (23 MB) | 14,075,755 | 14,034,903 | 40,852 |
| SpaceX prospectus (7 MB) | 7,366,024 | 7,222,872 | 143,152 |
| Tax form (3 MB) | 593,829 | 593,429 | 400 |

**And on one document Reduced-Size produces a larger file than pressing Save** — 7,366,024 against
7,220,185, 146 KB worse. Its images are already efficient, so the lossy pass buys nothing while the
missing `use_objstms` costs more than the recompression saves. A user reaching for "make this
smaller" gets something bigger than doing nothing.

**The reported `before` is also not what it claims.** `export_reduced_pdf`'s docstring promises it is
"what a plain Save of the current document would write (so the delta is the lossy tier's true effect,
not the lossless cleanup a Save gives anyway)". It is computed without `use_objstms`, so it
overstates the starting size — by 40,866 B, 143,143 B and 394 B on the three files above — and
therefore overstates how much the feature saved. A number whose whole job is to be the honest
baseline has to be measured against what the app really does, which is the same lesson §M109 drew
about `bytes_after`.

**`garbage=4` is correct in the exports and stays.** Reduced-Size is the one operation where the
caller has explicitly chosen smaller over faster, and `rewrite_images` genuinely creates duplicates —
re-encoding every image to JPEG can turn two different streams into identical ones, which only level
4 merges. The fix here is the missing option and the wrong baseline, not the level.

**The structural half is the point.** Both M110 and M111 exist because the save options are four
copies of a literal. They should be one named set with the route choosing the garbage level, so the
next change to how this project writes a PDF cannot land in one place and miss three.

#### Built (2026-08-21) — and the baseline needed a second half

The three export writes now take `write_options()` from `model/edit_engine.py`. Two decisions the
row above did not settle, both made against the measurements:

* **Both exports keep the deduplicating level, and the reason generalises the one M110 uses.** The
  design justified it by `rewrite_images` creating identical streams, which is an argument about
  Reduced-Size alone; flatten was built on the Save's route rule first and measured worse for it —
  `f8949.pdf` flattened to 81,578 B against 46,680. `bake()` turns every widget of a form into page
  content and a form's widgets share appearance streams, so flatten *creates* duplicates exactly as
  the graft does. Same rule either way: **level 4 cleans up after our own rewriting, never after
  somebody else's file.**
* **The honest baseline also has to carry the encryption.** With the options matched, the reported
  `before` was still 2,231 B short on `ssa-1-bk.pdf` — an AES-128 form, whose Save carries its
  encryption through (M93) at a cost in bytes. So the baseline is measured with
  `PyMuPDFEngine.save_keywords()`, the *complete* set a Save passes, rather than only the cleanup
  options.

Measured across five corpus documents, `before` now equals a real Save **exactly** on all five
(it was 18 KB–162 KB over), and Reduced-Size no longer returns a larger file than a Save on four of
them. `spaceX_prospectus.pdf` still ends up **2,706 B** larger — down from 144,998 — and that
residue is the lossy pass itself, not the options: its images are already efficiently encoded, so
re-encoding them buys nothing and costs a little. The dialog already reports that case honestly
("no smaller than a plain save"), which is the right answer to it.

| Document | reduced − save, before | after |
| --- | --- | --- |
| `ssa-1-bk.pdf` | +159,708 | **−2,231** |
| `spaceX_prospectus.pdf` | +144,998 | +2,706 |
| `f8949.pdf` | +68,849 | **−36** |
| `Patina-…-Brochure.pdf` | −7,523,443 | **−7,564,299** |
| `Account_Statement_Mar_2026.pdf` | −387,812 | **−406,266** |

### M112 — the bridge can *edit* an annotation, not only add one (owner-asked 2026-08-21)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M112** The bridge-side counterpart to M66–M68: delete a mark, recolour it, change its note, and **adopt** a foreign one so it becomes editable | A new tool over `model/foreign_annots.py` (`adopt_annotation`, `degradations`, `ForeignDeletion`) and `page_edits.restyle_mark`, addressed by the `fingerprint` `get_annotations` reports | WSL | An Edge highlight is adopted, recoloured and re-noted from the bridge and reopens editable in the app; adopting a mark carrying features the model cannot hold **reports** the loss rather than performing it silently; a delete removes exactly one mark and leaves its neighbours untouched |

**The gap, stated correctly** (the owner corrected an earlier framing of this on 2026-08-21). It is
*not* that KlarPDF handles other tools' marks poorly — it handles them well, across three
milestones: M66 deletes a foreign annotation, M67 moves one with its appearance preserved exactly,
M68 **adopts** one on double-click into an ordinary editable KlarPDF mark, and M90.4 shows its note.
Once adopted, recolouring, re-noting, extending and merging all work normally.

The gap is that **the bridge has none of it**:

| | App | Bridge (after M101) |
| --- | --- | --- |
| Read foreign marks | ✅ M66/M90.4 | ✅ `get_annotations` |
| Delete one | ✅ M66 | ❌ |
| Move one | ✅ M67 | ❌ |
| Adopt → recolour / note / merge | ✅ M68 | ❌ |

An agent can see a colleague's marks and add beside them; a person doing the same job in the app has
four more options. M101 recorded this as deliberately out of scope — correctly, for that milestone —
but "out of scope" is not "on the roadmap", and an undated exclusion is how a decision becomes a
permanent gap. Hence a number.

**Why it is worth building rather than merely consistent.** M101's review loop assumes step 2 happens
in KlarPDF. Reviewers use what their employer installed, so a real round trip often comes back
carrying Acrobat or Edge marks — and against those the bridge can only stack (§M101's merge is scoped
to marks this app wrote, deliberately: merging deletes a mark, and silently deleting a reviewer's
annotation to reattribute its span would be worse than a duplicate). Adoption is the principled way
out, and it already exists: it is an explicit act that takes ownership rather than a silent one.

**Three things to get right:**

1. **Naming a mark is the hard part, and it is not solved.** An editing tool has to say *which*
   mark to act on, and the obvious candidate does not work. `foreign_annots.fingerprint()` prefers
   an annotation's `/NM` name — an optional field in the PDF that is meant to identify an annotation
   uniquely on its page — and falls back to hashing type + rect + contents when there is none.
   Measured 2026-08-21:

   * **For one of our own marks it does not merely go stale — it silently rebinds to a different
     mark.** PyMuPDF fills `/NM` in automatically as `fitz-A0`, `fitz-A1`, …, which are *positions
     on the page*, not identities; and our marks are stripped and re-created from descriptors on
     **every** save, so the numbering is reassigned in whatever order they now sit. Three marks
     LEFT / MIDDLE / RIGHT; one call takes MIDDLE's span over; RIGHT is untouched but its id moves
     `fitz-A2` → `fitz-A1` — and `fitz-A1`, which meant MIDDLE, now resolves to RIGHT. An edit
     addressed to the old id hits the wrong mark **with no error**, because the name resolves
     perfectly.
   * **Whether a foreign mark carries a better one is unverified.** The intuition is that Acrobat
     and Edge write a real `/NM` and that the mark passes through our save untouched, so the
     identifier holds. Both corpus files checked came back with `fitz-` names because both had been
     annotated by *this app* (`pdfproj` is our old codename), so they prove nothing either way.
     **Check real third-party output before any design rests on this.**

   So `get_annotations` must **not** simply report `fingerprint()`. A field that is trustworthy for
   some rows and quietly wrong for others is worse than no field, because nothing in the reply
   separates them. *(An earlier draft of this section scheduled exactly that for the M101 fix PR.
   Withdrawn.)*

   Three options, in ascending cost:

   * **Report it for foreign marks only**, `null` for ours. Cheap, and it unblocks the case with no
     alternative today. Rests entirely on the unverified assumption above.
   * **Derive ours from content** — the hash path `fingerprint()` already falls back to, over type,
     area and note, all of which the descriptor reproduces exactly across a save. It still changes
     when a mark merges, but that is *correct*: an absorbed mark is a different mark. The property
     that matters is that it goes **stale rather than wrong** — a changed id matches nothing, so the
     edit fails loudly instead of hitting the wrong target.
   * **Give our marks a real identity**, carried on the descriptor and written into the file. The
     proper answer and much the largest: model, save path, undo and the app.

   **Recommendation: the second**, with the first as a fallback wherever a foreign mark turns out to
   carry a usable `/NM`. It is the only one that makes a wrong edit impossible rather than merely
   unlikely, it does not touch the app, and it does not depend on the unverified assumption.
2. **`degradations()` reports; it cannot prompt.** The app's adoption path shows a warning with a
   cancel button when a mark carries features the model cannot hold (`/RC` rich text, a non-base-14
   DA font, `/CA` opacity, `/CL` callouts). A tool has no cancel button, so the contract must invert:
   either refuse an adoption that would lose something and name what, or perform it and report the
   loss — the first is safer and matches the redaction path's "refuse rather than silently degrade".
   This is the milestone's real design decision.
3. **Move is probably not in it.** M67's value is dragging a mark with a mouse; an agent nudging one
   by coordinates has no equivalent need, and the geometry editing is a different kind of tool from
   the metadata editing above. Excluded unless a session asks for it.

**Description budget (M105) argues for one tool with an operation, not four tools.** The roster is
already nineteen; `edit_annotation(path, fingerprint, out, delete=… | color=… | note=… )` keeps it at
twenty rather than twenty-three.
### M113 — what TC-012 and TC-013 found in M101 (reviewed with the owner, 2026-08-21)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M113** Nine follow-ups from the two hands-on sessions against M101: three defects, one disclosure gap, and five documentation gaps | `mcp_bridge/annotations.py`, its two docstrings in `server.py`, `mcp_bridge/docs.py`, `mcp_bridge/config.py`, and `model/markup_palette.py` | WSL | A re-run leaves the file's content identical, note included; a reply stays inside the client's budget and pages with `offset`; a restricted document warns; a mark reports the text it landed on; a foreign mark's default yellow is named `Yellow` |

**Built 2026-08-25** — 2336 passed, 2 skipped (104 new since M101's 2232). Two things the build
settled that the plan above could not, both recorded in full at their own items below: **M113.7 was
resolved by a route the plan did not list** (the metric was wrong, not the tolerance — see below),
and **M113.2's cap turned out to need a second, uncapped read path** for `annotate`'s own echo,
since narrowing that echo to the marks a call touched means reading the file back *before* the
caller-facing caps apply, or a crowded page could crowd out the mark just written.

**How the two reports were treated.** Every finding was re-run against the code rather than accepted
as filed, and three did not survive contact: TC-013's "there is no cap and no `truncated` field"
(there is one, it works, their document was simply under it), its "each run stacks another full set,
unbounded" (it stacks exactly once, then merges into our own mark — their own control run shows it),
and its merge threshold of "somewhere in (0.01, 3] points" (it is just above 0.01; 0.5 merges).
TC-012's "cost scales with document size" was wrong in a way worth its own milestone — §M110.

**M113.1 — a re-run duplicates the note** *(defect, and the docs promise the opposite)*. The
description says a re-run is safe and gives "a file identical in content to the first run's". Run the
same call three times and the note reads its own text three times, blank-line separated; the mark,
its colour and `marks_added: 0` are all stable. Cause: `merge_markup` correctly carries an absorbed
mark's note onto the survivor (M81's rule — only deleting a mark deletes its note), and `_attach_note`
then adds the note from *this* call on top. Both are right alone; nothing distinguishes "a new
comment, keep the old" from "the same comment again". **Fix: skip a note already present, matched as
a whole joined segment — not as a substring**, or a note of "check" would be swallowed by an existing
"check the totals". Known limit, to be documented rather than engineered around: notes are joined
with a blank line, so a note that *contains* one is ambiguous under that encoding and will still
duplicate. **The test that was missing is the point** — `test_running_the_same_call_twice_does_not_stack`
passes today because it never attaches a note; it asserts the count, which is the weaker half of the
claim the docs make.

**M113.2 — the reply outgrows what a client will accept** *(defect; the cap is on the wrong unit)*.
The count cap works (600 marks → 500, `truncated` set). It cannot do the job: reply size per mark
runs 213–613 characters depending on note length, so "500 marks" is anywhere from 107 KB to 300 KB.
A real session died at **139,288 characters over 406 marks**, and the composition is not where
intuition puts it — **53% JSON scaffolding, 37% notes, 8% boxes**. **Fix: a character budget beside
the count**, well under the ~136 KB that failed. Two riders: `annotate`'s reply carries every mark on
the pages it touched, so adding one mark to a page holding eighty returns eighty-one — **narrow it to
the marks the call actually touched**, which is bounded by the request instead of by the page's
history. And `extract_text`'s 200,000-character cap has the same exposure — **not ours, log it
separately.**

**On overflow: drop whole marks, and let the caller ask for the rest** (owner, 2026-08-21 — the
alternative considered was trimming long notes to keep every mark's geometry). This makes
`get_annotations` **the bridge's first paginated tool**, which needs justifying, because every other
capped tool answers truncation with *narrow the request*: `search` says tighten the query,
`export_images` says list the directory. **That advice has no analogue here.** There is no query to
tighten — the marks are simply on the page — so the only lever is `pages`, and it fails precisely in
the case that overflows: one dense page carrying 400 marks cannot be narrowed at all. A caller
following the documentation would be told to page and find that paging does not help.

So add an `offset`: skip the first N marks, return the next batch, and report the true total
alongside — the same courtesy `search` already extends with `total_matches`, so a caller knows how
many rounds to expect before starting rather than discovering it one call at a time.

**A plain integer offset is enough here, and that is worth stating because it usually is not.**
Position-based paging is normally fragile — the data shifts between calls and rows are skipped or
repeated. Neither can happen: the order is deterministic (page order, then each page's own
annotation order), and **the document cannot change underneath the sequence**, because every write
tool refuses to write over its input, so a concurrent `annotate` produces a *different* file. No
cursor token, no snapshot, no staleness handling.

**The risk this trades for, and the docs must carry it:** dropping marks means a caller who filters
by colour and reads only the first batch gets an **incomplete answer that looks complete**. So
`more_available` has to be prominent, with the instruction stated plainly — when filtering across a
whole document, keep calling until it is false. A truncation that silently reads as "here is
everything orange" would be worse than the size error it replaced.

**As built.** `max_chars` defaults to **60,000** (`DEFAULT_MAX_ANNOTATION_CHARS`), well under the
~136 KB that failed, and sits beside the count cap rather than replacing it — whichever binds first
stops the batch. The reply gained `total_annotations`, `offset` and `more_available`; `truncated`
is **gone**, because it answered a question the caller can no longer act on ("narrow the request")
and `more_available` answers the one they can. Two things the build had to settle:

* **A batch always yields at least one mark**, even when that single mark's JSON already exceeds the
  budget. Otherwise a note longer than `max_chars` returns an empty batch with `more_available: true`
  forever, and a caller paging correctly never terminates.
* **`annotate`'s echo needs an *uncapped* read.** Narrowing the echo to the marks a call touched
  (the rider above) means reading the written file back and then filtering it — so the read itself
  must not be capped first, or a page already holding several hundred marks could exhaust the budget
  before reaching the one just written, and a successful call would echo nothing it did. Hence
  `_NO_CAP`, used only on that internal path: the caller-facing caps are applied to the
  caller-facing call, not to a read whose result is about to be filtered down to a handful.

**M113.3 — a document asking not to be annotated is annotated silently** *(disclosure gap)*. The
policy fixture carries `annotate: false`; `annotate` wrote sixteen marks and returned no `warnings`
key at all. Writing them is correct — the flag is advisory and enforced by nothing — so **the defect
is the silence**, exactly as at §M107. Three reasons it must speak: `get_info`'s own description
tells the caller to "tell the user when they are about to act against one", and the agent can only
relay what it is told; `fill_form` already warns for a read-only field and `redact_text` for four
distinct hazards, so silence here is the exception; and of the eight permission bits this is the one
naming this exact operation. **Deliberately not folded in:** the app does not warn either. That is a
GUI decision with its own design and should not ride on an MCP fix — logged, not scheduled.

**M113.4 — nothing says which corner the coordinates start from** *(documentation + a disclosure
that makes it self-revealing)*. Boxes are measured from the **top-left**, y downward. The PDF format
measures from the **bottom-left**, y upward, and so does every mainstream library except PyMuPDF —
the flip is PyMuPDF's own, exposed as `page.transformation_matrix` (`Matrix(1, 0, 0, -1, 0,
page_height)`), applied on the way in *and* out; we add no conversion and the file we write is always
standard PDF. Inside our own tools this never bites, because `search` → `annotate` →
`get_annotations` → `redact_regions` are one frame end to end. It bites at the seam with anything
that read the file directly: a box from another library lands **mirrored about the page's horizontal
axis** — valid, on the page, no error, wrong line. It caught the TC-013 tester building their own
fixture. **Owner decision 2026-08-21: do both halves.** (a) State the convention and the conversion
(`y' = page_height − y`; `get_info` reports the height). (b) **Report the text each mark landed on** —
reuse `PageText.snippet_for`, which already joins across a wrapped match, plus the full character
count so an over-wide box shows up too. That turns a silent failure into an obvious one for *every*
way a wrong box can arrive, not only this one, and it is worth having anyway. Limit to document: on a
page with no text layer the snippet is empty, which is indistinguishable from a wrong box.
**Rejected: any heuristic that guesses a box is mirrored** — marking the bottom of a page is
legitimate, so it would misfire in both directions.

**M113.5 — a mark never merges with one somebody else wrote** *(documentation, plus a disclosure)*.
Two sentences claim merging is universal, and one specifically says "a mark written here and one
drawn by hand resolve the same way". Against a foreign mark both merge branches are inert. **The
behaviour is right and must not change**: merging deletes a mark, so merging across authorship would
delete a reviewer's annotation and reattribute their span to us. The sentence is the defect — and it
is slippery rather than plainly false, since a mark drawn by hand *in KlarPDF* does resolve
identically. **Fix: scope both sentences to marks this app wrote, and say it in the reply too** — a
count, or a warning naming the author overlapped — since the docs only help someone who reads them.
Consequence worth stating in the docs: the review loop assumes step 2 happens in KlarPDF, and a
reviewer using Acrobat or Edge produces marks the next pass stacks against. The real answer to that
is adoption, which the app has had since M68 and the bridge does not — §M112.

**M113.6 — three smaller documentation gaps.** (a) **Marks must genuinely overlap to merge**;
touching exactly does not, nor does 0.01 pt, while 0.5 pt does. The case a caller meets is two
adjacent `search` hits: `New` ends at x=184.49 and `York` begins at 187.54, a 3 pt gap, so marking a
phrase word-by-word leaves two marks where the reader sees one. Say so, and say to pass a phrase as
one mark using the boxes a single hit gives. (b) **On a page with no text layer, two overlapping
marks merge into the box enclosing both** — `[100,500,300,560]` + `[200,520,400,580]` →
`[100,500,400,580]`, painting corners that were in neither. Invisible on a text page, where bars
follow the lines; on a scan there is no such structure. Documentation only: nothing is hidden, the
box is visible in the reply, and teaching the merge to keep separate areas on a scan is real work in
code the app shares. (c) **`marks_added` can be negative** — a bridging mark that collapses two into
one returns `-1`. Correct by the field's definition and honest; the description only ever discusses
it being smaller than requested, so one worked example is owed. Plus the grammar slip `'Yellow' is
not a underline colour`, where one string serves all three types.

**As built:** (a), (b) and the `marks_added` worked example are in `klarpdf://docs/annotate`, with
"pass a phrase as one mark" promoted into the description itself — it changes what a caller *sends*,
so it belongs above the fold (M105's split). The grammar slip is fixed by choosing the article from
the type name rather than by three format strings, since only `underline` takes "an", and a test
covers all three so a fourth mark type cannot reintroduce it.

**M113.7 — the documented colour filter cannot filter an Edge mark** (added 2026-08-23, from the
TC-012 Edge cross-check). `get_annotations` reports Edge's default highlight as `color_name: null`,
`color_exact: false`. That is *correct* by the rule §M101 set — "`null` when nothing is close, rather
than a misleading guess" — and it defeats the workflow the tools advertise in their own description:
*read, filter on `color_name`, pass the survivors to `redact_regions`*. Edge is the likeliest source
of a foreign mark a caller will ever meet, so the headline composition fails on the commonest real
input.

**The obvious fix is the dangerous one, and the numbers say so.** Edge's yellow is
`[1, 0.9412, 0.4]`. Against our highlight palette:

| our swatch | RGB | distance |
| --- | --- | --- |
| Yellow | `1, 0.86, 0.10` | 0.311 |
| **Orange** | `1, 0.72, 0.30` | **0.243** |
| Pink | `1, 0.65, 0.85` | 0.536 |

It is **nearest to Orange, not Yellow** — and `NAME_TOLERANCE` is 0.22, so nothing is returned.
Raising the tolerance to ~0.25 to make it nameable would name a *yellow* highlight **"Orange"**, and
the example in our own documentation is "redact everything highlighted in orange". A reviewer marking
up in Edge's default would have their highlights destroyed by an agent acting on somebody else's
instruction. The current `null` is the safe answer; the defect is that the docs promise a workflow it
cannot serve.

Three options were tabled, none free: **(a)** report the nearest name *with its distance*
(`color_near: {name: "Orange", distance: 0.243}`) and let the caller set its own threshold — honest,
but pushes a judgement onto every caller; **(b)** document that colour filtering covers marks this
app wrote, and point foreign-mark callers at the raw `color` — cheapest, and narrows an advertised
capability; **(c)** give `get_annotations` a `color_near` *filter* taking an RGB and a tolerance —
most useful, most work. **Not** a wider `NAME_TOLERANCE`.

**Resolved 2026-08-25 by none of them.** The owner's observation collapsed the problem: *"the
default color is yellow in KlarPDF (and it is also in Edge)"* — two tools independently shipping a
default they both call **yellow**, and our own naming function calling one of them orange. That
reframes the finding. Every option above is a way to work around a wrong answer; the answer is
simply wrong, and it is wrong for a reason that is fixable.

**The defect is the metric, not the tolerance.** `_distance` was plain Euclidean RGB, which weights
a blue-channel difference exactly as heavily as a green one. But blue is what makes a yellow look
**pale**; the split between "yellow" and "orange" is carried almost entirely by **green**. Edge's
yellow differs from ours mostly in blue (0.4 against 0.10) and only a little in green (0.94 against
0.86) — so a metric that overweights blue reads a *paler yellow* as a *different hue*. Measured:

| | → our Yellow | → our Orange | names it |
| --- | --- | --- | --- |
| plain Euclidean (was) | 0.311 | **0.243** | Orange ❌ |
| BT.709 luma-weighted (is) | **0.106** | 0.189 | Yellow ✅ |

So `nearest_name` now measures with `_perceptual_distance`, weighting the channels by the **ITU-R
BT.709 luma coefficients** (0.2126 / 0.7152 / 0.0722) — the standard sRGB weighting, chosen because
it is a published constant rather than a value tuned until this one case passed. `NAME_TOLERANCE`
recalibrates 0.22 → **0.12**, keeping its original invariant exactly: the ceiling sits just under
the closest pair of swatches *under the metric actually in use* (Yellow to Orange, 0.127). Acrobat's
default red still names `Red` (0.110). A test pins the invariant rather than the number, so
adding a swatch that crowds another cannot silently widen naming.

**`_distance` stays, unchanged, for `is_palette_color`.** That function asks a different question —
"did this value round-trip through PDF floats from one of our swatches" — where the error is uniform
noise on every channel and every metric agrees. Perceptual weighting there would be wrong for the
same reason plain distance was wrong here: the metric has to match the question.

**What this does *not* fix**, and it is the part that keeps M113.8(a) alive: it is a better answer,
not a claim about intent. A mark whose colour genuinely sits between two swatches still gets the
nearer name and `color_exact: false`, and the caller is still told to show the user what matched
before acting on it.

**M113.8 — two documentation gaps TC-012 raised and nobody logged.** *(a)* The report's "Colours"
section notes that **the line and highlight palettes differ for the same name**, and
`klarpdf://docs/get_annotations` still lists all seven in one breath — "Yellow, Green, Blue, Pink,
Orange, Red, Black" — as though they were one palette. Measured, the overlap is not a nuance:

| name | highlight | line | distance |
| --- | --- | --- | --- |
| Blue | `0.55, 0.80, 1.00` | `0.13, 0.35, 0.85` | **0.634** |
| Green | `0.55, 0.92, 0.45` | `0.13, 0.60, 0.20` | **0.584** |

against a `NAME_TOLERANCE` of 0.22 and a Yellow-to-Orange gap of 0.244. A caller filtering
`color_name == "Blue"` across mixed mark types collects two colours further apart than any two
swatches within either palette — the same class of mistake M113.7 is about, arriving from the
opposite direction. *(b)* The **refusal to write over the input** is documented only in the
server-level instructions; TC-012 recorded it as "not a defect… because it is a question a caller
will ask", and `annotate`'s own description does not answer it.

**As built.** *(a)* `klarpdf://docs/get_annotations` now separates the two palettes explicitly and
says to filter on `type` as well when the distinction matters. **M113.7's reweighting does not close
this and slightly sharpens it**: the two Blues are 0.417 apart under the new metric and the two
Greens 0.346 (down from 0.634 / 0.584, since the metric is smaller-scaled throughout) against a
tolerance of 0.12 — still more than three times the ceiling, so both remain nameable as "Blue" and
"Green" while being plainly different colours. Two tests pin exactly that, because it is the one
property a future palette edit could quietly break. *(b)* Both the no-overwrite refusal and M113.9's
rename-into-place are in `klarpdf://docs/annotate` under "Where the output appears, and when" —
one section, because they are the same fact about the write path seen from two sides.

**M113.9 — a caller polling the output path sees nothing until the call finishes** (TC-012
FINDING 2). Writes go to a temp file in the output directory and are renamed into place at the end.
That is correct — a crash cannot leave a half-written PDF where the caller expects a good one — and
it is documented nowhere, so a caller watching `out` reasonably concludes the call failed. The
report filed it as "compounds FINDING 1", which **is no longer true**: the write that prompted it ran
for minutes and the same call now takes ~2 s (M110). What is left is one sentence naming the
behaviour, and the note that its severity was borrowed from a problem that has since been fixed.

**For reference, the app's own defaults** (`model/markup_palette.py`, `main_window.py:162`):
highlight opens on **Yellow** `(1, 0.86, 0.10)`, underline and strikeout each open on **Red**
`(0.86, 0.10, 0.10)`, the redline convention — sticky per session since M78.5, independent per type.

*This paragraph used to end:* "Edge's yellow is 0.311 from ours, which is further than our Yellow
sits from our own Orange (0.244): two tools' 'default yellow' are not the same colour, and no
tolerance can make them one without colliding with a neighbouring name." **The last clause was
false, and worth leaving on the record as the shape of the mistake.** It is true that no *tolerance*
could fix it — and that was read as "nothing can", which closed the question and sent the design
toward three workarounds. The premise never examined was the **metric** producing those two numbers.
Under BT.709 luma weighting the same two colours are 0.106 and 0.189 apart, the ordering is correct,
and the collision the sentence predicted does not occur. Both defaults really are called yellow by
the people who chose them; it was our arithmetic that disagreed. The lesson generalises past
colour: **when every option on the table is a workaround, check the measurement the problem was
stated in.**

### M114 — a mark on one page rewrites all 572 (TC-012 retest, found 2026-08-22)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M114** Stop re-serialising every content stream on a save that did not change page content | `write_options` / `save_options` in `model/edit_engine.py` — `clean=True`, present since M1 and never justified in writing; the write mode becomes the second fork on the axis §M110 opened | WSL + Windows | A one-mark `annotate` on a 572-page document leaves 572/572 content streams byte-identical; Poppler extracts every untouched page identically; the corpus shows no file growing and nothing failing that `clean` was silently protecting. For the additive branch: the predicate refuses every non-additive edit kind (whitelist, unknown kinds denied) and `tests/test_redaction_orphans.py` still passes |

**From the TC-012 retest (2026-08-22), which was right about the half we did not look at.** M110 fixed
the cost — the retest measured "roughly a 10× speed-up" and the call returning inline — but reported
that the second half of the finding was untouched: *"every one of the 572 content streams is still
re-serialised, including on a one-mark call that touches a single page."* Re-run against the merged
code, that is exactly right: **572 of 572**, for a single highlight on page 337.

**The cause is `clean=True`, and nothing else.** Isolated by saving the same one-mark edit five ways:

| save | time | size | streams changed |
| --- | --- | --- | --- |
| `garbage=2 deflate clean use_objstms` (ours) | 2.89 s | 9,311,842 | **572 / 572** |
| the same without `clean` | 1.36 s | **8,834,048** | **0 / 572** |
| no `clean`, no `deflate` | 1.33 s | 12,219,287 | 0 / 572 |
| `garbage=0`, nothing else | 1.32 s | 13,339,118 | 0 / 572 |
| copy the file, then `incremental=True` | 0.05 s | 9,017,093 | 0 / 572 |

Through the real `annotate` pipeline rather than a raw probe, dropping `clean` gives **0/572 streams
changed, 1.85 s → 0.70 s, and 9,311,702 → 8,833,918 B** — smaller than the 9,015,879 B source, where
with `clean` the output was larger than it.

**What `clean` actually does**, shown on page 1 of that document — a page carrying no mark at all:

```
source                 93,544 B   /Artifact BMC /GS5 gs\r\n0.0784 0.4 0.525 rg\r\n3.36 737.02 …
saved with clean=True 112,322 B   /Artifact BMC q/GS5 gs .0784 .4 .525 rg 3.36 737.02 192.77 11.40…
saved with clean=False 93,544 B   byte-identical to the source
```

It sets **both** of MuPDF's `do_clean` and `do_sanitize` flags (one PyMuPDF keyword, two MuPDF
options), which re-parse every content stream and re-emit every operator: numbers are rewritten
(`0.0784` → `.0784`, and elsewhere `11.4` → `11.400024`), whitespace is normalised, and a `q` is
inserted. The retest saw exactly this and called it "more invasive"; it is right, and the stream
grows 20% on that page as a result.

**The retest's Poppler finding is real — this entry was wrong to doubt it, and wrong about why.**
An earlier version of this paragraph said the consequence "does not reproduce", on the strength of
`pdftotext` 24.02.0 finding **0 differing pages** on `dhariwal_ipo.pdf`. That measurement was
correct and the conclusion drawn from it was not: the document was simply not one of the ones it
happens to. Run across the 56-document corpus, `clean` changes the text Poppler extracts on **three**
of them — `Invoice-6KNSJA3E-0001.pdf`, `xfinity_march_2026.pdf` and one bank statement — and in
exactly the shape the retest described, pure re-ordering:

```
Invoice-6KNSJA3E-0001.pdf, source vs saved-with-clean
  @@ -36,0 +37,4 @@   +Jul 17–Aug 14, 2026  +Subtotal  +Total  +Amount due
  @@ -50,5 +53,0 @@   -Jul 17–Aug 14, 2026  -Subtotal  …
```

Same content, thirteen lines earlier. **Saved without `clean`, all three are byte-identical to the
source again.** So this is not a build difference and not an environment quirk: it is what
sanitising does, on documents whose operator order it happens to disturb. It also raises the stakes
of the milestone from cost to **fidelity** — anything consuming extracted text (a search index, a
screen reader, a diff, an agent reading the PDF) sees a reading order the source did not have.

The lesson for the entry, not just the code: one document returning "no difference" is evidence
about *that document*. It was generalised into a claim about the finding, and it took a corpus to
notice. The tester was right.

**The build difference is separate and still open.** The same controlled edit adds **+296,142 B**
here against the **+52,615 B** the report measures — a 5.6× gap on one highlight, which two
measurements of the same operation should not show. §M115 found and fixed one version drift (the
bridge's lock was on a different PyMuPDF from the app's) and that may be the whole of it; worth
asking the tester for `pymupdf.__doc__` from their harness to close it.

**M110 measured `clean` and cleared it — of the wrong charge.** §M110 records "`clean=True` … is
**not** implicated: measured at ~1.9 s". That was about the 202-second hunt, and it was true. Nobody
asked what else it did.

**Not a one-line change, and the corpus decided it.** `clean` has been in the save since M1 with no
recorded reason, which is not the same as having none: it sanitises content streams, and this project
*does* rewrite content — `apply_redactions` rewrites a page, the R4 content marks append streams to
one. The hypothesis was to keep it where we rewrite content and drop it where a save only copies
pages through. It got the treatment M110 got: all **56 corpus source documents** saved both ways
through the real pipeline (`write_options` patched, not reimplemented — a second copy of the literal
is the drift M111 was about), comparing size, time, per-page content streams, PyMuPDF text, Poppler
text and the catalog M93 taught us to check.

| | with `clean` (today) | without |
| --- | --- | --- |
| content streams left byte-identical to the source | **324 / 1,315 pages** | **1,315 / 1,315** |
| corpus save time | 10.9 s | **3.3 s** (−70%) |
| documents ending up **larger than their own source** | **3** | **1** |
| documents whose Poppler text differs from the source | **3** | **0** |
| total corpus bytes | 173,292,138 | 173,394,925 (+102,787) |

**The one cost, stated plainly:** 42 of 56 documents come back slightly *larger than today's save*
(`kasaragodhr.pdf` +515,744 B on a 29 MB source — 1.8% — is the worst; `IAS_CaseStudy.pdf` +30,307 B
on 75 MB is 0.04%), while 13 come back smaller (`dhariwal_ipo.pdf` −477,800 B). That sounds like a
regression and is not the promise this project makes. **The promise is that a save hands back
roughly what it was given** (§M110, `GARBAGE_COPY`) — and by that measure dropping `clean` is
strictly better: documents that end up bigger than the file the user opened go from three to one.
Cleaning was buying a slightly smaller output by rewriting content the user did not ask us to touch.

**What was *not* measured, and is kept anyway.** The corpus says nothing about the redaction path or
R4 content marks, because no corpus document exercises them at save time. Those writes rewrite page
content themselves, which is the original hypothesis for why `clean` was ever there — so they keep
it (:data:`CLEAN_REWRITTEN`), and the two exports keep it for the same reason: `bake()` draws
annotations into the page and `rewrite_images` re-encodes what it draws. Dropping it there too would
be tidier and is not supported by evidence, which is the wrong reason to change a save.

**Microsoft Edge sets the target, and it is not a hypothetical** (TC-012 cross-check, verified
here against the files). The same 9 MB / 572-page source, given **one highlight**, then Edge's own
mark read back through `get_annotations` and replayed through `annotate` as the *identical* edit —
same page, same six boxes, same `[1, 0.9412, 0.4]`, same type:

| | Edge | klarpdf, identical edit |
| --- | --- | --- |
| Bytes added | **+2,680** | **+296,142** |
| Source bytes preserved | **100%** — the first 9,015,879 B are byte-identical | diverges after 8 B |
| Content streams changed | **0 / 572** | **572 / 572** |

Edge writes a standard **incremental update**: the original bytes are left alone and 2,680 bytes are
appended — a new version of the page-1 object with its `/Annots`, the `/Highlight` and its
`/QuadPoints`, an appearance stream, a `/Popup`, and a new xref section. Every other page is
byte-identical *by construction*. This is what the PDF format provides for exactly this case.

**So incremental writing is not rejected — it is re-scoped.** An earlier draft of this entry rejected
it outright because it *appends*, leaving the previous revision recoverable inside the file, which on
the redaction path leaks precisely what a redaction promises to destroy. That reason is real and
stands **for the redaction path**; it is not a reason to rewrite 9 MB when the edit only adds an
annotation, and the tester is right to say so.

**The write mode is one decision, not two knobs.** Measured against PyMuPDF 1.27.2.3 / MuPDF 1.27.2,
an incremental write refuses garbage collection outright — so the cleanup level and the write mode
are the *same* choice at different granularity, and cannot be picked independently:

| `Document.save` keywords | result |
| --- | --- |
| `incremental=True, garbage=0, encryption=PDF_ENCRYPT_KEEP` | **OK** |
| `incremental=True, garbage=2` | `FzErrorArgument: Can't do incremental writes with garbage collection` |
| `incremental=True, garbage=0`, PyMuPDF's default `encryption=PDF_ENCRYPT_NONE` | `FzErrorArgument: Can't do incremental writes when changing encryption` |
| `incremental=True, garbage=0, clean=True` | OK, but writes 33% more than without `clean` — it defeats the point |
| `incremental=True, garbage=0` + `use_objstms=1` / `deflate=True` | OK |

That makes this the **second fork on the axis §M110 opened**, rather than a new mechanism beside it:

| what the edit set contains | route | write |
| --- | --- | --- |
| the page set changed | graft | `GARBAGE_GRAFT`, `clean`, full write |
| page set unchanged, any non-additive edit | copy of the origin | `GARBAGE_COPY`, full write |
| page set unchanged, **provably additive only** | copy of the origin | `garbage=0`, no `clean`, **incremental** |

Note where the third row sits: `garbage=0` is **below the redaction floor** that §M110 and
`save_options` both document — level 1 is what deletes an image a redaction detached from its page,
and `tests/test_redaction_orphans.py` pins it. So excluding redaction from the additive predicate is
required *twice over*, by the leak and by the floor.

**The predicate is smaller than an earlier draft of this entry claimed.** That draft said "today's
save path cannot tell those apart". It can, or very nearly: `save_options` is already one line asking
the model a question about the edit set (`GARBAGE_COPY if vdoc.page_set_unchanged() else
GARBAGE_GRAFT`), and two of the sub-questions already exist as methods written for exactly this kind
of reasoning — `VirtualDocument.has_redactions()` and `has_content_marks()`, which today gate the
commit-and-reload decision in `main_window`. The rest are one-line reads of immutable model state:
`ref.rotation_override`, `ref.crop_override`, `form_values`, `_metadata_override`,
`_encryption_staged`, and `ForeignDeletion` / `ForeignMove` among a page's annotations. The accurate
statement is that the save asks **one coarse question where it needs a finer one**, and the state to
answer the finer one is already in the model.

**It has to be a whitelist.** Additive *iff* every edit is one of a named set of known-additive
kinds, with an unrecognised annotation kind defaulting to non-additive — so the next descriptor added
to `model/page_edits.py` is safe on the day it lands rather than on the day someone remembers this
paragraph. The failure modes are not symmetric: too conservative costs a full rewrite, which is what
we do today; too permissive appends over a redaction and leaves the original bytes in the file.

**Two real obstacles — and the one the earlier draft named turns out not to exist.**

*Not an obstacle.* That draft said `_apply_page_edits` "strips and re-adds KlarPDF annotations on
**every** page, which would dirty 572 page objects even when one is marked", and that the pass would
have to be scoped. Measured by running the real pass and then taking an incremental save — whose
appended bytes are exactly the objects MuPDF considered dirty — on a 60-page document:

| what ran | appended |
| --- | --- |
| `_apply_page_edits` with no edits at all | **+0 B** |
| one highlight on page 30 | +890 B |
| highlights on pages 0, 30, 59 | +2,184 B |
| re-save of a file already carrying one baked KlarPDF mark | +918 B |

The pass is **already scoped in effect**: `strip_klarpdf_annotations` returns without touching a page
that carries no KlarPDF annotation, and reading `rotation_override` / `crop_override` off an
untouched page does not dirty it. Its docstring in `model/page_edits.py` says so and was right;
the entry was wrong. Nothing needs scoping here.

*Real obstacle 1 — the copy route builds its output in memory, so it cannot save incrementally at
all.* `VirtualDocument.fresh_source` opens via `fitz.open(stream=…tobytes(…))`, and PyMuPDF refuses a
stream-opened document: `ValueError: incremental needs original file` — MuPDF needs the document
opened *from the file being appended to*.

**Neither surface writes to the original file, and that is the point.** An earlier framing of this
obstacle said "the output goes to a *new* path", which reads as a `Save As` detail and is why the
obstacle looked bigger than it is. It is not surface-specific: **every** save, on **both** surfaces,
materialises into a fresh temp file beside the target and then renames it into place —
`MainWindow._write_to` (`mkstemp` → `materialize` → `atomic_replace`) and `mcp_bridge/transforms.py`
`_write`, which is the same shape by design (*"the two write paths deliberately do not diverge on
this"*, M38.5). The bridge additionally **refuses** to write over its input as policy
(*"transforms always write to a new file"*). So an app `Save`, an app `Save As` and an `annotate`
call are all the same case, and there is no in-place write anywhere to reason about separately.

That dissolves the obstacle rather than deepening it. **Seed the temp by copying the origin instead
of creating it empty**, open *that file*, append to it, and `atomic_replace` as before — atomicity is
preserved, the bridge's no-overwrite rule is untouched, and `Save`'s in-place semantics are untouched.
Measured end to end on a synthetic 572-page, 3.46 MB document, one highlight on page 337:

| | today (empty temp + full materialize) | copy-seeded temp + incremental |
| --- | --- | --- |
| time | 0.37 s | **0.03 s** |
| bytes added | full rewrite | **+1,189** |
| source prefix preserved | — | **all 3,456,976 B byte-identical** |
| the copy itself | — | 2 ms |

The mark reads back on page 337 and no other sampled page carries one. Because both surfaces funnel
through `PyMuPDFEngine().materialize(vdoc, path)`, the change lands in **one place and serves both**.

What remains genuinely open here is encryption, not paths: `fresh_source` round-trips through
`tobytes(encryption=PDF_ENCRYPT_KEEP)` for M54's sake (a document decrypted at open), so seeding from
the origin *file* has to re-supply the recorded password — and incremental requires
`encryption=PDF_ENCRYPT_KEEP` passed explicitly, so a save that *changes* encryption can never take
this branch. The predicate already excludes that via `_encryption_staged`.

*Real obstacle 2 — every save re-writes the document's title and author, even when nobody edited
them.*

A PDF keeps document metadata — title, author, subject, dates — in **two** places: the old **Info
dictionary**, a plain key/value store, and the newer **XMP packet**, an XML blob saying the same
things. Different viewers read different ones, which is why an *edit* has to write both (M53).

`apply_metadata` has three branches: metadata removed, metadata edited, and metadata untouched. The
untouched branch copies the origin's two stores onto the output:

```python
else:
    out.set_metadata({k: v for k, v in vdoc.origin_metadata.items() if k in INFO_KEYS})
    if vdoc.origin_xmp:
        out.set_xml_metadata(vdoc.origin_xmp)
```

**That branch was written for the graft, and only the graft needs it.** The graft builds a new
document with `insert_pdf`, which copies pages and neither metadata store — without the pass the
title and author simply vanish (that is M53). The copy route instead starts from a *copy of the
origin*, which already carries both. Measured on the same file:

| the route's starting point | Info title | XMP packet |
| --- | --- | --- |
| `fresh_source()` — the copy route | `'Original Title'` | present, **byte-identical to the origin** |
| `insert_pdf` into a new document — the graft | `''` | **gone** |

So on the copy route the pass writes back precisely what is already there. It is a no-op in
*meaning* and not a no-op in *bytes*: PyMuPDF marks both objects changed and re-serialises them.

**Today that costs nothing visible, because the save rewrites the whole file anyway.** It stops
being free the moment a save appends only what changed. Measured on a 60-page document with a
3,093-byte XMP packet and metadata the user never touched, the pass turns a **901 B** incremental
append into **4,249 B** — 3.3×, and on its own larger than the 2,680 bytes Edge writes for the
entire edit.

The fix is a guard: on the copy route, skip the pass when `metadata_override is None`. Worth doing
whatever is decided about incremental writing.

**If this is declined, one sentence is still owed.** TC-012's own fallback: *"If a full rewrite is
structurally required, say so in `klarpdf://docs/annotate` so callers size their expectations."* That
holds whatever is decided here — a caller diffing two versions of a document, or feeding one to a
search index, needs to know that adding a highlight rewrites every page. Declining the work does not
close the finding; it converts it into a documentation item. The same sentence also disposes of the
report's "Informational — text re-grouping on untouched pages" note, which is this cause seen from
the extraction side.

**What shipped (lever 1).** `write_options` takes `clean` as a **required** keyword beside `garbage`
— required rather than defaulted, because a defaulted option reaching only some call sites is
exactly how M111 happened. `save_options` decides it from the model, next to the route decision but
on a *different* question: the route asks who copied the objects, `clean` asks whether this write
rewrote page content, and the two do not split the same way. Both sub-questions were already
answered by `VirtualDocument.has_redactions()` / `has_content_marks()`, the same pair
`MainWindow._write_to` uses to decide whether a save is a point of no return. The `apply_metadata`
skip landed with it: the copy route now calls that pass only when `metadata_override is not None`.

Measured on the milestone's own acceptance case — one highlight on page 337 of the 572-page,
9,015,879 B prospectus, through the real `annotate` path:

| | before | after |
| --- | --- | --- |
| content streams byte-identical | 0 / 572 | **572 / 572** |
| time | 1.85 s | **0.66 s** |
| output size | 9,311,702 B (**larger** than the source) | **8,834,064 B** (−181,815 vs source) |
| Poppler text vs source | — | **identical** |

**One behaviour changed that nothing asked for, and it is a gain.** A plain save no longer renumbers
objects, so a *foreign* annotation keeps the xref the tool that wrote it recorded — an external
reference to it (a review database keyed on object number, a comment exported from Acrobat) still
resolves after a round-trip. `tests/test_foreign_annots.py` caught this by failing: it asserted
"xrefs really do change", which had been true of every save and is now true only of the graft. The
fingerprint machinery is still required — the graft renumbers — so the test was narrowed to the
route where the premise holds, and the new guarantee pinned beside it.

**One measured non-finding, recorded so it is not chased again.** Saving either of the two
user-password AES-256 statements upgrades the encryption revision `Standard V5 R5` → `R6`. It
happens with `clean` and without, so it is not this milestone's doing, and it is not a defect: R5 is
Adobe's withdrawn AES-256 revision and R6 is the ISO 32000-2 one. Same 256-bit AES, standard
revision.

**The second lever became §M116, and shipped.** It was written up here and nowhere else, which made
it invisible the moment M114 was ticked — a completed milestone's prose is not a backlog. The
analysis below stays here, because it is M114's and belongs with the measurements that produced it;
what was built from it, and what it cost, is **§M116**. The predictions in this section held: the
copy-seeded temp, the whitelist, and encryption as the only genuinely open question.

**Two levers — and the second subsumes the first on its own branch.** Dropping `clean` takes content
streams from 572/572 to 0/572 and the call from 1.85 s to 0.70 s, but still writes a complete 8.8 MB
file; it is small, and it helps **every** save in the app and the bridge, including every save the
additive predicate will refuse. Incremental writing is what closes the remaining distance to Edge's
2,680 bytes, and on that branch `clean` is off by construction (it is permitted there, but writes
33% more for nothing). So these are not two options to choose between: the first is the floor for
every save, the second a further step for the additive case only.

**The retest's timings do not reproduce, and the difference matters for how this is scoped.** It
reports 12.6 s for 11 marks and 7.6 s for one. Measured directly on the merged code, `annotate` is
**1.83 s** for the same eleven and **1.85 s** for one — and writing to `/mnt/c` rather than `/tmp`
accounts for 0.2 s of that, not six seconds. What *is* document-proportional in that workflow is the
`search` that locates the marks: **6.34 s** on this document, which is the already-carried
"a search is still one uninterruptible pass over every page". So the remaining defect is the rewrite,
not the clock.

### M115 — the app and the bridge were writing PDFs with different engines (found 2026-08-23)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M115** Declare the core's PDF engine in **one** place, so the two surfaces cannot resolve it differently | new `requirements-core.in` (the exact pin), `-r`-included by `requirements.in` and `requirements-mcp.in`, which no longer name PyMuPDF at all; `requirements-mcp.txt` / `requirements-dev.txt` / `packaging/mcpb/pyproject.toml` recompiled; `tests/test_mcp_packaging.py` + `tests/test_mcp_no_qt.py` | WSL + Windows | Every lock names one PyMuPDF; reintroducing the drift, or floating a shared library back to a floor, fails the suite; the bridge still reaches neither Qt nor pypdf with every tool exercised |

**Found while preparing §M114, which is entirely about what the engine writes.** The shipped app
pins `pymupdf==1.27.2.3`; the bridge's lock had `pymupdf==1.28.2`. PyMuPDF is not one dependency
among many here — it *is* the PDF engine, and `model/` hands it every read and write both surfaces
make. Two versions can write different bytes for the same edit, which makes a corpus measured on one
a poor description of the other.

**It was a drift, and the mechanism was named wrong.** Both inputs asked for the same thing:

```
requirements.in       PyMuPDF>=1.25.5
requirements-mcp.in   PyMuPDF>=1.25.5
```

`>=` is a **floor**, not a pin, and `pip-compile` resolves a floor to whatever was newest on the day
it ran. The app's lock was compiled at 1.27.2.3 and is bumped by hand afterwards (`RELEASE.md` §2 —
that is how the `pypdf` security bump was done); the bridge's was recompiled during M42, later, and
floated. The commit that moved it is the same one that added the comment promising it could not
happen:

```
commit 4043417  "M42: MCP dependency lock + packaging"
  requirements-mcp.txt   -pymupdf==1.27.2.3
                         +pymupdf==1.28.2
  requirements-mcp.in    +# PyMuPDF is pinned to the same floor as the app so the bridge
                         +# and the GUI cannot drift onto different MuPDF builds
```

*"Pinned to the same floor"* is the error in one phrase: a floor is the opposite of a pin, and
sharing one guarantees nothing about what either side resolves to.

**This is the §M114 "one core, two consumers" rule with the version underneath it.** `CLAUDE.md`
now asks that every change answer for both surfaces; that is worth little while the two do not agree
on the library the core is built on. It also plausibly explains the open TC-012 discrepancy — the
report measures 52,615 B added for one highlight where we measure 296,142 B for the identical edit,
a 5.6× gap, and a Poppler text difference on 41 pages that does not reproduce here at all.

**The direction is the bridge down, not the app up.** 1.27.2.3 is what the installer bundles, what
the hashed `win_amd64` lock covers and what the clean-machine install test has run against; moving
the app instead is a release-process change (`RELEASE.md` §2, a new hashed lock, that test re-run)
and should be its own decision rather than a side effect of this one.

**The fix is structural: PyMuPDF is declared once, because it belongs to neither surface.** The
owner's question settled the design — *"why does MCP specify its PyMuPDF version separately from the
app? Does it not belong to the core?"* It does. `model/` is what both surfaces share, and every read
and write either makes goes through PyMuPDF, so declaring it in `requirements.in` (as the app's) and
again in `requirements-mcp.in` (as the bridge's) was the bug. Two declarations that must agree is a
hazard; a test that checks they agree is a smoke alarm, not a fix. So:

```
requirements-core.in     PyMuPDF==1.27.2.3          <- the one place
requirements.in          -r requirements-core.in    + PySide6-Essentials, pypdf
requirements-mcp.in      -r requirements-core.in    + mcp
```

The `-r` include is already how `requirements-dev.in` pulls in `requirements.in`, so nothing new is
being invented. Both locks now record `# via -r requirements-core.in`, which makes the shared origin
visible in the generated files.

**The shared file is only half of it — the pin is the other half.** A shared *floor* fails exactly
as two separate floors do, because `pip-compile` resolves `>=` to whatever was newest on the day it
ran. Measured:

| `requirements-core.in` says | one lock recompiled with `-P PyMuPDF` | result |
| --- | --- | --- |
| `PyMuPDF>=1.25.5` | moves to **1.28.2** | still drifts |
| `PyMuPDF==1.27.2.3` | stays **1.27.2.3** | immune even to an explicit upgrade |

**And the old floor was a live hazard, not a historical one.** Compiling the *previous*
`requirements.in` today resolves PyMuPDF to **1.28.2** — so the next routine recompile of
`requirements-win.txt` would have silently moved the shipped app's engine, with no line of the diff
saying so. Under the new structure that recompile returns 1.27.2.3.

**One Windows step is outstanding and is cosmetic.** `requirements-win.txt` is `--generate-hashes`
and `win_amd64`, so it is regenerated on Windows (CLAUDE.md §Gotchas) and is not touched here. Its
pins are already correct — 1.27.2.3, the version this milestone standardises on — and the only line
that changes on the next Windows recompile is the annotation `# via -r requirements.in` →
`# via -r requirements-core.in`. Verified by compiling the old and new inputs side by side: the
resolved set is identical apart from that comment and the PyMuPDF version the old floor now picks up.

**The tests stay, as the backstop rather than the fix — and neither is about PyMuPDF.** The defect
is the *shape*, not the package, so both assert the general invariant:

* `test_the_bridge_and_the_app_never_ship_different_versions_of_a_shared_library` — **every**
  package both locks carry must be at one version, whichever one somebody adds next. It compares
  the **locks**, not the inputs: an input can say anything, a lock is what installs.
* `test_a_library_the_app_also_ships_is_pinned_in_the_bridge_input_not_floored` — the **root cause**
  asserted directly. The first test catches the drift once it has happened; this one catches the
  construction that allows it, which is the part that silently re-arms on the next recompile. Only
  packages the app also ships are constrained; `mcp>=2,<3` is shared with nothing and stays a range.

One existing test had to change with them. `test_the_declared_floors_match_the_locks_input` asserted
`pyproject.toml`'s dependency block and `requirements-mcp.in` were *string-equal* — workable only
while both were floors. They are legitimately different kinds, so it now asserts the same package
**set** plus "the compiled pin satisfies the declared floor", and its parser follows `-r` includes,
without which the shared engine would read as declared by nobody. One generated artefact follows the
lock and was regenerated: `packaging/mcpb/pyproject.toml`, which the suite caught by itself.

**The audit found a second library in the same position, and it was unguarded.** Asked whether
anything *else* belonging to the core is declared per-surface, the answer across `model/`, `viewer/`,
`organize/`, `util/` and `mcp_bridge/` is three third-party imports and no more:

| library | declared by | needed by | state |
| --- | --- | --- | --- |
| **PyMuPDF** | both, separately | `model/`, `viewer/`, `organize/`, `mcp_bridge/` | this milestone |
| **PySide6** | app only | `model/edit_commands.py` (`QUndoCommand`, module level) + all of `viewer/`, `organize/` | correct, and **proven by execution** |
| **pypdf** | app only | `model/edit_engine.py` only, *inside* `PyPdfEngine.materialize` | correct, and **was unproven** |

PySide6 is the model to copy: `tests/test_mcp_no_qt.py` runs every tool in a fresh interpreter and
asserts Qt is nowhere in `sys.modules`, precisely because "an import that only happens inside a tool
body is exactly the one a load-time check would miss". pypdf sits in that same shape —
`model/edit_engine.py` imports it *inside* a method, so the module loads without it and only reaching
`PyPdfEngine.materialize` fails — and its exclusion had nothing asserting it. A bridge user would get
`ModuleNotFoundError: pypdf`; CI would stay green, because CI installs `requirements-dev.txt`, which
carries pypdf for the app. **The same shape as the version drift: what CI runs is not what the bridge
ships.** Closed by adding `pypdf` to that exerciser's leak set — one line, on machinery that already
exercises every tool — and confirmed to fail when a regression is simulated.

**The gap underneath all of it: the bridge's own lock is audited but never run.** CI installs
`requirements-dev.txt` and runs the whole suite, `tests/test_mcp_*.py` included — but that lock
tracks the *app*, so the bridge's tests have only ever executed against the app's PyMuPDF. The
version the bridge actually ships was never the version anything tested. `requirements-mcp.txt` is
covered by the `audit` job (M42's fourth `pip-audit` step), and auditing a lock for advisories is
not the same as running a line of code against it. That is why three months passed. **Closed by
§M115.1.**

### M115.1 — run the bridge against its own lock (2026-08-24)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M115.1** A CI job that installs `requirements-mcp.txt` and runs the bridge suite against it | a second job in `.github/workflows/test.yml`; three `autouse` guards in `tests/conftest.py`; `importorskip` in four tests | WSL + CI | The bridge suite passes under the bridge's own lock; the full suite still passes under the dev lock; a PR that cannot affect the bridge reports the check without doing the work |

**Why a second job rather than a second test.** §M115's two tests compare the locks **as text**,
which catches a version drift and nothing else. They cannot see a *behaviour* difference between two
engines, which is the thing that actually corrupts a document. The only check that can is running
the bridge's code against the bridge's dependencies.

**Required, but gated inside the job.** The relevance decision is a step, not a workflow `paths:`
filter, for the reason G7 already documents on the `pytest` job: a filtered-out workflow never
creates a check run, and a ruleset cannot distinguish "not needed" from "not finished", so the PR
would wait on it forever. A PR touching nothing the bridge can see reports success without doing the
work. `requirements-win.txt` is deliberately outside the trigger — it is the *app's* lock, so it
cannot change what the bridge installs, and the "both locks agree" invariant is already asserted
unconditionally in the `pytest` job.

**What it cost, and the part worth knowing.** The job is 70 lines; the work was elsewhere.
`tests/conftest.py` has three `autouse` fixtures and **all three reach into Qt** —
`_no_real_modals`, `_instant_search` (`viewer/search.py`), `_instant_zoom` (`viewer/pdf_view.py`).
Under a Qt-free lock they error the *setup* of every bridge test, before a single test body runs, so
the first attempt produced a wall of errors that said nothing about the bridge. Each now returns
early on a shared `GUI_INSTALLED`, computed with `importlib.util.find_spec` rather than a
`try: import` so that asking the question does not itself drag ~60 MB of Qt into the interpreter.

**Four tests use dev-only tooling to *verify* bridge behaviour**, and skip rather than fail — the
same arrangement the Poppler cross-engine redaction check has always had:

| test | needs | why it is not a bridge dependency |
| --- | --- | --- |
| `test_the_guard_would_notice_qt` | PySide6 | a negative control: it imports Qt on purpose to prove the leak detector works |
| `test_the_app_find_bar_agrees_with_the_bridge` | `viewer.search` | it compares the bridge against the *app's* find bar |
| `test_merge_renames_colliding_form_fields…` | pypdf | a second-engine cross-check of a merge the test still asserts without it |
| `test_the_built_metadata_declares_dependencies` | setuptools | the build backend, not a runtime dependency |

Measured: **459 passed, 4 skipped** under `requirements-mcp.txt`, and the full suite still green
under the dev lock. The lock installs PyMuPDF 1.27.2.3 and carries neither pypdf nor PySide6, which
is itself a second proof of the quarantine `tests/test_mcp_no_qt.py` asserts from the inside.

**The job is only a gate while the ruleset says so, and that half lives outside the repository.**
Adding `bridge` to **Protect Main** is a GitHub setting, not a file — so nothing in a diff, a review
or a test run can tell you it is still there. Un-tick it and every PR goes green exactly as before,
which is the failure this milestone exists to prevent, wearing a different hat: a check that reports
without blocking is one people learn to scroll past. This project has already been bitten by a
setting drifting away from the paragraph describing it — `RELEASE.md` §2 carries a verification
command for the two Dependabot toggles for that reason, after they disagreed with their own policy
for a month. Same treatment here:

```sh
gh api repos/utyagi24/klarpdf/rulesets --jq '.[] | select(.name=="Protect Main") | .id' |
  xargs -I{} gh api repos/utyagi24/klarpdf/rulesets/{} \
    --jq '[.rules[] | select(.type=="required_status_checks")
           | .parameters.required_status_checks[].context] | join(", ")'
# -> pytest, emails, bridge
```

Confirmed enforced **2026-08-24**. The first PR to exercise the gate proved both halves on the same
day: `bridge` ran in **38 s** on the code branch that touches `mcp_bridge/` and `model/`, and
reported in **4 s** without doing the work on the docs-only branch stacked above it.

**One gap is left open deliberately.** `pyproject.toml` keeps a *floor*, raised to 1.27.2.3, because
an exact pin in package metadata conflicts with whatever a user co-installs. So the `pipx install .`
path the README documents still resolves to the newest PyMuPDF rather than ours. Carried in
`PROGRESS.md` §Open follow-ups rather than decided here.

### M116 — adding a highlight appends 1,865 bytes instead of rewriting 8.8 MB (2026-08-24)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M116** A save that only *adds* marks appends to the file it was given instead of rewriting it — §M114's second lever | `PyMuPDFEngine.appends` / `_append_to_origin` / `append_options` in `model/edit_engine.py`; `VirtualDocument.edits_are_additive` / `origin_bytes` / `origin_needed_repair`; the `ADDITIVE_MARK_TYPES` whitelist in `model/page_edits.py`; `save_size` for the Reduced-Size baseline | WSL + Windows | One highlight on page 337 of the 572-page prospectus appends **1,865 B** and leaves all 9,015,879 source bytes byte-identical; the predicate refuses every non-additive edit kind and `tests/test_redaction_orphans.py` still passes; no bridge transform's output changes |

**§M114 fixed what the save re-serialised; this is what it still rewrote.** After M114 a one-mark
save left all 572 content streams byte-identical and took 0.66 s — and still wrote a complete 8.8 MB
file. Edge, given the *identical* edit, appends 2,680 bytes and leaves the first 9,015,879 untouched,
because it writes a standard PDF **incremental update**. That remaining distance is this milestone,
and it closes with room to spare.

| | source | today (M114) | **M116** | Edge |
| --- | --- | --- | --- | --- |
| bytes written | 9,015,879 | 8,834,416 (−181,463) | **9,017,744 (+1,865)** | 9,018,559 (+2,680) |
| source bytes preserved | — | diverges after 8 B | **all 9,015,879** | all 9,015,879 |
| content streams changed | — | 0 / 572 | **0 / 572, by construction** | 0 / 572 |
| time | — | 0.71 s | **0.10 s** | — |

"By construction" is the difference that matters. M114's 0/572 was a *measurement* — the streams were
re-serialised identically. Here pages 1–336 and 338–572 are not written at all: they are the same
bytes in the same file, and a second engine extracting text from them cannot see a difference
because there is none to see.

#### The route

It is the **second fork on the axis §M110 opened**, not a new mechanism beside it. §M110's route
question is *who copied the objects*; this one is *whether anything in the file needs to change at
all*.

| what the edit set contains | route | write |
| --- | --- | --- |
| the page set changed | graft | `GARBAGE_GRAFT`, `clean`, full write |
| page set unchanged, any non-additive edit | copy of the origin | `GARBAGE_COPY`, full write |
| page set unchanged, **provably additive only** | the origin's own bytes | `GARBAGE_APPEND`, no `clean`, **incremental** |

**The obstacle §M114 identified dissolved exactly as it predicted.** MuPDF appends only to the file a
document was opened *from* — `ValueError: incremental needs original file` for anything opened from a
stream or saved to a second path — and this project never writes to the file it opened: both surfaces
materialise into a temp beside the target and rename it in (`MainWindow._write_to`,
`mcp_bridge/transforms._write`, deliberately the same shape, M38.5). So the temp is **seeded with the
origin's bytes** and appended to. Atomicity is untouched, the bridge still never writes over its
input, and `Save`'s in-place semantics are unchanged. The seed costs **18 ms** on a 9 MB document.

**Where the seed comes from is a correctness question, not a plumbing one.** It is the bytes captured
in `open_source` (`VirtualDocument.origin_bytes`), not a re-read of the path at save time. The file on
disk can have moved on since — an external editor, a sync client — and appending this session's marks
to pages nobody has looked at is how a save quietly ships somebody else's document. Keeping them
costs nothing: `fitz.open(stream=…)` holds a reference to that same object, so the dict stores a
pointer, and `tobytes()` cannot substitute because it re-serialises rather than handing the original
back. `tests/test_incremental_save.py` replaces the file under a live model and asserts the output
still carries the pages that were opened.

#### The predicate, which is the whole risk

An append leaves the previous revision inside the file, recoverable by anything that reads a PDF
properly. Harmless for a highlight; a betrayal for a **redaction**, whose entire promise is that the
content is gone. So `edits_are_additive` is a whitelist, and the failure modes are not symmetric —
too conservative costs a full rewrite, which is what every save did yesterday.

True requires: the page set is unchanged; every mark is one of `ADDITIVE_MARK_TYPES` (exactly the
seven kinds `apply_annotations` draws, each of which adds one annotation object and touches nothing
else); **every mark the pages arrived carrying is still there**; and no rotation, crop, form fill,
metadata edit or staged encryption change. `appends()` adds what the *file* has to allow: bytes to
seed from, no repair on open, and no encryption change.

**"Nothing may be taken away" is the half an earlier sketch of this would have missed.** Removing a
mark is not additive, because the removed one stays in the previous revision — and *editing* one is
removing one, so a text box the user emptied and saved would still carry its old wording. The
baseline for the comparison is captured in `_seed_ordered`, which already reads exactly this. It also
disposes of a case nobody would have thought to check: the bridge's `annotate` (M101) **merges**
overlapping markup, and the survivor replaces the marks it absorbed — refused, without the predicate
knowing anything about merging.

What it deliberately does *not* ask is whether the origin already carries KlarPDF marks. It usually
will — a document annotated last week, opened again, given one more highlight is still purely
additive, and refusing that case would send the commonest markup session back to the full rewrite
every time after the first.

**Redaction is excluded twice over, on independent grounds.** By the leak above, and by
`GARBAGE_APPEND = 0` sitting **below** the orphan floor `tests/test_redaction_orphans.py` pins — level
1 is what deletes an image a redaction detached from its page. The level is not a choice: MuPDF
refuses an incremental write with any collection at all.

**MuPDF's refusals are a closed set, which is why nothing falls back.** Measured on 1.27.2.3, an
incremental save is refused for exactly four reasons: garbage collection, an encryption change (and
PyMuPDF's *default* `PDF_ENCRYPT_NONE` counts as one, on a plain unprotected PDF — `PDF_ENCRYPT_KEEP`
is passed explicitly for that reason), a stream-opened document, and a **repaired** file, whose
rebuilt cross-reference offsets an update cannot chain onto. Each is closed by the predicate or by
construction. A fallback to the full rewrite would turn a defect in the predicate into silence:
correct output, no error, and nothing but a byte count to notice it by. The measurement that stands in
its place is the corpus, run through `materialize` itself rather than a copy of it.

#### Verified on the corpus (82 documents, one highlight each)

| | result |
| --- | --- |
| took the append route without raising | **82 / 82** |
| whole source file byte-identical at the front of the output | **82 / 82** |
| exactly the one mark, on its page, none anywhere else | **82 / 82** |
| page content streams changed, corpus-wide | **0** |
| catalog unchanged — `StructTreeRoot`, `MarkInfo`, `Perms`, `Names`, `AcroForm`, encryption, permissions | **82 / 82** |
| Poppler extracts identical text | **82 / 82** |
| pypdf parses the output | **82 / 82** |
| total time | **0.66 s** against 2.42 s for the same edits rewritten |

Median append: **1,827 B**. The encrypted documents are the interesting half — an owner-password file
opens without a password and is never decrypted, so its own bytes are the seed and `PDF_ENCRYPT_KEEP`
carries the encryption dictionary and the permission flags through untouched, which is M93's promise
obtained for free. A **user-password** document is refused twice over: its source is stored decrypted
(M32) so the file's bytes are not the model's document, and the save re-encrypts from that copy (M54),
which an append may not do.

**The one cost, stated plainly.** Across those 82 documents the append adds **+296,011 B** in total
where the full rewrite *removes* 3,874,410 B — today's save shrinks an average corpus file by ~47 KB
because it re-serialises more tightly than the tool that wrote it, and the append leaves it exactly as
it found it and adds the mark. That is the §M110 promise in its strongest form (*"a save hands back
what it was given"*), and **Reduced-Size PDF** remains where a smaller file is asked for. The related
consequence: an append cannot drop unreferenced objects, because it may not collect at all — but
those objects are the ones the file arrived with, and the one route that *creates* an orphan is the
redaction this predicate refuses.

**A save with no edits at all is now a copy.** MuPDF appends only what it considers dirty, and a
document nobody edited has nothing: **+0 bytes**, 0.04 s, output byte-identical to input. That also
settles what §M114 measured about `_apply_page_edits` — the pass is already scoped in effect, and now
the byte count proves it rather than a docstring.

#### One number had to follow, and a test caught it

`export_reduced_pdf` reports a "before" size it calls **what a plain Save would write** — §M111's
milestone, whose test compares it against a real `materialize`. The moment a Save stopped rewriting,
that test failed, which is exactly what it is for. `PyMuPDFEngine.save_size` now answers the question
by *doing* the save it describes: the rewrite routes measure the built document, the append route
writes a throwaway probe. The number also got more useful — for an additive document it is now the
size the user can see in their file manager, rather than a hypothetical re-serialisation they will
never encounter.

#### What this does not do yet

On a document that already carries our marks, `_apply_page_edits` strips and re-adds **all** of them,
so the append re-writes every mark rather than only the new one — measured on the same policy
document, **+23,189 B** with 20 marks already in the file against **+1,496 B** with none. Correct, and
still an order of magnitude cheaper than the rewrite; not minimal. Skipping the strip-and-re-add for a
page whose marks are unchanged would fix it, and it touches the pass the graft and `render_output`
share, so it wants its own milestone. Carried in `PROGRESS.md` §Open follow-ups.

And the milestone's headline caller is not on `main`: **no bridge tool takes this route today**, since
every one of them rotates, fills, redacts, flattens or changes the page set. `annotate` (M101) is the
one that will, and it reaches `materialize` through the same `_write`, so it inherits this on the day
it merges. `tests/test_mcp_transforms.py` pins the other half in the meantime — that M116 changed
what *no* existing bridge tool writes.

### M117 — an append should write the mark you added, not the two hundred already there (2026-08-25)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M117** On the append route, draw only the marks the file does not already have, instead of stripping every KlarPDF mark off the page and redrawing them all | `VirtualDocument.marks_to_append` against the `_source_marks` baseline `edits_are_additive` already compares with; `_apply_page_edits(…, appending=True)` / `_append_to_origin` in `model/edit_engine.py` | WSL + Windows | Adding one highlight to a 200-mark document appends **1,134 B** rather than 114,269; a **z-order** change still writes the new order; a mark left in place is byte-identical to the one the file already had, per kind, and the page renders the same as a full redraw |

**§M116's own follow-up, scheduled because the numbers do not survive the workflow the owner named
(2026-08-25).** The entry it came from measured a document carrying 20 marks and called the result
"not minimal". Measured against **front-heavy editing** — mark a document heavily in one sitting,
then reopen it repeatedly to add a few more — it is not a matter of minimality:

| | file |
| --- | --- |
| a clean 30-page document | 126,320 B |
| sitting 1 — **200 highlights**, one save | 239,494 B (+113,174, all of it real) |
| sittings 2–7 — **one** highlight each | 353,724 → 468,474 → 583,786 → 699,602 → 815,957 → **932,836 B** |

Six highlights worth about 4,800 bytes cost **693,342**, and the file **quadrupled**. That is 144×
overhead, and it lands on precisely the workflow this app exists for.

**The cause, and why it was invisible before M116.** `_apply_page_edits` strips *every* KlarPDF
annotation off the output page and redraws them all from the model — M31's round trip, and the
reason a reopened mark is editable at all. While every save rewrote the whole document that cost
nothing. An append cannot delete, so redrawing 200 marks now means writing 200 fresh copies at the
end of the file and orphaning the 200 that were already there.

The cost is therefore **`marks already in the file × ~800 B`, paid once per save** — set by the
document rather than by the edit. Adding twenty marks to a 9-mark file appends 22,947 B, not the
20 × 7,942 a per-mark penalty would give.

| marks already in the file | what one more highlight appends |
| --- | --- |
| 9 | +7,942 B |
| 50 | +35,274 B |
| 100 | +61,485 B |
| 200 | +114,225 B |

**Three things already bound it, and a user can see none of them.** Repeated saves *within* one
sitting are free — the append is always against the bytes that were **opened**, so five saves of five
marks is +11,155 B, not five penalties (`tests/test_incremental_save.py` pins it). Batching is much
cheaper per mark. And any **non-additive** save collects the lot: recolouring one mark on the
932,836 B file above takes the rewrite route and lands at **174,411 B**, deleting one at **174,083** —
both smaller than the file six sittings earlier. Nothing is lost, only uncollected; but "tweak
something and it fixes itself" is not a property anybody can be expected to discover.

**The fix.** On the append route the model's marks are a superset of the file's by construction —
that is what `edits_are_additive` proves. So the page needs no strip at all, and only the
**difference** needs drawing. `VirtualDocument.marks_to_append(page)` answers with it, against the
baseline already captured in `_source_marks` and already compared per page; `_apply_page_edits`
takes an `appending` flag and strips only a page that method will not vouch for. Both other routes
are untouched — they have no proof to lean on, and nothing to save by skipping a strip inside a
write that rewrites everything anyway.

The same seven sittings, run either side of the change. Both columns come from one run of one
script, which is why the "before" sits a few hundred bytes off the diagnosis table above — that one
was measured on a separately-built fixture, and the pair below is what may be subtracted:

| | before | after |
| --- | --- | --- |
| a clean 30-page document | 126,540 B | 126,540 B |
| sitting 1 — **200 highlights**, one save | 239,692 B | 239,692 B |
| sittings 2–7 — **one** highlight each | **933,069 B** | **246,527 B** |

Six highlights that cost **693,377 B** now cost **6,835**, and the per-save penalty stops being a
property of the document:

| marks already in the file | before | after |
| --- | --- | --- |
| 9 | +8,221 B | +1,078 B |
| 50 | +35,262 B | +1,086 B |
| 100 | +61,477 B | +1,103 B |
| 200 | +114,269 B | +1,134 B |

**Two constraints, one of them found by measuring rather than by reading.**

*Z-order is not a membership change, and it currently works.* `reorder_marks` (Bring to Front) leaves
the multiset identical, so `appends()` already returns **True** for it — verified — and the output is
correct today only because the strip-and-redraw re-lays every mark in the model's order. Skip the
redraw naively and a z-order change becomes a silent no-op: the save reports success and the file is
unchanged. So the comparison is a **prefix**, not a set: the page's marks must still *start* with the
ones it arrived with, in that order, because `/Annots` is an order and anything drawn now goes on the
end. A page that fails it falls back to the full strip-and-redraw, and only that page — the baseline
is per page, so one Bring to Front does not re-write a whole document's marks. This is the whole
reason the fix is not a two-line diff.

*A mark left in place must be indistinguishable from a redrawn one.* The wrinkle named in advance was
float drift — PDF stores numbers as 32-bit floats, so a colour saved as `0.86` reads back as
`0.8600000143` — and the answer turned out to be stronger than "indistinguishable": a mark nobody
redraws keeps its **xref number, its raw object and its appearance stream, byte for byte**, because
they are still the bytes at the front of the file. `tests/test_incremental_save.py` checks that per
mark kind, together with the descriptor the model reads back and the pixels the page renders to
against `render_output`'s full-rewrite build.

**Two things it changed that were not on the list.**

*The redraw was reshuffling annotations it did not write.* Stripping our marks and re-adding them
puts them on the end of `/Annots`, which pushes anything else on the page underneath. Measured on a
page carrying eight of ours and one somebody else's, a save that only added a highlight moved the
foreign annotation from last to **first**. The model has no opinion about where a foreign mark sits,
so it had no business having one; left in place, it stays where its author put it.

*And it was quietly resizing shapes* — [#292](https://github.com/utyagi24/klarpdf/issues/292), found
by the per-kind render comparison over the corpus (82 of 83 pages identical; the 83rd carried 4 pt
shapes from an earlier session). `parse_annotation` insets a `Square`/`Circle` `/Rect` by
`width / 2`, PyMuPDF grows it by exactly **1.0 pt** whatever the border, so a shape of any width but
the default 2.0 changes size on every save→reopen→save — 2 pt a side per save at 6 pt wide. It is a
defect in the *redraw*, not in this route, so it is filed rather than fixed here; M117 removes the
commonest way of hitting it, since a plain "add one more mark" save no longer redraws anything.

**Both surfaces, one change.** It lands in the pass `materialize` runs, so the app's Save and the
bridge's `annotate` (M101) get it together — and `annotate` is where an agent chains several passes
over one document, which is the front-heavy pattern with no human in it. As with §M116, **no bridge
tool takes this route today** — every one of them rotates, fills, redacts, flattens or changes the
page set — and `tests/test_mcp_transforms.py` still pins that from the bridge's own side.

**The corpus, as the standing measurement.** All 94 documents, two sittings each — twenty marks and
one save, then reopen and add one more — with the second save written twice, once with
`marks_to_append` live and once forced to `None`, which is exactly M116. 83 were appendable
(11 skipped: a password, or too little text to mark). The second sitting cost **136,427 B in 0.56 s**
against M116's **1,606,992 B in 2.29 s** — **11.8×** — with 83/83 keeping the whole sitting-1 file
byte-identical at the front, 83/83 reading the twenty marks back unchanged, 83/83 adding exactly one
mark on exactly one page, 83/83 with catalog, encryption and permissions unchanged, 83/83 identical
under Poppler and parsed by pypdf, and **0** content streams changed corpus-wide.

### M118 — the boundaries M113 stopped one step short of (TC-015, 2026-08-26)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M118** Five follow-ups from the TC-015 retest of M113: two mediums where a correct fix stopped at its own edge, plus three smaller ones | `model/markup_palette.py` (`nearest_name` and its radii), `mcp_bridge/annotations.py` (`_fit`, `_lines_of`, `_already_present`), `mcp_bridge/docs.py` | WSL | Pure yellow names `Yellow` while teal still names nothing; a 120,000-character note cannot push a reply past the budget and paging still terminates; a multi-paragraph note survives three re-runs unchanged; a mark with one quad per word says its sentence once |

**What TC-015 was:** a black-box retest of #296 against nine purpose-built fixtures plus three of the
owner's real documents, using PyMuPDF and pypdf as an oracle independent of the tool under test.
**It verified all nine M113 fixes** — 460 marks walked to exhaustion with zero gaps or duplicates,
the permissions warning, the coordinate convention, foreign-mark disclosure, the merge thresholds,
scan geometry, negative `marks_added`, the refusals — and then found where two of them stopped.
Every finding below reproduced exactly as filed; none was a tester error.

**The species is worth naming, because it is not the one M113 fixed.** M113's defects were contracts
that *lied* — documentation describing behaviour the code did not have. These are the opposite: the
behaviour is right and the *boundary* is wrong. A correct metric with a ceiling 1% too low; a
correct pagination rule with no floor under one mark. Neither misleads a caller about what the tool
did, and both are cheap. That is what a good fix failing looks like, and it is a healthier failure
than the one before it.

**M118.1 — pure yellow `#FFFF00` came back unnamed** *(medium; the M113.7 recalibration overshot)*.
`(1, 1, 0)` is Adobe Acrobat's default highlighter and the commonest highlight colour in
circulation. It sits **0.1214** from our Yellow under the M113.7 metric, against a `NAME_TOLERANCE`
of **0.12** — outside by 1.2% — so `color_name` was `null`, defeating the same advertised
composition (*read → filter on `color_name` → `redact_regions`*) that M113.7 existed to restore. The
boundary ran between colours no eye can separate:

| colour | `color_name` before |
| --- | --- |
| `1, 1, 0` — Acrobat's default | **`null`** |
| `1, 1, 0.02` | **`null`** |
| `0.99, 0.99, 0` | `Yellow` |

**The cause is that one number was answering two questions.** `NAME_TOLERANCE` has to decide both
*is this colour anywhere near our palette* and *is it unambiguously one swatch rather than stranded
between two* — and calibrating it against the closest swatch pair (M113.7's stated invariant) only
really addresses the second. Nothing about pure yellow is ambiguous: its runner-up, Orange, is
**0.2501** away, so Yellow is **2.06×** clearer. It was excluded by an absolute proxy for a
relative property.

So the questions are now asked separately. Inside `NAME_TOLERANCE` (0.12) nothing changes at all.
Between it and a new `NAME_MAX_DISTANCE` (0.16) a colour is named only if the nearest swatch beats
the runner-up by `NAME_MARGIN` (1.5×). Measured over the RGB cube in all three palette modes, this
changes **no** colour that already had a name — it is **purely additive**, which is what makes it
safe to ship on top of a fix that had just moved the same boundary. It admits pure yellow and its
neighbourhood; teal, mid-grey, brown, purple and white stay unnamed, and teal is the one that
matters — it is *inside* 0.16 of our Green and is rejected on the **margin**, not the distance,
which is exactly the discrimination the single ceiling could not make.

*Rejected: widening `NAME_TOLERANCE` to ~0.13.* That number is the Yellow-to-Orange gap itself, so
it would admit ties between two names, and it answers the wrong question — the issue was never that
0.12 was too small in general.

**M118.2 — one mark with a long note produced a reply the caller cannot receive** *(medium; the
M113.2 budget has no floor)*. A single annotation carrying a 120,000-character note returned a
**120,624-character** reply, which the client refused and spilled to disk — word for word the harm
the character budget was added to prevent, now reachable through **one** annotation instead of 406.

The pagination itself was correct throughout (`count: 1`, `more_available: true`, and `offset: 1`
recovered cleanly). What failed is a consequence of M113.2's own **"a batch always yields at least
one mark"** rule, which exists because an empty batch with `more_available: true` pages forever.
M113 read that as licence for one mark to set an unbounded *floor* on reply size, since whole marks
are never trimmed. **The budget bounded batches; nothing bounded a mark.**

**Fix: cut the note, not the mark** — the only field that grows without bound. Everything a caller
filters or redacts on (`boxes`, `color`, `color_name`, `page`, `type`, `snippet`, the flags) is
small, bounded, and comes through intact; the mark carries `note_truncated: true` and `note_length`,
the original count, so the reply says plainly that there is more and how much. That keeps
termination *and* the budget, which the previous design could not do at once. Measured on the
reported fixture: **120,624 → 31,458 characters**, and all six marks now arrive in one batch where
one did before.

**M118.3 — a note containing a blank line still duplicated on every re-run** *(low, and the
remnant of M113.1)*. M113.1 matched "is this note already here" as *membership of a segment list* —
exact while a note **is** one segment. A multi-paragraph note splits into several, matched nothing,
and was re-appended on every run: unbounded growth while `marks_added: 0` reported nothing had
changed — the original defect's exact shape. M113 disclosed this in the docs as an encoding limit,
but the disclosure sat in a parenthesis under a headline still promising *"re-running a call is
therefore safe"*, and a multi-paragraph review comment is an ordinary thing to write. **Fixed rather
than merely bounded** (owner, 2026-08-26): both sides are read as segment lists and the test is
whether the new note appears as a **contiguous run** of the existing one. That is exact for any
number of paragraphs and keeps the property M113.1 was right about — `"check"` against an existing
`"check the totals"` is one segment against another, does not match, and is appended rather than
swallowed as a substring.

**M118.4 — the `annotations` echo returns marks the call never touched** *(low, documentation)*. Two
marks laid over two foreign ones echoed **four** entries. The filter matches on page, type and
geometric overlap, which a foreign mark at the same span satisfies. **The behaviour is better than
the contract and stays**: having just laid a highlight over a reviewer's, theirs is exactly what you
need to see, and it pairs with the warning naming its author. The docs now say the echo has **three**
kinds of entry — written, merged into, and *landed beside* — and that `mine` separates them.

**M118.5 — a multi-box mark repeated its own sentence** *(cosmetic)*. A mark carrying one quad per
word — what a highlight over individual `search` word-boxes produces — snippetted each box
separately, returning **618 characters to describe 73**, spending the very budget M118.2 defends.
Boxes are now folded to one union per **line** before snippetting, which is what the field always
meant; measured 7.9× → 1.0× on the reported shape.

**Also recorded from TC-015, not fixed here.** `color_exact` was documented as distinguishing "the
reviewer picked Orange from the menu" from "something orange-ish arrived from elsewhere". Real files
carry KlarPDF-authored marks written under an **older swatch set**, which read `mine: true,
color_exact: false`, so that reading over-reaches. The field computes correctly; the docs now state
it as a fact about the stored value rather than about who wrote the mark. And a highlight authored
`pdfproj` — this app's own former codename — reports `mine: false` on a real document, which
confirms §M112's premise on a third file rather than opening anything new.

### M120 — a shape stops resizing itself on every save ([#292](https://github.com/utyagi24/klarpdf/issues/292), 2026-08-26)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M120** A rectangle or ellipse whose outline is not exactly 2 pt no longer shrinks or grows on each save→reopen→save | `_SHAPE_RECT_GROWTH` + the `Square`/`Circle` branch of `parse_annotation`, `model/page_edits.py` | WSL | A shape reopens at the size it was drawn at **0.5 / 1 / 2 / 3 / 6 / 12 pt**, both kinds; four rewriting saves leave it where it started; the constant is pinned against PyMuPDF's own measured growth; `FreeText` still insets by half its border |

**The defect.** `parse_annotation` insets a shape's stored `/Rect` to recover the box the user drew,
and it insets by **`width / 2`** — the overhang a stroke centred on the path *would* have. PyMuPDF
does not do that. Measured on the pinned 1.27.2.3 across widths **0.25 to 20.0**, for
`add_rect_annot` and `add_circle_annot` alike, the growth is **exactly 1.0 pt per side at every
width**. Reproduced before the fix, per save:

| border width | PyMuPDF grows | old inset (`width / 2`) | net per save |
| --- | --- | --- | --- |
| 0.5 | 1.0 | 0.25 | **+0.75** grows |
| 1.0 | 1.0 | 0.5 | **+0.5** grows |
| **2.0** | 1.0 | 1.0 | **0** |
| 3.0 | 1.0 | 1.5 | **−0.5** shrinks |
| 6.0 | 1.0 | 3.0 | **−2.0** shrinks |

**`Shape.width` defaults to 2.0, which is the single value where the two agree** — and the round
trip was only ever tested at the default. The suite could not have caught this, and did not: the one
width that is exercised is the one width that works.

**Where it bit.** Only on a save that **redraws** the mark. Since M117 an appending save leaves a
mark it is not changing exactly as it found it, so an ordinary "add one more highlight" save stopped
moving anybody's shapes; but a rewriting save — a rotation, a redaction, a page move, a form fill,
`Export ▸ Flattened`, a z-order change — still did. Found by M117's own per-kind comparison of a
left-in-place mark against a redrawn one over the 94-document corpus: 82 of 83 pages rendered
identically and the 83rd carried 4 pt shapes drawn in an earlier session.

**The fix is one constant, and the tests are the substance.** Three of them, each pinning something
the original got wrong:

1. **A shape reopens at the size it was drawn**, parametrised over both kinds × six widths. One
   cycle is enough — the drift compounds, but it is already there after the first save.
2. **Four rewriting saves leave it where it started** — the symptom as reported. Each cycle applies
   a no-op rotation, because since M117 a plain re-save no longer redraws and so cannot reproduce it.
3. **The growth we undo is the growth PyMuPDF applies** — measured live, per kind, across seven
   widths. This is the test whose absence allowed the bug: the old inset was *derived* rather than
   *measured*, and if a future PyMuPDF changes the growth this now fails here with the reason
   instead of appearing as documents quietly changing shape. A fourth test pins that `FreeText`
   still insets by `border_width / 2`, since its growth genuinely does track the border — two insets
   in one module that look like they should match and must not.

**The lesson, which is why this earns an entry rather than a one-line commit:** when a read-back has
to undo something a writer did, **measure what the writer actually did** rather than deriving it
from what it ought to be — and test a round trip at more than the default value, because the default
is the one most likely to be the value that happens to work.

**Left open, deliberately** (`PROGRESS.md` §Open follow-ups): the *bridge* reports a shape's raw
grown rect where the app now recovers the authored one, so the two surfaces name a 1 pt different
box for the same shape. That is not obviously wrong — a box fed to `redact_regions` arguably should
cover the drawn stroke — so it needs a decision rather than a patch.

### M121 — Insert Blank Page leaves you looking at the page you made ([#288](https://github.com/utyagi24/klarpdf/issues/288), 2026-08-26)

| Milestone | What | Where | Verify |
| --- | --- | --- | --- |
| **M121** A structural edit stops scrolling the Pages sidebar out from under the reader, and an insert lands the marker on the new page | `ThumbnailPanel.populate` (`organize/thumbnail_panel.py`), `_insert_blank_page` and the `_edited_page` consumption in `_on_doc_changed` (`main_window.py`) | WSL (offscreen GUI) | A rebuild with the marked row in view does not move the strip at all; a rebuild with it off-view centres rather than jams; inserting mid-document *and at the end* leaves the new page current and fully inside the sidebar viewport |

**The report.** Right-click a thumbnail → **Insert Blank Page**, and the strip scrolls so the clicked
thumbnail is jammed against the **bottom** of the sidebar while the page just created — which sits
immediately after it — is below the fold. You have to scroll back to see what you asked for.

**Two independent causes, and the first is not about inserting at all.**

*(a) The scroll jump belongs to `populate()`, which every structural edit runs.* `clear()` drops the
strip to the top, and the `setCurrentRow` that restores the marker scrolls the **minimum** distance
to bring it back — landing it hard against whichever edge it came from. That is precisely the defect
`_reveal_row` was written to fix for view-driven scrolling (M85, owner-reported 2026-08-13),
reappearing by a path that never went through it. So duplicate, insert-from-file, rotate and delete
all did it too; Insert Blank Page is simply where it is most visible, because it is the one that puts
something new immediately below the fold. **Fix:** capture the scroll offset alongside the row,
restore it before the marker, and reveal through the shared `util.reveal` policy — a row still
comfortably in view does not move at all, one that is not gets centred.

*(b) Which page is current after an insert.* `_insert_blank_page` pushed its command and stopped, so
the current row was a bare integer that survived the rebuild and therefore pointed at a **different
page** than before the insert. `_note_edit_on` — the M59.9 hook that already exists for exactly this
— makes the new page current, and `ThumbnailPanel.set_current` reveals it properly on the way.

**And the view has to move with it, which the first cut of this milestone missed** (owner, reported
against [#300](https://github.com/utyagi24/klarpdf/pull/300) before it merged). `_note_edit_on`
travels to the view through `PdfView.set_current_page`, which is **deliberately non-scrolling** —
its docstring says so, and it is right about its own case: an annotation applied to a page that is
not under the viewport should move the sidebar highlight without dragging the reader off what they
are reading. Applied to an insert it produced a *worse* state than the bug it fixed: the sidebar
highlighted the new page while the main view still showed the page it was inserted from, and the two
disagreed on screen. **An insert is a "take me there" gesture**, so `goto_page` runs after the push —
after it, because that is when the new page exists in the layout. The marker is still set explicitly
rather than left to fall out of the scroll, so it is deterministic and does not depend on the
viewport-centre calculation firing.

**The test this needed had to assert on geometry, not on the property.** `view.current_page` is a
stored value that `set_current_page` had already moved to the new page — so a test comparing it
against the sidebar row **passes against the bug**. The check computes which page actually occupies
most of the viewport and compares *that*: `assert 20 == 21` is the disagreement, in the terms the
reader sees it.

**The ordering bug this uncovered, which is the part worth recording.** Setting `_edited_page` alone
fixed an insert *into* the document and did nothing for one at the **end**. `_on_doc_changed`
consumed `_edited_page` **before** `thumbs.populate()`, and the marker travels to the sidebar as
`currentPageChanged` → `set_current`, which range-checks the row against the strip's *current* count
— so for a page appended past the old end, the row did not exist yet and the request was silently
dropped by the `0 <= index < self.count()` guard. Nothing failed; the marker just stayed put. The
consumption now runs **after** `populate()`: rebuild the strip, then place the marker on it. Caught
by testing the end-of-document case separately rather than assuming it was the same code path.

**Scope.** GUI-only, as the report says: `organize/` is shared with the MCP bridge, but this is
sidebar scroll and selection behaviour and the bridge has no insert tool. No core behaviour changes,
so there is nothing owed on the bridge side (`CLAUDE.md` §Two consumers share one core).


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
  (crypto deps + certificate UX; M63's image signature covers the everyday tier);
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
  `model/page_edits.py:read_klarpdf_annotations` skips any annotation whose title isn't ours, so it
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
  `model/toc_remap.py` does multi-level remap with orphan repair.

> Note: the view/print/annotate/redact **product features** that earlier sat here all shipped
> (M0–M22); the next tranche is scheduled in §Next roadmap (v0.5.0 → v0.7.0). Only the items above
> remain deferred.
