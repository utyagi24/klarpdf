"""The bridge and a box narrower than its text (M166, #430). The app's side is
``tests/test_textbox_narrow.py``.

The bridge draws no text boxes, but it re-saves ours: a tool that rewrites the page draws them again
from the model, so ``rotate`` wrapped a narrow box and lost its last word. ``annotate`` appends, so
it leaves the box as it found it.
"""

from __future__ import annotations

import pymupdf as fitz

from klarpdf.mcp_bridge import transforms as T
from klarpdf.model.page_edits import KLARPDF_AUTHOR
from tests.conftest import write_cut_off_textbox, write_narrow_textbox


def _box(page):
    (annot,) = [a for a in page.annots() if a.info.get("title") == KLARPDF_AUTHOR]
    return annot


def test_a_bridge_save_keeps_a_narrow_box_on_one_line(tmp_path):
    src = write_narrow_textbox(str(tmp_path / "narrow.pdf"))
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        page = doc[0]
        assert _box(page)._getAP().count(b"Tj") == 1


def test_a_bridge_save_restores_a_word_a_save_cut_off(tmp_path):
    src = write_cut_off_textbox(str(tmp_path / "cut.pdf"))
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        page = doc[0]
        assert " ".join(_box(page).get_text("text").split()) == "JANE DOE"


def test_a_bridge_save_keeps_every_letter_of_a_word_wider_than_its_box(tmp_path):
    """#435: a word wider than a fixed-width box breaks across lines instead of being cut off."""
    text = "ABCDEF ABCDEF ABCDEF"
    doc = fitz.open()
    page = doc.new_page()
    annot = page.add_freetext_annot(fitz.Rect(150, 82, 177, 123), text, fontsize=12, fontname="helv",
                                    border_width=1.0)
    annot.set_info(title=KLARPDF_AUTHOR)
    annot.update()
    src = str(tmp_path / "old.pdf")
    doc.save(src)
    doc.close()
    out = str(tmp_path / "rotated.pdf")
    T.rotate(src, 90, out)
    with fitz.open(out) as doc:
        page = doc[0]
        assert "".join(_box(page).get_text("text").split()) == "".join(text.split())
