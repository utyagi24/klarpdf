# KlarPDF

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)

Local, offline, **native-Windows** PDF viewer + page editor (Python · PySide6 · PyMuPDF) — a
trustworthy replacement for macOS Preview's view + splice/split workflow on Windows. The source is
the unit of audit; it ships as a pinned, fully offline Windows installer.

**Status: `v0.9.6` shipped** — [download the installer or portable exe](https://github.com/utyagi24/klarpdf/releases/latest). Milestones **M0–M38 complete**. Full status: [PROGRESS.md](PROGRESS.md).

**New in v0.9.0 — Encrypted & Links:** open **password-protected PDFs** (prompt + authenticate on
open; the saved copy is unencrypted); and **internal links that survive editing** — GoTo and
named-destination links are rebuilt at save so reorder / delete / Save keeps them working, and
they're **clickable in the viewer** (click to jump to the target page). Patches through **v0.9.6**
polish the open experience — a window opens on the **monitor under your cursor** at Fit Page with no
flicker, the Pages sidebar loads **lazily** (a 320-page doc opens in ~150 ms) and is hidden by
default; **v0.9.4** is a dependency **security patch** (`pypdf` 6.13.2 → 6.13.3); **v0.9.5** centres
the page and makes Fit Width / Fit Page sticky on resize; and **v0.9.6** stops the Pages-sidebar
thumbnails flickering and brings a second PDF opened from Explorer to the front.
**v0.8.0 — Images:** **import** a PNG/JPEG from Explorer onto the Pages sidebar as a new page, and
**export** selected pages as PNG/JPEG at a chosen DPI (edits-aware).
**v0.7.0 — Round-trip & Export:** reopen a saved document and **move / edit / remove your own
highlights & text boxes**; plus a flatten **Export → PDF** that bakes annotations + form widgets into
the page content (text-preserving).
**v0.6.0 — Rich Text & Live Preview:** **styled text boxes** — set the font family, size, and
colour, plus a box fill and outline, from a small formatting bar on the inline editor; **live
thumbnails** — the Pages sidebar reflects each page's current edits (annotations, redactions, fills);
and **dynamic theme icons** — the toolbar re-tints instantly when you switch Windows light↔dark.
**v0.5.0 — File Safety & Output:** **Revert to Saved** (discard edits, reload from disk); an
**external-change warning** when another program modifies the open file (Reload / Keep, plus an
overwrite guard before Save); and **edits-aware printing** — the printout shows your annotations,
form values, and redactions (a not-yet-saved redaction no longer prints the original).
**v0.4.0 — Annotate & Redact:** text **highlight** and **text boxes** (drag to move, double-click to
re-edit); and **true destructive redaction** — drag over text or a block to permanently remove it at
save (a cross-engine-verified, confirmed point of no return).
**v0.3.0:** better **drag-and-drop**, **drag a PDF in from File Explorer**, and a **Grab/Select**
viewer-mode toggle.
**v0.2.0:** theme-aware **icons**, a live **zoom %** indicator, **printing**, **Open Recent**, and
**form filling** (click and fill AcroForm fields, saved losslessly).

| Doc | What |
|---|---|
| [PLAN.md](PLAN.md) | Product spec, architecture, dependencies/packaging, portability, build order, **Execution**, verification |
| [PROGRESS.md](PROGRESS.md) | Live milestone checklist (M0–M38 shipped; v0.10.0 MCP roadmap planned) + **Open follow-ups** |
| [RELEASE.md](RELEASE.md) | Maintainer runbook — change a dependency · respond to a Dependabot alert · cut a release (via the `invoke` tasks) |
| [CLAUDE.md](CLAUDE.md) | Orientation + conventions for contributors/agents |
| [DEPENDENCIES.md](DEPENDENCIES.md) | Pinned libraries + build toolchain — exact versions, licenses |

## Use it (Windows)

Grab the [latest release](https://github.com/utyagi24/klarpdf/releases/latest):

- **`klarpdf-setup.exe`** — installer (per-user, no admin). Adds KlarPDF to the `.pdf` **Open With**
  list + a Start-Menu shortcut; clean uninstall. *Recommended.*
- **`klarpdf-portable.exe`** — single-file portable build; run from any folder (slower first launch,
  no file association).

No Python and no network needed at install or runtime. Unsigned for now → a one-time SmartScreen
"unknown publisher" prompt. Verify a download against `SHA256SUMS` in the release.

## Develop (WSL)

```bash
# one-time: base Ubuntu python lacks ensurepip
sudo apt install -y python3.12-venv

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
invoke test                     # 376 headless tests (offscreen Qt) — or run `pytest`
invoke --list                   # all build/release tasks: test · audit · lock · build · tag · publish
python launcher.py file.pdf     # run the GUI via WSLg
```

The cross-platform core (`model/`, `viewer/`, `organize/`) + headless tests run in WSL; the GUI
iterates via WSLg. Packaging and Windows shell-integration happen on Windows only
(PLAN.md §Development environment). **git is the only bridge** between the WSL and Windows checkouts.
Build steps are wrapped as [`invoke`](tasks.py) tasks; CI runs the full suite on every PR and a
weekly dependency audit (`.github/workflows/test.yml`, `audit.yml`).

## Build the Windows installer

On Windows (python.org 3.12 + Inno Setup 6), from the repo root:

```powershell
invoke build            # wraps packaging\build.ps1: wheels -> clean venv -> freeze -> installer + portable + SHA256SUMS (dist\)
```

CI does the same on a tag: push a `v*` tag and `.github/workflows/release.yml` builds on
`windows-latest` and publishes a **draft** GitHub Release (PLAN.md §Packaging §5). The full
end-to-end flow — version bump → tag → draft → smoke → publish, with the `invoke tag` / `invoke
publish` shortcuts — is in **[RELEASE.md](RELEASE.md)**.

## License

KlarPDF is licensed under the **GNU Affero General Public License v3.0 or later
(`AGPL-3.0-or-later`)** — full text in [LICENSE](LICENSE).

Why AGPL and not MIT/BSD: KlarPDF renders and edits PDFs with **PyMuPDF**, which is itself
**AGPL-3.0** (or an Artifex commercial license). KlarPDF links it and is a derivative work, so the
whole project must ship under the AGPL — it cannot be relicensed as MIT/BSD (see
PLAN.md §Public-release readiness). The LGPL-3.0 (PySide6 / shiboken6) and BSD-3-Clause (pypdf) terms
of the other bundled libraries are satisfied by the same source release. Per-dependency versions,
license identifiers, and notices are in **[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES)**
(cross-referenced by [DEPENDENCIES.md](DEPENDENCIES.md)).

Because the app is AGPL, public distribution must offer the corresponding source — this repository at
the exact release tag each installer is built from satisfies that. Building for **your own machines**
is private use with no such obligation.

**Build from source:** see [Develop (WSL)](#develop-wsl) to run it, and
[Build the Windows installer](#build-the-windows-installer) to produce `klarpdf-setup.exe` /
`klarpdf-portable.exe` yourself.

## Audit notes

Dependencies are pinned with hashes and vendored for an offline, auditable build, and
**continuously scanned** for known advisories (`pip-audit` in CI + Dependabot alerts; bumps follow
[RELEASE.md](RELEASE.md)). See DEPENDENCIES.md and PLAN.md §Packaging.
