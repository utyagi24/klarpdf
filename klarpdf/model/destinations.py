"""Reading and writing a destination **exactly as the file spells it** (PLAN.md, M150).

A link or a bookmark can say more than which page to open: ``/XYZ left top zoom`` names a point on
that page, ``/FitH top`` a horizontal band, ``/FitR`` a rectangle. Everything in this repo that
moves pages has to write those destinations out again, and **PyMuPDF's two writers each corrupt
them, in different ways** — measured on 1.27.2.3:

* ``Document.set_toc`` takes the point in the target page's *displayed* (rotated) space. At 0°, 90°
  and 180° that round-trips exactly. At **270°** it swaps the page's width and height, so the point
  lands somewhere else — and further away on every save, since the next read/write repeats it. It
  also **ignores a crop box whose origin is not 0,0**, at every rotation.
* ``Page.insert_link`` takes the same ``to`` point in *unrotated* space, while ``Page.get_links``
  reports it *rotated*. So feeding a link's own ``to`` straight back in — which is what a remap
  does — moves it on every page whose ``/Rotate`` is not 0. It handles a crop box correctly at 0°
  and ignores it at 90/180/270.

And both writers only ever emit ``/XYZ``: a reader gives back ``{'kind': 4, 'page': '3'}`` for
``/XYZ null 600``, ``{'view': 'FitH,192'}`` for ``/FitH``, ``{'viewrect': …}`` for ``/FitR``, none
of which ``set_toc`` can write, so those destinations come out as the top of the page. Across the
123-document corpus that is **467 of 886 positioned bookmarks** and every one of the 1,068 named
links (#373).

So this module does not convert coordinates at all on the write side. It reads the destination as
the file holds it — the raw PDF syntax after the page reference — and writes that string back with
only the page reference changed. Exact at every rotation, every crop box and every destination
form, by construction rather than by getting a transform right, and it stays exact if a future
PyMuPDF fixes either writer.

The one conversion that *is* needed is on the **read** side, for the viewer: where on the page does
this destination point, in the content coordinates the rest of the app uses (unrotated, crop-box
relative, y down)? That is :func:`content_point`, and it is pinned against PyMuPDF's own reader —
which, unlike its writers, is correct — by ``tests/test_destinations.py``.

Model-layer (uses PyMuPDF, no GUI) and headless-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The destination verbs that state a vertical position, and where it sits in their argument list.
#: ``/Fit``, ``/FitB``, ``/FitV`` and ``/FitBV`` are absent on purpose — they name no top, so a
#: reader honours them by showing the page from its top, which is what the app did for every
#: destination before this milestone.
#:
#: ``/FitR left bottom right top`` is the odd one: its top is the *fourth* argument, because the
#: rectangle is given in PDF order (y up), so the larger y — index 3 — is the top edge.
_TOP_ARG = {"XYZ": 1, "FitH": 0, "FitBH": 0, "FitR": 3}

#: Where the horizontal position sits, for the two verbs that state one. ``/FitV x`` and
#: ``/FitBV x`` name a left edge but no top, so they reach :func:`content_point` and contribute
#: only a left.
_LEFT_ARG = {"XYZ": 0, "FitR": 0, "FitV": 0, "FitBV": 0}

#: A destination array is ``[<page> /Verb args…]``. The page is normally an indirect reference to a
#: page object (``12 0 R``); in a destination copied from a remote-document context it can instead
#: be a bare **0-based page number**. Both are met in the wild, so both are read.
_INDIRECT_PAGE = re.compile(r"^\s*(\d+)\s+(\d+)\s+R\s*(.*)$", re.S)
_NUMERIC_PAGE = re.compile(r"^\s*(\d+)\s*(/.*)$", re.S)

_VERB = re.compile(r"^\s*/([A-Za-z]+)\s*(.*)$", re.S)


@dataclass(frozen=True)
class Destination:
    """A destination split into the part that moves and the part that must not change.

    ``page`` is the 0-based index, **in the document it was read from**, of the page the
    destination opens. ``tail`` is everything the destination says after naming that page, as the
    file spells it — ``"/XYZ 100 600 0"``, ``"/FitH 192"``, ``"/Fit"``. Writing the destination
    somewhere else is ``[<new page> 0 R <tail>]``: the tail is carried, never re-derived.
    """

    page: int
    tail: str

    def top(self) -> float | None:
        """The destination's y in **PDF space** (origin at the page's bottom-left, y up), or
        ``None`` when it names no vertical position."""
        return _argument(self.tail, _TOP_ARG)

    def left(self) -> float | None:
        """The destination's x in PDF space, or ``None`` when it names none — which for ``/XYZ``
        is the publisher asking the viewer to keep the reader's horizontal scroll."""
        return _argument(self.tail, _LEFT_ARG)


def _argument(tail: str, table: dict[str, int]) -> float | None:
    """The numeric argument ``table`` picks out of ``tail``, or ``None``.

    ``None`` covers three different things a reader treats the same way: the verb states no such
    argument (``/Fit`` has no top), the argument is the PDF null that means *keep what the reader
    has*, or the destination is malformed. A destination we cannot read is one we do not act on.
    """
    match = _VERB.match(tail)
    if match is None:
        return None
    index = table.get(match.group(1))
    if index is None:
        return None
    args = match.group(2).rstrip("]").split()
    if index >= len(args):
        return None
    try:
        return float(args[index])
    except ValueError:
        return None  # `null`, or something we do not understand


def split_destination(text: str, page_of_xref: dict[int, int]) -> Destination | None:
    """Split a raw destination array into its target page and its tail, or ``None``.

    ``text`` is the array as ``xref_get_key`` hands it over, brackets included. ``page_of_xref``
    maps a **page object's xref** to its 0-based index, which is how the indirect form names its
    page; build it once per document with :func:`page_index_map`.
    """
    inner = text.strip()
    if inner.startswith("["):
        inner = inner[1:]
    match = _INDIRECT_PAGE.match(inner)
    if match is not None:
        page = page_of_xref.get(int(match.group(1)))
        tail = match.group(3)
    else:
        match = _NUMERIC_PAGE.match(inner)
        if match is None:
            return None
        page = int(match.group(1))
        tail = match.group(2)
    if page is None:
        return None  # names a page object that is not a page of this document
    tail = tail.strip().rstrip("]").strip()
    return Destination(page, tail) if tail.startswith("/") else None


def page_index_map(doc) -> dict[int, int]:
    """``{page object xref: 0-based page index}`` for ``doc`` — how a destination names its page."""
    return {doc.page_xref(i): i for i in range(doc.page_count)}


_INDIRECT = re.compile(r"\d+\s+\d+\s+R")
_TOKEN = re.compile(r"/[^\s/\[\]<>(){}]*|[^\s/\[\]<>(){}]+")


def _scan_value(text: str, start: int) -> tuple[str | None, int]:
    """The one PDF object beginning at ``text[start:]``, and the index just past it.

    A hand-rolled scanner rather than a pattern, because the things it has to walk over nest:
    a destination name tree holds its pairs as ``(key)[value](key)[value]…`` in one ``/Names``
    array, so splitting on whitespace — or on the first ``]`` — pairs the wrong things together.
    Delimiters are counted, which is what a regex cannot do.
    """
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text):
        return None, i
    if text[i] == "(":                       # literal string: nests, and backslash escapes
        depth, j = 0, i
        while j < len(text):
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    return text[i : j + 1], j + 1
            j += 1
        return None, len(text)
    for opener, closer in (("<<", ">>"), ("[", "]"), ("<", ">")):
        if not text.startswith(opener, i):
            continue
        depth, j, step = 0, i, len(opener)
        while j < len(text):
            if text.startswith(opener, j):
                depth += 1
                j += step
            elif text.startswith(closer, j):
                depth -= 1
                j += step
                if depth == 0:
                    return text[i:j], j
            else:
                j += 1
        return None, len(text)
    match = _INDIRECT.match(text, i) or _TOKEN.match(text, i)
    return (match.group(0), match.end()) if match else (None, i)


def _pdf_text(token: str) -> str:
    """A PDF key token as the text it stands for — a ``/Name``, a ``(literal)`` or a ``<hex>``.

    All three spell destination names in the corpus, and a document may use one spelling for the
    key and another for the reference to it, so both sides are decoded before being compared. The
    hex form is how a generator writes a name that is not plain ASCII: one Nature paper's names run
    to UTF-16 with embedded byte-order marks, and 81 of its links name their destination that way.
    """
    token = token.strip()
    if token.startswith("(") and token.endswith(")"):
        return re.sub(r"\\(\d{1,3}|.)", _unescape, token[1:-1], flags=re.S)
    if token.startswith("<") and token.endswith(">"):
        digits = re.sub(r"\s", "", token[1:-1])
        if len(digits) % 2:
            digits += "0"
        try:
            raw = bytes.fromhex(digits)
        except ValueError:
            return token
        if raw[:2] == b"\xfe\xff":
            # Only the *leading* byte-order mark goes. PyMuPDF drops that one and keeps any
            # further U+FEFF in the text, and one Nature paper's names carry six of them apiece —
            # so stripping every mark, or none, makes the two sides disagree on all 81 of its
            # links while looking character-for-character identical in a print-out.
            return raw.decode("utf-16-be", "replace").removeprefix("\ufeff")
        return raw.decode("latin-1")
    if token.startswith("/"):
        return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), token[1:])
    return token


def _unescape(match: "re.Match") -> str:
    """One backslash escape inside a PDF literal string.

    The octal check is ``in "01234567"`` rather than ``isdigit()``, which accepts digits from other
    scripts — ``'\u0664'.isdigit()`` is True and ``int('\u0664', 8)`` raises. The same trap is
    pinned in ``tests/test_links_remap.py`` for link targets.
    """
    body = match.group(1)
    if body and all(c in "01234567" for c in body):
        return chr(int(body, 8) & 0xFF)
    return {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(body, body)


def _pairs(text: str):
    """``(decoded key, raw value)`` over a PDF dictionary body or a name array's ``/Names``."""
    i = 0
    stripped = text.lstrip()
    if stripped.startswith("<<"):
        i = text.index("<<") + 2
    elif stripped.startswith("["):
        i = text.index("[") + 1
    while True:
        key, i = _scan_value(text, i)
        if key is None:
            return
        value, i = _scan_value(text, i)
        if value is None:
            return
        yield _pdf_text(key), value


def _resolve_value(doc, raw: str) -> str | None:
    """A looked-up destination value reduced to a destination **array**.

    The value may already be one, or an indirect reference to one, or — the spelling the PDF spec
    prefers for a named destination — a dictionary whose ``/D`` holds it.
    """
    raw = (raw or "").strip()
    if _INDIRECT.fullmatch(raw):
        xref = int(raw.split()[0])
        inner = doc.xref_get_key(xref, "D")
        if inner[0] == "array":
            return inner[1]
        if inner[0] != "null":
            return _resolve_value(doc, inner[1])
        return _resolve_value(doc, doc.xref_object(xref, compressed=True))
    if raw.startswith("<<"):
        # Through `_lookup`, not `raw.find("/D")`, which matches the `/D` of a neighbouring
        # `/Desc` or `/Dest` and then scans from the middle of that key's name.
        found = _lookup(raw, "D")
        return _resolve_value(doc, found) if found else None
    return raw if raw.startswith("[") else None


def _lookup(text: str, name: str) -> str | None:
    """The raw value stored under ``name`` in the dictionary body or name array ``text``."""
    for key, value in _pairs(text):
        if key == name:
            return value
    return None


def _named_destination(doc, name: str, memo: dict | None = None) -> str | None:
    """The raw destination array a destination *name* stands for, or ``None``.

    ``memo`` is a per-document ``{name: result}`` cache. Without it every link re-reads and
    re-parses the whole table, which is quadratic in a document that leans on names: measured on
    a 143-page manual with 591 links over 73 names, a save spent an extra **197 ms** doing the same
    73 parses 591 times. It is optional because a single lookup should not have to build one.

    Two places hold them, and a document uses one or the other. ``/Root/Dests`` is a plain
    dictionary keyed by **name objects** (PDF 1.1 — 6 of the corpus's 15 name-carrying documents,
    including the Javadoc pages and the Sony manual); ``/Root/Names/Dests`` is a **name tree**
    keyed by strings (PDF 1.2 onward — the other 9, including Apple's and Cisco's filings).

    Both are read from the object's own text rather than through ``xref_get_key``'s dotted path,
    which cannot reach a name tree at all and which **raises** ``FzErrorArgument: path too long``
    on a long name — measured on the corpus, where one Nature paper's names are long enough to
    throw on 22 of its links.
    """
    if memo is not None and name in memo:
        return memo[name]
    found = _find_named(doc, name)
    if memo is not None:
        memo[name] = found
    return found


def _find_named(doc, name: str) -> str | None:
    catalog = doc.pdf_catalog()
    dests = doc.xref_get_key(catalog, "Dests")
    if dests[0] != "null":
        text = dests[1]
        if _INDIRECT.fullmatch(text.strip()):
            text = doc.xref_object(int(text.split()[0]), compressed=True)
        found = _lookup(text, name)
        if found is not None:
            return _resolve_value(doc, found)
    tree = doc.xref_get_key(catalog, "Names/Dests")
    if tree[0] == "xref":
        return _name_tree_lookup(doc, int(tree[1].split()[0]), name)
    return None


def _name_tree_lookup(doc, root: int, name: str, depth: int = 0) -> str | None:
    """Walk a PDF name tree (``/Kids`` branches, ``/Names`` leaves) for ``name``.

    Deliberately **not** ``Document.resolve_names()``, which hands back the destination already
    parsed into a point — and so cannot tell ``/XYZ null 749`` from ``/XYZ 0 749``, which is the
    difference between *keep the reader's horizontal scroll* and *scroll to the left edge*. 452 of
    the corpus's bookmarks and 368 of its links are the ``null`` form, so that distinction is the
    common case, not the corner one.

    The walk ignores ``/Limits`` rather than using it to prune: a tree written with wrong or absent
    limits still resolves, at the cost of reading nodes a binary search would have skipped, and a
    corpus name tree is a handful of nodes. ``depth`` stops a malformed tree that points at itself.
    """
    if depth > 50:
        return None
    names = doc.xref_get_key(root, "Names")
    if names[0] == "array":
        found = _lookup(names[1], name)
        if found is not None:
            return _resolve_value(doc, found)
    kids = doc.xref_get_key(root, "Kids")
    if kids[0] == "array":
        for kid in re.findall(r"(\d+)\s+\d+\s+R", kids[1]):
            found = _name_tree_lookup(doc, int(kid), name, depth + 1)
            if found is not None:
                return found
    return None


def read_destination(
    doc, xref: int, page_of_xref: dict[int, int], names: dict | None = None
) -> Destination | None:
    """The destination of the outline item or link annotation at ``xref``, or ``None``.

    ``None`` means *this object has no internal destination we can carry* — a bookmark that is a
    bare heading (18 in the corpus), a link whose action is ``/URI``, ``/Launch`` or ``/GoToR``, a
    destination naming a page that is not in this document, or one we could not parse. Every such
    case falls back to the behaviour that predates this module, which is the target page alone.

    Three spellings all arrive here, and the corpus has all three:

    * ``/Dest`` holding the destination directly — 624 of the corpus's bookmarks;
    * ``/A`` holding a ``/GoTo`` action **inline** — 254 more;
    * ``/A`` holding an **indirect reference** to that action — 156 more, which is how Cisco's
      annual report writes every one of its bookmarks. ``xref_get_key``'s dotted path follows the
      reference, so the two ``/A`` cases need no separate handling here; a version that stopped
      following it would drop those 156 silently, and a test pins that it still does.

    In each case the destination itself is either an array, an indirect reference to one, or a
    **name** standing for one, which :func:`_named_destination` resolves.
    """
    value = doc.xref_get_key(xref, "Dest")
    if value[0] == "null":
        if doc.xref_get_key(xref, "A/S")[1] != "/GoTo":
            return None  # /URI, /Launch, /GoToR, /Named: not a jump inside this document
        value = doc.xref_get_key(xref, "A/D")
    kind, text = value
    if kind in ("string", "name"):
        name = text[1:] if kind == "name" else text
        if not name:
            return None  # `/Dest/` — an empty name, naming nothing (39 links in one corpus file)
        text = _named_destination(doc, name, names)
    elif kind == "xref":
        text = _resolve_value(doc, text)   # `/Dest 103 0 R` — the array sits in its own object
    elif kind != "array":
        return None
    return split_destination(text, page_of_xref) if text else None


def write_destination(doc, xref: int, page_xref: int, tail: str) -> None:
    """Point the outline item or link annotation at ``xref`` at ``page_xref``, keeping ``tail``.

    Written as ``/Dest``, and any ``/A`` on the object is cleared — a PDF reader prefers ``/A`` when
    both are present, so leaving one behind would mean the destination written here was the one
    nobody used.
    """
    doc.xref_set_key(xref, "A", "null")
    doc.xref_set_key(xref, "Dest", f"[{page_xref} 0 R {tail}]")


def content_point(page, dest: Destination) -> tuple[float | None, float | None]:
    """``(left, top)`` of ``dest`` in ``page``'s **content coordinates**, either part ``None``.

    Content coordinates are the frame the rest of the app works in: unrotated, relative to the crop
    box's top-left, y increasing downward — the same frame as PyMuPDF's word boxes, widget rects
    and ``get_pixmap``, and what ``PdfView.scene_rect_for_box`` and ``page_transform`` take.
    Rotation is deliberately **not** applied: the view holds the page's rotation (including a
    per-page override the file does not know about) and applies it to everything else it draws, so
    a point that arrived here pre-rotated would be rotated twice.

    A destination's own coordinates are PDF user space, whose origin is the **media box's**
    bottom-left. PyMuPDF reports ``page.cropbox`` with its y already flipped about the media box's
    top, so the conversion is a subtraction on each axis with no page-size arithmetic.
    """
    # Both parts are returned, and **nothing acts on the left** (owner's rule, 2026-09-20: a
    # bookmark or a link moves the page up and down only). The viewer reads it and discards it,
    # and `set_outline` never writes one. It is kept here because this is the honest reading of
    # what the file says, and because the test that pins this conversion against PyMuPDF's own
    # reader is a stronger check for covering both axes.
    left, top = dest.left(), dest.top()
    box = page.cropbox
    x = None if left is None else left - box.x0
    y = None if top is None else (page.mediabox.y1 - top) - box.y0
    return x, y


#: Where :func:`~model.toc_remap.bake_dest` parks a carried destination tail inside the dict it
#: hands to ``Document.set_toc``. Riding in the dict rather than in a second, parallel list is what
#: keeps the two in step: one ``remapped_toc()`` call produces the entries *and* their tails, in one
#: order, so there is no way for a later edit to renumber one and not the other. ``set_toc`` ignores
#: a key it does not know — measured on 1.27.2.3, and pinned by ``tests/test_destinations.py``, so a
#: PyMuPDF that started validating its input fails there rather than on a user's document.
RAW_TAIL_KEY = "klarpdf_tail"


def outline_item_xrefs(doc) -> list[int]:
    """The document's outline item objects, **depth-first** — the order ``get_toc`` reports them
    and ``set_toc`` writes them, which is what lets a row and an item be paired by position."""
    outlines = doc.xref_get_key(doc.pdf_catalog(), "Outlines")
    if outlines[0] != "xref":
        return []
    found: list[int] = []

    def descend(xref: int | None, depth: int) -> None:
        while xref is not None and len(found) < 100_000:
            found.append(xref)
            first = doc.xref_get_key(xref, "First")
            if first[0] == "xref" and depth < 60:
                descend(int(first[1].split()[0]), depth + 1)
            nxt = doc.xref_get_key(xref, "Next")
            xref = int(nxt[1].split()[0]) if nxt[0] == "xref" else None

    first = doc.xref_get_key(int(outlines[1].split()[0]), "First")
    if first[0] == "xref":
        descend(int(first[1].split()[0]), 0)
    return found


def read_outline_destinations(doc, toc_length: int) -> list[Destination | None]:
    """One :class:`Destination` (or ``None``) per row of ``doc.get_toc(simple=False)``.

    Paired with the outline by **position**, so it is returned only when the depth-first walk finds
    exactly ``toc_length`` items. When it does not — a malformed tree, or a PyMuPDF that ever
    filtered rows out — the answer is an empty list, and every caller then behaves as it did before
    this module existed rather than pairing a bookmark with another bookmark's destination.
    Measured across the 123-document corpus the two agree on every file.
    """
    items = outline_item_xrefs(doc)
    if len(items) != toc_length:
        return []
    page_of_xref, names = page_index_map(doc), {}
    return [read_destination(doc, item, page_of_xref, names) for item in items]


def carried_tail(entry: list) -> str | None:
    """The destination a :meth:`~model.virtual_document.VirtualDocument.remapped_toc` row carries.

    ``None`` when the row names no spot on its page — a bookmark the document wrote as "open this
    page", or one an agent authored without a height. Read by the save, by the app's Outline tab
    and by the bridge's ``get_outline``, so it is one function rather than the same four lines
    written three times.
    """
    dest = entry[3] if len(entry) > 3 else None
    return dest.get(RAW_TAIL_KEY) if isinstance(dest, dict) else None


def pdf_top(page, top: float) -> float:
    """``top`` — how far **down** the visible page, in its own points — as a PDF ``y``.

    The exact inverse of :func:`content_point`'s vertical half, and the one conversion the *write*
    side needs: an agent measures a heading with ``get_heading_candidates`` or ``search``, both of
    which report boxes in that frame, and hands the number straight to ``set_outline``.

    A PDF measures upward from the bottom of the **paper**, while every box this project hands out
    is measured downward from the top of the **visible** area — which on a cropped page is not the
    same edge. Doing it in one place, next to the reader that undoes it, is what keeps the two from
    drifting; ``tests/test_destinations.py`` round-trips them against each other.
    """
    return page.mediabox.y1 - (top + page.cropbox.y0)


def xyz_tail(page, top: float) -> str:
    """A destination that opens ``page`` at ``top``, as PDF syntax.

    The left edge is written as ``null``, always — *keep whatever sideways position the reader
    has*. That is the owner's rule of 2026-09-20 (a bookmark moves the page up and down only), the
    same rule the viewer follows, and it is also the commonest thing real publishers write: 452 of
    the corpus's 886 positioned bookmarks leave the left edge blank.

    The magnification is written as ``0``, meaning *keep the reader's zoom* — the point of naming a
    spot is to arrive at it without being resized on the way.
    """
    return f"/XYZ null {pdf_top(page, top):.6g} 0"


def apply_outline_destinations(doc, toc: list) -> int:
    """Write each row's carried destination over the one ``set_toc`` just wrote. Returns how many.

    Called straight after ``Document.set_toc(toc)`` with the **same list**, so row *i* and outline
    item *i* are the same bookmark — ``set_toc`` writes items in the list's depth-first order, and
    :func:`outline_item_xrefs` reads them back in it.

    Nothing is written when the two disagree on how many items exist, which is the same refusal
    :func:`read_outline_destinations` makes and for the same reason: a bookmark given another
    bookmark's destination navigates confidently to the wrong place, which is worse than the page
    top it would otherwise get.
    """
    tails = [carried_tail(entry) for entry in toc]
    if not any(tails):
        return 0
    items = outline_item_xrefs(doc)
    if len(items) != len(toc):
        return 0
    written = 0
    for item, entry, tail in zip(items, toc, tails):
        page = entry[2] - 1
        if tail is None or not 0 <= page < doc.page_count:
            continue
        write_destination(doc, item, doc.page_xref(page), tail)
        written += 1
    return written
