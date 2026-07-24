"""Per-page text geometry: "what words/characters are under this box?" answered cheaply.

:class:`PageText` indexes a page **once** and answers every box lookup from that index. It exists
because the obvious way to ask — ``page.get_textbox(rect)`` — is wrong twice over, and both faults
cost real time before they were found (M78.7 in search, M78.8 in the annotations list):

* **It is O(page), not O(box).** ``get_textbox`` is a wrapper for ``get_text("text", clip=rect)``,
  so it re-extracts the page's whole text on *every call* — ~31 ms measured on a dense page. Any
  loop over boxes therefore re-reads the same page once per box: a one-letter search query
  (~72 000 hits on a 320-page file) took ~37 minutes, and an annotations list rebuild took 15.7 s
  at 200 highlights.
* **It answers by clipping**, so it returns whatever else shares the rect's band rather than what
  the rect covers. On single-spaced text the line above bleeds in; on a two-column page the other
  column does — 567 of 700 single-word boxes on one real page read back as something other than
  the word. :meth:`text_under` takes each character whose **centre** falls inside the box instead,
  which is both faster and what the callers actually meant.

The index is per **line band**: a box can only touch a word (or char) whose line shares its
vertical extent, so a lookup scans one line rather than one page. The char index is built lazily —
only :meth:`text_under` needs it, and the word-based lookups are the common case.

Lives in ``model/`` rather than beside either caller: it is pure PyMuPDF text geometry with no Qt
and no viewer or panel state, and both users (``viewer.search``, ``organize.annotations_panel``)
are downstream of the model. The alternative — a copy each — would leave two copies of a routine
that has already been subtly wrong once.
"""

from __future__ import annotations

import pymupdf as fitz

_SNIPPET_WORDS = 4   # context words kept either side of a match in a snippet


def boxes_touch(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class PageText:
    """A page's words (and, on demand, its characters) indexed by line for box lookups.

    Build from a ``page`` for the full service, or from a bare ``words`` list
    (``get_text("words")``) when only the word-based lookups are wanted and there is no page to
    hand — :meth:`text_under` is then empty, since characters can only come from a page.
    """

    def __init__(self, page=None, words: list | None = None) -> None:
        self._page = page
        self.words: list = page.get_text("words") if words is None else words
        self._by_key: dict[tuple, list] = {}
        for i, w in enumerate(self.words):
            self._by_key.setdefault((w[5], w[6]), []).append((i, w))
        self._lines = [(min(w[1] for _, w in ws), max(w[3] for _, w in ws), ws)
                       for ws in self._by_key.values()]
        self._chars: list | None = None

    @staticmethod
    def _band(lines: list, box: tuple) -> list:
        """The lines whose vertical band meets ``box``. A word can only touch the box if its own
        band does, and a line's band covers every word on it — so this narrows without dropping."""
        return [entry for entry in lines if entry[0] < box[3] and box[1] < entry[1]]

    def struck(self, box: tuple) -> list:
        """The page words ``box`` overlaps, as ``(index, word)`` in document order (what a full
        scan of ``words`` would return)."""
        found = [(i, w) for _ly0, _ly1, ws in self._band(self._lines, box)
                 for i, w in ws if boxes_touch(w[:4], box)]
        found.sort(key=lambda t: t[0])
        return found

    def is_whole_word(self, box: tuple, tol: float = 0.5) -> bool:
        """Is ``box`` a whole word rather than part of a longer one? Geometric: true when the words
        it touches do not extend past it on either side."""
        struck = self.struck(box)
        if not struck:
            return True                  # nothing to contradict it (e.g. a box with no word boxes)
        return struck[0][1][0] >= box[0] - tol and struck[-1][1][2] <= box[2] + tol

    def snippet(self, box: tuple) -> str:
        """Context snippet for ``box``: the words of its line, windowed to ±``_SNIPPET_WORDS``
        around the covered span, with ellipses marking a trimmed side."""
        struck = self.struck(box)
        if not struck:
            return ""
        first = struck[0][1]
        line = [w for _i, w in self._by_key[(first[5], first[6])]]
        matched = [i for i, w in enumerate(line) if boxes_touch(w[:4], box)]
        lo = max(0, matched[0] - _SNIPPET_WORDS)
        hi = min(len(line), matched[-1] + 1 + _SNIPPET_WORDS)
        text = " ".join(w[4] for w in line[lo:hi])
        return ("… " if lo > 0 else "") + text + (" …" if hi < len(line) else "")

    def _char_lines(self) -> list:
        """Per-line char index ``(y0, y1, [(x0, y0, x1, y1, char), …])``, built on first use."""
        if self._chars is None:
            self._chars = []
            if self._page is not None:
                raw = self._page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT)
                for block in raw.get("blocks", []):
                    for line in block.get("lines", []):
                        chars = [(*ch["bbox"], ch["c"])
                                 for span in line.get("spans", []) for ch in span.get("chars", [])]
                        if chars:
                            self._chars.append((min(c[1] for c in chars),
                                                max(c[3] for c in chars), chars))
        return self._chars

    def text_under(self, box: tuple) -> str:
        """The text actually under ``box`` — every character whose centre falls inside it, in
        reading order. See the module docstring for why this is not ``page.get_textbox(box)``."""
        x0, y0, x1, y1 = box
        return "".join(c[4] for _ly0, _ly1, chars in self._band(self._char_lines(), box)
                       for c in chars
                       if x0 <= (c[0] + c[2]) / 2 <= x1 and y0 <= (c[1] + c[3]) / 2 <= y1)
