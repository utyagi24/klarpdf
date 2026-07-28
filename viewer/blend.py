"""Scene items that paint with **multiply** blending — marks that must not wash out the text.

Source-over is wrong for anything that lies *over* words. A translucent fill at alpha *a* moves
every pixel under it a fraction *a* of the way toward the mark's colour — including the black
glyphs, which is how a highlighter ends up *reducing* legibility. Multiply darkens instead:
``result = src × dst``, so white paper takes the mark's full colour while black text stays black.

That is also what the **saved file** does. PyMuPDF writes highlight annotations with ``/BM
/Multiply`` (verified on our pinned version), so a preview that alpha-blends is not a different
taste — it is a preview that does not match the document it is previewing. Same for a watermark,
which bakes *beneath* the page content: a scene item cannot be painted under the page's own pixmap
(z-order below it just hides it), and painting a translucent mark under black text gives the same
result as multiplying it over black text.

These live in their own module rather than beside one of their callers because two overlays need
them — :mod:`viewer.annotations` for committed marks and :mod:`viewer.text_selection` for the live
drag-over-text preview — and because burying the idiom is how highlights went five milestones
without it (M84). The saved file is unaffected either way; this is purely so the preview does not
lie about legibility.
"""

from __future__ import annotations

from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem


class MultiplyPixmapItem(QGraphicsPixmapItem):
    """A pixmap painted with multiply blending — the under-the-content watermark preview."""

    def paint(self, painter, option, widget=None) -> None:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        super().paint(painter, option, widget)


class MultiplyRectItem(QGraphicsRectItem):
    """A filled rect painted with multiply blending — a highlight bar, committed or previewed.

    Fill it with the mark's colour at **full alpha**: multiply supplies the translucency a
    highlighter is supposed to have (the paper shows through wherever the colour is not 1.0), so an
    alpha here would wash the colour a second time and bring the dullness straight back.
    """

    def paint(self, painter, option, widget=None) -> None:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        super().paint(painter, option, widget)
