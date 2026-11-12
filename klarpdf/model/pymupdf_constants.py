"""PyMuPDF's ``PDF_`` constants, listed where a type checker can see them (M172).

PyMuPDF does not define names such as ``PDF_ANNOT_HIGHLIGHT`` in its source. It copies them in from
MuPDF's bindings with a loop when it is imported, so they exist when the code runs and are missing
from what PyMuPDF declares. mypy reported every ``fitz.PDF_…`` in ``klarpdf/`` as *Module has no
attribute*: 81 of its 127 errors. Listing the names here once keeps the rest of the check whole.
The alternatives each cost more: telling mypy to treat PyMuPDF as unknown would stop it checking
calls on a page or a document, and a suppression on every use is 82 comments that say one thing
(``PLAN.md`` §M172).

So ``klarpdf/`` imports these names from here and never writes ``fitz.PDF_…``. To use another one,
add a line below. A name PyMuPDF does not have fails when this module is imported, which is at the
start of every run and every test, and mypy refuses a name this module does not list.

The app's own code is not type-checked and still writes ``fitz.PDF_…``, which is the same value.
"""

from __future__ import annotations

from typing import Final

import pymupdf as fitz


def _constant(name: str) -> int:
    """``fitz.<name>``, checked to be the ``int`` the annotations below say it is."""
    value = getattr(fitz, name)
    if not isinstance(value, int):
        raise TypeError(f"pymupdf.{name} is {type(value).__name__}, not int")
    return value


# Annotation kinds: ``Annot.type[0]``.
PDF_ANNOT_LINK: Final = _constant("PDF_ANNOT_LINK")
PDF_ANNOT_FREE_TEXT: Final = _constant("PDF_ANNOT_FREE_TEXT")
PDF_ANNOT_LINE: Final = _constant("PDF_ANNOT_LINE")
PDF_ANNOT_SQUARE: Final = _constant("PDF_ANNOT_SQUARE")
PDF_ANNOT_CIRCLE: Final = _constant("PDF_ANNOT_CIRCLE")
PDF_ANNOT_HIGHLIGHT: Final = _constant("PDF_ANNOT_HIGHLIGHT")
PDF_ANNOT_UNDERLINE: Final = _constant("PDF_ANNOT_UNDERLINE")
PDF_ANNOT_SQUIGGLY: Final = _constant("PDF_ANNOT_SQUIGGLY")
PDF_ANNOT_STRIKE_OUT: Final = _constant("PDF_ANNOT_STRIKE_OUT")
PDF_ANNOT_INK: Final = _constant("PDF_ANNOT_INK")
PDF_ANNOT_POPUP: Final = _constant("PDF_ANNOT_POPUP")
PDF_ANNOT_WIDGET: Final = _constant("PDF_ANNOT_WIDGET")

# Line endings: ``Annot.line_ends``.
PDF_ANNOT_LE_NONE: Final = _constant("PDF_ANNOT_LE_NONE")
PDF_ANNOT_LE_OPEN_ARROW: Final = _constant("PDF_ANNOT_LE_OPEN_ARROW")

# Form field kinds: ``Widget.field_type``.
PDF_WIDGET_TYPE_CHECKBOX: Final = _constant("PDF_WIDGET_TYPE_CHECKBOX")
PDF_WIDGET_TYPE_COMBOBOX: Final = _constant("PDF_WIDGET_TYPE_COMBOBOX")
PDF_WIDGET_TYPE_LISTBOX: Final = _constant("PDF_WIDGET_TYPE_LISTBOX")
PDF_WIDGET_TYPE_RADIOBUTTON: Final = _constant("PDF_WIDGET_TYPE_RADIOBUTTON")
PDF_WIDGET_TYPE_TEXT: Final = _constant("PDF_WIDGET_TYPE_TEXT")

# Form field flags: bits of ``Widget.field_flags``.
PDF_FIELD_IS_READ_ONLY: Final = _constant("PDF_FIELD_IS_READ_ONLY")
PDF_FIELD_IS_REQUIRED: Final = _constant("PDF_FIELD_IS_REQUIRED")
PDF_TX_FIELD_IS_MULTILINE: Final = _constant("PDF_TX_FIELD_IS_MULTILINE")

# Permissions: bits of ``Document.permissions``.
PDF_PERM_PRINT: Final = _constant("PDF_PERM_PRINT")
PDF_PERM_MODIFY: Final = _constant("PDF_PERM_MODIFY")
PDF_PERM_COPY: Final = _constant("PDF_PERM_COPY")
PDF_PERM_ANNOTATE: Final = _constant("PDF_PERM_ANNOTATE")
PDF_PERM_FORM: Final = _constant("PDF_PERM_FORM")
PDF_PERM_ACCESSIBILITY: Final = _constant("PDF_PERM_ACCESSIBILITY")
PDF_PERM_ASSEMBLE: Final = _constant("PDF_PERM_ASSEMBLE")
PDF_PERM_PRINT_HQ: Final = _constant("PDF_PERM_PRINT_HQ")

# Encryption: the ``encryption`` argument of a save.
PDF_ENCRYPT_KEEP: Final = _constant("PDF_ENCRYPT_KEEP")
PDF_ENCRYPT_NONE: Final = _constant("PDF_ENCRYPT_NONE")
PDF_ENCRYPT_AES_256: Final = _constant("PDF_ENCRYPT_AES_256")
