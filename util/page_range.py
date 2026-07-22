"""Page-range parsing — "1-3, 7, 12-" → page indices (PLAN.md §R4, M62).

Small and shared: the stamp dialog's "apply to pages" (M62) and the watermark dialog's page range
both need it, and M64's search-&-redact scope will too. Kept GUI-free so it is headless-testable.

The syntax is the one every print dialog uses, so nobody has to learn it: comma-separated terms,
each a single page or an ``a-b`` span, 1-based and inclusive. Open-ended spans (``5-`` = "5 to the
end", ``-3`` = "up to 3") are accepted because "watermark from here on" is a real request.

Deliberately **forgiving on order and overlap** (``3-1`` is the same span as ``1-3``; duplicates
collapse) and **strict on nonsense** (a non-numeric term raises). Out-of-range numbers are clamped
rather than rejected — a range typed against a 10-page document that then loses a page should keep
working, not start erroring.
"""

from __future__ import annotations


class PageRangeError(ValueError):
    """A page-range string that could not be parsed. The message is user-facing."""


def parse_page_range(text: str, page_count: int) -> list[int]:
    """``text`` → sorted 0-based page indices within ``page_count``.

    An empty / whitespace-only string means **every page** — the natural reading of an untouched
    "Pages:" box, and what a watermark dialog should default to.
    """
    if page_count <= 0:
        return []
    text = (text or "").strip()
    if not text:
        return list(range(page_count))

    pages: set[int] = set()
    for term in text.split(","):
        term = term.strip()
        if not term:
            continue
        if "-" in term[1:] or term.startswith("-"):
            first, _, last = term.partition("-") if not term.startswith("-") else ("", "", term[1:])
            start = _number(first, default=1, term=term)
            end = _number(last, default=page_count, term=term)
        else:
            start = end = _number(term, default=None, term=term)
        if start > end:
            start, end = end, start
        start = max(1, min(start, page_count))
        end = max(1, min(end, page_count))
        pages.update(range(start - 1, end))
    if not pages:
        raise PageRangeError(f"No pages in {text!r}.")
    return sorted(pages)


def _number(part: str, default, term: str) -> int:
    part = part.strip()
    if not part:
        if default is None:
            raise PageRangeError(f"{term!r} is not a page number.")
        return default
    try:
        value = int(part)
    except ValueError:
        raise PageRangeError(f"{term!r} is not a page number.") from None
    if value < 1:
        raise PageRangeError(f"Page numbers start at 1 ({term!r}).")
    return value


def format_page_range(indices) -> str:
    """0-based indices → the shortest equivalent 1-based string ("0,1,2,5" → ``"1-3, 5"``).

    The inverse of :func:`parse_page_range` for the cases it can express, so a dialog can show back
    what it holds rather than a bare list.
    """
    ordered = sorted(set(indices))
    if not ordered:
        return ""
    spans: list[list[int]] = [[ordered[0], ordered[0]]]
    for index in ordered[1:]:
        if index == spans[-1][1] + 1:
            spans[-1][1] = index
        else:
            spans.append([index, index])
    return ", ".join(
        str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in spans
    )
