"""Run ``get_heading_candidates`` over documents that carry their own bookmarks, and check the result.

    python tools/heading_corpus_check.py PLAN.json --corpus DIR

**Why this exists** (``PLAN.md`` §M140). A heading this tool misses is lost to the agent for good, so
the question worth asking after any change is *which headings does it still find*. Documents that
ship bookmarks answer it without anyone labelling anything: their publishers already wrote the
titles and the page each one is on. A title printed on its page is a heading the tool must return
among that page's candidates.

**The "printed" test shares nothing with the module.** It reads ``page.get_text("text")`` lines and
asks whether the title, normalised, is one to three consecutive lines, or starts or ends such a
run. Bookmarks that are not printed — form IDs, "Slide 3" — are counted and set aside.

**Each run is compared with fixed expectations**, not only with the previous run: how many titles
are printed (which pins the answer key itself), how many the tool finds, which misses are known, and
how many candidates and styles the whole reply holds. The last two catch a change that adds noise
without losing a heading — reading a document's hidden text copy does exactly that, and recall alone
did not notice it. A section anchor pins the candidates of one style on a stretch of pages whose
headings were read by eye.

**The corpus is not in git.** ``tools/heading_corpus_public.json`` names public documents only.

Plan format::

    {"documents": [{"doc": "Some-10Q.pdf", "printed": 60, "found": 60, "missed": [],
                    "candidates": 382, "styles": 8}],
     "sections": [{"doc": "prospectus.pdf", "pages": [332, 340],
                   "style": {"font": "TimesNewRomanPS-BoldItalicMT", "size": 10.0},
                   "count": 42, "includes": ["National Highways Act, 1956"]}]}

The exit status is non-zero when an expectation fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz  # noqa: E402

from klarpdf.mcp_bridge import headings  # noqa: E402

_EVERYTHING = {"max_candidates": 10**9, "max_chars": 10**12}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    return re.sub(r"[\W_]+", " ", text).strip()


def _printed(title: str, lines: list[str]) -> bool:
    """Is ``title`` one to three consecutive lines joined, or the start or end of such a run?

    Three, because a numbered title can take a line for its number and two for its words
    (``1.5`` / ``Risks Related to Our Corporate Structure, Ownership of our Class A`` / ``Common
    Stock and the Offering``)."""
    if not title:
        return False
    for width in (1, 2, 3):
        for start in range(len(lines) - width + 1):
            joined = " ".join(lines[start:start + width])
            if joined == title or joined.startswith(title + " ") or joined.endswith(" " + title):
                return True
    return False


def check_document(path: str, expected: dict) -> list[str]:
    doc = fitz.open(path)
    started = time.perf_counter()
    reply = headings.heading_candidates(path, **_EVERYTHING)
    elapsed = time.perf_counter() - started
    by_page: dict[int, list[str]] = {}
    for candidate in reply["candidates"]:
        by_page.setdefault(candidate["page"], []).append(_norm(candidate["text"]))
    printed = found = 0
    missed: list[str] = []
    for _level, title, page in doc.get_toc():
        if not 1 <= page <= doc.page_count:
            continue
        key = _norm(title)
        lines = [_norm(line) for line in doc[page - 1].get_text("text").splitlines() if line.strip()]
        if not _printed(key, lines):
            continue
        printed += 1
        if _printed(key, by_page.get(page, [])):
            found += 1
        else:
            missed.append(" ".join(title.split()))
    size = len(json.dumps(reply["candidates"]))
    print(f"{os.path.basename(path)[:44]:<44} {doc.page_count:4d}p {elapsed:5.1f}s  printed {printed:4d}  "
          f"found {found:4d}  candidates {reply['total_candidates']:6d} ({size // 1000} KB)")
    problems = []
    if printed != expected["printed"]:
        problems.append(f"printed {printed}, expected {expected['printed']} — the answer key changed")
    if found < expected["found"]:
        problems.append(f"found {found}, expected {expected['found']}")
    unexpected = [title for title in missed if title not in expected.get("missed", [])]
    if unexpected:
        problems.append(f"new misses: {unexpected}")
    if found > expected["found"]:
        print(f"    note: found {found}, more than the {expected['found']} expected — update the plan")
    for field, actual in (("candidates", reply["total_candidates"]), ("styles", len(reply["styles"]))):
        if field in expected and actual != expected[field]:
            problems.append(f"{actual} {field}, expected {expected[field]} — check what changed, then update the plan")
    return problems


def check_section(path: str, anchor: dict) -> list[str]:
    first, last = anchor["pages"]
    reply = headings.heading_candidates(path, list(range(first, last + 1)), **_EVERYTHING)
    wanted = anchor["style"]
    rows = [s for s in reply["styles"] if s["font"] == wanted["font"] and s["size"] == wanted["size"]]
    texts = []
    if rows:
        texts = [c["text"] for c in reply["candidates"] if c["style"] == rows[0]["style"]]
    print(f"{os.path.basename(path)[:44]:<44} pp{first}-{last} {wanted['font']} {wanted['size']}: {len(texts)}")
    problems = []
    if len(texts) != anchor["count"]:
        problems.append(f"pp{first}-{last}: {len(texts)} candidates in that style, expected {anchor['count']}")
    for text in anchor.get("includes", []):
        if text not in texts:
            problems.append(f"pp{first}-{last}: {text!r} is not among them")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plan")
    parser.add_argument("--corpus", required=True)
    args = parser.parse_args(argv)
    with open(args.plan, encoding="utf-8") as handle:
        plan = json.load(handle)
    failures: list[str] = []
    for entry in plan.get("documents", []):
        path = os.path.join(args.corpus, entry["doc"])
        if not os.path.exists(path):
            print(f"{entry['doc']}: not in the corpus — skipped")
            continue
        failures += [f"{entry['doc']}: {p}" for p in check_document(path, entry)]
    for anchor in plan.get("sections", []):
        path = os.path.join(args.corpus, anchor["doc"])
        if not os.path.exists(path):
            print(f"{anchor['doc']}: not in the corpus — skipped")
            continue
        failures += [f"{anchor['doc']}: {p}" for p in check_section(path, anchor)]
    print()
    if failures:
        print("FAILED")
        for failure in failures:
            print("  " + failure)
        return 1
    print("all expectations hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
