# KlarPDF — brand & icon system

The visual identity for **KlarPDF**, a local, offline, privacy-first, open-source **PDF Viewer +
Editor** for native Windows (Qt / PySide6). Personality: **calm, precise, trustworthy, light, modern —
a clean "Preview for Windows."** The name is *klar* — "clear" in German and the Scandinavian
languages.

Production assets are the flat, Qt-safe **SVGs** in this folder and in `ui/icons/`; `tokens.json` holds
the machine-readable colour / type / grid tokens. (Design source: the Claude Design project "Sheaf PDF
application branding" — *sheaf* was the working name the marks were drawn under, before the rename.)

## Where the assets live

| Asset | File | Used by |
|---|---|---|
| **App icon (tile)** | `ui/icons/klarpdf.svg` | the **OS icon**: taskbar, title bar, Alt-Tab, Task Manager, Add/Remove Programs; `make_icon.py` → `packaging/klarpdf.ico` |
| **`.pdf` document icon** | `ui/icons/klarpdf-doc.svg` (master: `assets/brand/pdf-file-icon.svg`) | Explorer's icon for a PDF file; `make_icon.py` → `packaging/klarpdf-doc.ico`, wired via the ProgID `DefaultIcon` |
| App mark (free-standing) | `ui/icons/klarpdf-mark.svg` (master: `assets/brand/app-mark.svg`) | in-app on our own background: About dialog, empty states |
| Monochrome mark | `assets/brand/app-mark-mono.svg` | theme-tinted in-app use |
| Windows tile (light) | `assets/brand/app-tile.svg` | *unused* — a near-white tile; the shipped app icon is the gradient tile above |
| Toolbar glyphs (27) | `ui/icons/<name>.svg` | toolbar / menu actions (tinted per theme) |
| **README hero** | `assets/brand/github-hero-{light,dark}.svg` | top of `README.md`, theme-swapped — §GitHub assets |
| **Social preview** | `assets/brand/social-preview.{svg,png}` | the card shown when the repo link is shared — §GitHub assets |
| **Lockup generator** | `assets/brand/gen-lockup.py` | **writes** the two above from `tokens.json` |
| **App screenshots** | `assets/screenshots/klarpdf-{light,dark}.png` | `README.md`, theme-swapped |
| Icon system spec | `assets/brand/README-icons.md` | how to draw a new icon |

**None of these ship.** `packaging/klarpdf.spec` bundles only `ui/icons/`, `LICENSE` and
`THIRD_PARTY_LICENSES`; everything in `assets/` is design source and repo presentation.

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

- **Wordmark:** Quicksand SemiBold (600), lowercase `klarpdf`, tracking −0.5px — SIL OFL 1.1.
- **UI / tagline:** Nunito Sans — SIL OFL 1.1. Tagline "PDF VIEWER + EDITOR", uppercase, ~2px tracking.
- Bundle the OFL font files (with their `OFL.txt`) or outline the wordmark to paths in any shipped SVG lockup.
- **The drawn lockup now exists as an asset** — `gen-lockup.py` renders it from these exact tokens
  (Quicksand 600 at −0.5px; Nunito Sans 600, uppercase, 0.14em tracking) and outlines it to paths. It
  is the *only* place the wordmark is reproduced; regenerate rather than hand-editing path data. See
  §GitHub assets.

**Name casing.** The lowercase form is the *drawn* wordmark only — where we control the typeface and
tracking (About lockup, README header, splash). Anywhere the OS renders the name as a plain string in
its own font — window title, installer AppName, Add/Remove Programs, the `.pdf` "Open With" list — it
is **`KlarPDF`**, initialism capitalised. Lowercase identifiers (`klarpdf`) are a technical constraint
of paths, URLs and package names, not a style: see `PROGRESS.md` §G2 for the full mapping.

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

- **Clear space** ≥ the band height (≈ ¼ of the mark) on all sides — **for lockups only**.
- **An OS icon must span its canvas.** Windows hands an icon a *square* canvas (24×24 for the
  taskbar at 100% scaling). The free-standing mark is a portrait page, aspect 0.71, so it spans
  only 59% of that square — against 82–100% for every other app (Word 82%, cmd 84%, Notepad 91%,
  VS Code 100%). v0.10.0 shipped it as the app icon and it read as *tiny*. Hence the **tile**:
  a gradient-filled rounded square, ≥90% span, which is what `ui/icons/klarpdf.svg` now is.
- **A document is not the application.** `.pdf` files get `klarpdf-doc.svg` — portrait, page-shaped,
  deliberately *not* spanning the canvas. Never point the ProgID `DefaultIcon` at the exe.
- **Min size:** app tile ≥ 16px; free-standing mark ≥ 24px (below that its detail is sub-pixel);
  monochrome ≥ 24px.
- **Do** keep the teal→blue direction and the folded corner; recolour mono glyphs per theme.
- **Don't** rotate the mark, add shadows, restyle the gradient, or show the "PDF" label below hero size
  — **except on the `.pdf` document icon**, which carries it at every size on purpose. Below ~24px the
  label degrades to a blue smudge inside the page, but the icon stays unmistakably a *document*, and
  the smudge sits exactly where a reader expects a label. Verified against the real Explorer entry at
  16px before this exception was written down.

## GitHub assets (the repo's shop window)

The repo page is the first thing anyone sees, and **GitHub strips CSS from markdown** — no `<style>`,
no `style=` attributes, no coloured text (verified against the `POST /markdown` renderer, which drops
the attribute and keeps the element). Brand colour can therefore only arrive through **images** and
**badges**. Three assets carry it:

- **Hero** — `github-hero-{light,dark}.svg`, 1200×300, the canonical gradient band with the white
  lockup, at the top of `README.md`.
- **Screenshots** — `assets/screenshots/klarpdf-{light,dark}.png`: the real app, captured from a real
  build by forcing `QStyleHints.setColorScheme()` (**not** by changing a Windows setting — the app
  already follows the palette, so this drives the same code path a real theme switch does).
- **Social preview** — `social-preview.png`, 1280×640. Upload at **Settings ▸ General ▸ Social
  preview**; it is **manual**, there is no REST API for it. It is what renders when the repo link is
  pasted into Slack / X / Discord, and it is GitHub's `og:image`.

**Theming is done with `<picture>` + `prefers-color-scheme`,** which is the *supported* path: GitHub
wraps it in its own `<themed-picture>` element and swaps on the viewer's theme. Do not reach for the
older `#gh-dark-mode-only` URL-fragment trick.

**The wordmark is outlined, and that is not optional.** A README SVG renders inside an `<img>` and
**cannot load a webfont**; live `<text>` would substitute to whatever the viewer has, and Quicksand is
a system font nowhere. `gen-lockup.py` fetches Quicksand + Nunito Sans (both SIL OFL 1.1) to a temp
dir, outlines the lettering to paths at 2dp, and writes the SVGs. **The font binaries are never
committed** — converting glyphs to outlines is explicitly permitted by the OFL and the resulting paths
are artwork, not Font Software, so no `OFL.txt` obligation follows. This is the "**or** outline the
wordmark to paths" half of §Type's instruction.

**Documented exception to "don't restyle the gradient":** the *dark* hero lays a `#0B1220` scrim at
**0.28** over the band. The stops and the 120° direction are untouched — it is a dim, not a restyle —
and it exists because the full-chroma band glares against GitHub's dark chrome (`#0d1117`). Applies to
the README hero only; never to an icon, a tile, or the mark.

## Licensing

Ships in a public **AGPL** repo. Quicksand & Nunito Sans are **SIL OFL 1.1** (free to bundle /
redistribute; include their `OFL.txt`). All SVG / PNG assets here are original and redistributable; no
embedded third-party raster or fonts (all lettering is outlined / stroked).
