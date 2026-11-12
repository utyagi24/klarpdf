"""The PyMuPDF constants `klarpdf/` lists for the type checker are PyMuPDF's own (M172).

`klarpdf/model/pymupdf_constants.py` names each constant twice on one line: the name it defines, and
the name it reads from PyMuPDF. A line that defines `PDF_ANNOT_LINE` and reads `PDF_ANNOT_LINK`
would import cleanly, pass mypy, and give every caller the wrong number.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.model import pymupdf_constants

NAMES = sorted(name for name in vars(pymupdf_constants) if name.startswith("PDF_"))


def test_the_module_lists_constants():
    """Guards the test below, which passes over an empty list."""
    assert len(NAMES) >= 33


@pytest.mark.parametrize("name", NAMES)
def test_each_constant_is_the_one_pymupdf_gives_that_name(name):
    assert getattr(pymupdf_constants, name) == getattr(fitz, name)


def test_a_name_that_is_not_a_number_is_refused():
    """The module declares every name an `int`, and mypy takes its word for it."""
    with pytest.raises(TypeError, match="not int"):
        pymupdf_constants._constant("Rect")


def test_a_name_pymupdf_does_not_have_is_refused():
    with pytest.raises(AttributeError):
        pymupdf_constants._constant("PDF_ANNOT_HIGHLITE")
