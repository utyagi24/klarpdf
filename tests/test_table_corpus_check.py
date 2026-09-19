"""M145 — the table corpus checker, held to the standard it holds ``get_tables`` to.

``tools/table_corpus_check.py`` compares ``get_tables`` with fixed expectations on real documents.
Those are not in git, so nothing in CI runs it. What CI can check is the checker: that a wrong or a
missing title fails a run, that a key it does not know fails it too — both used to pass, because it
only ever read the keys it knew — and that the title it compares is the one the tool returns, a
caption from the page before included. Each document here is built with a known answer.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pymupdf as fitz
import pytest

from tests.test_mcp_tables import ASSETS, FONT, PITCH, _save, _statement

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def checker():
    path = ROOT / "tools" / "table_corpus_check.py"
    spec = importlib.util.spec_from_file_location("_klarpdf_table_corpus_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def corpus(tmp_path) -> pathlib.Path:
    """Page 1: a statement under its title. Page 2: a statement with a caption stranded below it.
    Page 3: the table that caption titles."""
    doc = fitz.open()
    titled = doc.new_page()
    titled.insert_text((230, 90), "CONDENSED BALANCE SHEETS", fontsize=FONT)
    _statement(titled, 100, ASSETS)
    stranded = doc.new_page()
    _statement(stranded, 40, ASSETS)
    stranded.insert_text((60, 40 + len(ASSETS) * PITCH + 30), "Headphone cable connected", fontsize=FONT)
    _statement(doc.new_page(), 40, ASSETS)
    _save(doc, tmp_path, "doc.pdf")
    return tmp_path


def _run(checker, corpus: pathlib.Path, anchors: list[dict], pages=(1, 2, 3)) -> int:
    plan = corpus / "plan.json"
    plan.write_text(json.dumps({"pages": [["doc.pdf", list(pages)]], "anchors": anchors}), encoding="utf-8")
    return checker.run(str(plan), str(corpus), None, str(corpus / "snapshots"))


def _anchor(page: int, titles: list) -> dict:
    return {"doc": "doc.pdf", "page": page, "titles": titles}


def test_the_titles_the_tool_returns_pass(checker, corpus, capsys):
    anchors = [_anchor(1, ["CONDENSED BALANCE SHEETS"]), _anchor(2, [None]), _anchor(3, ["Headphone cable connected"])]
    assert _run(checker, corpus, anchors) == 0
    assert "titles pinned: 3 right, 0 missing, 0 wrong" in capsys.readouterr().out


def test_a_wrong_title_fails_the_run(checker, corpus, capsys):
    assert _run(checker, corpus, [_anchor(1, ["BALANCE SHEETS"])]) == 1
    assert "title of table 1 is wrong: 'CONDENSED BALANCE SHEETS'" in capsys.readouterr().out


def test_a_title_pinned_where_none_comes_back_is_missing(checker, corpus, capsys):
    assert _run(checker, corpus, [_anchor(2, ["Assets"])]) == 1
    assert "title of table 1 is missing: None" in capsys.readouterr().out


def test_a_list_accepts_each_of_its_answers_and_nothing_else(checker, corpus):
    either = [_anchor(1, [["BALANCE SHEETS", "CONDENSED BALANCE SHEETS"]]), _anchor(2, [["Assets", None]])]
    assert _run(checker, corpus, either) == 0
    assert _run(checker, corpus, [_anchor(1, [["BALANCE SHEETS", None]])]) == 1


def test_a_key_the_checker_does_not_know_fails_before_anything_is_read(checker, corpus, capsys):
    """Measured before this checker learnt titles: an anchor holding ``"title": "DEFINITELY NOT THE
    TITLE"``, or ``"row"`` for ``"rows"``, reported 0 problems."""
    for anchor in ({"doc": "doc.pdf", "page": 1, "title": "DEFINITELY NOT THE TITLE"},
                   {"doc": "doc.pdf", "page": 1, "row": [["Cash and cash equivalents"]]},
                   {"doc": "doc.pdf", "page": 1, "titles": "CONDENSED BALANCE SHEETS"}):
        assert _run(checker, corpus, [anchor]) == 1, anchor
        out = capsys.readouterr().out
        assert "the plan is malformed; nothing was read" in out, anchor
        assert "(doc.pdf p1)" in out and ("unknown key" in out or "must be a list" in out), anchor


def test_a_note_is_the_one_key_nothing_compares(checker, corpus):
    """A plan records why an expectation is what it is; JSON has no comments."""
    plan = corpus / "plan.json"
    anchor = {**_anchor(1, ["CONDENSED BALANCE SHEETS"]), "note": "centred over the table"}
    plan.write_text(json.dumps({"note": "synthetic", "pages": [["doc.pdf", [1]]], "anchors": [anchor]}))
    assert checker.run(str(plan), str(corpus), None, str(corpus / "snapshots")) == 0
    assert checker.plan_problems({"pages": [], "note": ["not a string"]}) == ["plan: 'note' must be a str"]


def test_a_caption_from_the_page_before_counts_only_when_that_page_is_in_the_plan(checker, corpus):
    """The checker compares what ``get_tables`` returns for the plan's pages in one call, so the page
    after a gap gets no caption from the page before it (#366)."""
    caption, untitled = [_anchor(3, ["Headphone cable connected"])], [_anchor(3, [None])]
    assert _run(checker, corpus, caption, pages=(2, 3)) == 0
    assert _run(checker, corpus, untitled, pages=(2, 3)) == 1
    assert _run(checker, corpus, untitled, pages=(1, 3)) == 0
    assert _run(checker, corpus, caption, pages=(1, 3)) == 1
