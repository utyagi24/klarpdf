"""Headless test fixtures, built programmatically with fitz (no binaries checked in).

PLAN.md, Verification / Headless pytest:
 * ``A.pdf`` — 3 pages, each with a unique text layer, a **multi-level** outline, and a form
   field named ``name``.
 * ``B.pdf`` — 2 pages with distinct text and a form field of the **same name** ``name`` (to
   exercise duplicate-field handling on merge).

These run with no Qt display (offscreen), so they execute in WSL, CI, and web sessions.
"""

from __future__ import annotations

import gc
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


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item, nextitem):
    """Destroy every widget the finished test left behind — **the suite leaked all of them**.

    Closing a window does not destroy it. ``MainWindow`` is a parentless top-level, so Qt hands
    ownership to Python — and Python could not free it either, because the overlay controllers hold
    *bound methods of the window* (``_add_annotation``, ``_set_field_value``, …) as callbacks, so
    window → view → overlay → bound method → window is a cycle that also spans into C++, where the
    collector cannot follow. ``gc.collect()`` on its own frees **nothing** (measured).

    So every test that opened a document left the whole window alive. Measured on the CI runner
    before this hook: **~107,000 live widgets, ~16,000 of them top-level, and 8 GiB RSS** by the end
    of the suite, growing linearly from the first twenty-five tests. That is what has been
    segfaulting the Ubuntu runner — memory exhaustion inside whatever allocation happened to be next,
    which is why the crash reported itself from ``QGraphicsView``'s constructor in a test that had
    nothing to do with the change, and why the *reported* test moved with every edit. After this
    hook: **0 widgets, 0 top-level, a flat ~200 MiB** across all of it.

    Four details, each of which cost a wrong attempt:

    * **A ``trylast`` hookwrapper, not an autouse fixture.** An autouse fixture declared here is set
      up first and therefore torn down *last*-to-first — i.e. **before** the test's own ``win``
      fixture has finished with the window. Everything after this ``yield`` runs after every
      finaliser.
    * **Drain pending events first.** The inline text-box editor defers its commit with
      ``QTimer.singleShot(0, …)``; delete the widgets first and that callback fires against a freed
      C++ object ("Internal C++ object already deleted") on the way out of an unrelated test.
    * **``sendPostedEvents(DeferredDelete)``, not ``processEvents()``.** ``processEvents`` documents
      that it does *not* deliver ``DeferredDelete`` — Qt only delivers those when the event loop that
      posted them returns, and this suite never runs one. ``deleteLater()`` alone would post events
      nothing ever collects.
    * **Clear ``PdfApp._windows``.** The registry keeps a window until its ``closeEvent`` runs, so a
      test that never closed one leaves a wrapper pointing at an object destroyed here; the next
      test's fixture then calls ``.close()`` on it and dies. The registry must not outlive the
      widgets.

    Deliberately **not** ``close()``: ``closeEvent`` prompts on a dirty document, and the
    ``_no_real_modals`` guard would turn that into a baffling teardown error. Destroying a widget
    does not run ``closeEvent``, and everything that hook releases (render copies, cache entries,
    file handles) is released by destruction anyway.
    """
    yield
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    app.processEvents()                       # let deferred callbacks finish while their widgets live
    registry = getattr(app, "_windows", None)
    if registry is not None:
        registry.clear()
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


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


@pytest.fixture(autouse=True)
def _instant_search(monkeypatch):
    """Run live search synchronously. In the app a keystroke only *schedules* the search
    (``SEARCH_DEBOUNCE_MS``), because one full-document scan per keystroke made a 320-page file
    unusable; tests type with ``setText`` and assert on the result in the next line, with no event
    loop to let a timer fire. ``test_search_perf.py`` restores the real interval to test the
    debounce itself."""
    import viewer.search

    monkeypatch.setattr(viewer.search, "SEARCH_DEBOUNCE_MS", 0)


@pytest.fixture(autouse=True)
def _instant_zoom(monkeypatch):
    """Apply Ctrl+wheel zoom on the next event-loop pass instead of a frame later. In the app a
    wheel event only *accumulates* (``_ZOOM_COALESCE_MS``), because a burst rebuilt the scene once
    per event; tests send a detent and assert on the next line, with no event loop to let a 16 ms
    timer expire. ``test_zoom_coalescing.py`` restores the real interval to test the coalescing
    itself — the same arrangement ``_instant_search`` has with ``test_search_perf.py``."""
    import viewer.pdf_view

    monkeypatch.setattr(viewer.pdf_view, "_ZOOM_COALESCE_MS", 0)


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
