"""Drawing a slow page in pieces: which piece next, and how big (PLAN.md §M152, M152.2).

A page that takes longer than the budget to draw is drawn a piece at a time on the window's own
thread, so the window can handle clicks, moves and resizes between pieces. This module is the
bookkeeping: it knows nothing of Qt or PyMuPDF, so it can be tested on its own. The view in
``viewer/pdf_view.py`` does the drawing.

**Tiles.** A page at one size is cut into a grid of ``TILE`` × ``TILE`` squares, in device pixels
of the page as displayed. A piece is a rectangle of tiles, drawn by one call to PyMuPDF. The grid
records which tiles are drawn, so a piece is never drawn twice and the page is known to be finished
when every tile is.

**How big a piece is.** The time one piece takes has two parts: a fixed part, paid by every piece
however small, and a part that grows with the piece's area. Both were measured (§M152.2). On the
NADA cover at 375% the fixed part is 3–4 ms and the rest is about 1.6 µs a pixel, so a piece of
about one tile takes the budget. On ``IAS_CaseStudy.pdf`` page 6 at 300% the fixed part is about
200 ms, and a piece of any size takes at least that. :class:`Sizer` fits both parts to the pieces
drawn so far and picks the next size from them:

* when the fixed part is under half the budget, a piece is made to take about the budget;
* when it is not, no piece can take the budget, and small pieces would pay the fixed part again and
  again: 21 s for that photo page in 128 px pieces against 4.8 s in 512 px ones. So a piece is made
  large enough that the fixed part is half of its time.
"""

from __future__ import annotations

import math
from statistics import median

#: The side of a tile, in device pixels: the smallest piece. On the slowest page measured, the NADA
#: cover at 375%, one tile takes about the 30 ms budget (median 30 ms, slowest 64 ms), so a smaller
#: tile would only add pieces.
TILE = 128

#: How many recent pieces :class:`Sizer` fits its two parts to. Old enough pieces were drawn in
#: another part of the page, where the cost can differ.
_HISTORY = 8


class Tiles:
    """The tile grid of one page at one size, and which tiles are drawn.

    Rectangles are ``(x0, y0, x1, y1)`` in device pixels of the page as displayed, measured from
    its top-left corner. A piece is ``(c0, r0, c1, r1)``: columns ``c0`` to ``c1`` and rows ``r0``
    to ``r1``, both ends included.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cols = max(1, math.ceil(width / TILE))
        self.rows = max(1, math.ceil(height / TILE))
        self.drawn: set[tuple[int, int]] = set()

    def tiles_in(self, rect) -> set[tuple[int, int]]:
        """The tiles that ``rect`` touches, with at least one whole pixel of it."""
        x0, y0, x1, y1 = rect
        c0, c1 = max(0, math.floor(x0 / TILE)), min(self.cols - 1, math.ceil(x1 / TILE) - 1)
        r0, r1 = max(0, math.floor(y0 / TILE)), min(self.rows - 1, math.ceil(y1 / TILE) - 1)
        if x1 - x0 < 1 or y1 - y0 < 1:
            return set()
        return {(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)}

    def box(self, piece) -> tuple[int, int, int, int]:
        """The device-pixel rectangle a piece covers, cut to the page."""
        c0, r0, c1, r1 = piece
        return (c0 * TILE, r0 * TILE, min(self.width, (c1 + 1) * TILE),
                min(self.height, (r1 + 1) * TILE))

    def complete(self) -> bool:
        return len(self.drawn) == self.cols * self.rows

    def mark(self, piece, drawn: bool = True) -> None:
        c0, r0, c1, r1 = piece
        tiles = {(c, r) for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)}
        if drawn:
            self.drawn |= tiles
        else:
            self.drawn -= tiles

    def _distance(self, tile, centre) -> float:
        c, r = tile
        return math.hypot((c + 0.5) * TILE - centre[0], (r + 0.5) * TILE - centre[1])

    def nearest(self, wanted, centre) -> "tuple[tuple[int, int], float] | None":
        """The undrawn tile of ``wanted`` nearest ``centre``, and how far it is, or ``None``."""
        best = None
        for tile in wanted:
            if tile in self.drawn:
                continue
            d = self._distance(tile, centre)
            if best is None or d < best[1] or (d == best[1] and tile < best[0]):
                best = (tile, d)
        return best

    def grow(self, seed, wanted, centre, count: int) -> tuple[int, int, int, int]:
        """A piece of about ``count`` tiles around ``seed``, made only of undrawn ``wanted`` tiles.

        It grows one row or column at a time, toward whichever side is nearest ``centre``, so the
        pieces spread out from the middle of the window. It stops at ``count`` tiles, or when no
        side can grow without taking a tile that is drawn or not wanted.
        """
        free = {t for t in wanted if t not in self.drawn}
        c0 = c1 = seed[0]
        r0 = r1 = seed[1]
        while (c1 - c0 + 1) * (r1 - r0 + 1) < count:
            options = []
            for side, tiles in (
                ("left", [(c0 - 1, r) for r in range(r0, r1 + 1)]),
                ("right", [(c1 + 1, r) for r in range(r0, r1 + 1)]),
                ("up", [(c, r0 - 1) for c in range(c0, c1 + 1)]),
                ("down", [(c, r1 + 1) for c in range(c0, c1 + 1)]),
            ):
                if all(t in free for t in tiles):
                    options.append((min(self._distance(t, centre) for t in tiles), side))
            if not options:
                break
            side = min(options)[1]
            if side == "left":
                c0 -= 1
            elif side == "right":
                c1 += 1
            elif side == "up":
                r0 -= 1
            else:
                r1 += 1
        return c0, r0, c1, r1


class Sizer:
    """How many tiles the next piece of one page should hold, from how long its pieces took.

    ``budget`` is how long a piece should take, in seconds. ``rate`` is the seconds a pixel took
    in the page's last whole drawing, used for the first piece only: it may include unpacking the
    page's photos, which a piece drawn later does not pay again. A page never drawn has no rate,
    and its first piece is ``first`` tiles.
    """

    def __init__(self, budget: float, rate: "float | None" = None, first: int = 1) -> None:
        self.budget = budget
        self.rate = rate
        self.first = first
        self._seen: list[tuple[int, float]] = []   # (pixels, seconds), oldest first, the recent ones
        self._all: list[tuple[int, float]] = []    # every piece at this size
        self._fixed: "float | None" = None         # the fixed part, once two sizes have been seen

    def record(self, pixels: int, seconds: float) -> None:
        self._seen.append((pixels, seconds))
        del self._seen[:-_HISTORY]
        self._all.append((pixels, seconds))
        slopes = [(t2 - t1) / (p2 - p1)
                  for i, (p1, t1) in enumerate(self._seen)
                  for p2, t2 in self._seen[i + 1:] if p2 != p1]
        if slopes:
            # A robust line: the slope is the median of the slopes between every two pieces, so
            # one piece that also paid for unpacking a photo cannot tilt it.
            b = max(median(slopes), 0.0)
            a = median(t - b * p for p, t in self._seen)
            self._fixed = min(max(0.0, a), min(t for _p, t in self._seen))

    def fixed(self) -> float:
        """The part of a piece's time that does not grow with its area, in seconds. Zero until
        two sizes of piece have been drawn, and never more than the quickest recent piece."""
        return self._fixed or 0.0

    def per_pixel(self, fixed: float) -> float:
        """The part per pixel to plan for, in seconds.

        The higher of two figures. The first is the second highest of the recent pieces', so
        that one piece that also unpacked a photo does not shrink the ones after it; with fewer
        than three, the latest piece's. The second is the average over every piece at this size
        but the slowest. Pieces over a blank margin are cheap, and sizing from them alone made
        the next piece run into the dense middle of the NADA cover at 11 × 7 tiles, 1.7 s. The
        page's average stops that.
        """
        rates = [(t - fixed) / max(1, p) for p, t in self._seen]
        recent = sorted(rates)[-2] if len(rates) >= 3 else rates[-1]
        rest = sorted(self._all, key=lambda seen: seen[1])[:-1]
        average = (sum(t - fixed for _p, t in rest) / max(1, sum(p for p, _t in rest))) if rest else 0.0
        return max(recent, average, 1e-12)

    def tiles(self) -> int:
        """The next piece's size, in tiles, at least one.

        When the fixed part is under half the budget, the piece is made to take about the
        budget. When it is not, no piece can, so the piece is made large enough that the fixed
        part is half of its time: small pieces would pay it again and again.
        """
        if not self._seen:
            return max(1, int(self.budget / self.rate / (TILE * TILE))) if self.rate else self.first
        pixels, seconds = self._seen[-1]
        if self._fixed is None and pixels <= TILE * TILE and seconds > self.budget:
            # A piece of one tile, and still too slow. The fixed part is not known, so try a piece
            # twice the size: if it takes about as long, the fixed part is most of the cost.
            return 2
        a = self.fixed()
        b = self.per_pixel(a)
        pixels = (self.budget - a) / b if a < self.budget / 2 else a / b
        return max(1, int(pixels / (TILE * TILE)))
