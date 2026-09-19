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
                  "first_cell_prefix": ["Total assets"], "unread": ["side_by_side"],
                  "titles": ["CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)"]}]}

``null`` pages means every page, up to 30. ``unread`` lists reason codes (``tables._REASONS`` keys)
that must appear. ``titles`` holds one entry per table on the page, in the order they are returned:
the exact title, ``null`` for a table that must come back untitled, or a list of the answers that
are all right (``null`` in the list accepts no title as well). A title is compared as ``get_tables``
returns it when asked for all of the plan's pages of that document in one call, so a caption
stranded at the foot of the page before counts when that page is in the plan. Each title in the key
is one verified on a render.

**A key the checker does not know is an error**, never a silence: an anchor holding a misspelt key
or a wrong title used to pass, because this checker only ever read the keys it knew (``PLAN.md``
§M145). ``note``, on the plan or on an anchor, is the one key nothing compares: a string recording
why an expectation is what it is. The exit status is non-zero when the plan is malformed, an expectation fails, or the word
check disagrees.
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

_ANCHOR_KEYS = {
    "doc": str,
    "page": int,
    "tables": int,
    "shapes": list,
    "rows": list,
    "first_cell_prefix": list,
    "unread": list,
    "titles": list,
    "note": str,
}
_PLAN_KEYS = {"pages": list, "anchors": list, "note": str}


def _displayed(page: fitz.Page, words: list) -> list[tuple]:
    out = []
    for word in words:
        rect = fitz.Rect(word[:4]) * page.rotation_matrix
        rect.normalize()
        out.append((rect.x0, rect.y0, rect.x1, rect.y1, word[4]))
    return out


def _is_leader_text(text: str) -> bool:
    """A run of dots joining a label to its figure: typesetting, which no cell should hold.

    Written out here rather than imported, like the one-point floor below: this check is only worth
    anything while it reaches its verdict without the module's help.
    """
    return len(text) > 1 and set(text) <= set(".·…")


def word_check(page: fitz.Page, table: dict) -> tuple[dict, dict]:
    """``(printed but not returned, returned but not inside the box)`` for one table.

    Text under a point and dot leaders are not read into cells, so neither counts as printed here.
    """
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
        if x0 <= (t[0] + t[2]) / 2 <= x1
        and y0 <= (t[1] + t[3]) / 2 <= y1
        and (o[3] - o[1]) >= 1.0
        and not _is_leader_text(t[4])
    )
    returned = collections.Counter(token for row in table["rows"] for cell in row for token in cell.split())
    covered = collections.Counter(
        o[4]
        for o in ordinary
        if x0 - 0.05 <= o[0] and o[2] <= x1 + 0.05 and y0 - 0.05 <= o[1] and o[3] <= y1 + 0.05
    )
    return dict(printed - returned), dict(returned - covered)


def plan_problems(plan: dict) -> list[str]:
    """What is wrong with the plan itself: a key nothing reads, or a value of the wrong kind."""
    problems = []
    for key, value in plan.items():
        if key not in _PLAN_KEYS:
            problems.append(f"plan: unknown key {key!r}, which nothing would read")
        elif not isinstance(value, _PLAN_KEYS[key]):
            problems.append(f"plan: {key!r} must be a {_PLAN_KEYS[key].__name__}")
    if "pages" not in plan:
        problems.append("plan: no 'pages'")
    anchors = plan.get("anchors", [])
    for number, anchor in enumerate(anchors if isinstance(anchors, list) else [], 1):
        if not isinstance(anchor, dict):
            problems.append(f"anchor {number}: must be an object, got {anchor!r}")
            continue
        where = f"anchor {number} ({anchor.get('doc')} p{anchor.get('page')})"
        for key in ("doc", "page"):
            if key not in anchor:
                problems.append(f"{where}: no {key!r}")
        for key, value in anchor.items():
            kind = _ANCHOR_KEYS.get(key)
            if kind is None:
                problems.append(f"{where}: unknown key {key!r}, which nothing would check")
            elif not isinstance(value, kind) or isinstance(value, bool):
                problems.append(f"{where}: {key!r} must be a {kind.__name__}, got {value!r}")
        for entry in anchor.get("titles", []) if isinstance(anchor.get("titles"), list) else []:
            answers = entry if isinstance(entry, list) else [entry]
            if not answers or not all(a is None or isinstance(a, str) for a in answers):
                problems.append(f"{where}: a title is a string, null, or a list of those; got {entry!r}")
    return problems


def title_outcomes(anchor: dict, titles: list[str | None]) -> list[tuple[str, str]]:
    """``(outcome, message)`` for each pinned title — ``right``, ``missing`` or ``wrong``."""
    expected = anchor["titles"]
    if len(expected) != len(titles):
        return [("wrong", f"titles pinned for {len(expected)} tables, got {len(titles)}: {titles}")]
    outcomes = []
    for index, (want, have) in enumerate(zip(expected, titles), 1):
        if have in (want if isinstance(want, list) else [want]):
            outcomes.append(("right", ""))
        else:
            kind = "missing" if have is None else "wrong"
            outcomes.append((kind, f"title of table {index} is {kind}: {have!r}, expected {want!r}"))
    return outcomes


def anchor_failures(anchor: dict, result: tables.PageRead, titles: list[str | None]) -> list[str]:
    """What the page got wrong against ``anchor``. ``titles`` are the page's titles as the tool
    returns them, which can differ from ``read_page``'s by a caption from the page before."""
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
    if "titles" in anchor:
        fails.extend(message for outcome, message in title_outcomes(anchor, titles) if outcome != "right")
    return fails


def run(plan_path: str, corpus: str, snapshot: str | None, snapshots: str) -> int:
    plan = json.load(open(plan_path, encoding="utf-8"))
    malformed = plan_problems(plan)
    if malformed:
        print(f"the plan is malformed; nothing was read ({len(malformed)} problems)")
        for problem in malformed:
            print("  " + problem)
        return 1
    anchors = plan.get("anchors", [])
    records, problems = [], []
    counts: collections.Counter = collections.Counter()
    title_counts: collections.Counter = collections.Counter()
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
        before: tuple[int, tables.PageRead] | None = None
        for number in sorted(set(pages)) if pages else range(1, min(doc.page_count, PAGE_CAP) + 1):
            page = doc[number - 1]
            result = tables.read_page(page)
            previous = before[1] if before is not None and before[0] == number - 1 else None
            titles = [title for title, _ in tables.titles_after(result, previous)]
            before = (number, result)
            parts = []
            record = {"doc": name, "page": number, "tables": [], "unread": [
                {k: u[k] for k in ("bbox", "_code", "_detail")} for u in result.unread
            ]}
            for table, title in zip(result.tables, titles):
                counts[table["reader"]] += 1
                missing, extra = word_check(page, table)
                shape = [len(table["rows"]), max(len(r) for r in table["rows"])]
                record["tables"].append({
                    "reader": table["reader"], "shape": shape, "title": title,
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
                    failures = anchor_failures(anchor, result, titles)
                    problems.extend(f"expectation {name} p{number}: {f}" for f in failures)
                    if "titles" in anchor:
                        title_counts.update(outcome for outcome, _ in title_outcomes(anchor, titles))
            records.append(record)
            print(f"{name[:40]:40} p{number:<4} " + " ".join(parts), flush=True)
    for anchor in anchors:
        if (anchor["doc"], anchor["page"]) not in evaluated:
            problems.append(f"expectation never evaluated (typo, or page not in the plan): {anchor['doc']} p{anchor['page']}")
    print(f"\n{dict(counts)} in {time.perf_counter() - started:.0f}s")
    if title_counts:
        print(f"titles pinned: {title_counts['right']} right, {title_counts['missing']} missing, "
              f"{title_counts['wrong']} wrong")
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
