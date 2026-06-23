# pdfproj — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

**Status:** ✅ **v0.9.3 shipped** (open-behavior patch) — milestones **M0–M38 complete** (v0.1.0 = M0–M9,
v0.2.0 = M10–M15, v0.3.0 = M16–M19, v0.4.0 = M20–M22, v0.5.0 = M23–M26, v0.6.0 = M27–M30,
v0.7.0 = M31 + M31.5 + M34, v0.8.0 = M35–M37, v0.9.0 = M32 + M33 + M38). Releases:
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.3> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.2> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.1> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.0>. **v0.9.3** is an open-behavior patch
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
**annotation round-trip editing** (reopen a saved doc → move / edit / remove our `PDFPROJ_AUTHOR`-tagged
highlights & text boxes; the page render strips our baked marks so the editable overlay is the single
source of truth, and text selection reads that stripped page) and a flatten **Export → PDF**
(`File ▸ Export`; bakes annotations + form widgets into page content via `Document.bake()`,
text-preserving — a locked counterpart to the round-trip). v0.6.0 "Rich Text & Live Preview" adds
**styled text boxes**, **live thumbnails**, and **dynamic theme icons**. v0.5.0 "File Safety & Output"
adds **Revert to Saved**, an **external-change warning**, and **edits-aware printing**. v0.4.0
"Annotate & Redact" adds text **highlight** + **text boxes** and **true destructive redaction**.
**Next:** **v0.10.0 — "MCP / Agent Bridge"** (M39–M44, planned) — expose pdfproj's PDF engine to
Claude Code / Claude Desktop / agentic clients as a local **MCP server** (`PLAN.md` §MCP / Agent
Bridge roadmap). Other deferred items live in `PLAN.md` §Future enhancements.
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

- [x] **M31** ⭐ Annotation round-trip editing — reopen → move/edit/remove our `PDFPROJ_AUTHOR`-tagged annotations (strip-then-re-add at materialize); page render + text selection read the stripped page so the editable overlay is authoritative (no double-draw / stale-position select) — *WSL (model+tests) + WSLg* — [#46](https://github.com/utyagi24/pdfproj/pull/46)
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

## Roadmap — v0.10.0 "MCP / Agent Bridge" (planned)

Spec + architecture in `PLAN.md` §MCP / Agent Bridge roadmap. Same conventions: **one PR per
milestone**, tick the box here on merge. ⭐ marks the keystone (GUI-free, fully headless-testable).
A new MCP server surface (`mcp/` package) that reuses the GUI-free `model/` core **without PySide6**
and ships as a separate optional component — the `pdfproj-setup.exe` audit surface is untouched.

- [ ] **M39** ⭐ MCP scaffold + read-only core — `mcp/` FastMCP stdio server; headless query/metadata
  tools (`get_info`, `get_outline`, `search`, `extract_text`, `render_page`, `get_form_fields`); no
  PySide6 import on the server path; headless tests — *WSL*
- [ ] **M40** Transform tools — `split` / `merge` / `reorder` / `delete_pages` / `rotate` /
  `fill_form` / `flatten` / `export_images` to an explicit out path (never overwrites source;
  lossless OCR/TOC/forms); headless tests — *WSL*
- [ ] **M41** Redaction + encrypted — `redact_regions` / `redact_text` (destructive + cross-engine
  leak verify) and encrypted-input (`password`) tools; headless leak assertion — *WSL*
- [ ] **M42** Dependency lock + packaging — separate `requirements-mcp.{in,txt}` (GUI lock untouched);
  `pdfproj-mcp` entry point; `.mcp.json` + Claude Desktop config docs; optional `.mcpb` — *Windows*
- [ ] **M43** Hardening + docs — path allowlist, return-size caps, read-only flag, error handling;
  README usage + example agent workflows — *WSL*
- [ ] **M44** Verify + release → tag **v0.10.0** (tool round-trips + leak verify + no-network +
  runs from Code/Desktop) — *Windows*

> Decisions to confirm with owner (see `PLAN.md` §MCP / Agent Bridge roadmap → Decisions): packaging
> (separate vs bundled), write-tools-now vs read-only-first, stdio-only vs HTTP, same-repo vs sibling repo.

## Open follow-ups (carried)

Carried items — none block work:

- **Clean-machine install** — the one deferred M9 verification item: run `pdfproj-setup.exe` on a
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
  edits only pdfproj's own author-tagged marks; foreign annotations are shown but not editable — a
  deliberate fidelity-safety boundary, see PLAN.md).
