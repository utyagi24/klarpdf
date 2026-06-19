# pdfproj — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

**Status:** ✅ **v0.4.0 shipped** — milestones **M0–M22 complete** (v0.1.0 = M0–M9,
v0.2.0 = M10–M15, v0.3.0 = M16–M19, v0.4.0 = M20–M22). Release:
<https://github.com/utyagi24/pdfproj/releases/tag/v0.4.0>. v0.4.0 "Annotate & Redact" adds text
**highlight** + **text boxes** (move / re-edit / auto-grow), and **true destructive redaction**
(region + text-flow) with cross-engine leak verification and a redacted-save "point of no return".
Annotate/redact tools are **one-shot armed** gestures; cross-window page drag/paste **carries
per-page edits**. **Next:** **v0.5.0 → v0.7.0** planned (M23–M34) — see the roadmap below and
`PLAN.md` §Next roadmap. **Open follow-ups** (carried items) are at the bottom.

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

## Releases — v0.2.0 ✅ → v0.3.0 ✅ → v0.4.0 ✅ → v0.5.0 → v0.6.0 → v0.7.0

Spec + architecture in `PLAN.md` (§Shipped roadmap for v0.2–v0.4, §Next roadmap for v0.5–v0.7). Same
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
- [x] **M25** Better printing + preview + Print-to-PDF — **print preview** (`QPrintPreviewDialog` reusing `render_to_printer`; a separate dialog from the native print dialog's preview pane, whose "doesn't support print preview" placeholder is a Qt limitation) + print scaling/fit + `QPrinter` PDF destination ("Save as PDF"), all on one **edits-aware** render so preview/print/export show annotations / form values / redactions (today's path renders the raw source page + rotation only — a not-yet-saved redaction would otherwise print the original) — *WSL logic; Windows print validation* — [#39](https://github.com/utyagi24/pdfproj/pull/39)
- [ ] **M26** Verify + release → tag **v0.5.0** — *Windows*

**v0.6.0 — "Rich Text & Live Preview"** (planned)

- [ ] **M27** ⭐ Rich text boxes — font/size/colour + bold/italic/underline + box outline & fill (formatting bar on the inline editor) — *WSL (model+tests) + WSLg*
- [ ] **M28** Live thumbnails — thumbnails reflect the page's edited state (annotations/redactions/fills) — *WSLg*
- [ ] **M29** Dynamic theme icons — runtime OS light↔dark re-tint (verify/complete `refresh_for_theme`) — *WSLg + Windows*
- [ ] **M30** Verify + release → tag **v0.6.0** — *Windows*

**v0.7.0 — "Round-trip & Documents"** (planned)

- [ ] **M31** ⭐ Annotation round-trip editing — reopen → move/edit/remove our `PDFPROJ_AUTHOR`-tagged annotations (strip-then-re-add at materialize) — *WSL (model+tests) + WSLg*
- [ ] **M32** Encrypted / password PDFs — detect `needs_pass`, prompt, `authenticate` on open — *WSL + WSLg*
- [ ] **M33** Internal GoTo-link remap — `toc_remap` → `links_remap` (fix cross-run link targets; clean headless keystone) — *WSL (model+tests)*
- [ ] **M34** Verify + release → tag **v0.7.0** — *Windows*

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
- **Product features** → view/print/annotate/redact all shipped (M0–M22). The next tranche is
  **scheduled** in §Next roadmap above (M23–M34): rich text, live thumbnails, theme icons, revert,
  disk-change warning, print preview + print-to-PDF, annotation round-trip editing, encrypted PDFs, GoTo-link remap.
  Still **deferred beyond** the roadmap (PLAN.md §Future enhancements): new-field form designer,
  drop-to-open in the main view, re-encryption on save.
