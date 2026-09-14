"""Run ``get_tables`` over a corpus of real PDFs and compare every run with fixed expectations.

    python tools/table_corpus_check.py PLAN.json --corpus DIR [--snapshot NAME] [--snapshots DIR]
    python tools/table_corpus_check.py --diff OLD.json NEW.json

**Why this exists** (``PLAN.md`` §M141). Every regression the first M141 attempt shipped was caught by
comparing its output with the page, and none by a test; the rewrite was then driven by this runner.
Two lessons are built into it:

* **Each table is checked against its page by code that shares nothing with the module**: the words
  whose letters are centred inside the reported box must be exactly the words in the cells, and every
  word in the cells must lie inside the box by the ordinary boxes ``search`` returns.
* **Each run is compared with fixed expectations** verified by eye, arithmetic or render — not only
  with the previous run. A label column lost in one round survived six rounds of run-to-run diffs.

**The corpus is not in git, and neither are expectations about private documents.** A plan file names
the documents, the pages and what must hold; ``tools/table_corpus_public.json`` covers public filings
and reports only. Keep the plan for private statements and forms outside the repository.

Plan format::

    {"pages": [["Some-10Q.pdf", [4, 5]], ["small-form.pdf", null]],
     "anchors": [{"doc": "Some-10Q.pdf", "page": 4, "tables": 1, "shapes": [[28, 9]],
                  "rows": [["Net sales", "$", "109,417", "$", "94,036"]],
                  "first_cell_prefix": ["Total assets"], "unread": ["side_by_side"]}]}

``null`` pages means every page, up to 30. ``unread`` lists reason codes (``tables._REASONS`` keys)
that must appear. The exit status is non-zero when an expectation fails or the word check disagrees.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz  # noqa: E402

from klarpdf.mcp_bridge import tables  # noqa: E402

PAGE_CAP = 30


def _displayed(page: fitz.Page, words: list) -> list[tuple]:
    out = []
    for word in words:
        rect = fitz.Rect(word[:4]) * page.rotation_matrix
        rect.normalize()
        out.append((rect.x0, rect.y0, rect.x1, rect.y1, word[4]))
    return out


def word_check(page: fitz.Page, table: dict) -> tuple[dict, dict]:
    """``(printed but not returned, returned but not inside the box)`` for one table."""
    x0, y0, x1, y1 = table["bbox"]
    old = bool(fitz.TOOLS.set_small_glyph_heights())
    fitz.TOOLS.set_small_glyph_heights(True)
    try:
        tight = _displayed(page, page.get_text("words"))
    finally:
        fitz.TOOLS.set_small_glyph_heights(old)
    ordinary = _displayed(page, page.get_text("words"))
    printed = collections.Counter(
        t[4]
        for t, o in zip(tight, ordinary)
        if x0 <= (t[0] + t[2]) / 2 <= x1 and y0 <= (t[1] + t[3]) / 2 <= y1 and (o[3] - o[1]) >= 1.0
    )
    returned = collections.Counter(token for row in table["rows"] for cell in row for token in cell.split())
    covered = collections.Counter(
        o[4]
        for o in ordinary
        if x0 - 0.05 <= o[0] and o[2] <= x1 + 0.05 and y0 - 0.05 <= o[1] and o[3] <= y1 + 0.05
    )
    return dict(printed - returned), dict(returned - covered)


def anchor_failures(anchor: dict, result: tables.PageRead) -> list[str]:
    fails = []
    found = result.tables
    if "tables" in anchor and len(found) != anchor["tables"]:
        fails.append(f"expected {anchor['tables']} tables, got {len(found)}")
    if "shapes" in anchor:
        shapes = [[len(t["rows"]), max(len(r) for r in t["rows"])] for t in found]
        if shapes != anchor["shapes"]:
            fails.append(f"shapes {shapes} != {anchor['shapes']}")
    for row in anchor.get("rows", []):
        if not any(row == r for t in found for r in t["rows"]):
            fails.append(f"missing row {row}")
    for prefix in anchor.get("first_cell_prefix", []):
        if not any(r and r[0].startswith(prefix) for t in found for r in t["rows"]):
            fails.append(f"no row starting {prefix!r}")
    for code in anchor.get("unread", []):
        if not any(u["_code"] == code for u in result.unread):
            fails.append(f"no unread region with reason {code}")
    return fails


def run(plan_path: str, corpus: str, snapshot: str | None, snapshots: str) -> int:
    plan = json.load(open(plan_path, encoding="utf-8"))
    anchors = plan.get("anchors", [])
    records, problems = [], []
    counts: collections.Counter = collections.Counter()
    evaluated: set[tuple[str, int]] = set()
    started = time.perf_counter()
    for name, pages in plan["pages"]:
        path = os.path.join(corpus, name)
        if not os.path.exists(path):
            print(f"-- not in the corpus: {name}")
            continue
        doc = fitz.open(path)
        if doc.needs_pass:
            print(f"-- needs a password, skipped: {name}")
            continue
        for number in pages or range(1, min(doc.page_count, PAGE_CAP) + 1):
            page = doc[number - 1]
            result = tables.read_page(page)
            parts = []
            record = {"doc": name, "page": number, "tables": [], "unread": [
                {k: u[k] for k in ("bbox", "_code", "_detail")} for u in result.unread
            ]}
            for table in result.tables:
                counts[table["reader"]] += 1
                missing, extra = word_check(page, table)
                shape = [len(table["rows"]), max(len(r) for r in table["rows"])]
                record["tables"].append({
                    "reader": table["reader"], "shape": shape, "title": table["title"],
                    "bbox": [round(v, 1) for v in table["bbox"]], "rows": table["rows"],
                })
                flag = ""
                if missing or extra:
                    flag = f" WORDS(missing {sum(missing.values())}, outside {sum(extra.values())})"
                    problems.append(f"word check {name} p{number}: missing {missing} outside {extra}")
                parts.append(f"{table['reader']} {shape[0]}x{shape[1]}{flag}")
            for unread in result.unread:
                counts["declined " + unread["_code"]] += 1
                parts.append(f"[{unread['_code']}]")
            for anchor in anchors:
                if anchor["doc"] == name and anchor["page"] == number:
                    evaluated.add((name, number))
                    problems.extend(f"expectation {name} p{number}: {f}" for f in anchor_failures(anchor, result))
            records.append(record)
            print(f"{name[:40]:40} p{number:<4} " + " ".join(parts), flush=True)
    for anchor in anchors:
        if (anchor["doc"], anchor["page"]) not in evaluated:
            problems.append(f"expectation never evaluated (typo, or page not in the plan): {anchor['doc']} p{anchor['page']}")
    print(f"\n{dict(counts)} in {time.perf_counter() - started:.0f}s")
    print(f"{len(anchors)} expectations, {len(problems)} problems")
    for problem in problems:
        print("  " + problem)
    if snapshot:
        os.makedirs(snapshots, exist_ok=True)
        out = os.path.join(snapshots, snapshot + ".json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=1)
        print(f"snapshot -> {out}")
    return 1 if problems else 0


def diff(old_path: str, new_path: str) -> int:
    old = {(r["doc"], r["page"]): r for r in json.load(open(old_path, encoding="utf-8"))}
    new = {(r["doc"], r["page"]): r for r in json.load(open(new_path, encoding="utf-8"))}
    changed = 0
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a is None or b is None:
            print(f"only in one snapshot: {key[0]} p{key[1]}")
            changed += 1
            continue
        tables_a = [(t["shape"], t["rows"], t["title"]) for t in a["tables"]]
        tables_b = [(t["shape"], t["rows"], t["title"]) for t in b["tables"]]
        codes_a = [u["_code"] for u in a["unread"]]
        codes_b = [u["_code"] for u in b["unread"]]
        if tables_a == tables_b and codes_a == codes_b:
            continue
        changed += 1
        print(f"{key[0]} p{key[1]}: tables {[t[0] for t in tables_a]} -> {[t[0] for t in tables_b]}; "
              f"declines {codes_a} -> {codes_b}")
        for (sa, ra, ta), (sb, rb, tb) in zip(tables_a, tables_b):
            if ta != tb:
                print(f"    title {ta!r} -> {tb!r}")
            for index, (x, y) in enumerate(zip(ra, rb)):
                if x != y:
                    print(f"    row {index}: {x}\n          -> {y}")
                    break
    print(f"{changed} pages changed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plan", nargs="?", help="plan file: pages to read and expectations")
    parser.add_argument("--corpus", help="directory holding the PDFs the plan names")
    parser.add_argument("--snapshot", help="save this run's tables under this name")
    parser.add_argument("--snapshots", default="table_snapshots", help="where snapshots are saved")
    parser.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="compare two saved snapshots")
    args = parser.parse_args(argv)
    if args.diff:
        return diff(*args.diff)
    if not args.plan or not args.corpus:
        parser.error("a plan file and --corpus are required, unless --diff is given")
    return run(args.plan, args.corpus, args.snapshot, args.snapshots)


if __name__ == "__main__":
    sys.exit(main())
