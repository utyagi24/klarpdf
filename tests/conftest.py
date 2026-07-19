"""Headless test fixtures, built programmatically with fitz (no binaries checked in).

PLAN.md, Verification / Headless pytest:
 * ``A.pdf`` — 3 pages, each with a unique text layer, a **multi-level** outline, and a form
   field named ``name``.
 * ``B.pdf`` — 2 pages with distinct text and a form field of the **same name** ``name`` (to
   exercise duplicate-field handling on merge).

These run with no Qt display (offscreen), so they execute in WSL, CI, and web sessions.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless: no display needed
# Headless printing: QPrinter's only Linux backend is the CUPS plugin, which enumerates print
# destinations at construction. With no cupsd (a bare WSL box has none), libcups falls back to
# localhost:631; on WSL2 that connect never gets refused, so QPrinter(...) hangs the whole suite
# (test_printing.py). Point CUPS at a dead domain socket so it fails fast — the tests render to PDF
# and need no real printer. setdefault keeps a real CUPS setup (or CI's) if one is configured.
os.environ.setdefault("CUPS_SERVER", "/dev/null")

import pymupdf as fitz
import pytest

# Unique, searchable strings per page so we can assert a specific page's text survived a move.
A_TEXT = ["ALPHA-zero-A0", "ALPHA-one-A1", "ALPHA-two-A2"]
B_TEXT = ["BETA-zero-B0", "BETA-one-B1"]


@pytest.fixture(autouse=True)
def _no_real_modals(monkeypatch):
    """Turn any unexpected modal dialog into a loud failure. Offscreen, a real modal blocks
    forever — nothing can click it — and it has deadlocked the suite twice: a stale
    file-changed prompt from a lingering closed window, then a "Save failed" error box whose
    underlying exception the hang swallowed. Tests that exercise a prompt patch the
    ``_confirm_*`` / provider seam *above* these Qt calls (their per-test monkeypatch overrides
    this one), so anything reaching a real Qt modal is a bug — and the message raised here
    carries the dialog's text, so the root cause lands in the failure output."""
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    def deny(cls_name: str, method: str):
        def raiser(*args, **kwargs):
            raise AssertionError(f"unexpected modal {cls_name}.{method} in headless test: {args!r}")

        return raiser

    for method in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(deny("QMessageBox", method)))
    monkeypatch.setattr(QMessageBox, "exec", deny("QMessageBox", "exec"))
    for method in ("getInt", "getText", "getItem"):
        monkeypatch.setattr(QInputDialog, method, staticmethod(deny("QInputDialog", method)))
    for method in ("getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, method, staticmethod(deny("QFileDialog", method)))


def _build(path: str, texts: list[str], field_value: str) -> None:
    doc = fitz.open()
    for i, text in enumerate(texts):
        page = doc.new_page()
        page.insert_text((72, 72 + 20 * i), text, fontsize=11)
    # A form field named "name" on page 0 (both A and B use the same name on purpose).
    widget = fitz.Widget()
    widget.field_name = "name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 200, 272, 220)
    widget.field_value = field_value
    doc[0].add_widget(widget)
    doc.save(path)
    doc.close()


@pytest.fixture
def a_pdf(tmp_path) -> str:
    """3-page A.pdf with a multi-level outline and a ``name`` form field. Returns the path."""
    path = str(tmp_path / "A.pdf")
    _build(path, A_TEXT, field_value="A-value")
    # Multi-level outline: Chapter 1 (p1) > Section 1.1 (p2); Chapter 2 (p3).
    doc = fitz.open(path)
    doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 3]])
    doc.saveIncr()
    doc.close()
    return path


@pytest.fixture
def b_pdf(tmp_path) -> str:
    """2-page B.pdf with a colliding ``name`` form field. Returns the path."""
    path = str(tmp_path / "B.pdf")
    _build(path, B_TEXT, field_value="B-value")
    return path
