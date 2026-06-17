# pdfproj — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

**Status:** ✅ **v0.1.0 shipped** (2026-06-17) — all milestones **M0–M9 complete**. Offline Windows
installer released: <https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0>. **Next up:** the
**v0.2.0 → v0.3.0** roadmap (M10–M18) is planned below; see `PLAN.md` §Next-release roadmap for the
spec + the page-edit-layer architecture. **Open follow-ups** (carried items) are at the bottom.

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

## Next release — v0.2.0 → v0.3.0 (planned)

Spec + architecture in `PLAN.md` §Next-release roadmap. Same conventions: **one PR per milestone**,
tick the box here on merge. Numbering continues M10+. ⭐ marks the keystone (most risk, GUI-free
core, fully headless-testable).

**v0.2.0 — "Polish, Print & Forms"**

- [x] **M10** Icons — app `.ico` + toolbar icons (undo/redo, zoom, cut/copy/paste) — *WSLg + Windows (frozen-exe icon)* — [#18](https://github.com/utyagi24/pdfproj/pull/18) (frozen-exe icon validated at M15)
- [ ] **M11** Zoom UX — live magnification % indicator + Actual-Size / 100% reset (Ctrl+0) + presets — *WSLg*
- [ ] **M12** Printing — `QtPrintSupport` system print dialog; PyMuPDF render at printer DPI — *WSL logic; Windows print validation*
- [ ] **M13** Recent documents — MRU list + dynamic File ▸ Open Recent submenu — *WSL*
- [ ] **M14** ⭐ Page-edit layer + form filling (fill existing AcroForm fields) — *WSL (model+tests) + WSLg*
- [ ] **M15** Verify + release → tag **v0.2.0** (fold in CI Node-24 bumps + code signing) — *Windows*

**v0.3.0 — "Annotate & Redact"** (keystone release)

- [ ] **M16** ⭐ Annotations — text highlight + text-box (free-text) on the M14 layer — *WSL + WSLg*
- [ ] **M17** ⭐ Redaction — true destructive `apply_redactions` + leak verification — *WSL (model+verify) + WSLg*
- [ ] **M18** Verify + release → tag **v0.3.0** — *Windows*

## Open follow-ups (carried)

Carried items — land opportunistically in the release milestones above (M15/M18), none block work:

- **Clean-machine install** — the one deferred M9 verification item: run `pdfproj-setup.exe` on a
  Windows VM with **no Python and networking disabled** (Win10 Home has no Sandbox → VirtualBox /
  spare machine / fresh local user). Everything else in the Verification matrix is green.
- **CI action versions** — bump the Node-20 GitHub Actions (`actions/checkout`, `setup-python`,
  `upload-artifact`, `softprops/action-gh-release`) to their Node-24 releases to clear the
  deprecation warning. Non-blocking; the release build succeeds today.
- **Code signing** — deferred Authenticode step (removes the SmartScreen prompt); slots into
  `release.yml` before packaging (PLAN.md §Packaging §5). Pairs naturally with M10's app icon → fold
  into the **v0.2.0** release (M15).
- **App icon** → now scheduled as **M10** (no longer a loose follow-up).
- **Product features** (view/print/annotate) → now scheduled in **§Next-release roadmap** (M10–M18).
  Still deferred beyond it: encrypted/password PDFs, internal GoTo-link remap
  (`model/links_remap.py`), annotation round-trip editing, new-field form designer — PLAN.md
  §Future enhancements.
