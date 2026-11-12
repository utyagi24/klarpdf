"""Names for the plain tuples the engine, the bridge and the app pass between them (M174).

PyMuPDF hands most of these back as values mypy knows nothing about, so the name a function's
signature gives is the only statement of what it holds. Each is an ordinary ``tuple`` when the code
runs: nothing here changes a value, only what mypy checks about it.
"""

from __future__ import annotations

#: A rectangle as ``(x0, y0, x1, y1)``, in page points unless the name it is given says otherwise.
Box = tuple[float, float, float, float]

#: A point as ``(x, y)``.
Point = tuple[float, float]

#: A colour as ``(red, green, blue)``, each from 0 to 1. The colour of every KlarPDF mark.
Color = tuple[float, float, float]

#: One entry of ``page.get_text("words")``: its box, its text, and the numbers of its block, its line
#: in that block and its place in that line.
Word = tuple[float, float, float, float, str, int, int, int]
