"""M140 — ``get_heading_candidates``: the lines that look like headings, for an agent to judge.

**What it does.** A heading is set so that a reader can see it is one. So a line is a candidate when
its style stands out **from the document's body text** — the style most of the document is set in.
There are four ways to stand out, and each is a comparison with the body, not a tuned number:

* larger than the body;
* bold where the body is not;
* italic where the body is not, at the body's size or larger;
* or the same three, for the **opening phrase** of a line whose text then continues in another style
  — a run-in heading such as ``Post-2009 Plan Information: Following amendment of…``.

Nothing here decides which candidates are headings, or at what level. The calling agent does that,
from the text and from a table of the styles in play, and writes the result with ``set_outline``.
That split is the owner's standing position (PLAN.md §M138-M140).

**Why the body is measured over the whole document**, not over the pages asked for. A section can be
mostly tables. In seven of the 37 sections of a 572-page prospectus the most common style is table
text, and measured against that, ordinary paragraphs count as "larger": its 17-page *Basis for Issue
Price* returns 1,159 candidates instead of 315. Where the most common style is bold table text, bold
stops counting at all. The whole-document pass takes under 2 s on those 572 pages.

**Recall, not precision.** A heading this misses is lost to the caller for good; a line it returns
that is not a heading costs a glance. Ten documents in the test corpus carry bookmarks written by
their publishers, and 502 of those titles are printed on their page. The four tests find 501 (the
miss is a figure caption whose title runs on in ordinary text); ``tools/heading_corpus_check.py``
repeats the check. Two tests the plan proposed were measured and
dropped: a numbering pattern and "a short line before body text" found nothing the four did not, and
an agent reads ``2.8.1`` in the text itself. **A signal is reported, never used to drop a line** —
``in_table`` above all, which marked five of those real headings as table text, because
``get_tables`` had read each one into a table cell.

**Text a reader cannot see is not read.** A span drawn with neither fill nor stroke — a hidden OCR
layer, or a hidden copy of the visible text — is skipped. A 400-page prospectus carries every line
twice that way, the copy at slightly different sizes. Read, the copy added 362 candidates and 31
styles to the reply — ordinary paragraph lines that happened to measure larger than the body — and
not one heading. A page with nothing drawn is named in ``pages_without_visible_text``.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass

import pymupdf as fitz

from klarpdf.mcp_bridge.queries import _page_of, open_document, resolve_pages
from klarpdf.mcp_bridge.tables import _READ_LOCK, MIN_TEXT_SIZE, read_page

MAX_CANDIDATES = 500
"""How many candidates one reply carries — a mis-call bound beside the character cap, which is the one
that usually binds (an entry runs roughly 70-150 characters)."""

MAX_CANDIDATE_CHARS = 60_000
"""How many characters of candidates and style table one reply carries. The style table is charged
against it, so a long table leaves less room for candidates rather than making the reply longer. It
ran to 10 KB at most on the measured documents (a 572-page prospectus with 49 candidate styles)."""

_DRAWN = fitz.mupdf.FZ_STEXT_FILLED | fitz.mupdf.FZ_STEXT_STROKED
"""A character carrying neither bit is not painted: render mode 3 (invisible) or 7 (clip only)."""

_BOLD_CHAR = fitz.mupdf.FZ_STEXT_BOLD

_WEIGHTS = ("bold", "black", "heavy", "demi")
"""Weights heavier than regular, read from the style part of a font's name (after its last ``-`` or
``,``, so a family called *Blackwood* is not heavy). MuPDF's two bold flags agreed with each other on
every measured document; the name adds weights MuPDF does not flag — ``Arial-Black`` in a product
manual's headings."""

_EXAMPLES = 2
_EXAMPLE_CHARS = 60


@dataclass(frozen=True)
class Style:
    """How a run of text is set. Sizes are kept to a tenth of a point: PyMuPDF reports a nominal
    10 pt as 9.96 on one document and as 10.0 on the next, and no printed hierarchy differs by less."""

    font: str
    size: float
    bold: bool
    italic: bool

    def rank(self) -> tuple:
        """Largest first, then bold, then italic — so a lower id is a louder style."""
        return (-self.size, not self.bold, not self.italic, self.font)

    def describe(self) -> dict:
        return {"font": self.font, "size": self.size, "bold": self.bold, "italic": self.italic}


def _weight_part(font: str) -> str:
    cut = max(font.rfind("-"), font.rfind(","))
    return font[cut + 1:].lower() if cut >= 0 else ""


def _style_of(span: dict, known: dict[tuple, Style] | None = None) -> Style:
    """The span's style. ``known`` shares one object per distinct style across a document, because a
    whole-document call holds every line until the body style is known: on a 572-page prospectus the
    call peaked 10 MB lower with it (about 90 MB above the server's baseline in all)."""
    font = span["font"]
    key = (font, round(span["size"], 1), span["flags"], span["char_flags"] & _BOLD_CHAR)
    if known is not None and key in known:
        return known[key]
    bold = (
        bool(span["flags"] & fitz.TEXT_FONT_BOLD)
        or bool(span["char_flags"] & _BOLD_CHAR)
        or any(weight in _weight_part(font) for weight in _WEIGHTS)
    )
    lowered = font.lower()
    italic = bool(span["flags"] & fitz.TEXT_FONT_ITALIC) or "italic" in lowered or "oblique" in lowered
    style = Style(font, round(span["size"], 1), bold, italic)
    if known is not None:
        known[key] = style
    return style


def _emphasised(style: Style, body: Style) -> bool:
    """Is ``style`` set to stand out from ``body``? Larger; bold where the body is not; or italic where
    the body is not, at no smaller a size — small italic is a source note or a caption far more often
    than a heading, and no measured heading was set that way, while small bold ones were."""
    if style.size > body.size:
        return True
    if style.bold and not body.bold:
        return True
    return style.italic and not body.italic and style.size >= body.size


@dataclass(eq=False)
class _Line:
    """One MuPDF text line in the page's displayed orientation, with its spans in order.

    A span holding only whitespace has ``style`` ``None``. It still separates the words either side
    of it — a prospectus sets the space in ``Naked Short Selling`` as a span of its own, in another
    font, and dropping it printed ``ShortSelling`` — but carries no ink, so it never counts towards a
    style and never ends an opening phrase.
    """

    spans: list[tuple[Style | None, str, fitz.Rect]]
    block: int

    @property
    def styled(self) -> list[tuple[Style, str, fitz.Rect]]:
        return [span for span in self.spans if span[0] is not None]

    @property
    def rect(self) -> fitz.Rect:
        styled = self.styled
        rect = fitz.Rect(styled[0][2])
        for _, _, box in styled[1:]:
            rect |= box
        return rect

    @property
    def text(self) -> str:
        return " ".join("".join(text for _, text, _ in self.spans).split())

    @property
    def style(self) -> Style:
        """The style carrying most of the line's characters; a tie goes to the one met first."""
        weights: Counter[Style] = Counter()
        for style, text, _ in self.styled:
            weights[style] += len("".join(text.split()))
        return weights.most_common(1)[0][0]

    @property
    def first_style(self) -> Style:
        return self.styled[0][0]

    @property
    def last_style(self) -> Style:
        return self.styled[-1][0]

    def has_ordinary(self, body: Style) -> bool:
        """Does any of the line's text *not* stand out from ``body``?"""
        return any(not _emphasised(style, body) for style, _, _ in self.styled)

    def lead(self, body: Style) -> tuple[Style, str, fitz.Rect] | None:
        """The line's opening run of text set to stand out from ``body``, when ordinary text follows.

        ``None`` when the line opens with ordinary text, or when nothing ordinary follows (the whole
        line stands out). The run may mix emphasised styles — a bold ``2.10.1.1`` before a bold
        italic title — and its style is the one carrying most of its characters. How long the run is
        against the rest of the line does not matter: a prospectus prints ``Occupational Safety,
        Health and Working Conditions Code, 2020,`` before four words of ordinary text, and judged by
        the line's commonest style it came back with those words attached.
        """
        rect: fitz.Rect | None = None
        texts: list[str] = []
        weights: Counter[Style] = Counter()
        for style, text, box in self.spans:
            if style is not None and not _emphasised(style, body):
                if rect is None:
                    return None
                return weights.most_common(1)[0][0], " ".join("".join(texts).split()), rect
            texts.append(text)
            if style is not None:
                weights[style] += len("".join(text.split()))
                rect = fitz.Rect(box) if rect is None else rect | box
        return None


@dataclass
class _Page:
    lines: list[_Line]
    visible: bool


def _read_page_lines(page: fitz.Page, known: dict[tuple, Style] | None = None) -> _Page:
    """The page's visible, horizontal text lines, as the page is displayed.

    **Under the table reader's lock.** ``find_tables`` switches PyMuPDF's process-wide small-glyph
    setting on while it runs, and the MCP SDK runs tool calls in worker threads; a ``get_text`` taken
    meanwhile would measure tighter boxes than the same call a moment later.
    """
    with _READ_LOCK:
        data = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    matrix = page.rotation_matrix if page.rotation else None
    lines: list[_Line] = []
    visible = False
    for block in data["blocks"]:
        for line in block.get("lines", ()):
            spans: list[tuple[Style | None, str, fitz.Rect]] = []
            for span in line["spans"]:
                box = fitz.Rect(span["bbox"])
                if matrix is not None:
                    box = box * matrix
                    box.normalize()
                if not span["text"].strip():
                    spans.append((None, span["text"], box))
                    continue
                if not span["char_flags"] & _DRAWN or span["alpha"] == 0:
                    continue
                visible = True
                if span["size"] < MIN_TEXT_SIZE:
                    continue
                spans.append((_style_of(span, known), span["text"], box))
            if not any(style is not None for style, _, _ in spans):
                continue
            dx, dy = line["dir"]
            if matrix is not None:
                dx, dy = dx * matrix.a + dy * matrix.c, dx * matrix.b + dy * matrix.d
            if abs(dx - 1.0) > 0.01 or abs(dy) > 0.01:
                continue  # set at an angle as the page is displayed: a label, not a heading line
            lines.append(_Line(spans, block["number"]))
    return _Page(lines, visible)


def _weigh(lines: list[_Line], weights: Counter[Style]) -> None:
    """Add each line's characters to the style they are set in."""
    for line in lines:
        for style, text, _ in line.styled:
            weights[style] += len("".join(text.split()))


def body_of(lines: list[_Line]) -> Style | None:
    """The style carrying most of these lines' characters: the body, measured over what is given."""
    weights: Counter[Style] = Counter()
    _weigh(lines, weights)
    return weights.most_common(1)[0][0] if weights else None


@dataclass(eq=False)
class _Candidate:
    page: int
    parts: list[tuple[fitz.Rect, str, Style]]
    run_in: bool
    block: int

    @property
    def rect(self) -> fitz.Rect:
        rect = fitz.Rect(self.parts[0][0])
        for box, _, _ in self.parts[1:]:
            rect |= box
        return rect

    @property
    def text(self) -> str:
        return " ".join(text for _, text, _ in sorted(self.parts, key=lambda part: part[0].x0))

    @property
    def style(self) -> Style:
        weights: Counter[Style] = Counter()
        for _, text, style in self.parts:
            weights[style] += len("".join(text.split()))
        return weights.most_common(1)[0][0]


def _side_by_side(a: fitz.Rect, b: fitz.Rect) -> bool:
    """Two boxes on one visual row: each one's vertical middle lies within the other."""
    return a.y0 <= (b.y0 + b.y1) / 2 <= a.y1 and b.y0 <= (a.y0 + a.y1) / 2 <= b.y1


def _names_something(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _candidates(page_number: int, lines: list[_Line], body: Style) -> list[_Candidate]:
    """The candidates on one page, in reading order.

    A line that opens with text set to stand out and then continues in ordinary text is a **run-in**
    heading, and only its opening phrase is the candidate — unless the line before it in the same text
    block is an ordinary line ending in that same style. That is a bold phrase wrapping onto this line
    rather than a heading starting it (measured on a prospectus: ``National Highways Authority of
    India Act, 19`` / ``88 (the "NHAI Act")``). A line above that stands out all through is a heading,
    so it does not count: two headings can follow each other with the second one's tail in body text.

    Any other line qualifies whole when the style carrying most of it stands out — a heading, or a
    ``1.`` in body text before a bold title.

    Candidate lines side by side in one text block are one entry: a numbered heading is often two
    lines, ``2.8.1`` and its title. Lines from different blocks are never joined — on a two-column
    page that would weld a heading to whatever stands level with it in the other column.
    """
    previous: dict[int, _Line] = {}
    whole: list[_Candidate] = []
    run_ins: list[_Candidate] = []
    for line in lines:
        before = previous.get(line.block)
        previous[line.block] = line
        lead = line.lead(body)
        if lead is not None:
            lead_style, lead_text, lead_rect = lead
            wrapped = (
                before is not None
                and before.last_style == line.first_style
                and before.has_ordinary(body)
            )
            if _names_something(lead_text) and not wrapped:
                run_ins.append(_Candidate(page_number, [(lead_rect, lead_text, lead_style)], True, line.block))
            continue
        style = line.style
        if _emphasised(style, body) and _names_something(line.text):
            whole.append(_Candidate(page_number, [(line.rect, line.text, style)], False, line.block))

    joined: list[_Candidate] = []
    for candidate in sorted(whole, key=lambda c: ((c.rect.y0 + c.rect.y1) / 2, c.rect.x0)):
        box = candidate.parts[0][0]
        for existing in joined:
            if existing.block == candidate.block and _side_by_side(existing.rect, box):
                existing.parts.extend(candidate.parts)
                break
        else:
            joined.append(candidate)
    found = joined + run_ins
    found.sort(key=lambda c: (c.rect.y0, c.rect.x0))
    return found


def _unrotated(page: fitz.Page, rect: fitz.Rect) -> list[float]:
    """Displayed space back to the unrotated space ``search``, ``clip`` and the redactions use."""
    box = fitz.Rect(rect) * page.derotation_matrix if page.rotation else fitz.Rect(rect)
    box.normalize()
    return [round(v, 1) for v in box]


def _example(text: str) -> str:
    return text if len(text) <= _EXAMPLE_CHARS else text[: _EXAMPLE_CHARS - 1] + "…"


def _style_table(
    found: list[_Candidate], ids: dict[Style, str], marked: dict[int, bool] | None
) -> list[dict]:
    """One row per style the candidates use, loudest first — what an agent classifies from.

    **Examples are texts that occur once.** A style's first lines are very often a running head —
    the bold style of a 572-page prospectus opened with its company name and registration number,
    which says nothing about whether that style also sets headings. A text printed once in the scope
    is the better witness; repeated ones are used only when there is nothing else. ``distinct``
    beside ``count`` says how much of a style is repetition.
    """
    by_style: dict[Style, list[_Candidate]] = {}
    for candidate in found:
        by_style.setdefault(candidate.style, []).append(candidate)
    table = []
    for style in sorted(by_style, key=Style.rank):
        members = by_style[style]
        texts = Counter(candidate.text for candidate in members)
        ordered = list(texts)
        chosen = [text for text in ordered if texts[text] == 1][:_EXAMPLES]
        chosen += [text for text in ordered if text not in chosen][: _EXAMPLES - len(chosen)]
        entry = {
            "style": ids[style],
            **style.describe(),
            "count": len(members),
            "distinct": len(texts),
            "pages": len({candidate.page for candidate in members}),
            "examples": [_example(text) for text in chosen],
        }
        if marked is not None:
            entry["in_table"] = sum(marked[id(candidate)] for candidate in members)
        table.append(entry)
    return table


def heading_candidates(
    path: str,
    pages: list[int] | None = None,
    *,
    styles: list[str] | None = None,
    tables: bool = False,
    password: str | None = None,
    max_candidates: int = MAX_CANDIDATES,
    max_chars: int = MAX_CANDIDATE_CHARS,
    offset: int = 0,
) -> dict:
    """Every line on ``pages`` (default: all) set to stand out from the document's body text.

    See the module docstring for what qualifies and why. The reply is paginated on two caps, as
    ``get_links`` is, with the style table charged against the character cap.

    ``styles`` narrows the candidates to those ids **after** the style table is built, so the table
    still describes everything in scope — the same order ``get_links`` keeps between its ``kinds``
    census and its filter. The ids rank every style in the document, so they are stable across page
    ranges.

    ``tables`` reads each page that carries a candidate with the same :func:`tables.read_page`
    ``get_tables`` uses, so ``in_table`` agrees with that tool by construction. It needs ``pages``:
    reading tables costs tens of times more than reading text.
    """
    if offset < 0:
        raise ValueError(f"offset must be >= 0; got {offset}")
    if tables and not pages:
        raise ValueError(
            "tables: true needs an explicit page range — it reads each page's tables, which costs "
            "tens of times more than reading its text. Name the section's pages, or leave tables off."
        )
    if styles is not None and not all(isinstance(s, str) for s in styles):
        raise ValueError(f"styles must be a list of style ids such as \"s3\"; got {styles!r}")

    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        in_scope = set(indices)
        weights: Counter[Style] = Counter()
        kept: dict[int, _Page] = {}
        known: dict[tuple, Style] = {}
        for index0 in range(vdoc.page_count):
            read = _read_page_lines(_page_of(vdoc, index0), known)
            _weigh(read.lines, weights)
            if index0 in in_scope:
                kept[index0] = read

        ids = {style: f"s{rank}" for rank, style in enumerate(sorted(weights, key=Style.rank), start=1)}
        body = weights.most_common(1)[0][0] if weights else None

        found: list[_Candidate] = []
        if body is not None:
            for index0 in indices:
                found.extend(_candidates(index0 + 1, kept[index0].lines, body))

        marked: dict[int, bool] = {}
        if tables:
            reads: dict[int, list[fitz.Rect]] = {}
            for candidate in found:
                index0 = candidate.page - 1
                if index0 not in reads:
                    reads[index0] = [t["bbox"] for t in read_page(_page_of(vdoc, index0)).tables]
                rect = candidate.rect
                centre = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
                marked[id(candidate)] = any(box.contains(centre) for box in reads[index0])

        style_table = _style_table(found, ids, marked if tables else None)

        if styles is not None:
            known = set(ids.values())
            unknown = [s for s in styles if s not in known]
            if unknown:
                offered = ", ".join(entry["style"] for entry in style_table) or "none"
                raise ValueError(
                    f"unknown style id(s) {', '.join(repr(s) for s in unknown)}; the candidate styles "
                    f"on these pages are {offered}. Ids come from the `styles` list of a reply for "
                    "this document."
                )
            wanted = set(styles)
            found = [c for c in found if ids[c.style] in wanted]

        def describe(candidate: _Candidate) -> dict:
            entry = {
                "page": candidate.page,
                "bbox": _unrotated(_page_of(vdoc, candidate.page - 1), candidate.rect),
                "text": candidate.text,
                "style": ids[candidate.style],
                "run_in": candidate.run_in,
            }
            if tables:
                entry["in_table"] = marked[id(candidate)]
            return entry

        total = len(found)
        batch: list[dict] = []
        used = len(json.dumps(style_table))
        for candidate in found[offset:]:
            if len(batch) >= max_candidates:
                break
            entry = describe(candidate)
            size = len(json.dumps(entry))
            # Always take at least one: an empty batch with `more_available: true` pages forever.
            if batch and used + size > max_chars:
                break
            batch.append(entry)
            used += size
        more_available = offset + len(batch) < total

        result = {
            "count": len(batch),
            "total_candidates": total,
            "offset": offset,
            "candidates": batch,
            "body": body.describe() if body is not None else None,
            "styles": style_table,
            "tables_checked": tables,
            "pages_without_visible_text": [i + 1 for i in indices if not kept[i].visible],
            "pages_scanned": [i + 1 for i in indices],
            "source": os.path.abspath(path),
            "more_available": more_available,
        }
        if more_available:
            result["warnings"] = [
                f"{total} candidates in scope; returned {len(batch)} starting at offset {offset}. "
                f"Call again with offset: {offset + len(batch)} for the rest, or narrow with "
                "`styles` / `pages`."
            ]
        return result
