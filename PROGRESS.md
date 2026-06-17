# pdfproj — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

**Status:** ✅ **v0.1.0 shipped** (2026-06-17) — all milestones **M0–M9 complete**. Offline Windows
installer released: <https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0>. See **Open
follow-ups** below for next-release starting points.

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

## Open follow-ups (post-v0.1.0)

Starting points for the next release — none block v0.1.0:

- **Clean-machine install** — the one deferred M9 verification item: run `pdfproj-setup.exe` on a
  Windows VM with **no Python and networking disabled** (Win10 Home has no Sandbox → VirtualBox /
  spare machine / fresh local user). Everything else in the Verification matrix is green.
- **CI action versions** — bump the Node-20 GitHub Actions (`actions/checkout`, `setup-python`,
  `upload-artifact`, `softprops/action-gh-release`) to their Node-24 releases to clear the
  deprecation warning. Non-blocking; the release build succeeds today.
- **App icon** — the frozen exe + installer use PyInstaller/Inno defaults; add a `.ico` and wire it
  into `packaging/pdfproj.spec` (`icon=`) + `installer.iss` (`SetupIconFile` / `DefaultIcon`).
- **Code signing** — deferred Authenticode step (removes the SmartScreen prompt); slots into
  `release.yml` before packaging (PLAN.md §Packaging §5).
- **Product features** — PLAN.md §Future enhancements: encrypted/password PDFs, internal GoTo-link
  remap (`model/links_remap.py`), multi-level-outline / duplicate form-field hardening.
