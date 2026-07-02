# sheaf — brand & icon system

The visual identity for **sheaf**, a local, offline, privacy-first, open-source **PDF Viewer + Editor**
for native Windows (Qt / PySide6). Personality: **calm, precise, trustworthy, light, modern — a clean
"Preview for Windows."**

Production assets are the flat, Qt-safe **SVGs** in this folder and in `ui/icons/`; `tokens.json` holds
the machine-readable colour / type / grid tokens. (Design source: Claude Design project "Sheaf PDF
application branding".)

## Where the assets live

| Asset | File | Used by |
|---|---|---|
| App mark (full-colour) | `ui/icons/pdfproj.svg` (source copy: `assets/brand/app-mark.svg`) | window / taskbar icon; `make_icon.py` → `packaging/pdfproj.ico` |
| Monochrome mark | `assets/brand/app-mark-mono.svg` | theme-tinted in-app use (About, empty state) |
| Windows tile | `assets/brand/app-tile.svg` | boxed / tile contexts |
| `.pdf` file icon | `assets/brand/pdf-file-icon.svg` | Explorer file-association icon (G2 part 2) |
| Toolbar glyphs (27) | `ui/icons/<name>.svg` | toolbar / menu actions (tinted per theme) |
| Icon system spec | `assets/brand/README-icons.md` | how to draw a new icon |

## Colour  (machine-readable in `tokens.json`)

- **Gradient:** linear 120°, `#13B8A6` (teal) → `#3B82F6` (blue), top-left → bottom-right.
- **Ramp (light / dark):** teal `#13B8A6` / `#5EEAD4` · aqua `#1CA6C9` / `#5CC7E0` ·
  blue `#3B82F6` / `#60A5FA` · blue-deep `#2563EB` / `#3B82F6`.
- **Mark tints:** page-back `#A7E8E0`, back-leaf `#8FDDD3`, fold `#D8F3EE`, band-return `#C7DBEA`,
  knot `#2563EB`, band `#FFFFFF`.
- **Neutrals — light:** ink `#0F172A` · slate-700 `#334155` · slate-500 `#64748B` ·
  slate-300 `#CBD5E1` · slate-100 `#F1F5F9` · canvas `#F8FAFC` · surface `#FFFFFF`.
- **Neutrals — dark:** bg `#0B1220` · surface `#111A2E` · line `#1E293B` · text `#E2E8F0` ·
  muted `#94A3B8` · glyph `#CBD5E1`.

Each brand/neutral colour ships a **light and dark** value so any element the app draws itself stays
correct when Windows switches theme (which the app already follows).

## Type

- **Wordmark:** Quicksand SemiBold (600), lowercase, tracking −0.5px — SIL OFL 1.1.
- **UI / tagline:** Nunito Sans — SIL OFL 1.1. Tagline "PDF VIEWER + EDITOR", uppercase, ~2px tracking.
- Bundle the OFL font files (with their `OFL.txt`) or outline the wordmark to paths in any shipped SVG lockup.

## Icon system (summary; full spec in `README-icons.md`)

Artboard 24×24, live area 20×20 (2px padding); uniform **2px** stroke, round caps / joins, 2px corner
radius; **single flat colour** (`#1E293B`) that the app alpha-tints per theme (only the redaction blocks
are solid-filled). Shared keyshapes: the rounded page, the page + folded corner, the arrowhead.

## Qt (QtSvg) safety — required for any new icon

Allowed: `path rect circle ellipse line polygon g`, solid fills, simple linear / radial gradients,
`transform`, `stroke-dasharray`. Forbidden: filters / drop-shadow / blur, `mask`, clip-path, CSS
`<style>` / classes, embedded raster (`<image>` / base64), `<text>` / `<tspan>` (outline all text),
patterns, `<use>` cross-refs, SVG2-only features. Optimise with SVGO.

## Usage rules

- **Clear space** ≥ the band height (≈ ¼ of the mark) on all sides.
- **Min size:** full-colour mark ≥ 16px; monochrome ≥ 24px.
- **Do** keep the teal→blue direction and the folded corner; recolour mono glyphs per theme.
- **Don't** rotate the mark, add shadows, restyle the gradient, or show the "PDF" label below hero size.

## Licensing

Ships in a public **AGPL** repo. Quicksand & Nunito Sans are **SIL OFL 1.1** (free to bundle /
redistribute; include their `OFL.txt`). All SVG / PNG assets here are original and redistributable; no
embedded third-party raster or fonts (all lettering is outlined / stroked).
