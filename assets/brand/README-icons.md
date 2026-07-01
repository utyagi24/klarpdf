# sheaf - icon system

All glyphs share ONE keyline so the family stays coherent.

## Grid
- Artboard: 24 x 24
- Live area: 20 x 20 (2 px padding all sides)
- Terminals align to the grid; optical size fills the live area.

## Stroke
- Width: 2.0 px, uniform
- Caps & joins: round / round
- Outer corner radius: 2 px
- Colour: a single flat colour (source = #1E293B). The app recolours / alpha-tints
  it for light & dark themes. NEVER multi-tone.
- Exception: redaction blocks (redact, redact-text) use a solid fill of the same
  single colour - they are intrinsically filled shapes.

## Shared keyshapes (reuse verbatim)
- page: rounded rectangle, r=2
- page + folded corner: page with a top-right dog-ear (see insert, pdf-file-icon)
- arrow: 2 px chevron head, 90deg, round terminals (see undo, redo, rotate, export)

## Drawing a NEW icon
1. Start inside the 20 x 20 live area.
2. Compose from the keyshapes above where possible.
3. Keep the 2 px round stroke and 2 px corner radius.
4. Snap terminals to the grid; balance optical weight against the set.
5. Export as a single flat Qt-safe SVG: only <path> <rect> <circle> <ellipse>
   <line> <polygon> <g>, inline attributes, no filters / masks / CSS / <text>.
6. Name it ui/icons/<name>.svg and run through SVGO.

## Qt (QtSvg) safety
Allowed: path, rect, circle, ellipse, line, polygon, g, solid fills,
simple linear/radial gradients, transform, stroke-dasharray.
Forbidden: filters, drop-shadow/blur, mask, clip-path, CSS <style>/classes,
embedded raster (<image>/base64), <text>/<tspan> (outline all text),
patterns, <use> cross-refs, SVG2-only features.
