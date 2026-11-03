"""An encrypted file with no ``/ID`` still opens after we save it (M161). Headless.

The key of an RC4 or AES-128 document is made from its password *and* the first element of the
trailer's ``/ID``, and a file with no ``/ID`` was keyed on the empty string. MuPDF writes a fresh
``/ID`` on every save while ``PDF_ENCRYPT_KEEP`` keeps the old key, so an owner-password document of
that shape came back asking for a password nobody had ever set. It was reported against a single
highlight and was true of every route: the append wrote the locked file, and the copy and the graft
raised *document closed or encrypted* before writing anything, because each reopens a copy of the
source that MuPDF has just written the same way.

The bridge reaches the same three routes through the same engine; ``test_mcp_file_id.py`` covers it.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from klarpdf.model.edit_engine import PyMuPDFEngine
from klarpdf.model.page_edits import Highlight
from klarpdf.model.virtual_document import VirtualDocument, pin_file_id
from tests.conftest import write_idless_pdf

_TEXTS = ["first page, no file ID", "second page, no file ID"]
_MARK = Highlight(((72, 60, 200, 80),), (1.0, 0.86, 0.10))

#: Every algorithm MuPDF can write. AES-256 does not use ``/ID`` and was never broken; it stays in
#: to show the pin is harmless where it is not needed.
_ALGORITHMS = {
    "rc4-40": fitz.PDF_ENCRYPT_RC4_40,
    "rc4-128": fitz.PDF_ENCRYPT_RC4_128,
    "aes-128": fitz.PDF_ENCRYPT_AES_128,
    "aes-256": fitz.PDF_ENCRYPT_AES_256,
}


def _mark(vdoc):
    vdoc.add_annotation(0, _MARK)


def _rotate(vdoc):
    vdoc.rotate_pages([0], 90)


def _reverse(vdoc):
    vdoc.ordered = vdoc.ordered[::-1]
    vdoc.dirty = True


#: One edit per save route, so each test can check the route it meant to take was the one taken.
_ROUTES = {"append": _mark, "copy": _rotate, "graft": _reverse}


def _route_of(vdoc) -> str:
    if PyMuPDFEngine().appends(vdoc):
        return "append"
    return "copy" if vdoc.page_set_unchanged() else "graft"


@pytest.fixture(params=list(_ALGORITHMS), ids=list(_ALGORITHMS))
def idless_pdf(request, tmp_path) -> str:
    return write_idless_pdf(str(tmp_path / "idless.pdf"), _TEXTS, _ALGORITHMS[request.param])


@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_save_route_writes_a_file_that_opens(idless_pdf, tmp_path, route):
    vdoc = VirtualDocument.from_path(idless_pdf)
    _ROUTES[route](vdoc)
    assert _route_of(vdoc) == route

    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vdoc, out)

    with fitz.open(idless_pdf) as before, fitz.open(out) as after:
        assert not after.needs_pass, "the save wrote a file that asks for a password"
        assert sorted(page.get_text().strip() for page in after) == _TEXTS
        if route != "graft":  # a rebuild is a new document and never carried encryption
            assert after.metadata["encryption"] == before.metadata["encryption"]
            assert after.permissions == before.permissions
        if route == "append":
            assert [annot.type[1] for annot in after[0].annots()] == ["Highlight"]


def test_a_copy_of_the_source_opens(idless_pdf):
    """``fresh_source`` is what print, export, thumbnails and the viewer's form rendering start
    from, not only the save, and it reopens what it writes — so this is where they all failed."""
    vdoc = VirtualDocument.from_path(idless_pdf)
    with vdoc.fresh_source(vdoc.origin_source_id) as copy:
        assert not copy.needs_pass
        assert copy[0].get_text().strip() == _TEXTS[0]


def test_a_second_engine_opens_the_appended_file(tmp_path):
    """The pin has to mean the same thing to a reader that is not MuPDF. RC4 because pypdf
    decrypts it without the optional ``cryptography`` extra."""
    pypdf = pytest.importorskip("pypdf", reason="the second engine is a dev-only cross-check")
    path = write_idless_pdf(str(tmp_path / "idless.pdf"), _TEXTS, fitz.PDF_ENCRYPT_RC4_40)
    vdoc = VirtualDocument.from_path(path)
    _mark(vdoc)
    out = str(tmp_path / "out.pdf")
    PyMuPDFEngine().materialize(vdoc, out)

    reader = pypdf.PdfReader(out)
    assert reader.decrypt("")
    assert "first page" in reader.pages[0].extract_text()
    assert [a.get_object()["/Subtype"] for a in reader.pages[0]["/Annots"]] == ["/Highlight"]


def test_the_pin_leaves_a_file_that_needs_none_alone(tmp_path):
    """Only an encrypted file without an ``/ID`` is changed: one that has an identifier keeps it,
    and an unencrypted file's identifier binds nothing, so MuPDF may write whatever it likes."""
    plain = write_idless_pdf(str(tmp_path / "plain.pdf"), _TEXTS)
    with fitz.open(plain) as doc:
        pin_file_id(doc)
        assert doc.xref_get_key(-1, "ID") == ("null", "null")

    keyed = str(tmp_path / "keyed.pdf")
    with fitz.open() as doc:
        doc.new_page()
        doc.save(keyed, encryption=fitz.PDF_ENCRYPT_RC4_128, owner_pw="owner",
                 permissions=int(fitz.PDF_PERM_PRINT))
    with fitz.open(keyed) as doc:
        before = doc.xref_get_key(-1, "ID")
        pin_file_id(doc)
        assert doc.xref_get_key(-1, "ID") == before
