# KlarPDF — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

> **This file is the single source of truth for live status** — shipped versions, per-release notes,
> release links, milestone ticks, and open follow-ups. `PLAN.md` (design/spec) and `CLAUDE.md`
> (conventions) **link here, they don't restate it** — see CLAUDE.md §How we work → "Where things live".

**Status:** ✅ **v0.14.0 "Markup Tools" shipped** — the GUI tranche's **R3 (M56–M60) is complete**,
and because the **v0.13.0 tag was cut but never published** (owner call), this release also delivers
**R2 "Document Hygiene" (M51–M54)** to users: extract / insert-blank / duplicate pages, **Reduced
Size** export, document **Properties + metadata** editing (both stores), and **AES-256** password
protection. R3 itself is the markup kit: **underline & strikeout** on Highlight's text-quad path,
a **pen** plus **lines / arrows / rectangles / ellipses**, a shared **colour · width · opacity ·
fill** picker with curated per-verb text-markup palettes, and full **object editing** — marquee and
Ctrl-click multi-select, move, **resize** (single + group, about the bounding box), **z-order**
(Bring to Front / Send to Back, which is both paint *and* hit order), and group **copy / cut /
paste** that preserves the arrangement. Everything bakes into the saved PDF and reopens editable.
Four fixes came out of owner testing and shipped in the same tranche: re-marking text now **merges**
into the existing mark instead of stacking a second layer ([#139](https://github.com/utyagi24/klarpdf/pull/139)),
mark paint order in the preview now follows the model's z-order rather than the mark's *type* — so a
filled shape hides a text box exactly as it does in the saved file ([#140](https://github.com/utyagi24/klarpdf/pull/140)),
group copy/paste reversed an earlier deferral ([#141](https://github.com/utyagi24/klarpdf/pull/141)),
and the toolbar's dropdown arrows share one position ([#142](https://github.com/utyagi24/klarpdf/pull/142)).
Release: <https://github.com/utyagi24/klarpdf/releases/tag/v0.14.0>. 737 headless tests green
(1 expected skip — the Poppler `pdftotext` cross-check, absent on Windows).

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

## Roadmap — GUI feature tranche R1–R5 (planned; M45–M70)

Spec, per-milestone scope, and the binding **design budgets** (UI / lightness / honesty) in
`PLAN.md` §GUI feature roadmap. Owner-decided **2026-07-18** (23 features approved; radio-button
groups rejected → §Future enhancements). Same conventions: **one PR per milestone**, tick here on
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
  remembered**: it is the one field where a stale value is destructive. — *Windows (headless +
  offscreen GUI)* — 30 new tests, 1005 green
- [ ] **M70** Verify + release → tag — *Windows*

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
