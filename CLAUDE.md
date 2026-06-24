# CLAUDE.md — pdfproj

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
  (`C:\Users\<you>\pdfproj`). **Never** edit one across `\\wsl$` or `/mnt/c`.
- **Branch + commit + PR for every change — never leave edits uncommitted or on `main`.** This
  applies to **planning/docs** (`PLAN.md`, `PROGRESS.md`, `CLAUDE.md`), not just code. The moment a
  change is ready, create a branch (`plan/…`, `feat/m39-…`, `fix/…`, `docs/…`), commit, push, and
  open a PR with `gh` — **proactively, without being asked**. This is standing authorization; it
  overrides the default of committing only on request. The only exceptions: a throwaway the user said
  not to keep, or when the user explicitly says to hold off. (Local `gh`/`git` quirks live in memory.)
- **Always branch from an up-to-date `main`.** Before creating a branch, check what's checked out
  (`git branch --show-current`); a new branch must be based on **`origin/main`**, *not* on whatever
  feature branch is currently active — else that branch's commits ride into your PR (e.g. an unrelated
  open PR leaking into a new one). Use `git fetch origin && git switch -c <name> origin/main`. The one
  exception is *intentionally stacking* on an open PR — then base the branch on it **and** set the PR's
  base to match. Sanity-check before pushing: `git diff --stat origin/main..HEAD` should list only your
  own files.
- **One PR per milestone** (implementation); one PR per logical unit for planning/process changes. In
  the same PR, tick the milestone's box in `PROGRESS.md` and link the PR.
- **Cite sources.** Tie claims/numbers back to a `PLAN.md` section; don't present assumptions as facts.

## Gotchas (cost real time if missed)
- **Windows Python must be python.org 3.12.x**, not the Microsoft Store stub (which can't build).
- **WSL dev venv installs from `requirements-dev.txt`** (same `==` versions, **no hashes**):
  `pip install --require-hashes` fails on Linux by design (manylinux wheel hashes ≠ the `win_amd64`
  hashes pinned in `requirements-win.txt`). The hashed/offline lock is the **Windows ship** artifact only.
- **Keep OS-specific code quarantined** behind `platform_integration.py` and `packaging/` — never
  inline in `app.py`/`launcher.py`. `util/paths.py:normalize_path()` is the single identity chokepoint.
- **Win10 Home has no Windows Sandbox** — the clean-machine install test (M9) uses VirtualBox / a
  spare machine / a fresh local user with networking disabled.

## Status
**v0.9.4 shipped** (dependency security patch) — milestones **M0–M38 complete** (v0.1.0 = M0–M9; v0.2.0 =
M10–M15; v0.3.0 = M16–M19; v0.4.0 = M20–M22; v0.5.0 = M23–M26; v0.6.0 = M27–M30; v0.7.0 = M31 + M31.5
+ M34; v0.8.0 = M35–M37; v0.9.0 = M32 + M33 + M38). **v0.9.4** dependency security patch: bump
**pypdf 6.13.2 → 6.13.3**, clearing GHSA-jm82-fx9c-mx94 (Moderate memory-DoS in the `pypdf` fallback
edit engine `model/edit_engine.py:PyPdfEngine`; no functional change). **v0.9.3** patch (PR #66): a window opens **on the
monitor under the cursor** (`QGuiApplication.screenAt(QCursor.pos())` in `main_window.py`
`_place_window`) instead of always the primary; the **open-from-Explorer flicker is gone** —
`platform_integration.py` `activate_window` raises via a `SetWindowPos` TOPMOST→NOTOPMOST z-order
nudge (`_raise_to_front_win32`) instead of toggling `WindowStaysOnTopHint` (changing a window flag
recreates the native window on Windows → a flash every raise); and the **Pages sidebar is hidden by
default**, remembered app-wide via `store/settings.py` `get_pref`/`set_pref`. **v0.9.2** patch: open
render/zoom **flicker fixed** (window placed + the page rendered once at Fit Page *before* show —
`_shown_once`/`open_at` in `viewer/pdf_view.py`, `_place_window` in `main_window.py`; PR #63) + **lazy
thumbnails** (the Pages sidebar rasterises only the pages scrolled into view —
`organize/thumbnail_panel.py`; a 320-page doc opens ~1010 ms → ~150 ms; PR #64). **v0.9.1** patch: a
document window opens at the **full screen height, centred horizontally, at Fit Page** (`main_window.py`
`_open_geometry` / `showEvent`; PR #61). **v0.9.0 "Encrypted & Links"** added **encrypted /
password PDFs** (`needs_pass` → prompt → `authenticate` on open, then the source is held decrypted
via `PDF_ENCRYPT_NONE` so the output stays unencrypted — `model/virtual_document.py`) and **internal
links**: `model/links_remap.py` rebuilds GoTo *and* named-destination links at materialize (both
baked to remapped GoTo — `insert_pdf` drops named dests entirely) so reorder/delete/Save keeps them
working, plus **in-viewer click-to-navigate** (`viewer/links.py`). **v0.8.1** patch: double-click
open from a case-sensitive `\\wsl.localhost\` / UNC folder works for every file (the single-instance
hand-off now passes the raw path, not a lower-cased one — `launcher.py`; PR #55).
Releases:
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.4> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.3> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.2> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.1> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.8.1> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.8.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.7.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.6.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.5.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.4.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.3.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.2.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0>. On top of v0.1.0 (view + splice/split +
single-instance + undo/redo + lossless materialize-on-save + freeze→installer→CI), **v0.2.0** added
**icons**, a **zoom %** indicator, **printing**, **recent documents**, and **form filling** on a new
**page-edit layer** (`model/page_edits.py` — immutable per-doc edit descriptors applied at
materialize; sources stay read-only); **v0.3.0** added **drag-and-drop visuals**, **Explorer
file-drop**, and a **Grab/Select** viewer-mode toggle (`viewer/tools.py`); **v0.4.0** added
**annotations** (highlight + movable/re-editable text boxes) and **true destructive redaction**
(region + text-flow, `apply_redactions` with cross-engine leak verification and a redacted-save
point-of-no-return) — all on the M14 page-edit layer, with annotate/redact exposed as **one-shot
armed** tools (`viewer/tools.py` `ArmedTool`); **v0.5.0** added **Revert to Saved**, an
**external-change warning** (`QFileSystemWatcher`), and **edits-aware printing** (print/preview
render the edited page — annotations / fills / redactions — via `render_output`); **v0.6.0** added
**styled text boxes** (font family / size / colour + box fill + box outline, via a formatting bar on
the inline editor — `viewer/text_format_bar.py`), **live thumbnails** (the Pages sidebar renders each
page's edited state via `render_output`), and **dynamic theme icons** (the toolbar re-tints on a live
OS light↔dark switch — `changeEvent` handles `PaletteChange`); **v0.7.0** added **annotation
round-trip editing** (reopen → move/edit/remove our `PDFPROJ_AUTHOR`-tagged highlights & text boxes —
`read_pdfproj_annotations` seeds the model on open, strip-then-re-add at materialize; the page render
+ text selection read the our-marks-stripped page so the editable overlay is authoritative) and a
flatten **Export → PDF** (`File ▸ Export`; `model/export.py` bakes annotations + form widgets into
page content via `Document.bake()`, text-preserving — the locked counterpart to round-trip);
**v0.8.0** added **image import** (drag a PNG/JPEG from Explorer onto the Pages sidebar → a page,
`VirtualDocument.open_image_source` via PyMuPDF `convert_to_pdf`) and **image export**
(`File ▸ Export ▸ Image…`; `model/export.py:export_page_images` rasterises `render_output` → PNG/JPEG
at a chosen DPI, edits-aware), plus UI polish (clearer multi-page selection, vertically-centred
fitting page, centred text-box text); **v0.9.0** added **encrypted / password PDFs**
(`_authenticate_and_decrypt` in `model/virtual_document.py` — prompt + authenticate on open, source
held decrypted so the output stays unencrypted) and **internal links** (`model/links_remap.py`
rebuilds GoTo + named-destination links at materialize; `viewer/links.py` makes them clickable in
the viewer). 369 headless tests; real-Windows + frozen-build validation. **The planned roadmap
(M0–M38) is complete** — further work lives in `PLAN.md` §Future enhancements. **Planning the next
release?** Read `PROGRESS.md` (status + **Open follow-ups**), then `PLAN.md` §Future enhancements.
