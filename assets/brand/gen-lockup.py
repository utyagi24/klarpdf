"""Generate the README hero banner + the GitHub social-preview card from `tokens.json`.

**One-shot generator, like `vendor/gen-sources.py`** — the SVGs it writes are committed; this exists so
they have provenance. A blob of `<path d="M8.2 0Q6.8 0 5.9-.9…">` with no record of where it came from
cannot be reviewed, corrected, or regenerated at another size.

Why the lettering is outlined rather than `<text>`: an SVG in a README renders inside an `<img>`, which
**cannot load a webfont**. Live text would silently substitute to whatever the viewer happens to have —
Quicksand is not a system font anywhere. `BRAND.md` §Type sanctions exactly this ("bundle the OFL font
files … **or** outline the wordmark to paths"); outlining is the cheaper half of that "or", because it
means the repo never redistributes the font binary at all. Converting glyphs to outlines is explicitly
permitted by the SIL OFL — the resulting paths are artwork, not Font Software.

Run (needs network for the fonts, and `pip install fonttools`; PySide6 comes from the dev lock):

    python assets/brand/gen-lockup.py

Fonts are fetched to a temp dir and **never committed**:
  * Quicksand  — SIL OFL 1.1 — wordmark, SemiBold 600, tracking −0.5px (BRAND.md §Type)
  * Nunito Sans — SIL OFL 1.1 — tagline, uppercase, ~2px tracking
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TOKENS = json.loads((HERE / "tokens.json").read_text(encoding="utf-8"))
TEAL, BLUE = TOKENS["brand"]["gradient"]["stops"]

FONTS = {
    "quicksand": "https://github.com/google/fonts/raw/main/ofl/quicksand/Quicksand%5Bwght%5D.ttf",
    "nunito": "https://github.com/google/fonts/raw/main/ofl/nunitosans/"
              "NunitoSans%5BYTLC%2Copsz%2Cwdth%2Cwght%5D.ttf",
}

# The monochrome mark (app-mark-mono.svg), inlined so the banner is one self-contained file — an <img>
# cannot resolve an <image> or <use> reference to another SVG. Content bbox: x 66..182, y 44..214.
MONO = """    <path d="M66 190 L66 60 Q66 44 82 44 L150 44"/>
    <path d="M92 60 L146 60 L182 96 L182 198 Q182 214 166 214 L96 214 Q80 214 80 198 L80 74 Q80 60 92 60 Z"/>
    <path d="M146 60 L146 96 L182 96"/>
    <path d="M80 156 L182 156"/>
    <path d="M80 182 L182 182"/>
    <path d="M131 159 L142 169 L131 179 L120 169 Z" fill="{ink}" stroke="none"/>"""
MARK_BBOX = (66, 44, 182, 214)


def font(name: str, cache: Path) -> Path:
    dst = cache / f"{name}.ttf"
    if not dst.exists():
        urllib.request.urlretrieve(FONTS[name], dst)
    return dst


def outline(ttf: Path, text: str, weight: float, size: float, tracking: float = 0.0):
    """Outline `text` to a single SVG path, baseline at y=0 in SVG (Y-down) space."""
    f = instancer.instantiateVariableFont(TTFont(ttf), {"wght": weight})
    upem = f["head"].unitsPerEm
    cmap, hmtx, glyphs = f.getBestCmap(), f["hmtx"], f.getGlyphSet()
    scale = size / upem

    def ntos(n):  # 2dp: raw floats triple the file for precision nobody can see (BRAND.md: optimise)
        s = f"{round(n, 2):g}"
        return s[1:] if s.startswith("0.") else ("-" + s[2:] if s.startswith("-0.") else s)

    pen = SVGPathPen(glyphs, ntos=ntos)
    x = 0.0
    for ch in text:
        gname = cmap[ord(ch)]
        # Flip Y here so callers only ever deal with SVG coordinates.
        glyphs[gname].draw(TransformPen(pen, Transform(scale, 0, 0, -scale, x * scale, 0)))
        x += hmtx[gname][0] + tracking / scale
    return pen.getCommands(), (x - tracking / scale) * scale


def build(cache: Path, width, height, radius, wm_size, tag_size, ink="#FFFFFF", scrim=False) -> str:
    wm_d, wm_w = outline(font("quicksand", cache), "klarpdf", 600, wm_size, -0.5)
    tg_d, tg_w = outline(font("nunito", cache), "PDF VIEWER + EDITOR", 600, tag_size, tag_size * 0.14)

    mark_h = height * 0.44
    mark_w = mark_h * (MARK_BBOX[2] - MARK_BBOX[0]) / (MARK_BBOX[3] - MARK_BBOX[1])
    gap = wm_size * 0.36
    # Lay the lockup out as one block, then centre the block, so mark and text always move together.
    x0 = (width - (mark_w + gap + max(wm_w, tg_w))) / 2
    cy = height / 2

    s = mark_h / (MARK_BBOX[3] - MARK_BBOX[1])
    mx, my = x0 - MARK_BBOX[0] * s, (cy - mark_h / 2) - MARK_BBOX[1] * s
    tx = x0 + mark_w + gap
    wm_base = cy + wm_size * 0.26  # optical centre: Quicksand's x-height sits low in the em
    tg_base = wm_base + tag_size + wm_size * 0.30

    # Dark variant: dim the band so it sits against GitHub's dark chrome instead of glaring. The
    # gradient's stops and 120° direction are untouched — see BRAND.md §Usage rules.
    scrim_svg = (f'  <rect width="{width}" height="{height}" rx="{radius}" fill="#0B1220" opacity="0.28"/>\n'
                 if scrim else "")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" role="img" aria-label="KlarPDF — PDF viewer + editor">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{TEAL}"/><stop offset="1" stop-color="{BLUE}"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" rx="{radius}" fill="url(#g)"/>
{scrim_svg}  <g transform="translate({mx:.2f} {my:.2f}) scale({s:.4f})" fill="none" stroke="{ink}"
     stroke-width="11" stroke-linecap="round" stroke-linejoin="round">
{MONO.format(ink=ink)}
  </g>
  <path transform="translate({tx:.2f} {wm_base:.2f})" fill="{ink}" d="{wm_d}"/>
  <path transform="translate({tx:.2f} {tg_base:.2f})" fill="{ink}" opacity="0.82" d="{tg_d}"/>
</svg>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        for name, kw in [
            ("github-hero-light.svg", dict(width=1200, height=300, radius=24, wm_size=76, tag_size=13)),
            ("github-hero-dark.svg", dict(width=1200, height=300, radius=24, wm_size=76, tag_size=13, scrim=True)),
            ("social-preview.svg", dict(width=1280, height=640, radius=0, wm_size=104, tag_size=18)),
        ]:
            (HERE / name).write_text(build(cache, **kw), encoding="utf-8")
            print("wrote", HERE / name)
    print("\nsocial-preview.png is rasterised from social-preview.svg — see BRAND.md §GitHub assets.")


if __name__ == "__main__":
    main()
