# pdfproj — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

**Status:** M5 (single-instance) in review. **Next: M6 (Windows ship lock).**

- [x] **M0** Scaffold + WSL dev venv — *step 1 (WSL); WSL* — [#4](https://github.com/utyagi24/pdfproj/pull/4)
- [x] **M1** Correctness core: `model/` + headless tests green ⭐ — *steps 5, 7; WSL* — [#5](https://github.com/utyagi24/pdfproj/pull/5)
- [x] **M2** Viewer: render / scroll / zoom / rotate / thumbnails — *step 3; WSLg* — [#6](https://github.com/utyagi24/pdfproj/pull/6)
- [x] **M3** Selection + search — *step 4; WSLg* — [#7](https://github.com/utyagi24/pdfproj/pull/7)
- [x] **M4** Editing loop: cross-window cut/copy/paste + undo/redo + Save/Save As + close-prompt — *steps 6, 8; WSLg* — [#8](https://github.com/utyagi24/pdfproj/pull/8)
- [x] **M5** Single-instance launcher logic — *step 2; WSL (validate on Windows)* — [#PR](https://github.com/utyagi24/pdfproj/pull/PR)
- [ ] **M6** Windows ship lock: python.org + hashed `win_amd64` wheels — *step 1 (Win); Windows*
- [ ] **M7** Windows validation: instance / focus / Open-With + GUI fidelity — *step 2; Windows*
- [ ] **M8** Freeze + installer → `pdfproj-setup.exe` — *step 9; Windows*
- [ ] **M9** Verification matrix + release tag — *Verification §; Windows*

⭐ M1 is the keystone — most correctness risk, GUI-free, fully testable in WSL/CI.
