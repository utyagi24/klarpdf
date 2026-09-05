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

# ---- invisible text (M95) ----------------------------------------------------
#
# White-on-white text is the classic redaction hazard: live to `get_text`, copy-paste and every
# downstream indexer, and absent from the render a human signs off on. A machine-generated bill
# found in TC-003 carried its account number twice that way, in 10 pt Arial at the extreme margins.
#
# **Colour alone cannot answer this, and the measurement is what says so.** That same page has 21
# white spans; 19 of them are ordinary table headers — "Customer Name", "Bill Date" — painted on
# dark banners and perfectly legible. A "white means invisible" rule would have flagged all 21, and
# a flag that fires on every table header of every bill is one a reader learns to ignore, which is
# worse than no flag on the two that matter.
#
# So colour is only a **pre-filter**, and the answer comes from rendering the box and asking whether
# anything was actually drawn there. Measured on that page: contrast 1 for the two invisible tags,
# 163-215 for all 19 legible headers. The gap is not close, which is what makes the threshold safe.
_LIGHT_LUMINANCE = 200    # a span this pale is only legible on a dark ground — worth rendering
_TRANSPARENT_ALPHA = 8    # …as is one that is barely painted at all
_INVISIBLE_CONTRAST = 12  # rendered luminance spread below this: nothing was drawn


def _luminance(color: int) -> float:
    """Perceived brightness of a packed ``0xRRGGBB``, 0 (black) to 255 (white)."""
    return ((color >> 16 & 0xFF) * 299 + (color >> 8 & 0xFF) * 587 + (color & 0xFF) * 114) / 1000


def boxes_touch(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def shares_line(word: tuple, box: tuple) -> bool:
    """Do ``word`` and ``box`` sit on the same line of text, rather than merely overlapping? (M96)

    A word's box is not the ink — it spans the font's full ascender-to-descender height, so on a
    tightly-leaded page consecutive lines' boxes **overlap vertically**. :func:`boxes_touch` is a
    plain 2-D intersection and cannot tell that apart from a word the box genuinely covers, so a hit
    was "striking" words from the line above and below it.

    That is not cosmetic: :meth:`PageText.is_whole_word` reads the first and last struck word as the
    ones at the hit's edges, so a neighbour from the previous line — whose letters of course extend
    to the left of the hit — made the left edge look like the middle of a word and the match was
    dropped. On the SSA-3, ``search "Security"`` with ``whole_words`` on returned **1 of 5**: four
    were rejected against words on an adjacent line (TC-004).

    The test is by **centre**, in either direction: a word belongs to the box when its own vertical
    midline falls inside the box, or the box's midline falls inside the word. One direction alone is
    not enough — the first fails a hit box shorter than its word, the second fails a box spanning a
    line whose words are shorter than it — and requiring either keeps both cases while still putting
    a whole line's leading between a word and its neighbour's midline.
    """
    word_middle = (word[1] + word[3]) / 2
    box_middle = (box[1] + box[3]) / 2
    return box[1] <= word_middle <= box[3] or word[1] <= box_middle <= word[3]


def _centre_inside(char: tuple, box: tuple) -> bool:
    """Does ``char``'s centre fall inside ``box``? The rule the whole module answers by.

    A character belongs to *one* box — the one containing its midpoint — which is what makes
    "the text under this rectangle" well defined when rectangles overlap, and what lets
    :meth:`PageText.text_under_all` deduplicate by character rather than by geometry.
    """
    return box[0] <= (char[0] + char[2]) / 2 <= box[2] and box[1] <= (char[1] + char[3]) / 2 <= box[3]


def _is_word_char(ch: str) -> bool:
    """Does ``ch`` carry word content, as opposed to being punctuation wrapped around it?"""
    return ch.isalnum() or ch == "_"


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
        self._spans: list | None = None

    @staticmethod
    def _band(lines: list, box: tuple) -> list:
        """The lines whose vertical band meets ``box``. A word can only touch the box if its own
        band does, and a line's band covers every word on it — so this narrows without dropping."""
        return [entry for entry in lines if entry[0] < box[3] and box[1] < entry[1]]

    def struck(self, box: tuple) -> list:
        """The page words ``box`` covers, as ``(index, word)`` in document order.

        Overlap alone is not the test — see :func:`shares_line`. A word from the neighbouring line
        can intersect the box on a tightly-leaded page, and counting it here is what made
        whole-word search drop four of five matches on a dense form (M96 / TC-004).
        """
        found = [(i, w) for _ly0, _ly1, ws in self._band(self._lines, box)
                 for i, w in ws if boxes_touch(w[:4], box) and shares_line(w[:4], box)]
        found.sort(key=lambda t: t[0])
        return found

    def is_whole_word(self, box: tuple, tol: float = 0.5) -> bool:
        """Is ``box`` a whole word rather than part of a longer one?

        Geometry first: when the words the box touches stop at its edges, nothing can contradict it
        and no character index is needed. That fast path is the common case and is what keeps a
        whole-word search over a long document cheap.

        When a word *does* run past an edge, the overhang decides — because geometry alone gets
        that case wrong. MuPDF splits ``get_text("words")`` on **whitespace**, so ``expression.``
        is a single word whose box includes the period; a hit covering just the letters ends
        ~2.4 pt short of it, and the purely-geometric test this replaces read that period as more
        word and rejected the match. Every whole-word hit at the end of a sentence was silently
        dropped — in the find bar since M64, and in the MCP bridge's ``search`` / ``redact_text``,
        where M44's verification pass caught it leaving a redacted phrase legible (TC-001).

        So the characters the word puts *outside* the box are consulted instead: the box is a whole
        word when none of them is a letter, digit or underscore. Read the other way round, a whole
        word is the struck token stripped of the punctuation around it — ``expression.`` matches,
        and ``expressionless`` does not.

        Deliberately **not** a regex ``\\b``, which tests only the single adjacent character and
        would therefore find ``ALPHA`` inside ``ALPHA-zero-A0``. This app has treated a hyphenated
        compound as one word since M64 and its find bar is tested on exactly that string; widening
        the boundary must not widen the match.
        """
        struck = self.struck(box)
        if not struck:
            return True                  # nothing to contradict it (e.g. a box with no word boxes)
        return (self._edge_is_boundary(struck[0][1], box[0], right=False, tol=tol)
                and self._edge_is_boundary(struck[-1][1], box[2], right=True, tol=tol))

    def _edge_is_boundary(self, word: tuple, edge: float, *, right: bool, tol: float) -> bool:
        """Does ``word`` stop at ``edge``, or is everything it puts past ``edge`` non-word text?

        Only ``word``'s **own** characters are considered, and that scoping is what makes the test
        reliable without a gap heuristic: MuPDF has already decided where the whitespace breaks
        are, so a character on the far side of a space can never be mistaken for part of this word.
        A PDF that positions its words without emitting space glyphs — common — would otherwise
        offer the ``n`` of ``given`` as the neighbour of ``regular`` and reject a good match.

        Falls back to the geometric answer when there is no character index to consult, which is the
        case for a words-only :class:`PageText` (see :func:`viewer.search.is_whole_word`).
        """
        if (word[2] <= edge + tol) if right else (word[0] >= edge - tol):
            return True                  # the word ends at the box; geometry already settles it
        chars = self._chars_under(word[:4])
        if not chars:
            return False                 # no char index: geometry is all we have, and it says no
        centres = [((c[0] + c[2]) / 2, c[4]) for c in chars]
        overhang = [ch for cx, ch in centres if (cx > edge if right else cx < edge)]
        return not any(_is_word_char(ch) for ch in overhang)

    def is_invisible(self, box: tuple) -> bool:
        """Is the text under ``box`` present in the file but absent from the page as drawn?

        The question a redaction caller cannot otherwise ask. White-on-white text reads back from
        ``get_text`` and from any indexer, and shows up in neither ``render_page`` nor the eye of
        the person approving the redaction — so it is exactly where sensitive values hide, and
        TC-003 found an account number sitting there twice.

        **Rendered, not inferred**, for the reason recorded at :data:`_LIGHT_LUMINANCE`: on the page
        that prompted this, 19 of 21 white spans were legible headers on dark banners. Colour picks
        the candidates cheaply; the pixels decide. A box whose rendered clip has essentially no
        luminance spread had nothing drawn in it.

        Catching more than it was built for, and none of it by accident: text painted in the
        background colour whatever that colour is, text with zero alpha, and text covered by an
        opaque image drawn over it all render to the same flat patch. What it does **not** catch is
        dark text on an equally dark ground — the pre-filter is for pale text, because a page's
        ground is nearly always the pale one. ``False`` therefore means "not invisible in the way
        this can see", which is why it reads as a flag and never as a guarantee.

        Always ``False`` for a words-only :class:`PageText`: there is no page to render.
        """
        if self._page is None:
            return False
        spans = self._spans_under(box)
        if not spans:
            return False
        if not any(_luminance(color) >= _LIGHT_LUMINANCE or alpha <= _TRANSPARENT_ALPHA
                   for _bbox, color, alpha in spans):
            return False        # ordinary ink on an ordinary ground; no render needed
        return self._rendered_contrast(box) < _INVISIBLE_CONTRAST

    def _rendered_contrast(self, box: tuple) -> float:
        """Spread between the lightest and darkest pixel of ``box`` as the page actually draws it.

        Rendered at 72 dpi — the glyphs only have to disturb the ground, not be readable, and a
        pale-text page costs one small pixmap per candidate box (measured: ~3 ms).
        """
        try:
            pixmap = self._page.get_pixmap(clip=fitz.Rect(*box), alpha=False)
        except Exception:  # noqa: BLE001 — an unrenderable box is not a claim that it is invisible
            return float("inf")
        if not pixmap.width or not pixmap.height:
            return float("inf")
        samples, stride = pixmap.samples, pixmap.n
        levels = [
            (samples[i] * 299 + samples[i + 1] * 587 + samples[i + 2] * 114) / 1000
            for i in range(0, len(samples) - stride + 1, stride)
        ]
        return max(levels) - min(levels) if levels else float("inf")

    def _spans_under(self, box: tuple) -> list:
        """``(bbox, colour, alpha)`` for the spans ``box`` touches. Lazy, and a cheaper extraction
        than the character index: only the span's paint is wanted, not its glyphs."""
        if self._spans is None:
            self._spans = []
            if self._page is not None:
                raw = self._page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
                for block in raw.get("blocks", []):
                    for line in block.get("lines", []):
                        entries = [(span["bbox"], span.get("color", 0), span.get("alpha", 255))
                                   for span in line.get("spans", [])]
                        if entries:
                            self._spans.append((min(e[0][1] for e in entries),
                                                max(e[0][3] for e in entries), entries))
        return [entry for _ly0, _ly1, entries in self._band(self._spans, box)
                for entry in entries if boxes_touch(entry[0], box)]

    def matches_case(self, box: tuple, term: str) -> bool:
        """Does the text under ``box`` match ``term`` case-sensitively?

        MuPDF's ``search_for`` is always case-insensitive, so this is how a case-sensitive search
        gets filtered. The test is **containment, not equality**, because a phrase that wraps a
        line break comes back as one hit box per line fragment: the box under ``regular`` is a
        genuine part of a case-sensitive match for ``regular expression``, and comparing it against
        the whole term dropped the occurrence outright — which in ``redact_text`` meant leaving it
        in the file. For a hit that does not wrap, the fragment *is* the whole term and
        containment is equality, so nothing is loosened for the ordinary case.
        """
        under = self.text_under(box).strip()
        return bool(under) and under in term

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

    def group_matches(self, boxes: list[tuple], term: str) -> list[tuple[tuple, ...]]:
        """Fold one term's hit boxes, in reading order, into one entry per **occurrence**.

        MuPDF returns a match that wraps a line break as one rect per line, so a phrase occurring
        five times can come back as seven boxes. Every box is real and redaction has to clear all
        of them — dropping the second is what left a legible ``expression.`` under a black box in
        TC-001 — but *counting* a fragment as a match is a different claim, and a wrong one: the
        find bar would say "4 of 7" and step through one occurrence twice.

        Occurrences are recovered by reading the page rather than by guessing from geometry: the
        text under each box is accumulated until it spells the term, which is exactly the condition
        that closes an occurrence. A run that stops building toward the term is emitted box by box
        instead — **a box is never dropped**, because the caller may be about to redact it, and an
        ungrouped box costs a miscount while a missing one costs a leak.
        """
        wanted = " ".join(term.split()).casefold()
        groups: list[tuple[tuple, ...]] = []
        pending: list[tuple] = []
        acc = ""
        for box in boxes:
            piece = self.text_under(box).strip()
            acc = f"{acc} {piece}".strip() if pending else piece
            pending.append(box)
            if acc.casefold() == wanted:
                groups.append(tuple(pending))
                pending, acc = [], ""
            elif not wanted.startswith(acc.casefold()):
                groups.extend((box,) for box in pending)   # not one occurrence after all
                pending, acc = [], ""
        groups.extend((box,) for box in pending)           # never leave a box behind
        return groups

    def snippet_for(self, boxes: list[tuple]) -> str:
        """Context snippet for a whole match: the per-line snippets joined when it wraps, so a
        reader sees the phrase rather than the half of it that fitted on the first line."""
        parts = [part for part in (self.snippet(box) for box in boxes) if part]
        return " ".join(parts)

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

    def _lines_under(self, box: tuple) -> list[list]:
        """The characters under ``box``, **grouped by the text line they sit on**, in reading order.

        The grouping is the whole point: a box tall enough to cover two lines covers the end of one
        and the start of the next, and those are not adjacent text. Flattening them first is what
        made :meth:`text_under` report ``'TYAGI1703'`` for a mailing block (M97 / TC-005).
        """
        found: list[list] = []
        for _ly0, _ly1, chars in self._band(self._char_lines(), box):
            inside = [c for c in chars if _centre_inside(c, box)]
            if inside:
                found.append(inside)
        return found

    def _chars_under(self, box: tuple) -> list:
        """The character entries whose centres fall inside ``box``, in reading order. Empty for a
        words-only :class:`PageText`, which has no page to take characters from."""
        return [c for line in self._lines_under(box) for c in line]

    def text_under(self, box: tuple) -> str:
        """The text actually under ``box`` — every character whose centre falls inside it, in
        reading order. See the module docstring for why this is not ``page.get_textbox(box)``.

        **Lines are separated, not concatenated.** A box covering two lines ends one and begins the
        next, and joining them edge to edge invents a word that is in no document: the mailing block

            UMESH TYAGI
            1703 PORCELLANO WAY

        read back as ``'UMESH TYAGI1703 PORCELLANO WAY'``, whose tokens include ``TYAGI1703``.
        Nothing downstream could find that string, because it does not exist — which is how a
        correct region redaction came to fail its own verification and delete its output (M97 /
        TC-005). Callers that split on whitespace get the two words they should always have had;
        callers comparing against a single line are unaffected, because a single line gains no
        separator.
        """
        return "\n".join("".join(c[4] for c in line) for line in self._lines_under(box))

    def text_under_all(self, boxes: list[tuple]) -> str:
        """The text under **any** of ``boxes``, each character counted **once**, lines separated.

        Not the same as joining :meth:`text_under` over the boxes, and the difference is the whole
        reason this exists (M100). Two boxes covering the same characters — a phrase query and a
        sub-phrase query that both matched the same run of text — each report those characters, so
        a caller counting occurrences over the concatenation counts them twice. Verification then
        claims to have covered a token more often than the page ever contained it, and the budget
        ``before - covered`` goes negative: an impossible expectation that no output can satisfy,
        which is M97's ``_shortfall`` path firing on a redaction that was actually correct.

        Deduplication is at the **character**, not the rectangle. Overlapping rects are not the
        question — a character belongs to whichever box contains its *centre*, so two rects can
        intersect in a band holding no centres and share nothing at all, while one rect wholly
        inside another shares everything. Merging the rectangles instead would answer the wrong
        question and, across two lines, would widen the redaction to cover what sits between them.

        **Separated into contiguous runs, not just into lines** — M97's rule generalised. That
        milestone found that joining two *lines* edge to edge invents a token in no document
        (``UMESH TYAGI`` + ``1703 PORCELLANO WAY`` → ``TYAGI1703``); a box set does the same thing
        *within* a line, because two boxes on one line have uncovered text between them.
        Concatenating a line's covered characters turned redactions of ``Smith`` and ``Jones`` into
        the token ``SmithJones``, which the source contains zero times — so the budget went negative
        and a correct redaction deleted its own output: the identical failure by the identical
        mechanism, one axis over. A run therefore ends wherever an uncovered character interrupts
        it, and each run is emitted separately.
        """
        if not boxes:
            return ""
        runs: list[str] = []
        for ly0, ly1, chars in self._char_lines():
            # The same narrowing :meth:`_band` does, read the other way round: only boxes whose
            # vertical extent meets this line can hold any of its characters. Without it every
            # character is tested against every box, which measured 4.5x slower than the per-box
            # scan this replaced (90 boxes on a 540-word page: 16.0 ms against 3.5 ms).
            near = [box for box in boxes if box[1] < ly1 and ly0 < box[3]]
            if not near:
                continue
            run: list[str] = []
            for char in chars:
                if any(_centre_inside(char, box) for box in near):
                    run.append(char[4])
                elif run:
                    runs.append("".join(run))
                    run = []
            if run:
                runs.append("".join(run))
        return "\n".join(runs)
