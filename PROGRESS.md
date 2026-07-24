# KlarPDF — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

> **This file is the single source of truth for live status** — shipped versions, per-release notes,
> release links, milestone ticks, and open follow-ups. `PLAN.md` (design/spec) and `CLAUDE.md`
> (conventions) **link here, they don't restate it** — see CLAUDE.md §How we work → "Where things live".

**Status:** ✅ **v0.15.0 "Stamp, Sign & Watermark" shipped** — delivers **R4 (M61–M64)** and **R5
(M66–M69.16)** together: M65's release cut was skipped by owner call (2026-07-20) so R4 would ship
alongside R5 rather than under an unpublished tag. A **unified content-draw engine**
(`model/content_marks.py`) underlies stamps, signatures and watermarks — two descriptors, `Stamp`
(text + optional frame) and `ImageStamp` (a placed raster), that bake into the page's **content
stream** at save; built **vector** so stamp text stays searchable and crisp at any zoom and
arbitrary rotation comes free. A watermark isn't a third type, just either descriptor with
`under=True` applied across a page range. **Placement** rides the existing object tools — drag,
move, corner-resize, z-order, delete — rather than a second system
([#146](https://github.com/utyagi24/klarpdf/pull/146), [#147](https://github.com/utyagi24/klarpdf/pull/147)).
**Image stamp / signature**: "make white background transparent" keys a phone photo of a signature
so it stops blanking out whatever it covers, with a **recent-signatures** list for two-click reuse
([#148](https://github.com/utyagi24/klarpdf/pull/148)). **Tools ▸ Find and Redact…** finds every
occurrence of a search term, reviews hits in the search panel (checkable, click to jump), and
redacts the checked ones as one undo step — text-layer only, image-only pages named rather than
silently reporting zero matches ([#149](https://github.com/utyagi24/klarpdf/pull/149)).
R5 adds a **foreign-annotation** layer for marks another PDF tool wrote: infrastructure + **delete**
([#150](https://github.com/utyagi24/klarpdf/pull/150)), **move** with the appearance stream
preserved byte-for-byte ([#151](https://github.com/utyagi24/klarpdf/pull/151)), and
**adopt-on-edit** — double-click a foreign mark of a modeled type (highlight / underline /
strikeout / ink / line / square / circle / FreeText) to make it an ordinary editable KlarPDF mark,
with a degrade warning that fires only when something would actually be lost
([#152](https://github.com/utyagi24/klarpdf/pull/152)). **Form-field creation** — Tools ▸ Add Form
Field ▸ Text / Checkbox / Dropdown — places an ordinary AcroForm field, so inline fill, lossless
save, print and flatten all work on it by construction, no new code path
([#153](https://github.com/utyagi24/klarpdf/pull/153)). A sixteen-item polish pass (**M69.1–M69.16**)
followed from owner testing across the whole R4/R5 surface — a rotated stamp's mirror-image bug,
watermark interaction + live-thumbnail fixes, merging the stamp and watermark UI into one feature
(owner call: *"given the similarity…"*), large-document mark performance, whole-page marks visible
by default, an angle slider, a mark-dialog geometry warning, a recent-signature crash, signature-drag
lag, a backwards opacity slider, and — the last three — making a created form field behave as an
ordinary object: selected on placement, and grabbed **press-to-move / double-click-to-type** like
every other text box instead of by hunting for its border
([#154](https://github.com/utyagi24/klarpdf/pull/154), [#155](https://github.com/utyagi24/klarpdf/pull/155)).
Release: <https://github.com/utyagi24/klarpdf/releases/tag/v0.15.0>. 1068 headless tests green
(1 expected skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.14.0 "Markup Tools"** — the GUI tranche's **R3 (M56–M60)**, and because the **v0.13.0 tag was
cut but never published** (owner call), this release also delivered **R2 "Document Hygiene"
(M51–M54)** to users: extract / insert-blank / duplicate pages, **Reduced Size** export, document
**Properties + metadata** editing (both stores), and **AES-256** password protection. R3 itself is
the markup kit: **underline & strikeout** on Highlight's text-quad path, a **pen** plus **lines /
arrows / rectangles / ellipses**, a shared **colour · width · opacity · fill** picker with curated
per-verb text-markup palettes, and full **object editing** — marquee and Ctrl-click multi-select,
move, **resize** (single + group, about the bounding box), **z-order** (Bring to Front / Send to
Back, which is both paint *and* hit order), and group **copy / cut / paste** that preserves the
arrangement. Everything bakes into the saved PDF and reopens editable. Four fixes came out of owner
testing and shipped in the same tranche: re-marking text now **merges** into the existing mark
instead of stacking a second layer ([#139](https://github.com/utyagi24/klarpdf/pull/139)), mark
paint order in the preview now follows the model's z-order rather than the mark's *type* — so a
filled shape hides a text box exactly as it does in the saved file
([#140](https://github.com/utyagi24/klarpdf/pull/140)), group copy/paste reversed an earlier
deferral ([#141](https://github.com/utyagi24/klarpdf/pull/141)), and the toolbar's dropdown arrows
share one position ([#142](https://github.com/utyagi24/klarpdf/pull/142)). Release:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.14.0>. 737 headless tests green (1 expected
skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.12.0 "Navigate & Polish"** — the GUI tranche's **R1 (M45–M50)**. **Outline sidebar**: a document with bookmarks gets a Pages | Outline switcher (no
TOC → no tab and no tab bar, owner rule) showing the **live** `remapped_toc()` tree — follows
edits, tracks scroll, click-to-jump — plus **Go to Page…** (Ctrl+G)
([#117](https://github.com/utyagi24/klarpdf/pull/117)). **Context menus everywhere**, hit-test
routed — selection / internal link / **external link (Copy Link Address)** / annotation / bare
page / sidebar ([#118](https://github.com/utyagi24/klarpdf/pull/118)). **Search-all results
panel** — List All shows page + context-snippet rows, click-to-jump; the surface M64 reuses
([#119](https://github.com/utyagi24/klarpdf/pull/119)). **Crop pages** — `crop_override` rides the
PageRef like rotation; page/selected/all scopes; *hidden, not removed*; Remove Crop restores the
full MediaBox even for pre-cropped files ([#120](https://github.com/utyagi24/klarpdf/pull/120)).
**Night reading mode** — view-only inversion; file/print/export stay true-colour
([#121](https://github.com/utyagi24/klarpdf/pull/121)). **The Tools menu** — modes out of View,
Rotate into Edit beside the page ops ([#123](https://github.com/utyagi24/klarpdf/pull/123)).
Review-testing fixes folded in: toolbar text tools **apply to a live selection** (Preview-style);
the find bar **revives its kept query** on reopen; the sidebar keeps its width bounds with the
switcher mounted; two offscreen-suite deadlock classes fixed (stale-watcher zombie prompts; a
conftest guard that fails loudly on any unexpected modal); and a **save-fidelity fix** — URI links
PyMuPDF's `insert_pdf` silently drops (unbalanced-paren URIs, seen in the wild) are restored at
materialise ([#122](https://github.com/utyagi24/klarpdf/pull/122)). **v0.11.0 stays reserved for
the MCP / Agent Bridge** (owner decision, PR #116) — hence R1 = v0.12.0. 485 headless tests green.

**v0.10.1** — a patch fixing the Windows shell integration v0.10.0 got wrong.
**The app icon is now a tile.** The brand mark is a portrait page, so it spanned only **59%** of the
square canvas Windows gives an icon (24×24 for the taskbar) — against 82–100% for every other app on a
typical machine — and read as *tiny*. `ui/icons/klarpdf.svg` is a gradient rounded square that spans
100%. **`.pdf` files get their own icon**: the ProgID `DefaultIcon` pointed at `klarpdf.exe,0`, so
every PDF on disk wore the *application's* icon; a new `klarpdf-doc.ico` (from the brand's
`pdf-file-icon.svg`, drawn for this and never wired up) now shows a page. The free-standing mark
survives in the About dialog. And **Setup and the uninstaller now refuse to run while KlarPDF is
open** — the app holds a named mutex Inno watches (`AppMutex`). Without it, uninstalling a running app
left the install directory behind (Windows won't delete a running `.exe`) and *recreated*
`%LOCALAPPDATA%\klarpdf`, because the dying process rewrites `view_state.json` on shutdown. It refuses
rather than force-closes: KlarPDF prompts on unsaved edits, and Restart Manager would bypass that
prompt.

**v0.10.0** — **"KlarPDF"**, the rebrand + open-source release. The app formerly
built as `pdfproj` is now **KlarPDF** (*klar* = "clear"): new name, new mark and toolbar glyph set, a
root **AGPL-3.0-or-later `LICENSE`** + `THIRD_PARTY_LICENSES`, and a **Help menu** — About (version,
licence, no-warranty notice, a link to the source at *this exact tag*) and Open-Source Licenses (the
bundled texts, offline). Community-health files and a governance policy landed too: issues are open to
everyone, pull requests are restricted to the maintainer and invited collaborators. Windows-facing
consequences of the rename: a **fresh Inno `AppId`**, so `klarpdf-setup.exe` installs as a *new* app —
**uninstall `pdfproj` first** (`RELEASE.md`) — a `KlarPDF.Document` ProgID, `%LOCALAPPDATA%\klarpdf`
for settings, and the exes finally carry **version metadata** (`ProductName`/`FileVersion`), which the
spec had never set. Milestones **M0–M38 complete** (v0.1.0 = M0–M9,
v0.2.0 = M10–M15, v0.3.0 = M16–M19, v0.4.0 = M20–M22, v0.5.0 = M23–M26, v0.6.0 = M27–M30,
v0.7.0 = M31 + M31.5 + M34, v0.8.0 = M35–M37, v0.9.0 = M32 + M33 + M38). Releases:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.12.0> ·
<https://github.com/utyagi24/klarpdf/releases/tag/v0.10.1> ·
<https://github.com/utyagi24/klarpdf/releases/tag/v0.10.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.6> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.5> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.4> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.3> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.2> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.1> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.0>. **v0.9.6** is a bug-fix patch: the **Pages
sidebar thumbnails no longer flicker** at the window height where the bottom edge meets the last
thumbnail — the thumbnail is sized off a scrollbar-invariant width, so the vertical scrollbar toggling
on/off can no longer drive a resize→scrollbar→resize loop
([#87](https://github.com/utyagi24/pdfproj/pull/87)); and **a second PDF opened from Explorer now comes
to the front** (previously only the first did) — the forwarding launch hands its foreground right to the
resident instance via `AllowSetForegroundWindow`, which Windows otherwise denies a background process
([#88](https://github.com/utyagi24/pdfproj/pull/88)). **v0.9.5** is a viewer-polish patch: the page
opens **centred** and stays centred, and **Fit Width / Fit Page are sticky on resize**
([#80](https://github.com/utyagi24/pdfproj/pull/80)); the **Pages sidebar** gets a narrower default
with a centred single column whose thumbnails **scale with the sidebar width** (Preview-style, capped);
a **rotated page that's wider than the view is centred** on fit; and the **Highlight** and
**Redact-Text** tools now **preview the armed selection in their final colour** (highlight colour /
redaction black) while you drag ([#81](https://github.com/utyagi24/pdfproj/pull/81)). **v0.9.4** is a dependency security patch:
bump **pypdf 6.13.2 → 6.13.3**, clearing GHSA-jm82-fx9c-mx94 (Moderate memory-DoS in the `pypdf`
fallback edit engine; no functional change). **v0.9.3** is an open-behavior patch
([#66](https://github.com/utyagi24/pdfproj/pull/66)): a new window opens **on the monitor under the
cursor** (where you double-clicked in Explorer) instead of always the primary; the **open-from-Explorer
flicker is gone** — `activate_window` now raises the window with a `SetWindowPos` z-order nudge instead
of toggling the `WindowStaysOnTopHint` flag (changing a window flag recreates the native window on
Windows → a visible flash every raise); and the **Pages sidebar is hidden by default**, remembered
app-wide, for a clean fast open. **v0.9.2** was a load-time / UX patch:
the open render/zoom **flicker is gone** (the page renders once at Fit Page, at the final geometry, instead of
being re-sized/re-zoomed after the window is already visible — [#63](https://github.com/utyagi24/pdfproj/pull/63)),
and the **Pages sidebar renders thumbnails lazily** (only the pages scrolled into view, not every
page up front), so large documents open far faster — a 320-page doc went ~1010 ms → ~150 ms
([#64](https://github.com/utyagi24/pdfproj/pull/64)). **v0.9.1** was a UX patch: a document window
opens at the **full screen height, centred horizontally, at Fit Page**
([#61](https://github.com/utyagi24/pdfproj/pull/61)). v0.9.0 "Encrypted & Links" adds
**encrypted / password-protected PDFs** (prompt + `authenticate` on open, then the source is held
decrypted so the output stays unencrypted) and **internal links** — `links_remap` rebuilds GoTo
**and** named-destination links at materialize so reorder/delete/Save keeps them working, and the
viewer makes them **clickable** (click → jump to the target page; `viewer/links.py`). **v0.8.1** was
a bug-fix patch: double-click open from a case-sensitive `\\wsl.localhost\` / UNC folder works for
every file ([#55](https://github.com/utyagi24/pdfproj/pull/55)). v0.8.0 "Images" adds **image import**
(drag a local PNG/JPEG/… from Explorer onto the Pages sidebar → it inserts as a page, converted via
PyMuPDF `convert_to_pdf`) and **image export** (`File ▸ Export ▸ Image…`; selected page(s) → PNG/JPEG
at a chosen DPI, edits-aware off `render_output`), plus UI polish (clearer multi-page selection,
vertical-centred fitting page, centred text-box text). v0.7.0 "Round-trip & Export" adds
**annotation round-trip editing** (reopen a saved doc → move / edit / remove our author-tagged
highlights & text boxes; the page render strips our baked marks so the editable overlay is the single
source of truth, and text selection reads that stripped page) and a flatten **Export → PDF**
(`File ▸ Export`; bakes annotations + form widgets into page content via `Document.bake()`,
text-preserving — a locked counterpart to the round-trip). v0.6.0 "Rich Text & Live Preview" adds
**styled text boxes**, **live thumbnails**, and **dynamic theme icons**. v0.5.0 "File Safety & Output"
adds **Revert to Saved**, an **external-change warning**, and **edits-aware printing**. v0.4.0
"Annotate & Redact" adds text **highlight** + **text boxes** and **true destructive redaction**.
**R2 "Document Hygiene" (M51–M54) is merged and tagged `v0.13.0` — no published release** (owner
call, 2026-07-19): the owner validated the merged build directly and skipped M55's release cut;
R2's features first *ship* with the next published release. **Next:** two planned roadmaps —
**v0.11.0 "MCP / Agent Bridge"** (M39–M44; `PLAN.md` §MCP / Agent Bridge roadmap; the version
number stays reserved for it) and the **GUI feature tranche's remaining releases R3–R5**
(M56–M70; `PLAN.md` §GUI feature roadmap — next tranche milestone is **M56**; sequencing vs the
bridge stays the owner's call). Other deferred items live in `PLAN.md` §Future enhancements.
**Open follow-ups** (carried items) are at the bottom.

- [x] **M0** Scaffold + WSL dev venv — *step 1 (WSL); WSL* — [#4](https://github.com/utyagi24/pdfproj/pull/4)
- [x] **M1** Correctness core: `model/` + headless tests green ⭐ — *steps 5, 7; WSL* — [#5](https://github.com/utyagi24/pdfproj/pull/5)
- [x] **M2** Viewer: render / scroll / zoom / rotate / thumbnails — *step 3; WSLg* — [#6](https://github.com/utyagi24/pdfproj/pull/6)
- [x] **M3** Selection + search — *step 4; WSLg* — [#7](https://github.com/utyagi24/pdfproj/pull/7)
- [x] **M4** Editing loop: cross-window cut/copy/paste + undo/redo + Save/Save As + close-prompt — *steps 6, 8; WSLg* — [#8](https://github.com/utyagi24/pdfproj/pull/8)
- [x] **M5** Single-instance launcher logic — *step 2; WSL (validate on Windows)* — [#9](https://github.com/utyagi24/pdfproj/pull/9)
- [x] **M6** Windows ship lock: python.org + hashed `win_amd64` wheels — *step 1 (Win); Windows* — [#11](https://github.com/utyagi24/pdfproj/pull/11)
- [x] **M7** Windows validation: instance / focus + GUI fidelity (Open-With → M8/M9) — *step 2; Windows* — [#12](https://github.com/utyagi24/pdfproj/pull/12)
- [x] **M8** Freeze + installer → `pdfproj-setup.exe` + portable + CI — *step 9; Windows* — [#14](https://github.com/utyagi24/pdfproj/pull/14)
- [x] **M9** Verify + release: matrix green + CI build + **v0.1.0** tagged & released — *Verification §; Windows* — [release](https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0)

⭐ M1 is the keystone — most correctness risk, GUI-free, fully testable in WSL/CI.

## Releases — v0.2.0 ✅ → v0.3.0 ✅ → v0.4.0 ✅ → v0.5.0 ✅ → v0.6.0 ✅ → v0.7.0 ✅ → v0.8.0 ✅ → v0.9.0 ✅

Spec + architecture in `PLAN.md` (§Shipped roadmap for v0.2–v0.4, §Next roadmap for v0.5–v0.9). Same
conventions: **one PR per milestone**, tick the box here on merge. ⭐ marks a keystone (most risk,
GUI-free core, fully headless-testable).

**v0.2.0 ✅ — "Polish, Print & Forms"** (shipped)

- [x] **M10** Icons — app `.ico` + toolbar icons (undo/redo, zoom, cut/copy/paste) — *WSLg + Windows (frozen-exe icon)* — [#18](https://github.com/utyagi24/pdfproj/pull/18) (frozen-exe icon validated at M15)
- [x] **M11** Zoom UX — live magnification % indicator + Actual-Size / 100% reset (Ctrl+0) + presets — *WSLg* — [#19](https://github.com/utyagi24/pdfproj/pull/19)
- [x] **M12** Printing — `QtPrintSupport` system print dialog; PyMuPDF render at printer DPI — *WSL logic; Windows print validation* — [#21](https://github.com/utyagi24/pdfproj/pull/21) (physical-printer dialog = manual check)
- [x] **M13** Recent documents — MRU list + dynamic File ▸ Open Recent submenu — *WSL* — [#22](https://github.com/utyagi24/pdfproj/pull/22)
- [x] **M14** ⭐ Page-edit layer + form filling (fill existing AcroForm fields) — *WSL (model+tests) + WSLg* — [#24](https://github.com/utyagi24/pdfproj/pull/24) (model foundation) + [#25](https://github.com/utyagi24/pdfproj/pull/25) (inline fill)
- [x] **M15** Verify + release → tag **v0.2.0** (CI Node-24 action bumps folded in; code signing still deferred) — *Windows* — [#26](https://github.com/utyagi24/pdfproj/pull/26)

**v0.3.0 ✅ — "Interaction & Drag-and-Drop"** (shipped)

- [x] **M16** Drag visuals — page-thumbnail drag pixmap (+ "N pages" badge) + custom drop-insertion marker — *WSLg* — [#28](https://github.com/utyagi24/pdfproj/pull/28)
- [x] **M17** Explorer file drop — drag a `.pdf` from Explorer onto the Pages panel → insert at the drop slot — *WSL (logic) + WSLg* — [#29](https://github.com/utyagi24/pdfproj/pull/29)
- [x] **M18** Grab / Select mode — hand/pan vs text-selection toggle (default Select), toolbar + View menu — *WSLg* — [#30](https://github.com/utyagi24/pdfproj/pull/30)
- [x] **M19** Verify + release → tag **v0.3.0** — *Windows* — [#31](https://github.com/utyagi24/pdfproj/pull/31)

**v0.4.0 ✅ — "Annotate & Redact"** (keystone release, shipped)

- [x] **M20** ⭐ Annotations — text highlight + text-box (free-text) on the M14 layer — *WSL + WSLg* — [#32](https://github.com/utyagi24/pdfproj/pull/32) (per-page model) + [#33](https://github.com/utyagi24/pdfproj/pull/33) (viewer highlight/text-box interaction)
- [x] **M21** ⭐ Redaction — true destructive `apply_redactions` + leak verification (`fitz` + Poppler `pdftotext` cross-engine). Two entry points, one multi-rect `Redaction` descriptor: **Redact Region** (one-shot rubber-band, for images/logos) + **Redact Selection** (text-flow, one continuous bar per line). A redacted **Save is a point of no return** (confirm → write clean → reload from clean file → clear undo: secret gone from disk *and* RAM). Bundled text-box UX polish (one-shot armed inserts; drag-to-move; double-click re-edit; auto-grow W+H; clamp to page). Forward-compat hooks for future round-trip + font/size/colour picker (`TextBox.fontname`; pdfproj author-tag on baked annots). Annotate/redact tools unified as **one-shot armed** gestures (Text Box click; Highlight/Redact-Text drag-over-text — continuous bar per line; Redact-Block drag-rect), grouped together. Cross-window page drag/paste **carries per-page edits** (annotations + redactions + rotation). — *WSL (model+verify) + WSLg* — [#34](https://github.com/utyagi24/pdfproj/pull/34)
- [x] **M22** Verify + release → tag **v0.4.0** (version bump + docs; 232 headless tests green) — *Windows* — [#35](https://github.com/utyagi24/pdfproj/pull/35)

**v0.5.0 — "File Safety & Output"** (planned)

- [x] **M23** Revert / Reopen — discard all edits + reload from disk (reuse `reload_from_file` + clear undo, dirty-confirm) — *WSL + WSLg* — [#37](https://github.com/utyagi24/pdfproj/pull/37)
- [x] **M24** External-change warning — file-changed-on-disk detection (`QFileSystemWatcher` + `(mtime, size)` signature) → Reload / Keep prompt (+ Overwrite / Reload / Cancel before an overwriting Save) — *WSL (logic) + Windows* — [#38](https://github.com/utyagi24/pdfproj/pull/38)
- [x] **M25** Edits-aware printing — Print renders the same edits-applied output a Save would write (page order, rotation, form values, highlights, text boxes, redactions), so a not-yet-saved redaction no longer prints the original. Preview / "Save as PDF" / scale modes were dropped (the native dialog can't host them; a rasterised PDF is worse than Save As) — the page→image render is kept as the engine for the planned **image export** (M36). — *WSL logic; Windows print validation* — [#39](https://github.com/utyagi24/pdfproj/pull/39)
- [x] **M26** Verify + release → tag **v0.5.0** — *Windows* — [#40](https://github.com/utyagi24/pdfproj/pull/40)

**v0.6.0 ✅ — "Rich Text & Live Preview"** (shipped)

- [x] **M27** ⭐ Styled text boxes — font family/size/colour + box fill + box outline (on/off, black), via a formatting bar on the inline editor. **B/I/U + coloured outline descoped** (owner call — base-14 bold/italic variant names don't render on PyMuPDF's FreeText appearance path; they'd force the heavier richtext path). Simple `add_freetext_annot` (`text_color`/`fill_color`/`border_width`), text stays in `/Contents`. — *WSL (model+tests) + WSLg* — [#41](https://github.com/utyagi24/pdfproj/pull/41) (model) + [#42](https://github.com/utyagi24/pdfproj/pull/42) (viewer)
- [x] **M28** Live thumbnails — thumbnails reflect the page's edited state (annotations/redactions/fills), rendered from the shared `render_output` bake (only when the doc has edits; clean docs keep the fast source render) — *WSLg* — [#43](https://github.com/utyagi24/pdfproj/pull/43)
- [x] **M29** Dynamic theme icons — runtime OS light↔dark re-tint. Verify revealed it never fired: `changeEvent` matched only `ApplicationPaletteChange`, but Qt delivers `PaletteChange`; now handles both, so the toolbar glyphs re-tint live (app icon is theme-agnostic) — *WSLg + Windows* — [#44](https://github.com/utyagi24/pdfproj/pull/44)
- [x] **M30** Verify + release → tag **v0.6.0** (version bump + docs; 285 headless tests green) — *Windows* — [#45](https://github.com/utyagi24/pdfproj/pull/45)

**v0.7.0 ✅ — "Round-trip & Export"** (shipped)

- [x] **M31** ⭐ Annotation round-trip editing — reopen → move/edit/remove our author-tagged annotations (strip-then-re-add at materialize); page render + text selection read the stripped page so the editable overlay is authoritative (no double-draw / stale-position select) — *WSL (model+tests) + WSLg* — [#46](https://github.com/utyagi24/pdfproj/pull/46)
- [x] **M31.5** Export → PDF (flatten) — new **Export** action (`File ▸ Export`); bake annotations + form widgets into page content (PyMuPDF `Document.bake()`, text-preserving — locks the marks, the opposite of M31's round-trip). Extensible Export path (`model/export.py`); M36 adds an image format. — *WSL (model+tests) + WSLg* — [#48](https://github.com/utyagi24/pdfproj/pull/48)
- [x] **M34** Verify + release → tag **v0.7.0** (version bump + docs + re-scope; 317 headless tests green) — *Windows* — [#49](https://github.com/utyagi24/pdfproj/pull/49)

> Re-scope (owner, 2026-06-20): encrypted-PDF (M32) + internal-link remap (M33) moved **out of
> v0.7.0** to a new **v0.9.0**, so the image work (v0.8.0) ships next.

**v0.8.0 ✅ — "Images"** (shipped)

- [x] **M35** Image import — drag a local image (jpg/png/…) from Explorer onto the Pages sidebar → insert as a new page (reuse M17 drop + PyMuPDF `convert_to_pdf`) — *WSL (logic) + WSLg* — [#50](https://github.com/utyagi24/pdfproj/pull/50)
- [x] **M36** Image export — **extend the M31.5 Export feature** to images: selected page(s) → PNG/JPEG at a chosen DPI (reuse M25 `render_output` + `_page_image`; edits-aware) — *WSL (render) + WSLg* — [#51](https://github.com/utyagi24/pdfproj/pull/51)
- [x] **M37** Verify + release → tag **v0.8.0** (version bump + docs; 341 headless tests green) — *Windows* — [#54](https://github.com/utyagi24/pdfproj/pull/54)

> Pre-release polish (owner, 2026-06-20): clearer multi-page selection in the sidebar + vertically
> centred fitting page ([#52](https://github.com/utyagi24/pdfproj/pull/52)) and vertically centred
> text-box text ([#53](https://github.com/utyagi24/pdfproj/pull/53)).

**v0.9.0 ✅ — "Encrypted & Links"** (shipped; re-scoped out of v0.7.0)

- [x] **M32** Encrypted / password PDFs — detect `needs_pass`, prompt, `authenticate` on open (then store the source decrypted in memory; output stays unencrypted) — *WSL + WSLg* — [#57](https://github.com/utyagi24/pdfproj/pull/57)
- [x] **M33** Internal link remap **+ navigation** — `links_remap` rebuilds GoTo **and** named-destination links at materialize (reorder/delete/Save keeps them working; named dests baked to GoTo — insert_pdf drops them entirely), **and** the viewer makes internal links clickable (click → jump to target page; pointing-hand on hover, `viewer/links.py`) — *WSL (model+tests) + WSLg* — [#58](https://github.com/utyagi24/pdfproj/pull/58)
- [x] **M38** Verify + release → tag **v0.9.0** (version bump + docs; 369 headless tests green) — *Windows* — [#59](https://github.com/utyagi24/pdfproj/pull/59)

## Roadmap — v0.11.0 "MCP / Agent Bridge" (planned)

Spec + architecture in `PLAN.md` §MCP / Agent Bridge roadmap. Same conventions: **one PR per
milestone**, tick the box here on merge. ⭐ marks the keystone (GUI-free, fully headless-testable).
A new MCP server surface (`mcp/` package) that reuses the GUI-free `model/` core **without PySide6**
and ships as a separate optional component — the `klarpdf-setup.exe` audit surface is untouched.

- [ ] **M39** ⭐ MCP scaffold + read-only core — `mcp/` FastMCP stdio server; headless query/metadata
  tools (`get_info`, `get_outline`, `search`, `extract_text`, `render_page`, `get_form_fields`); no
  PySide6 import on the server path; headless tests — *WSL*
- [ ] **M40** Transform tools — `split` / `merge` / `reorder` / `delete_pages` / `rotate` /
  `fill_form` / `flatten` / `export_images` to an explicit out path (never overwrites source;
  lossless OCR/TOC/forms); headless tests — *WSL*
- [ ] **M41** Redaction + encrypted — `redact_regions` / `redact_text` (destructive + cross-engine
  leak verify) and encrypted-input (`password`) tools; headless leak assertion — *WSL*
- [ ] **M42** Dependency lock + packaging — separate `requirements-mcp.{in,txt}` (GUI lock untouched);
  `klarpdf-mcp` entry point; `.mcp.json` + Claude Desktop config docs; optional `.mcpb` — *Windows*
- [ ] **M43** Hardening + docs — path allowlist, return-size caps, read-only flag, error handling;
  README usage + example agent workflows — *WSL*
- [ ] **M44** Verify + release → tag **v0.11.0** (tool round-trips + leak verify + no-network +
  runs from Code/Desktop) — *Windows*

> Decisions to confirm with owner (see `PLAN.md` §MCP / Agent Bridge roadmap → Decisions): packaging
> (separate vs bundled), write-tools-now vs read-only-first, stdio-only vs HTTP, same-repo vs sibling repo.

## Roadmap — GUI feature tranche R1–R6 (planned; M45–M79)

Spec, per-milestone scope, and the binding **design budgets** (UI / lightness / honesty) in
`PLAN.md` §GUI feature roadmap. Owner-decided **2026-07-18** (23 features approved; radio-button
groups rejected → §Future enhancements); **R6 added 2026-07-22** from the macOS-Preview UI
comparison session (spec + the decided-against list in `PLAN.md` §GUI feature roadmap → R6). Same
conventions: **one PR per milestone**, tick here on
merge; ⭐ = keystone. **Zero new dependencies** across the tranche. Versions provisional
(v0.12.0 → v0.16.0 if the MCP bridge ships v0.11.0 first; assigned at tag time). (**R#** = release —
**G#** already belongs to the Public-Release Readiness milestones below.)

**R1 — "Navigate & Polish"** (prov. v0.12.0)

- [x] **M45** ⭐ Outline sidebar (no TOC → no tab; live `remapped_toc`; scroll tracking) + Go to Page (Ctrl+G). The sidebar becomes a Pages | Outline switcher **only** for a TOC'd document (dock title "Sidebar"; the View-menu/toolbar toggle is renamed "Sidebar" — one stable label for both document kinds); TOC-less docs keep the bare Pages panel. Bundled fix: a reload-in-place now resyncs the file watcher, and a **closed** window can no longer raise the "file changed on disk" prompt (a lingering hidden window's stale watcher + a stray activation event = an unanswerable modal — it deadlocked the offscreen suite, and the pre-existing save-cancel path could trigger it too). — *WSL + WSLg* — [#117](https://github.com/utyagi24/klarpdf/pull/117)
- [x] **M46** Context menus everywhere — selection / link / empty-page / sidebar, hit-test routed.
  `PdfView.contextMenuEvent` delegates to a MainWindow-built menu by hit state: our annotation →
  Remove (the pre-M46 menu, now routed); live selection → Copy / **Highlight Selection** / **Redact
  Selection** (apply-now, vs the toolbar's armed one-shots); internal link → Go to Page N; **external
  link → Copy Link Address** (URI links stay non-clickable — clipboard only, offline guarantee
  intact); bare page → the routed View-menu QActions (fits · rotate · Go to Page). Sidebar menu adds
  Rotate Left/Right (extract joins at M51; paste-object at M59). — *WSLg* — [#118](https://github.com/utyagi24/klarpdf/pull/118)
- [x] **M47** Search-all results panel (page + snippet, click-to-jump; M64 reuses it). The FindBar
  gains a **List All** toggle → a hit-list band under the bar ("p. N   …snippet…"; hidden until
  asked — no dead chrome). Snippets are the hit's text line windowed ±4 words with ellipses; click
  a row → that hit becomes current and is revealed; the panel follows the query as typed, tracks
  next/prev, and empties with the overlay on a structural edit. — *WSLg* — [#119](https://github.com/utyagi24/klarpdf/pull/119)
- [x] **M48** Crop pages — `crop_override` on PageRef; page/selected/all scopes; "hidden, not
  removed" wording; reset offered. Rides the PageRef exactly like `rotation_override` (absolute
  content-frame rect; snapshots for undo; follows reorder **and** cross-window copy/paste);
  materialised via `set_cropbox`; live in the viewer (crop-aware geometry/overlay mapping + clip
  render + baked thumbnails). Armed **Crop Pages** drag → scope prompt (This/Selected/All) with the
  honesty wording; **Remove Crop** restores the full MediaBox — *including a crop the file arrived
  with* (pre-cropped sources also now display by their CropBox, fixing their layout). Odd/even
  book-scan crops stay deferred. — *WSL + WSLg* — [#120](https://github.com/utyagi24/klarpdf/pull/120)
- [x] **M49** Night reading mode (view-only pixmap invert). **View ▸ Night Reading Mode**
  (checkable, remembered app-wide): the page render inverts and the pre-render placeholder goes
  black (no bright flash); the file, print/export renders, and thumbnails keep true colours;
  independent of the followed OS theme. — *WSLg* — [#121](https://github.com/utyagi24/klarpdf/pull/121)
- [x] **R1 polish — the Tools menu** ([#123](https://github.com/utyagi24/klarpdf/pull/123); owner-decided during the stack review): the tranche's one
  budgeted top-level menu (`PLAN.md` §Design budgets) lands with the tools it was reserved for —
  Select/Grab and the armed one-shots (Text Box · Highlight · Redact ×2 · Crop + Remove Crop) move
  out of View into **Tools**; **Rotate Left/Right moves to Edit** beside the other page operations
  (it is a real, saved edit — the View placement implied a view-only spin). Shortcuts and the
  toolbar are unchanged; R3's Markup/Draw and R4's Stamp land straight into Tools.
- [x] **M50** Verify + release → tag **v0.12.0** (version bump + docs; 485 headless tests green on the
  merged main; local onedir build + smoke; CI draft → published) — *Windows* —
  [release](https://github.com/utyagi24/klarpdf/releases/tag/v0.12.0)

**R2 — "Document Hygiene"**

- [x] **M51** Extract selected pages → PDF + Insert blank / duplicate page — *Windows (headless + offscreen GUI)* — [#125](https://github.com/utyagi24/klarpdf/pull/125)
- [x] **M52** Reduce file size — Export ▸ Reduced Size PDF…; true-value presets + custom dpi/quality knobs; actual before→after — *Windows (headless + offscreen GUI)* — [#126](https://github.com/utyagi24/klarpdf/pull/126)
- [x] **M53** Properties + metadata (view · edit · remove; Info dict **and** XMP both) — *Windows (headless + offscreen GUI)* — [#127](https://github.com/utyagi24/klarpdf/pull/127)
- [x] **M54** ⭐ Document encryption — set/change/remove/carry-through, AES-256; optional advisory restriction flags — *Windows (headless + offscreen GUI)* — [#128](https://github.com/utyagi24/klarpdf/pull/128)
- [x] **M55** Verify + ~~release~~ tag — verify done (full headless suite green on merged main;
  owner validated the changes directly); **release cut skipped (owner call, 2026-07-19)** — main
  tagged **`v0.13.0`** only, so the version marks the R2 state without a published release. The
  CI draft the `v*` tag produces stays unpublished; R2's features first ship with the next
  published release. — *Windows*

**R3 — "Markup Tools"**

- [x] **M56** Underline & strikeout (Highlight's quad path; round-trip; Markup ▾ split-button) — *Windows (headless + offscreen GUI)* — [#130](https://github.com/utyagi24/klarpdf/pull/130)
- [x] **M57** ⭐ Pen & shapes model — ink/line+arrows/rect/ellipse descriptors, apply + read-back — *Windows (headless)* — [#131](https://github.com/utyagi24/klarpdf/pull/131) (shows **Closed**: merging #130 with `--delete-branch` removed this PR's *base* branch, which closes it irrecoverably; the commits reached `main` via #132, and the diff/review history is intact)
- [x] **M58** Pen & shapes tools — draw/move/delete, Shift-constrain, Draw ▾ split-button — *Windows (offscreen GUI)* — [#132](https://github.com/utyagi24/klarpdf/pull/132)
- [x] **M59** Copy / paste objects — object clipboard, cross-window, focus-routed Ctrl+C/X/V — *Windows (offscreen GUI)* — [#133](https://github.com/utyagi24/klarpdf/pull/133)
- [x] **M59.5** Markup colour · width · fill — shared sticky `MarkupStyle` + toolbar swatch button for underline/strikeout + pen & shapes — *Windows (headless + offscreen GUI)* — [#134](https://github.com/utyagi24/klarpdf/pull/134)
- [x] **M59.6** Multi-object selection — Objects mode: marquee + Ctrl-click; group restyle / move / delete (one undo each) — *Windows (offscreen GUI)* — [#135](https://github.com/utyagi24/klarpdf/pull/135)
- [x] **M59.7** Object resize — selection handles; single + group bounding-box resize (reusable placement component) — *Windows (headless + offscreen GUI)* — [#136](https://github.com/utyagi24/klarpdf/pull/136)
- [x] **M59.8** Object z-order — Bring to Front / Send to Back for a mark or group (paint + hit order) — *Windows (headless + offscreen GUI)* — [#137](https://github.com/utyagi24/klarpdf/pull/137)
- [x] **M59.9** Polish & fidelity — curated markup colour palettes (Markup ▾) · object opacity (`/CA`) · redaction preview z-order fix · edits keep your scroll place — *Windows (headless + offscreen GUI)* — [#138](https://github.com/utyagi24/klarpdf/pull/138)
- [x] **M59.10** Markup merge — re-marking text folds into the existing mark instead of stacking: same colour absorbs/extends, a different colour recolours what it covers and splits what it doesn't; one Remove, one undo step — *Windows (headless + offscreen GUI)* — [#139](https://github.com/utyagi24/klarpdf/pull/139)
- [x] **M59.11** Preview z-order fidelity — mark paint order follows the page's annotation tuple (not the mark's *type*), so a filled shape hides a text box's text as it does in the saved file, and the M59.8 z-order verbs restack the preview across types — *Windows (headless + offscreen GUI)* — [#140](https://github.com/utyagi24/klarpdf/pull/140)
- [x] **M59.12** Group copy / cut / paste — a multi-selection copies, cuts and pastes as a unit, keeping its arrangement (reverses M59.6's deferral, owner call); one undo step; labels count the set — *Windows (offscreen GUI)* — [#141](https://github.com/utyagi24/klarpdf/pull/141)
- [x] **M59.13** Dropdown-arrow placement — the Markup ▾ / Draw ▾ / style-swatch arrows all sit vertically centred with room from the icon, instead of Qt's two different per-popup-mode positions (one mid-height, one bottom-corner) — *Windows (offscreen GUI)* — [#142](https://github.com/utyagi24/klarpdf/pull/142)
- [x] **M60** Verify + release → **v0.14.0** tagged & published — *Windows*

**R4 — "Stamp, Sign & Watermark"**

- [x] **M61** ⭐ Unified content-draw engine (Way 2: presets = prefilled custom stamps; baked at save).
  `model/content_marks.py`: two descriptors — **`Stamp`** (text + optional rounded frame) and
  **`ImageStamp`** (a placed raster) — that ride the PageRef exactly like an annotation, but bake
  into the page's **content stream** at materialise instead of staying annotations. A **watermark is
  not a third type**: it is either of those with `under=True` (`overlay=False`, so the page's text
  sits on top), applied to every page in a range — the range is the UI's loop, not model state.
  Presets are **prefilled `Stamp`s**, so a placed preset is editable like a hand-made one and there
  is no second code path. Built **vector** (a throwaway one-page PDF placed via `show_pdf_page`)
  rather than the planned high-DPI pixmap: crisp at any zoom, stamp text stays searchable, and
  arbitrary rotation comes free — see `PLAN.md` §R4 "M61 as built". Because a content mark leaves
  nothing author-tagged to read back, a save that writes one is a **point of no return** like a
  redaction (`has_content_marks()` → confirm, write, reload from the clean file, or the next save
  would bake a second copy); the confirm now names which of the two it is committing. Move / resize
  / copy come from the existing `translate_mark` / `scale_mark` primitives. Print, export and live
  thumbnails inherit it via `render_output`. — *WSL (model+tests)* — 31 new tests, 767 green
- [x] **M62** Stamp & watermark UI — placement + dialogs + page-range apply. **There is no second
  placement system**: a content mark is a free-placed rect, so it joins `_OBJECT_TYPES` and inherits
  hit-testing, selection, move, corner-resize, z-order and delete from the M58/M59 object tools —
  which *is* the milestone's "drag rect, move, corner-resize until save", built by reuse. A new
  one-shot **`ArmedTool.STAMP`** shares the draw-gesture path (drag the box; Shift squares it).
  Two flows over the one engine: a **stamp / signature** is composed then *placed*, a **watermark**
  covers whole pages so it applies at once, sized to **each page's own** box. `ui/stamp_dialog.py`
  (text · colour · angle · opacity · frame · page range; presets prefill and stay editable) +
  `util/page_range.py` (`"1-3, 7, 12-"`, shared with M64's scope). Both dialogs state the bake
  boundary in the dialog. Toolbar: one new slot, the **Stamp ▾** split-button (three new icons).
  Live preview renders through the *same generator that bakes at save*; an `under=True` watermark is
  drawn with **multiply** compositing, since Qt cannot paint beneath the page pixmap — the page's
  text darkens through it exactly as in the saved file. — *Windows (headless + offscreen GUI)* —
  46 new tests, 812 green
- [x] **M63** Image stamp / signature — the sign-and-return workflow, on M62's placement UI.
  **"Make white background transparent"** (`white_to_alpha` + threshold) keys the paper out of a
  **phone photo** of a signature, which otherwise arrives as ink on an opaque white rectangle that
  blanks out whatever it covers; a transparent PNG still works through its own alpha, and existing
  alpha is **intersected, never replaced**, so keying can't resurrect pixels the author removed.
  Keying runs at C speed (MuPDF greyscale + one `bytes.translate`) because a 12-megapixel input is
  realistic. `ui/signature_dialog.py` previews through the *same generator that bakes at save*, so
  the threshold is judged on the real result. **Recent signatures store paths only** — KlarPDF keeps
  no copy of a signature image, and deleting the file is the revocation mechanism; the list hangs
  off Stamp ▾ (hidden until non-empty), making the second use **two clicks**, no dialog. Documented
  as ink-equivalent, **not** a cryptographic signature, in the dialog itself. — *Windows (headless +
  offscreen GUI)* — 21 new tests, 833 green
- [x] **M64** Search & redact — **Tools ▸ Find and Redact…**: mark-all → review → redact-checked.
  The dialog drives the **real** `SearchController`, so hits highlight on the page while they are
  reviewed in **M47's results panel, now checkable** — a doubtful row can be clicked to jump to it
  before deciding. Hits arrive **ticked but prunable** (the user asked for all of them, then
  prunes), and **Match case** / **Whole words only** exclude the classic false positive wholesale:
  MuPDF's `search_for` is always case-insensitive and always matches inside words, so both are
  filters over its hits — case compares the text under the box, whole-word is a *geometric* test
  that the touched words don't extend past the hit. **Nothing here is destructive**: checked hits
  become ordinary `Redaction` descriptors (one per page, all in one macro → one undo step) that the
  existing confirmed Save applies, so the app keeps exactly one destructive path. Honesty stated in
  the dialog: text-layer only, **image-only pages are detected and named**, form-field values are
  out of reach, and a box's width hints the removed string's length. — *Windows (headless +
  offscreen GUI, incl. the cross-engine Poppler leak check)* — 23 new tests, 856 green
- [ ] ~~**M65** Verify + release → tag~~ — **skipped by owner call (2026-07-20)**; R4's features
  ship with the next published release. Work continues at **R5 (M66)**.

**R5 — "Foreign Annotations & Form Fields"**

- [x] **M66** ⭐ Foreign-annot infra + delete — `model/foreign_annots.py`, the shared machinery M67
  and M68 consume. **Identity is the hard part**: an annotation's `xref` is renumbered by
  `insert_pdf`, so a descriptor holding one would target the wrong annotation at materialise;
  identity is instead the `/NM` name when the writing tool set one, else a hash of type + rect
  (rounded — a PDF float round-trip is not bit-exact) + contents, with identical twins resolved
  **positionally within the page**. `/NM` must be read from the **object dictionary** —
  `annot.info["name"]` is always empty, a trap that silently disables the preferred path (pinned by
  a test). First verb: **delete**, a `ForeignDeletion` riding the PageRef, applied to the
  materialised copy — so undo restores it, the shared source is never touched, and it works for
  **every** annotation type because it removes rather than rewrites. Fidelity is asserted on the
  surviving annotations' dictionaries **and appearance-stream bytes** (indirect references
  normalised — removing an object necessarily renumbers the rest). Viewer: hit-test + outline +
  right-click **Delete** / **Copy Comment Text**; a pending deletion is dropped from a per-*ordered-
  page* render copy, since a foreign mark lives in the page's own pixmap and no overlay can hide it.
  — *Windows (headless + offscreen GUI)* — 30 new tests, 886 green
- [x] **M67** Move foreign marks — drag any foreign annotation; a `ForeignMove` rides the PageRef
  and translates it at materialise. **The appearance stream is preserved verbatim** — a rich callout
  box moves with zero degradation because nothing re-renders it (asserted byte-for-byte). Not
  `Annot.set_rect`: on the quad-based text-markup types that **silently returns `False`** and leaves
  the rect alone, so a move built on it would fail invisibly on every highlight / underline /
  strikeout. Instead every geometry key in the annotation's dictionary (`/Rect`, `/QuadPoints`,
  `/Vertices`, `/L`, `/CL`, `/InkList`) is translated in place — all of them, or a highlight whose
  rect moved but whose quads didn't gets snapped back by any viewer that regenerates appearances.
  Deltas convert fitz's y-down to PDF's y-up. Fingerprints are **resolved once up front**, because a
  move changes the rect a hash fingerprint is derived from; deletion wins over a move for the same
  mark. Moves **combine rather than stack**, so one descriptor per mark always holds the original
  fingerprint; the viewer reports moved rects so hit-testing follows the mark you can see. — *Windows
  (headless + offscreen GUI)* — 24 new tests, 910 green
- [x] **M68** Adopt-on-edit — double-click a foreign mark of a **modeled type** (highlight ·
  underline · strikeout · ink · line · square · circle · FreeText) → it becomes an ordinary editable
  KlarPDF mark. The mechanism is **entirely M66's**: a `ForeignDeletion` of the original plus the
  parsed descriptor, one macro — so at materialise the original is stripped and ours is re-added
  author-tagged, and from then on it round-trips like a mark we drew. Parsing reuses
  `page_edits.parse_annotation` (extracted from `read_klarpdf_annotations`), so an adopted mark and a
  round-tripped one **cannot drift apart**. Unmodeled types (sticky note, stamp…) stay delete/move
  only and say so. **The degrade warning fires exactly when something would be lost** — rich text,
  a real callout, a reply thread, a dashed border, transparency on a type whose descriptor has no
  opacity field, a non-base-14 font. Getting "exactly" right was the work: `/RD` and `/CL` are
  written *routinely* by PyMuPDF itself, so naive key-presence warned on marks losing nothing, which
  is how a warning stops being read — a callout is now detected by `/IT /FreeTextCallout`, and `/RD`
  is ignored. A pending M67 move is folded into the adopted mark so it doesn't snap back. — *Windows
  (headless + offscreen GUI)* — 36 new tests, 946 green
- [x] **M69** Form-field creation — **Tools ▸ Add Form Field ▸ Text · Checkbox · Dropdown**: compose
  in a small properties dialog (type · name · default · choices), then drag the box with M62's
  placement gesture. `model/form_fields.py`'s `NewField` rides the PageRef and materialises via
  `page.add_widget`. **The output is not a KlarPDF construct** — it is an ordinary AcroForm field, so
  inline filling, lossless value save, edits-aware print and flatten all work on it *by construction*
  (each asserted by running the existing path over a created field, not new code). Creation runs
  **before** the fill pass, so a value typed into a field made in the same session persists; and
  `read_form_fields` reports placed-but-unsaved fields, so the form overlay tints one the moment it
  is drawn. The dialog requires a name (AcroForm keys values by name) and *warns without blocking*
  on a collision. **Radio groups stay rejected** (owner, 2026-07-18) — pinned by a test. — *Windows
  (headless + offscreen GUI)* — 29 new tests, 975 green
- [x] **M69.1** R4 stamp polish — four owner-reported items from the M61–M69 test pass, all in the
  stamp surface. **(1) A rotated mark baked as its own mirror image**: `show_pdf_page`'s `rotate` is
  clockwise-positive while `Stamp.angle`, the dialog spinner and the viewer preview are all
  counter-clockwise, so a −45° stamp tilted one way on the page and the other in the thumbnail —
  which renders the bake, and therefore the saved file. Every watermark shipped so far had its
  documented "bottom-left to top-right" diagonal backwards. **(2) A stamp had no Copy/Cut/z-order on
  its right-click menu**, though it selected, moved, resized and Ctrl+C/X/V'd fine: the menu carried
  a hand-written type list that predated the R4 content marks. It now reads
  `viewer.annotations.OBJECT_TYPES`, and the Remove verb defers to `mark_noun` instead of falling
  back to "Remove annotation". **(3) Stamp lettering could only be sized by resizing the box**, which
  auto-fit turns into a fight with the padding (a 260×100pt box fits "APPROVED" at 44.5pt on *width*,
  leaving 39pt of vertical slack you can only close by making the box narrower). The dialog now has a
  **Size** field — "Fit to box" (the unchanged default) or a point size — and a pinned size makes the
  box hug the text via `content_marks.natural_size`, so a **click** places it and there is no padding
  to fight. A resize carries the pinned size along (smaller axis governs) so the hug survives.
  **(4) The composed stamp/watermark style is now sticky across sessions** through `Settings`
  prefs — text, colour, size, angle, opacity, frame. The **page range is deliberately not
  remembered**: it is the one field where a stale value is destructive.
  **(5) A pinned size now means the size that is drawn**, which the first cut of (3) did not
  deliver. `render_mark_document` built the artwork at the *rect* size, so `show_pdf_page` — which
  fits rotated artwork into its rect — silently rescaled it: **120pt at −45° baked at 40pt**, in a
  box 89pt wider than A4 that could not be centred, with the artwork sitting diagonally inside a
  rect shaped for horizontal text (the reported "resizing distorts the stamp"). Artwork size and
  rect are now separate concerns — `art_size` / `placement_size` / `art_scale` / `art_target_rect`
  — so a pinned mark is placed at its **rotated extent** and baked at scale 1, is never enlarged to
  fill a roomier rect (still shrunk rather than allowed to spill), and `size_for_page` reduces a
  size too large for the paper to the largest that fits so it can be centred. A resize re-derives
  the box from the new size instead of stretching it, so the hug is exact at every step. The
  viewer's preview reads the same `art_scale` the bake applies, so the two cannot drift.
  — *Windows (headless + offscreen GUI)* — 45 new tests, 1020 green
- [x] **M69.2** Watermark interaction + live-thumbnail fixes — two owner-reported defects, neither
  watermark-specific underneath. **(1) Text selection stopped working on a watermarked page**: a
  full-page mark was an ordinary object hit target, so a press *anywhere* grabbed it before text
  selection got a look in (the armed markup tools were unaffected, which is exactly how it presented
  — "highlight and underline work, but selecting text does not"). A mark that blankets the page is
  no longer an interaction target for move / select / marquee; it stays reachable by right-click,
  which offers **Remove watermark** and drops the object verbs that would do nothing to it.
  **(2) Thumbnails did not update**: `populate()` runs on every edit and reset *every* row to a blank
  grey placeholder, re-rendering only the rows in the viewport — so one edit emptied the sidebar and
  most rows stayed empty until scrolled to. Rows now carry their previous render (a structural edit
  carries nothing, since row N is then a different page). Compounding it, a range apply called
  `_note_edit_on` per page, scrolling the view — and the sidebar — to the **last** page of the range;
  it now follows the first. — *Windows (headless + offscreen GUI)* — 11 new tests, 1031 green (1029 after the M69.3 merge folded two dialogs into one)
- [x] **M69.3** ⭐ Stamp and watermark merged into one feature — owner call: *"given the similarity
  I am not seeing much value offering them as two separate features"*. They were never two: the model
  has only `Stamp` / `ImageStamp` and a watermark is one with `under=True`. Of the seven axes on
  which the two dialogs differed, **six were defaults** and exactly one was structural — **how the
  mark is placed**. So `ui/mark_dialog.py` replaces both dialogs with one carrying a **Place**
  control ("Where I drag it" / "Over the whole page") that rewrites the style fields visibly and
  hides Size + Frame for a page-covering mark; `Tools ▸ Stamp / Watermark…` replaces the two menu
  entries. Presets became **one list of words** (`MARK_PRESETS`) prefilling text + colour only —
  ending the collision where "Draft" and "Confidential" sat in *both* lists meaning different
  things. Rationale and the Way-2 argument in `PLAN.md` §R4. Done before R4's first release, while
  it was still free. — *Windows (headless + offscreen GUI)* — 1029 green
- [x] **M69.4** Large-document mark performance — owner-reported: watermarking all pages of a
  **320-page** document left the app sluggish *afterwards*, not just during. Measured on
  `spaceX_prospectus.pdf`: the apply took **10.6s**, and **every subsequent edit cost 10.7s**. Two
  causes, both O(document) where they should be O(what's on screen). **(1) The overlay rasterised
  every content mark in the document on every repaint** — 320 marks to display about two. Content
  marks are the one overlay built by rendering a real PDF rather than from cheap Qt items, and the
  page pixmaps had been lazy since M25 while the overlay never caught up; they now share the view's
  prefetch band (`content_band`) and paint incrementally as pages scroll in (incrementally, not by
  `repaint`, which would drop the object selection on every scroll tick). **(2) The auto-fit search
  was uncached** — `_fit_fontsize` runs a 14-step binary search, each step opening a throwaway PDF
  and embedding a font, so one repaint ran ~4,500 of them to compute the *same* answer 320 times.
  Memoised on its scalar inputs, which fully determine the result. **Apply 10.6s → 0.93s; a later
  edit 10.7s → 0.89s; `view.reload()` 8.8s → 0.16s.** The remaining per-edit cost is the thumbnail
  panel's whole-document `render_output` bake (0.69s of the 0.89s) — see Open follow-ups.
  — *Windows (headless + offscreen GUI)* — 5 new tests, 1034 green
- [x] **M69.5** Whole-page marks: visible by default, and they stop moving the reader — two more
  owner-reported items. **(1) "Behind the page content" produced nothing.** Reported as *"does not
  update the thumbnails, does not save with the document"* — but the mark **was** saved: on
  `spaceX_prospectus.pdf` the watermark's text is in the saved page's text layer and is simply
  **invisible**, because `under=True` puts it beneath *everything the page draws* and most real
  PDFs paint an opaque full-page background. The on-screen preview hid the problem by compositing
  with **multiply on top**, which shows regardless — so the preview and the file genuinely disagreed
  (the M62 code comment claimed they were equivalent; that only holds for a page with a transparent
  background). Whole-page marks now default to **over** the content, which at watermark opacity is
  what a watermark should look like anyway — visible, with the page's own text fully legible
  through it. `under` is unchanged as a capability and still offered, with a tooltip that says when
  it will disappear. Making `under` itself honest is in Open follow-ups. **(2) The current page
  jumped** to the first or last page when marking the whole document. `_note_edit_on` exists to
  follow a mark to the page it landed on; a **range** mark did not land anywhere in particular, so
  it is no longer called for one — marking every page changes nothing about where the reader is.
  — *Windows (headless + offscreen GUI)* — 4 new tests, 1038 green
- [x] **M69.6** "Behind the page content" removed from the UI — owner call: *"I don't see any value
  for under given that we have opacity control in place already."* M69.5 had defaulted it off and
  warned in a tooltip; this drops the control. `Stamp.under` stays an **engine** capability (the M61
  "one engine, over or under" design and its tests are untouched) — the UI simply stops offering a
  control whose ordinary outcome is *nothing appears*, which is worse than dead chrome. The
  previously-recorded fix (bake `under` as an over-content `/BM /Multiply` draw so the file matches
  the multiply-composited preview) was **rejected**: it would not restore the one thing true
  under-print uniquely gives — page images *covering* the mark — and it means hand-built
  `/ExtGState` PDF code in the **save path**, adding exactly the cross-renderer variability that
  §M61's "no cross-renderer calibration" owner call exists to avoid. A pre-M69.6 settings file
  carrying `"under": true` is ignored rather than resurrecting the mode. — *Windows (headless +
  offscreen GUI)* — 1038 green
- [x] **M69.7** Two use cases, two controls — owner call: *"There are basically two use cases…
  so we don't need the third option of dragging to stamp."* Dragging a rectangle was only ever a way
  of **sizing** a text stamp; once a point size is on the dialog it is a second answer to a question
  already answered, and the worse one — a dragged box sets the size only indirectly, through the
  padding auto-fit leaves (which is what M69.1 was reported about). `Kind` is now *Stamp (click to
  place)* or *Watermark (whole page)*; the size field drops its "Fit to box" position and defaults to
  36pt; a stamp is centred on the **press point**, not the middle of a stray drag, and draws no
  rubber band to advertise a box it will not take. **Signature/image placement and M69 field
  creation keep the drag** — neither has a font size, so the box is genuinely how you size them, and
  `fontsize=0` stays the engine's auto-fit sentinel under them. — *Windows (headless + offscreen
  GUI)* — 5 new tests, 1042 green
- [x] **M69.8** Angle slider — owner request. The angle keeps its spin box and gains a slider
  beside it: two views of **one** value (the slider to find a tilt by eye, the spin box to say one
  exactly), synced without either driving the other in a loop. Ticks at the quarter turns plus a 3°
  snap to them, because 0° and ±45° are most of the angles anyone wants and exactly the ones a free
  drag over a −180…180 range is least likely to land on; the snap is short enough that a deliberate
  38° still sticks. Degrees are whole now, so the two views cannot disagree. — *Windows (headless +
  offscreen GUI)* — 5 new tests, 1047 green
- [x] **M69.9** Angle sign corrected + one shape for every numeric row — two owner items.
  **(1) The angle sign was backwards.** `Stamp.angle` was clockwise-positive — `-45` gave the
  north-east diagonal — while the field's own docstring said counter-clockwise and the watermark
  default was written `-45` with the comment "bottom-left to top-right". The owner asked the obvious
  question (*"shouldn't north-east be +45?"*); it should, so the **descriptor** was corrected rather
  than the docs bent to fit it. `+45` is now north-east, `-45` south-east. That cancelled the
  negation `apply_content_marks` had carried since M69.1 and added one in the preview (Qt's
  `setRotation` is clockwise-positive in a y-down scene). Free to fix: R4 has never shipped.
  **(2) Angle and Opacity are one shape.** They had drifted into two layouts — a slider stacked over
  a read-only label, and a slider beside an arrow-spinner — which is what made the dialog read as
  cluttered. Both are now `_SliderField`: a slider plus a typable, **spinner-free** value box, built
  once so the rows are identical by construction rather than by care. The box is a spin box with its
  buttons hidden, not a line edit, which keeps range clamping and number parsing for free.
  — *Windows (headless + offscreen GUI)* — 1047 green
- [x] **M69.10** Mark dialog geometry warning — owner-reported: switching Kind logged
  `QWindowsWindow::setGeometry: Unable to set geometry … Resulting geometry: …` on every switch. Qt
  was promising Windows a minimum **48px shorter** than the layout then needed. Cause: the wrapped
  bake note under the form. A word-wrapped `QLabel`'s height is a function of its **width**, which
  `minimumSizeHint` does not consult — so a dialog narrow enough for the note to re-wrap taller
  advertised a minimum it could not actually live in, and showing/hiding the Size + Frame rows made
  Qt ask for a geometry the platform then had to override. Fixed by pinning the note's minimum
  height to what it needs at the dialog's narrowest allowed width, giving the dialog that width
  floor, and resizing deliberately on a Kind switch instead of leaving the window manager to infer
  it. Cosmetic (a console warning, nothing misrendered) but it was the layout genuinely fighting
  itself. Nothing to do with the document being edited — dialog geometry only.
  — *Windows (headless + offscreen GUI)* — 3 new tests, 1050 green
- [x] **M69.11** Crash: picking a recent signature from the dropdown — owner-reported. The handler
  called `_rebuild_signature_menu`, which does `menu.clear()` and therefore **destroys the submenu's
  `QAction` objects — including the one whose `triggered` signal was still being delivered**. That is
  undefined behaviour: a hard crash on Windows, surfacing under PySide as *"Internal C++ object
  (QAction) already deleted"*. `Clear List` carried the identical hazard, being an action in the menu
  its own handler empties. The rebuild is now deferred by a zero-delay timer so the signal unwinds
  before anything is deleted. Reproduced first (triggering the *oldest* entry, which reorders the
  list and so forces a real rebuild), and both regression tests fail on the pre-fix code with that
  exact RuntimeError. — *Windows (headless + offscreen GUI)* — 3 new tests, 1053 green
- [x] **M69.12** Signatures made dragging other objects lag — owner-reported, worse with each
  signature added, *"even if added to other pages"*. A content mark is the one overlay built by
  **rendering a real PDF**, and it was re-rasterised on **every repaint** — and a drag repaints. So
  the cost of dragging anything scaled with how many signatures were in view, which had nothing to
  do with what was being dragged. Measured: **~98ms per transparent signature per repaint**, linear
  (4 signatures = 392ms *per repaint*). Two fixes. **(1) The rasterised artwork is cached**, keyed
  on `(mark, on-screen width)`; the descriptors are frozen dataclasses, so a moved or restyled mark
  is a different key and can never be served the stale image of its previous self — no explicit
  invalidation. Bounded LRU, so a document of distinct marks costs memory like one. **(2)
  `_drop_white`'s alpha intersection** ran a Python `zip`/`min` **per pixel** whenever the image
  *already had* transparency — the exact "full transparency" case — making a transparent PNG
  **4.6× slower** than an opaque one (110ms vs 24ms) in a module whose docstring warns that a Python
  per-pixel loop "would stall the UI for seconds". Now `map(min, …)`, which runs the loop inside
  CPython (~1.6×); MuPDF has no per-pixel alpha-intersect and numpy is not a dependency, so this
  stays the floor — the cache is what makes it stop mattering. **Repaint after the first: 98ms per
  signature → 0.0–0.7ms regardless of count.** — *Windows (headless + offscreen GUI)* — 3 new tests,
  1056 green
- [x] **M69.13** Signature removal slider ran backwards — owner-reported: *"transparency increases
  if I keep the slider towards zero and decreases as I drag it right."* It did. The slider exposed
  `ImageStamp.white_threshold` **raw**, and a threshold is a luminance *cutoff*, so lowering it
  removes more: measured on a grey ramp, far-left removed **129/256** pixels and far-right **4/256**.
  It also had **no label and no tooltip** — a bare slider under a checkbox, which reads as "how
  much", the one thing it was not. Now labelled **"Remove"** and inverted, so right removes more;
  the mapping to the cutoff (`(100 - strength) / 100`) lives at the dialog edge so
  `ImageStamp` keeps the plain threshold semantics the renderer wants. The default is unchanged —
  strength 15 is the old 0.85 — and the reach is the same (1–50 spans the old 0.99–0.50), so this
  inverts the control without quietly re-tuning it. — *Windows (headless + offscreen GUI)* — 2 new
  tests, 1058 green
- [x] **M69.14** A created form field is an ordinary object — owner-reported: fields could not be
  moved, even before saving. The **model had always been ready**: `PLACEABLE_TYPES` lists `NewField`,
  `translate_mark` and `scale_mark` both handle it, and its `bounding_rect` docstring says it exists
  *"so the viewer's shared hit-test / outline helpers work on it unchanged"*. But the viewer's
  `OBJECT_TYPES` tuple was never told, so a field was invisible to select / move / resize / marquee —
  the **third** time that hand-maintained list has gone stale behind a new descriptor (stamps at
  M69.1, watermarks at M69.2). A field is drawn by the *form* overlay rather than the annotation
  overlay, which is what let it go unnoticed. `mark_noun` also gained "form field", so the menu no
  longer offers "Remove newfield". **Note the deliberate mode split**, now pinned by a test: in
  **Objects** mode a click moves a field; in **Select** mode the form overlay claims it so you can
  type into a field you just created (M69's feature). — *Windows (headless + offscreen GUI)* —
  6 new tests, 1064 green
- [x] **M69.15** A freshly placed mark is selected — owner-reported: a form field could not be
  selected right after creating it (workaround: switch mode and marquee around it). **Placement
  committed with nothing selected**, so the next click went to the *form* overlay to be filled —
  which, to someone who had just drawn the box, looked like the field could not be selected at all.
  Paste has selected-after-add since M59.7 for exactly this reason (*"the add reloads the view,
  which clears any selection"*); placement never did. Now it does, for fields and content marks
  alike. Second half: a press on an **already-selected** object now leads the Select-mode priority
  list, so a field can be dragged without switching to Objects mode — gated on *selected*, so a
  click on an unselected field still means "type into it" (M69's in-session fill, pinned by a test).
  **A note on the diagnosis**: the first attempt looked like it did not work, because the test drove
  `finish_draw()` directly — the view disarms on mouse *release*, so the tool stayed armed and ate
  the next press. The owner re-testing is what caught that; the tests now go through real press /
  move / release events. — *Windows (headless + offscreen GUI)* — 3 new tests, 1067 green
- [x] **M69.16** A created field grabs like a text box — owner-reported: *"if I am in text select
  mode, clicking over it takes me into text entry mode… I have to click precisely on the edge, which
  is hit and a miss most of the times."* M69.15 had gated the Select-mode grab on the field being
  *already selected*, which left no way to select it by clicking in the first place — so the border
  was the only handle. A field **you created this session** now follows the contract a text box has
  had since M20: **press to move, double-click to type into it**. A **document's own** form fields
  are untouched — single-click still fills them, which is what filling in a form requires. The
  distinction is who owns the thing under the cursor, and it lives in one predicate
  (`PdfView._grabs_before_form`). — *Windows (headless + offscreen GUI)* — 2 new tests, 1068 green
- [x] **M70** Verify + release → tag **v0.15.0** (version bump + docs; 1068 headless tests green on
  the merged main; audit green; CI draft → published) — *Windows* —
  [release](https://github.com/utyagi24/klarpdf/releases/tag/v0.15.0)

**R6 — "Simplify & Read"** (planned; prov. v0.16.0)

- [x] **M71** Two-tier toolbar — the single ~29-slot bar is now two tiers, the R6 budget revision
  made real: at rest the app shows only the **reading bar** (Sidebar · Save · Undo/Redo · the zoom
  cluster unchanged · Rotate L/R · a **Markup** toggle · Find); the **markup bar** the toggle
  summons carries the whole kit (Select/Grab/Objects · Text Box · Markup ▾ · Draw ▾ · style swatch ·
  Stamp ▾ · Redact ×2 until M72 merges them). Open/Print and the page-op buttons left the toolbar —
  the File/Edit menus and the M46 context menus carry every removed verb, with **no Pages-panel
  action strip** (owner call); whether Open returns beside Save is the one-line call at the M71
  review. Visibility is remembered app-wide exactly like the sidebar — only an *explicit* toggle
  persists. The arm/visibility interplay is honest both ways: arming a kit tool from the Tools menu
  **summons** the hidden bar (the armed state must be visible on the lit button), and dismissing the
  bar **disarms** a kit tool (an invisible armed state is a trap) while leaving the menu-only CROP
  arm and the Select/Grab/Objects base mode alone (Grab is a reading tool). View ▸ Markup Toolbar
  (Ctrl+Shift+M); new `markup` icon. — *Windows (headless + offscreen GUI)* — 12 new tests, 1082
  green ([#159](https://github.com/utyagi24/klarpdf/pull/159))
- [x] **M72** One Redact tool — the markup bar's two Redact slots are **one armed tool** with
  Preview-style gesture detect: `ArmedTool.REDACT` resolves **at press** on the existing text-hit
  path (`TextSelection.has_word_at`, exact containment — a margin press must mean *block*, never
  nearest-snap) — press-on-word → the text-flow redaction, press-elsewhere → the rubber-band block,
  and the resolution swaps in the concrete tool so the armed tint / release / one-shot disarm are
  exactly the explicit tools'. A press off any page stays armed **and unresolved**; a no-commit
  click restores the combined arm (the resolved gesture can't lock in); a rotated view resolves to
  block (text selection is disabled there). The slot's button lights for the whole redact family
  and a click on the lit button always disarms; a live selection applies immediately (the M46
  contract). Tools ▸ Redact Text (Ctrl+Shift+R) / Redact Block unchanged — the slot is toolbar
  sugar, not a third verb. — *Windows (headless + offscreen GUI)* — 14 new tests, 1096 green
  ([#160](https://github.com/utyagi24/klarpdf/pull/160))
- [x] **M73** Sticky markup arming — Highlight / Underline / Strike Out / Pen **stay armed across
  gestures** (Preview's repeat-use behaviour): a new `ArmedTool.sticky` property names the quartet,
  and the view's two release paths (drag-over-text apply, draw commit) keep the arm instead of
  one-shot disarming — passage after passage, stroke after stroke, on one arm. Three exits (owner),
  all riding pre-existing paths: the lit button again (`_arm_tool`'s toggle) · Esc · arming any
  other tool; mode switches and dismissing the markup bar (M71) exit too. Placement and destructive
  tools stay **one-shot** — including the M72 combined slot's resolved text gesture — because a
  stuck destructive mode is a trap. The armed state stays visible throughout (lit button + M71's
  summon-on-arm), and the quartet's tooltips say so. — *Windows (headless + offscreen GUI)* — 13
  new tests, 1109 green ([#161](https://github.com/utyagi24/klarpdf/pull/161))
- [x] **M74** ⭐ Arrow ends as style — Preview treats arrowheads as *line style*, and it is right:
  `MarkupStyle.line_ends` joins the M59.5 picker as an **Arrowheads** submenu (None · Start · End ·
  **Both**), lines-only by the applicability-follows-the-model rule; **Arrow leaves Draw ▾**
  (`ArmedTool.ARROW` removed, four tools remain, the Tools menu drops the entry) — one Line tool
  draws every variant, and **Both is new capability**. WYSIWYG holds: the live preview draws the
  style's heads from the first drag pixel (`_line_path` shared with the overlay); `restyle_mark`
  takes `line_ends` (`None` = keep) so a selected line's ends change in place like colour — one
  undo step — and `from_mark` loads a selected line's ends into the picker. **Zero file-format
  change**: materialise already wrote `set_line_ends` per boolean and the parser read both back
  (M57's model was built for this), so pre-R6 arrows reopen editable + unchanged (pinned) and a
  both-ended line bakes as an `/LE` OpenArrow pair (asserted on the saved file). — *Windows
  (headless + offscreen GUI)* — 12 new tests, 1121 green
  ([#162](https://github.com/utyagi24/klarpdf/pull/162))
- [x] **M75** Find bar match options — **Match case** + **Whole words** on the interactive FindBar:
  M64's existing `SearchController.search` filters, surfaced at last, with the M64 dialog's labels
  and both off by default (exactly the pre-M75 behaviour). A toggle **re-runs the live query in
  place** — highlights, the "N of M" label and a visible List All panel follow without retyping;
  next/prev/goto operate on the filtered set by construction; the bar's kept-query revive inherits
  the kept toggles; the Find-and-Redact dialog keeps its own independent checkboxes (pinned). —
  *Windows (headless + offscreen GUI)* — 7 new tests, 1128 green
  ([#163](https://github.com/utyagi24/klarpdf/pull/163))
- [x] **M76** Markup context menu — right-click on marked text offers Preview's change set, scoped
  to the clicked mark's words: the curated **highlight colours** (recolour in place through the
  M59.10 merge — trim/absorb, never stacking — or lay a highlight under a clicked underline/
  strikeout) + **No Highlight** (Preview's slashed swatch) + **Underline** / **Strike Out**
  toggles (add in the sticky Markup ▾ line colour, or remove). Removal is the merge's new inverse
  — `remove_markup` trims covered same-type marks by exactly the span (a middle cut splits, full
  coverage drops), so a wider underline keeps its tail beyond the clicked words; `marks_over` is
  the shared tick-state query. One `SetAnnotationsCommand` per action = **one undo step**;
  identical repaints and no-layer removals are no-ops. "Remove <noun>" still closes the menu. —
  *Windows (headless + offscreen GUI)* — 10 new tests, 1138 green
  ([#164](https://github.com/utyagi24/klarpdf/pull/164))
- [x] **M76.1** Markup context menu reshaped to **Preview's swatch rows** (owner feedback from the
  M78 test pass: the M76 layout offered *two* removal wordings at once — "No Highlight" beside
  "Remove highlight"). Now three sections — Highlight · Underline · Strike Out — each a header over
  one **horizontal row of colour dots** (`SwatchRowAction`) ending in the standard **slashed
  no-colour dot**: a colour recolours/lays the layer through the merge, the slashed dot removes it
  (verb on hover — the owner asked for better than the word "None", and a glyph with "Remove
  highlight" as its tooltip beats any label), a ring marks each layer's current state (radio
  semantics). The rows are the complete change set, so the trailing "Remove <noun>" entry is gone —
  **exactly one removal path per layer** — and the line layers gain direct colour choice their
  toggles never had. Offscreen render inspected (layout/rings/slash correct; header tofu is the
  Windows offscreen font stack, confirmed by a control grab). — *Windows (headless + offscreen
  GUI)* — 12 tests rewritten incl. the one-path regression, 1162 green
  ([#167](https://github.com/utyagi24/klarpdf/pull/167))
- [x] **M76.2** HUS colours **always visible** in the Markup ▾ menu + the armed-highlight preview
  colour (two owner reports from the same test pass). (1) Picking a colour then arming was *two*
  menu trips (colours hid in submenus): the `SwatchRowAction` gains a state-setting mode
  (`include_remove=False, close_on_pick=False`) and the dropdown now carries the highlight + line
  colours as **always-visible dot rows that don't close the menu on a pick** — one menu visit, and
  no click at all when the ring already sits on your colour (`set_active` moves it in place); the
  old `_add_color_submenu` is gone. (2) An armed Highlight previewed a fixed yellow that only
  "converted" on release — it now reads the sticky colour the window keeps on the view
  (`PdfView.highlight_preview_color`, seeded at init + synced on change), so the chosen colour shows
  from the first pixel of drag (underline/strikeout keep the selection blue by design). — *Windows
  (headless + offscreen GUI)* — tests updated (menu rows + wired-preview case), 1162 green
  ([#168](https://github.com/utyagi24/klarpdf/pull/168))
- [x] **Dashed stroke style** (owner request while testing R6; extends M74's "line ends as style").
  `Line` / `Shape` / `InkStroke` gain a `dashed` bool: PyMuPDF bakes it as a PDF `/BS /D` border on
  line/square/circle/ink and reads it back on reopen, so the solid/dashed choice round-trips with no
  extra model state (the array is re-derived from the width at bake, scaled so a thick line dashes
  boldly). The style picker's **"Width" sub-menu becomes "Line Style"** — the three thicknesses plus
  a Solid/Dashed radio group (independent groups); `_drawn_pen` dashes the preview to match the bake;
  draw + restyle-in-place carry it like colour. **Trap fixed**: PyMuPDF silently writes no `/D` for
  *float* dash values, so `_dash_array` returns ints (the round-trip test catches it). Both renders
  verified to match (overlay + baked PDF). — *Windows (headless + offscreen GUI)* — 16 new tests,
  1178 green ([#169](https://github.com/utyagi24/klarpdf/pull/169))
- [x] **M78.1** View-mode navigation fixes (three owner reports from the R6 test pass). (1) **A
  slideshow step is a row, not a page index**: in Two-Page view pages 1|2 share a row, so
  `goto_page(current + 1)` scrolled to the offset already on screen and `_update_current` snapped
  back to the row's first page — click, Right and Down looked dead while the backward keys (which
  land on the previous row) worked. `PdfView.step_slide` now moves `_layout_rows()` entries and
  keeps the projected row (`_slide_row`) as the mode's own position instead of re-deriving it from
  the scroll offset; Home/End jump to the ends. (2) **The wheel steps whole slides too** — free
  scrolling a one-page-per-screen mode could come to rest straddling two pages, and from a straddle
  the page under the viewport centre isn't the page being read, so the next click stepped from the
  wrong one (the reported "jumps and comes back / click twice"); one detent = one slide, hi-res
  deltas accumulated to a detent, and `step_slide` pins `set_current_page` to the row it projected
  so a clamped scroll near the end can't drift it. (3) **A coasting wheel can no longer undo a
  click** — the reported "clicked eight times and the first slide never moved, worse each time I
  flick to the end and back". A flywheel wheel keeps emitting for seconds after the hand leaves it,
  and those events walked the deck back under the reader; a click/key step now parks the wheel
  until it has actually gone quiet (250 ms, `event.timestamp()`, falling back to our own clock so
  an unstamped platform fails *open*). Reproduced end-to-end on the owner's brochure — click → page
  2 → coast → page 1 before, holds at page 2 after. A step onto the row already showing costs
  nothing now, so a burst piling into either end of the deck renders nothing at all. (4) A
  **double-click** steps a slide: impatient clicking makes every second press a double-click, which
  the press handler never saw. (5) **F5 during a slideshow is a no-op** instead of a leave-and-
  re-enter blink, and from Full Screen it switches the projection on **in place**. (6) **F11 exits
  Full Screen**, not just Esc: a menu action's shortcut is live only while its menu bar is
  *visible*, which the mode hides — the window now carries the Full Screen + Slideshow actions
  itself, and F11 toggles chrome-free reading whichever mode is up (during a slideshow it leaves
  that too). — *Windows (headless + offscreen GUI)* — 9 new tests (each verified red without the
  fix), 1187 green ([#170](https://github.com/utyagi24/klarpdf/pull/170))
- [x] **M75.1** Find bar: **Whole words** decides *what the query is*, and the hit verbs go dead
  without hits (two owner reports from the R6 test pass). (1) The toggle was only M64's
  boundary filter — a multi-word query was a phrase either way, so ticking it on "electric heater"
  changed nothing visible. It now also splits the query: **off**, the query is a list of words and
  any of them matches on its own (every *electric*, every *heater*, still inside longer words);
  **on**, it is one unit — the phrase, and only as whole words. A single-word query is untouched in
  both states, so nothing M75 shipped changes meaning. `search()` runs `search_for` per term and
  re-orders a multi-term page into reading order (next/prev must walk the page as it is read), with
  duplicate boxes collapsed and the case filter comparing against the term that found the hit. The
  Find-and-Redact dialog drives the same controller, so its identically-labelled toggle gains the
  same meaning. (2) **Previous / Next / List All are disabled while the search has no hits** —
  dead verbs, not clickable no-ops (the M77 rule) — and the results panel goes with them, an empty
  band saying nothing the "No results" label doesn't; it returns still listing when the query
  matches again. — *Windows (headless + offscreen GUI)* — 4 new tests, 1191 green
  ([#171](https://github.com/utyagi24/klarpdf/pull/171))
- [x] **M63.1** Signature transparency is **remembered per image** (owner report: "Signature/Image
  does not remember the transparency setting last used"). How much paper to drop out of a scan is a
  property of that scan, not of the day, so the settings ride beside the recent list keyed by path
  (`Settings.signature_settings`) — *beside*, never inside it, so "paths, never pixels" still
  describes the list itself, and the tuning is pruned with its entry (a file deleted to revoke a
  signature leaves nothing behind). Choosing a known image in the dialog restores its checkbox +
  slider; an image with no memory — anything just browsed to — keeps whatever is on the controls,
  which is the last-used setting, since the dialog opens on the most recent entry (so a *re-scan*
  starts tuned). The **Recent Signatures menu** gains the most from it: with no dialog it had
  nowhere to re-tick "make white background transparent", so a photo signature came back with its
  paper on — it now places exactly as last time, which is what that path claims to be. — *Windows
  (headless + offscreen GUI)* — 7 new tests, 1197 green
  ([#172](https://github.com/utyagi24/klarpdf/pull/172))
- [x] **M63.2** The recent submenu is **"Recent Signatures / Images"** (owner: the old "Recent
  Signatures" assumed every insert is a signature). It is named for the command that fills it —
  Signature / Image… — because the list holds whatever that placed, seals and logos included, and a
  list named for one of its uses reads as a filter. *Not* "Recent Inserts", the alternative
  considered: **Insert already means pages** in this app (Edit ▸ Insert Pages from File… / Insert
  Blank Page), so it would name the one thing the list never holds. Internal names and the stored
  `recent_signatures` key are unchanged — renaming the key would silently drop every existing
  list. — *Windows (headless + offscreen GUI)* — 1 new test, 1198 green
  ([#173](https://github.com/utyagi24/klarpdf/pull/173))
- [x] **M77.1** The Annotations tab lists **text markups only** — highlights, underlines,
  strike-outs and notes (owner: it was listing drawn lines and shapes too). A markup is a
  *passage*: the row's snippet reads back what you marked, and a list of them is a reading of the
  document's margin. A pen stroke, line, shape, text box, stamp or form field is a placed
  **object** — no passage to read back (its row said "p. 3 · line"), visible where it sits, and
  arranged through the Objects mode that exists for exactly that. `is_listed` /
  `is_listed_foreign` in `organize/annotations_panel.py` are the single definition, shared with
  the tab's existence check (`_doc_has_listed_marks`), so a document of drawings alone gets **no
  tab** rather than a tab over an empty list. Foreign markups list on the same terms — including
  Squiggly (a wavy underline we cannot draw but can list) and sticky notes, which is "notes"
  arriving from another tool ahead of our own. — *Windows (headless + offscreen GUI)* — 2 new
  tests + 4 rewritten, 1199 green ([#174](https://github.com/utyagi24/klarpdf/pull/174))
- [x] **M79.1** Sidebar: **no title bar, Pages by default, the rest on demand** (three owner calls
  from the R6 test pass). (1) The dock's title strip is gone — a label reading "Sidebar" over the
  sidebar is chrome about chrome, and its ✕ was a third way to do what the toolbar button and
  View ▸ Sidebar already do, the only one leaving no lit button behind to say how to get it back
  (an empty, zero-height title widget; the window title survives for screen readers). (2) **Pages
  alone by default**: Outline and Annotations no longer mount by themselves, so the panel is the
  same shape on every document. (3) The sidebar toolbar button becomes a **split button** whose ▾
  carries a checkable entry per optional tab, remembered app-wide (`sidebar_tabs`), so the choice
  follows the reader across documents and launches. A tab shows when it is **asked for *and*
  applies** (M45/M77's rules intact), asking for one **opens a hidden sidebar** (as arming a markup
  tool summons the markup bar), and the ▾ lists only tabs this document could show — dropping the
  arrow entirely when it could show none, so a tick never produces nothing. — *Windows (headless +
  offscreen GUI)* — 9 new tests + 2 rewritten (the toggle now rides as a widget), 1208 green
  ([#175](https://github.com/utyagi24/klarpdf/pull/175))
- [x] **M79.2** The dropped ▾ really goes (owner: on a document with no outline and no marks the
  arrow was still drawn, and clicking it did nothing). M79.1 dropped the *menu*; a QToolButton draws
  the split section from its **popup mode**, so the button kept its 14 px arrow over an empty menu —
  a dead click, and exactly the greyed-out chrome the sidebar work was removing. The mode now flips
  with the menu. Making the *width* follow took a re-polish as well: neither of the two caches
  between the mode and the geometry notices `setPopupMode` (a bare property write) — QStyleSheetStyle
  holds the rule sizing `::menu-button` until the widget is re-polished, QToolButton holds its own
  sizeHint until a menu is attached or detached — so a returning arrow was drawn squeezed over the
  icon. Verified at the pixel: 31 px plain, 44 px split, across open · first mark · undo · redo. —
  *Windows (headless + offscreen GUI)* — 1 new test + 1 strengthened (it asserted `menu() is None`,
  which passed while the arrow was still painted), 1209 green
  ([#177](https://github.com/utyagi24/klarpdf/pull/177))
- [x] **M79.3** A new mark **offers** the Annotations tab; it no longer mounts one (owner: "don't
  add the Annotations tab automatically as soon as I add annotations — just the dropdown option").
  M77 had the tab track edits live *including its own existence*, so marking up a page pushed a
  panel into the sidebar mid-stroke. The existence rule is still M77's — asked for **and**
  something to list — but "asked for" is now read from *this window* rather than from the app-wide
  preference, which is exactly where it went wrong: a preference carried in from another document
  is not a request for a tab on this one. So a new mark only makes the ▾ entry offerable, while a
  window already carrying the tab keeps tracking live — deleting the last mark **through** the tab
  folds the empty panel away (owner, second report: an empty tab is the dead chrome this tranche
  removes) and undoing that deletion brings the panel back with the mark, rather than leaving the
  reader a restored annotation and no list. Putting the tab away by hand is different from it
  folding on empty: once unticked it stays away, and a later mark only offers it again. Each tick
  now mirrors **what the sidebar is showing** rather than the stored preference — the two part
  company the moment a mark makes the
  entry offerable on a document whose tab is not mounted, and a tick drawn from the preference would
  sit checked over an absent tab, one click from doing the opposite of what it says. The preference
  still decides what mounts at open, so the ask still follows the reader across documents. The ▾
  entries lost their "Tab" suffix in the same pass (owner) — each is named for the tab it produces,
  matching that tab's own label; "Tab" was our vocabulary for an entry that already sits under the
  sidebar button with a tick beside it. — *Windows (headless + offscreen GUI)* — 6 new tests +
  9 rewritten (they pinned the summon-on-mark behaviour this replaces), 1214 green
  ([#178](https://github.com/utyagi24/klarpdf/pull/178))
- [x] **M71.1** New icon for the **Markup toggle** — a page with a pen, chosen by the owner from six
  candidates rendered at real toolbar size (16/20/24 px, lit and unlit, beside their neighbours).
  The old pencil-in-a-circle failed twice over: the circle dominated at 20 px so the pencil inside
  read as a blob, and a bare pencil **is the Pen tool's icon**, two slots away on the bar this
  button opens. The button summons the whole kit, so it must depict none of its tools — the page is
  what makes it "mark up this document" rather than one more instrument, and it also rules out the
  obvious chisel-marker glyph, which is already Highlight's. One file (`ui/icons/markup.svg`);
  the toolbar and View menu both resolve it by name and the tint follows the theme. — *Windows
  (headless + offscreen GUI)* — 1208 green ([#176](https://github.com/utyagi24/klarpdf/pull/176))
- [x] **M77** Annotations sidebar tab — a third tab beside Pages | Outline listing **every mark
  in the document** as "p. N · type · snippet" rows: ours from the PageRef descriptors (text
  markups read their covered page text as the snippet; boxes/stamps/fields their own), foreign
  through a provider seam (the overlay's live `foreign_annotations` — deletions dropped, moves
  applied), so `organize/annotations_panel.py` depends only on the model + a callable. **The tab
  exists only while the document has marks** (inapplicable chrome is invisible): `_doc_has_marks`
  short-circuits on our marks and scans foreign *presence* once per source page (sources are
  immutable in-session; cache cleared on `_reset_to_file`). Tracks edits/undo live **including its
  own existence** — the first mark summons it, undoing the last dismisses it, with remounts keeping
  the active tab by label. Click = the M47 pattern: jump + real object selection for free-placed
  marks, the outline for foreign, plain jump for text-anchored/page-wide. — *Windows (headless +
  offscreen GUI)* — 11 new tests, 1149 green
  ([#165](https://github.com/utyagi24/klarpdf/pull/165))
- [x] **M78** View modes — the reading modes Preview offers, all **view-only** (the M49 principle;
  nothing is undoable because nothing is an edit): **Full Screen** (F11, checkable — menu bar, both
  toolbars, sidebar, find bar step aside; F11/Esc restores exactly the chrome that was up, and
  programmatic hides never rewrite the remembered sidebar/markup prefs) · **Slideshow** (F5 —
  chrome-free + one page per screen at Fit Page; click / Right/Down/Space/PgDn advance, Left/Up/
  PgUp back, clamped; selection, forms, links and menus inert; Esc exits and the prior zoom/fit
  returns exactly — Esc reaches MainWindow because the view leaves it unconsumed when nothing is
  armed) · **Two-Page View** (facing pairs 1|2, 3|4 … in the ordinary window: `_build_scene` lays
  out by row, `page_and_local_at` disambiguates within a row by x, and Fit Width/Page frame the
  whole spread via `_fit_dims`; session-only, like rotation). Surfaced in the View menu + the
  bare-page right-click menu as the same QActions. — *Windows (headless + offscreen GUI)* — 11 new
  tests, 1160 green ([#166](https://github.com/utyagi24/klarpdf/pull/166))
- [x] **M78.2** Nudge objects with arrow keys — arrow-move the object selection (1 pt / Shift 10 pt, page-clamped); a held key coalesces to one undo, taps stay separate — *Windows (headless)* — 10 new tests ([#180](https://github.com/utyagi24/klarpdf/pull/180))
- [x] **M78.3** Resize text-box width — a lone box's right-edge handle reflows the text (left pinned, height auto-fits); group resize leaves text boxes unstretched; the fold survives save+reopen (`auto_width` inferred) — *Windows (headless + offscreen GUI)* — 9 new tests ([#181](https://github.com/utyagi24/klarpdf/pull/181))
- [x] **M78.4** Icon polish — new Grab (filled outline hand, separated fingers) / Text Box (T in a box) / Pen (pencil on a baseline) glyphs, chosen from rendered candidates; verified light + dark, re-tint intact — *Windows (offscreen render)* — `pen` added to the icon test roster + 3 non-blank/QtSvg-safe checks ([#182](https://github.com/utyagi24/klarpdf/pull/182))
- [x] **M78.5** Highlight/Underline/Strike arming swatches — Markup ▾ becomes three colour rows; a pick sets the verb's colour **and** arms it (marking a live selection at once, and moving the split-button face); underline vs strike colours now independent — *Windows (headless + offscreen GUI)* — 4 new/rewritten tests ([#183](https://github.com/utyagi24/klarpdf/pull/183))
- [x] **M78.6** Split the markup style button → three markup-bar buttons over one shared `MarkupStyle`: Line Styling (thickness · dash · arrowheads) · Colors (Border + Fill rows + custom + No Fill) · Opacity (a slider showing/accepting an exact %); selecting an object loads its style into all three — *Windows (headless + offscreen GUI)* — new/updated tests across 7 suites ([#184](https://github.com/utyagi24/klarpdf/pull/184))
- [ ] **M79** Verify + release → tag (prov. **v0.16.0**) — *Windows*

## Public-Release Readiness — go open-source under AGPL-3.0 (planned)

**The repo is public** as of **2026-07-17**, as an `AGPL-3.0-or-later` project — the flip (G8) is done.
**G1–G5, G7 and G8 are complete; `G6 Part 2` (enrol in GitHub Sponsors) is the only item left**, and
nothing depends on it. Independent of the v0.11.0 MCP roadmap — this track landed first. Full
execution detail in `PLAN.md` §Public-release readiness (plan introduced in
[#83](https://github.com/utyagi24/pdfproj/pull/83)). **One PR per item**; tick the box on merge and
append the PR link. Steps were ordered — **G1 ran first, while the repo was still private**, and the
flip itself was a manual GitHub action, not a PR. The pre-public hygiene scan was clean (no secrets in
tree or history; `.gitignore` excludes build artifacts/wheels/`report.json`; CI uses `${{ secrets.* }}`).

- [x] **G1** Commit-author cleanup (**done** — history rewrite, no PR) — `git filter-repo` mailmap
  remapped the maintainer's personal email (162 commits) **and** the older bare-form no-reply (80
  web-commit authors) onto the canonical `<id>+username@users.noreply.github.com`, author + committer;
  content byte-identical (trees unchanged); `main` + all 15 release tags force-pushed; verified **0**
  personal-email / bare-form authors remaining and all Releases intact. Done first, while private. — *WSL/Windows*
- [x] **G2** Branding — name + logo (**done** — all three parts). **Decision gate: closed.** The product is **KlarPDF** (*klar* =
  "clear" in German / the Scandinavian languages); `pdfproj` was the dev codename. An earlier pick,
  *sheaf*, was dropped for clashing with existing GitHub PDF-processing projects — the marks were drawn
  under that name, hence the design-source title in `assets/brand/BRAND.md`. Name mapping: display
  string **`KlarPDF`** (window title, About, installer AppName) · drawn wordmark lowercase `klarpdf`
  (BRAND.md §Type) · repo + exe + `%LOCALAPPDATA%` leaf + single-instance id `klarpdf` · ProgID
  `KlarPDF.Document`.
  - [x] **Part 1 — visual assets** — toolbar glyph set (24 replaced + 3 new: `about`, `donate`,
    `export`), app mark, regenerated `packaging/klarpdf.ico`, and `assets/brand/` (tokens + `BRAND.md`
    + icon spec). No code changes; icon filenames unchanged. — *WSL* —
    [#91](https://github.com/utyagi24/pdfproj/pull/91)
  - [x] **Part 2 — the name sweep** — app strings (`app.py`, window title, `platform_integration.py`
    single-instance id, `store/settings.py` `%LOCALAPPDATA%` leaf); `PDFPROJ_AUTHOR` → `KLARPDF_AUTHOR`
    with the tag **value** `klarpdf`, and its five `*_klarpdf_annotations` helpers; asset filenames
    (`klarpdf.svg` / `.ico` / `.spec`); `installer.iss` AppName/Publisher/ProgID + **a fresh `AppId`
    GUID**; `build.ps1`, `release.yml`, `pyproject.toml`, `tasks.py`, tests, docs. **No back-compat
    shims**: the app has never been distributed (single user), so the settings dir and the annotation
    tag change outright rather than carrying a migration. The fresh `AppId` stops Inno treating the
    renamed setup as an in-place upgrade (which would skip the old uninstaller's registry +
    config-dir cleanup and reuse its install dir) — **uninstall `pdfproj` before installing
    `KlarPDF`** (`RELEASE.md`). Historical release notes, shipped artifact names and repo URLs are
    left as-is: they record what actually shipped. — *WSL* — [#92](https://github.com/utyagi24/pdfproj/pull/92)
  - [x] **Part 3 — GitHub repo rename** (**manual**) — `gh repo rename klarpdf` (**not**
    `gh repo edit --rename`, which doesn't exist), run while the repo was still private.
    `utyagi24/pdfproj` → `utyagi24/klarpdf`; GitHub redirects the old URLs, so the historical
    release/PR links above keep resolving, and PRs/issues/releases are untouched (a *repo* rename is
    safe — it is a *branch* rename that closes an open PR, see #86). Re-pointing each checkout's
    `origin` is **optional** — GitHub redirects the old remote over **both** HTTPS and SSH — but if you
    do it, **keep the checkout's existing protocol**: the Windows checkout is HTTPS
    (`https://github.com/utyagi24/klarpdf.git`), the WSL checkout is SSH
    (`git@github.com:utyagi24/klarpdf.git`). Rewriting an SSH remote to the HTTPS form makes git ignore
    `~/.ssh/config` and start prompting for a password that cannot work (password auth was removed in
    2021). Local working-directory names deliberately keep the old codename. — *GitHub*

  Feeds the copyright name (G3), the About name+logo (G4), and the community files (G5).
- [x] **G3** License + notices — root `LICENSE` (full AGPL-3.0-or-later) + `THIRD_PARTY_LICENSES`
  (PyMuPDF AGPL-3.0, PySide6 + shiboken6 LGPL-3.0, pypdf BSD-3; cross-ref `DEPENDENCIES.md`) +
  README license section + badge + build-from-source pointer (uses the G2 name) — *WSL* — [#95](https://github.com/utyagi24/klarpdf/pull/95)
- [x] **G4** In-app About + Open-Source Licenses dialog — Help menu (`main_window.py`) → **About**
  (mark + version + AGPL + the AGPL §15-16 no-warranty notice + a *tagged* corresponding-source link,
  never `main`), **Open-Source Licenses** (the bundled texts, one tab each, offline), **View Source**.
  New `ui/about.py` (dialogs) + `util/resources.py` (freeze-aware `resource_path()`, mirroring
  `ui/icons.py`'s `_MEIPASS` dance); `packaging/klarpdf.spec` `datas` ships `LICENSE` +
  `THIRD_PARTY_LICENSES` to the bundle root. Links open via `QDesktopServices` on **user click only**,
  so the offline / no-telemetry guarantee holds. `tests/test_about_dialog.py` drives the real Help
  menu and simulates `sys._MEIPASS` — the frozen path the headless suite otherwise never executes —
  and asserts the spec still bundles both texts, since a `datas` regression is invisible to CI.
  — *WSL + WSLg* — [#97](https://github.com/utyagi24/klarpdf/pull/97)
- [x] **G5** Community-health files — `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant), `.github/ISSUE_TEMPLATE/*` + `pull_request_template.md`
  — *WSL* — [#96](https://github.com/utyagi24/klarpdf/pull/96)
- [x] **G5.1** Governance — **open source, closed to PRs** (decided after G5 landed; rationale in
  `PLAN.md` §Public-release readiness). **Issues open to everyone** (bugs / security / feature
  requests); **PRs restricted to the maintainer + invited collaborators**, others auto-closed by
  `.github/workflows/close-external-prs.yml` (`pull_request_target`, *no* checkout — that trigger runs
  with write access, so it must never execute a fork's code). DCO 1.1 is now **deemed accepted** on
  submission: no `Signed-off-by`, no CI check — G5 had claimed unsigned PRs "cannot be merged", which
  nothing enforced. **Open decision:** the DCO grants no rights, so the *first* merged contribution by
  anyone other than the maintainer — a collaborator included — forecloses commercial relicensing
  (PLAN.md's Artifex hatch). A CLA or explicit relicensing grant must be settled **before** a
  collaborator's first merge. **Repo settings (manual):** keep Issues **on**; enable private
  vulnerability reporting; Wiki/Projects/Discussions off; **no interaction limits** (they would block
  the public from opening issues, defeating the policy). — *WSL + GitHub settings* —
  [#98](https://github.com/utyagi24/klarpdf/pull/98)
- [ ] **G6** Donations — repo + product — let users support the project. **Platform: GitHub Sponsors**
  (decision gate closed) — same host as the source link, so supporting the project introduces no
  third-party domain into the app or the repo. Open-source + donations is fully AGPL-compatible.
  - [x] **Part 1 — the code** — `.github/FUNDING.yml` (`github: utyagi24`); README **Support** section
    + Sponsor badge; **Help ▸ Donate…** (extends the G4 Help menu, grouped with *View Source* — the
    separator splits "opens a dialog" from "hands a URL to the browser") + an About-dialog link, both
    via `QDesktopServices.openUrl` on **user click only, so the offline / no-telemetry guarantee holds**
    (the app opens no socket itself). A test asserts `FUNDING.yml` and `ui/about.py` name the *same*
    account — see the trap below. — *WSL + WSLg* — [#107](https://github.com/utyagi24/klarpdf/pull/107)
  - [ ] **Part 2 — enrol the account in GitHub Sponsors (manual; the actual gate)** — Stripe +
    identity + GitHub review, so it takes **days**, not minutes: start it early. Verify with
    `gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'` → currently **`false`**.
    **The trap:** `https://github.com/sponsors/utyagi24` does **not** 404 without a listing — GitHub
    silently **redirects it to the plain profile page**, so a dead Donate link is indistinguishable
    from a working one, in the app and in CI. Nothing automated can catch this; hence the one-time
    gate in `RELEASE.md` §3, to be checked before the first release that ships the menu item.
    **This is now the only thing standing between the project and a finished public-release track**
    (G1–G5, G7, G8 all done). Two consequences of that ordering, both live today:
    - the in-app **Help ▸ Donate…** and About link **already shipped** in v0.10.0, so they currently
      lead to a silent redirect to the plain profile — harmless, but wrong;
    - the repo **Sponsor button** (the check moved here from G8) needs the repo public — ✅ since
      2026-07-17 — *and* the listing. So the listing is now the sole remaining blocker for it.
    Verify both once enrolled: `hasSponsorsListing` → `true`, and the Sponsor button renders on the
    repo page. — *GitHub account*
- [x] **G7** Lock-in identity, hygiene & branch rulesets — keeps the G1 scrub true, permanently. Three
  parts: repo-side (one PR), manual identity, and the ruleset. The ruleset was *decided and
  pre-authored* here and **completed at G8**, where its premise turned out to be wrong: G7 recorded
  that a ruleset *cannot exist* while private (a 403 on `GET .../rulesets`), but two were already
  active — the 403 was the **API**, not the rulesets. See Part 3 and G8.
  - [x] **Part 1 — repo side** — `.gitignore` gains `*.pfx *.pem *.key .env *.log` (nothing matches
    today; `*.pfx` is the live one — Authenticode signing is a carried follow-up and a cert lands in the
    tree as exactly that). New `.github/workflows/author-email-guard.yml` fails a PR whose author (or
    committer) email is not a GitHub no-reply — the backstop under the local `user.email` and the
    account-level push block, both of which are per-machine/per-account state a fresh clone or a new
    machine silently loses. `test.yml` loses the `paths-ignore` on its **`pull_request`** trigger and
    gains an in-job docs-only gate, so the `pytest` check reports on every PR without running the suite
    on markdown — the prerequisite for requiring it below (rationale + the two-workflow trap it avoids:
    `PLAN.md` §Public-release readiness). — *WSL* — [#106](https://github.com/utyagi24/klarpdf/pull/106)
  - [x] **Part 2 — identity (manual, per machine + account)** — **all four verified.** `git config
    user.email` = the no-reply on **both** checkouts: **Windows** (local + global =
    `12071588+utyagi24@…`) and **WSL** (owner-verified — the two checkouts are bridged only by git, so
    each is its own machine). GitHub account ▸ Emails: **Keep my email addresses private** ✅ +
    **Block command line pushes that expose my email** ✅ (owner-verified in the UI — the first needs a
    `user` API scope this checkout's `gh` lacks, the second has **no API at all**, so neither is
    machine-checkable from here; the public profile `email` field now reads empty, which is consistent).
    With the push block on, the *server* now rejects an exposing push — Part 1's `emails` workflow
    remains the backstop for what these per-machine/per-account settings cannot cover (a fresh clone,
    a new machine, a changed account).
  - [x] **Part 3 — the `main` ruleset, decided + pre-authored** (**reconciled** at G8 — see the
    correction there). Payload lives at [`.github/rulesets/main.json`](.github/rulesets/main.json)
    with the rationale beside it, so the rules are reviewable in a diff rather than clicks made once
    in the UI. In short: **block force-push** + **restrict deletions** + **require the `pytest` and
    `emails` checks**, **empty bypass list**; *require review* dropped while solo (G5.1), *linear
    history* rejected (the project merges with merge commits), *signed commits* deferred (unsigned
    today; needs GPG/SSH for the no-reply identity first). Full rule-by-rule reasoning: `PLAN.md`
    §Public-release readiness. **This part's premise was wrong, corrected at G8:** it recorded that a
    ruleset *cannot exist* before the flip, from `GET /repos/utyagi24/klarpdf/rulesets` → **403
    "Upgrade to GitHub Pro or make this repository public"**. The 403 was about the **API**, not the
    rulesets — two were already active. — *WSL* — [#107](https://github.com/utyagi24/klarpdf/pull/107)
- [x] **G8** Flip to public (**manual; not a PR**) — **done 2026-07-17. The repo is public.**
  (`gh repo edit --visibility public --accept-visibility-change-consequences`; the second flag is
  **required**, `gh` refuses `--visibility` without it.) Every item in G8's own scope is complete and
  verified; the Sponsors-listing check that used to sit here is G6's and moved there. Docs landed in
  [#111](https://github.com/utyagi24/klarpdf/pull/111) + [#112](https://github.com/utyagi24/klarpdf/pull/112)
  — the settings themselves are not a PR.
  - [x] **Private vulnerability reporting** — enabled; `GET
    repos/utyagi24/klarpdf/private-vulnerability-reporting` → `{"enabled": true}`. Flip-gated (404s on
    a private repo — public-repo-only). Until it was on, `/security/advisories/new` 404'd — and that
    URL is the *only* reporting channel `SECURITY.md`, `CODE_OF_CONDUCT.md`,
    `.github/ISSUE_TEMPLATE/config.yml` and the auto-close workflow's comment advertise.
  - [x] **Secret scanning + push protection** — both `enabled`. Also flip-gated (they need paid GHAS
    while private; free once public).
  - [x] **Dependabot security updates** — `enabled` (`PUT repos/utyagi24/klarpdf/automated-security-fixes`).
    **Not in the original G8 list**; added here because it is free on a public repo and is the
    mechanical version of the pypdf advisory (**GHSA-jm82-fx9c-mx94**) that was caught by hand in
    v0.9.4. See Open follow-ups.
  - [x] **The `main` ruleset — reconciled, not created.** G7's premise was **wrong**: it recorded that
    rulesets cannot exist before the flip (`GET .../rulesets` → 403 *"Upgrade to GitHub Pro or make
    this repository public"*). That 403 was the **API** being unavailable on a private free repo, not
    an absent ruleset — **"Protect Main"** (id 18233952) and **"Protect Tags"** (id 18234032) had been
    active since **2026-06-28**. So `deletion` + `non_fast_forward` were already in place and the only
    rule G8 actually added was **`required_status_checks`: `pytest` + `emails`**, `PUT` into the
    existing ruleset rather than `POST`ed as a second overlapping one. Its pre-existing `pull_request`
    rule (0 approvals) was **kept** — it enforces `CLAUDE.md`'s "never leave edits on `main`", and G7
    only ever meant to decline required *reviews*. `main.json` is now a **mirror** of the live
    ruleset. Verify: `gh api repos/utyagi24/klarpdf/rulesets/18233952 --jq '.rules'`.
    **Likely-but-unconfirmed:** a private free repo *creates* rulesets without *enforcing* them (it
    fits the 403, and G1's force-push succeeding weeks after `non_fast_forward` was listed) — so `main`
    plausibly acquired real protection only at the flip. `PLAN.md` §Public-release readiness.
  - [x] **The two dynamic README badges went live** — verified: `tests: passing`, `release: v0.10.1`.
    They read the GitHub API, so while private they rendered *"repo or workflow not found"* and
    *"inaccessible"*; the flip fixed both with no edit.
  - ~~Add repo description/topics~~ — **done ahead of the flip** (needs no public repo): description
    set + 13 topics. Nothing to do here at G8.
  - [x] **Upload the social preview** (**manual — the one G8 step a human had to click**; there is no
    REST API for it) — Settings ▸ General ▸ Social preview ▸ `assets/brand/social-preview.png`.
    **Done + verified**: the repo's `og:image` meta now resolves to `repository-images.
    githubusercontent.com` (a custom upload; the default would be `opengraph.githubassets.com`) and the
    served bytes are **sha256-identical** to `assets/brand/social-preview.png`, 1280×640. This is what
    renders when the repo link is pasted anywhere. Re-check with:
    `curl -sL https://github.com/utyagi24/klarpdf | grep 'og:image'`.
  - ~~Check the Sponsors listing is live~~ — **moved to G6 Part 2**, where it belongs: it verifies
    *G6's* deliverable, and was only parked here because the Sponsor button also needs a public repo
    — a condition now permanently satisfied. Leaving it here would make G8's status a lagging shadow
    of G6's, which is exactly the duplication `CLAUDE.md` §"update in exactly one place" forbids. — *GitHub*

## Open follow-ups (carried)

Carried items — none block work:

- **The thumbnail sidebar bakes the *whole document* on every edit.** `ThumbnailPanel._edited_render`
  calls `PyMuPDFEngine.render_output(vdoc)` — a full materialise of every page — so the panel can
  rasterise the handful of thumbnails actually on screen. On a 320-page document that is **~0.69s per
  edit** (measured at M69.4 on `spaceX_prospectus.pdf`, after the mark-rendering fixes took the rest
  of the edit cost from 10.7s to 0.89s); it is now the single largest remaining O(document) cost per
  edit, and it grows with page count on *every* edit, not just marks. The fix is a **per-page bake**:
  an engine entry point that materialises only the pages asked for, which the panel would call for
  its visible rows the same way `_render_visible_thumbs` already scopes rasterising. Deferred because
  it touches the shared `render_output` path that print and save also use, so it wants its own
  milestone and its own verification rather than riding a bug-fix branch. Not a blocker: 0.89s per
  edit on a 320-page document is responsive, and small documents are unaffected.

- **Upstream PyMuPDF bug: URI links with an unbalanced paren are dropped by `insert_pdf` /
  `insert_link`** (unescaped re-serialisation of the URI text; console shows "skipping bad link /
  annot item N"; seen in the wild in a novaPDF-produced file whose URI is `http://www.adobe.com)`).
  Worked around in `model/links_remap.py`: the materialise link pass re-adds any URI link
  `insert_pdf` dropped, with the text pre-escaped (round-trips correctly). Consider reporting
  upstream to PyMuPDF; if fixed there, the restore pass simply finds nothing missing.

- **A stale `vendor/wheels/` silently shadows the lock in `-Offline` builds.** Found while building
  v0.10.0: the local cache still held `pypdf-6.13.2` (the wheel the v0.9.4 security bump replaced), so
  `build.ps1 -Offline` failed with *"Could not find a version that satisfies pypdf==6.13.3"*. The repo
  was correct (`requirements-win.txt`, `vendor/wheels-sources.md` both say 6.13.3); `vendor/wheels/` is
  **gitignored**, so it drifts per-machine and never gets re-vendored by a `git pull`. CI is unaffected
  (it fetches fresh). Fix by re-running `build.ps1` **without** `-Offline` once, then re-running with
  it. Worth a guard in `build.ps1` that diffs the cache against the lock before an offline build.
- **Flaky test: `test_single_instance.py::test_handoff_opens_window_in_resident_instance`.** Failed
  once, passed on rerun (timing-sensitive Windows IPC: a race between the resident instance binding its
  socket and the forwarding launch connecting). **Could not reproduce** — 5 isolated runs + several
  full suites all green. **Stakes rose twice at G8** and this is now the most actionable follow-up:
  the repo is public, so a flake is a red X on a stranger's first CI run — *and* `pytest` is now a
  **required status check**, so a flake no longer merely looks bad, it **blocks the merge** until
  someone re-runs the job. (The bypass list is empty by design, so there is no override; the escape is
  re-running the check, or flipping `enforcement` to `disabled` and back.) Note the check runs on
  `ubuntu-latest` while the observed flake was on Windows.
- **Dependency vuln: pypdf → 6.13.3** → ✅ fixed in **v0.9.4**: bumped `pypdf` 6.13.2 → 6.13.3
  (**GHSA-jm82-fx9c-mx94**, Moderate memory-DoS in the `pypdf` fallback edit engine), recompiled the
  locks + regenerated `vendor/wheels-sources.md`, and removed the audit-gate ignore.
- **Clean-machine install** — the one deferred M9 verification item: run `klarpdf-setup-x64.exe` on a
  Windows VM with **no Python and networking disabled** (Win10 Home has no Sandbox → VirtualBox /
  spare machine / fresh local user). Everything else in the Verification matrix is green.
- **CI action versions** → ✅ done in M15: `actions/checkout@v6`, `setup-python@v6`,
  `upload-artifact@v7`, `softprops/action-gh-release@v3` (all Node-24).
- **Code signing** — deferred Authenticode step (removes the SmartScreen prompt); needs a cert, so
  it stays deferred (still unsigned through v0.4.0); slots into `release.yml` before packaging
  (PLAN.md §Packaging §5). Carry to a future release once a cert is available.
- **App icon** → ✅ shipped in **M10** (v0.2.0).
- **Product features** → view/print/annotate/redact/round-trip/flatten-export all shipped (M0–M31.5).
  The next tranche is **scheduled** in §Next roadmap above: image import/export (v0.8.0, M35–M37);
  encrypted PDFs + GoTo-link remap (v0.9.0, M32/M33, re-scoped out of v0.7.0).
  Still **deferred beyond** the roadmap (PLAN.md §Future enhancements): new-field form designer,
  drop-to-open in the main view, re-encryption on save, cross-app annotation editing (M31 round-trip
  edits only KlarPDF's own author-tagged marks; foreign annotations are shown but not editable — a
  deliberate fidelity-safety boundary, see PLAN.md).

- **Help ▸ Donate… points at a GitHub Sponsors listing that does not exist.**
  `gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'` returns **false**, and
  `/sponsors/utyagi24` **redirects to the plain profile** rather than 404ing — so the dead link is
  indistinguishable from a working one and no test can catch it. This is exactly the one-time gate
  in `RELEASE.md` §3, which was **not** satisfied before v0.12.0 shipped the menu item, nor before
  v0.14.0. Owner call at v0.14.0: **ship as-is, fix separately.** Clearing it is a GitHub account
  step (enable the Sponsors listing), after which the gate check returns true and the gate block can
  be deleted from `RELEASE.md`.

- **Flaky test: the save path's `os.replace` hits `[WinError 5] Access is denied`.** Seen twice
  while preparing v0.14.0, both times in `tests/test_external_change.py` (different tests each
  time: `test_save_no_external_change_does_not_prompt`, then `test_save_overwrite_proceeds`), and
  only in **full-suite** runs — the file passed 4/4 in isolation, and a clean full re-run was green
  both times. `_write_to` writes a temp file next to the target then `os.replace`s it, so a
  transient lock on the freshly written temp (real-time antivirus is the usual suspect on this
  machine) fails the rename; the resulting "Save failed" modal is what the conftest guard reports.
  Environmental rather than a code defect, but it is a **release-gate** annoyance and shares the
  "red X on a stranger's CI run" stakes with the single-instance flake above. Worth a bounded
  retry around the `os.replace` before declaring the save failed.

- **`MarkupStyleButton.style()` shadows `QWidget.style()`** (it returns the `MarkupStyle`
  dataclass). Harmless in paint — Qt calls the C++ method — but any Python-level `button.style()`
  gets the wrong object, and it already cost one debugging detour in M59.13 (workaround:
  `QWidget.style(btn)`). Rename to `markup_style()` when that file is next touched.
