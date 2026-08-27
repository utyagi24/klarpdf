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

import importlib.util

import pymupdf as fitz
import pytest

#: Is the GUI toolkit installed at all? (M115.1)
#:
#: Normally yes — every lock this project tests with carries PySide6. The exception is the `bridge`
#: CI job, which installs **`requirements-mcp.txt`**, the lock a bridge *user* installs: it has no
#: PySide6 and no pypdf on purpose, because the server never touches either. That job exists because
#: nothing had ever run a line of code against that lock, which is how the app and the bridge came
#: to be on different PyMuPDF builds for three months (M115).
#:
#: The three `autouse` fixtures below all reach into Qt, so without this they would error the
#: *setup* of every bridge test — before any test body ran — and the job would report a wall of
#: errors that say nothing about the bridge. Each returns early instead: there are no widgets to
#: destroy, no modal to intercept and no debounce to zero out when the toolkit is not installed.
#:
#: ``find_spec`` rather than a ``try: import``: it answers "is it installed" **without importing
#: it**, so this line does not pull ~60 MB of Qt into the interpreter a moment earlier than the
#: suite would have anyway.
GUI_INSTALLED = importlib.util.find_spec("PySide6") is not None

# Unique, searchable strings per page so we can assert a specific page's text survived a move.
A_TEXT = ["ALPHA-zero-A0", "ALPHA-one-A1", "ALPHA-two-A2"]
B_TEXT = ["BETA-zero-B0", "BETA-one-B1"]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Name every skipped test at the end of a run, with the reason beside it (M125).

    **Why this is not just `-rs`.** pytest's own short summary prints `path:line: reason`, which
    tells a reader where to look rather than what did not run — they still have to open the file to
    learn the test's name. That matters here more than it usually would, because the skip count is
    genuinely confusing: it differs by platform (5 tests are platform-locked and can only ever run on
    one OS) *and* by shell on the same machine, since `pdftotext` sits on Git Bash's PATH but not
    PowerShell's. "7 skipped" on its own is a puzzle; "7 skipped, and here they are" is a report.

    The names carry the answer now — a `_windows_only` or `_posix_only` suffix says why a skip was
    inevitable rather than suspicious — so printing them is what makes the naming convention visible
    at the moment someone is looking at the number.
    """
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return

    # ASCII on purpose: this line lands in CI logs and in consoles whose encoding is not UTF-8,
    # and a mojibaked header on the one report meant to remove confusion would be its own joke.
    terminalreporter.write_sep("-", f"{len(skipped)} skipped - what did NOT run here")
    for report in skipped:
        # For a skip, `longrepr` is a (path, lineno, reason) triple; the reason is prefixed
        # "Skipped: " by pytest. Fall back to the raw value if that ever stops holding.
        reason = ""
        longrepr = getattr(report, "longrepr", None)
        if isinstance(longrepr, tuple) and len(longrepr) == 3:
            reason = str(longrepr[2]).removeprefix("Skipped: ")
        elif longrepr:
            reason = str(longrepr)
        terminalreporter.write_line(f"  {report.nodeid}")
        if reason:
            terminalreporter.write_line(f"      reason: {reason}")


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
    if not GUI_INSTALLED:
        return              # no toolkit, no widgets to destroy — see GUI_INSTALLED
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
    carries the dialog's text, so the root cause lands in the failure output.

    Skipped when the toolkit is absent — nothing there can raise a modal (see ``GUI_INSTALLED``)."""
    if not GUI_INSTALLED:
        return
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
    debounce itself.

    Skipped when the toolkit is absent — ``viewer/search.py`` imports Qt at module level, and there
    is no live search to make instant without it (see ``GUI_INSTALLED``)."""
    if not GUI_INSTALLED:
        return
    import viewer.search

    monkeypatch.setattr(viewer.search, "SEARCH_DEBOUNCE_MS", 0)


@pytest.fixture(autouse=True)
def _instant_zoom(monkeypatch):
    """Apply Ctrl+wheel zoom on the next event-loop pass instead of a frame later. In the app a
    wheel event only *accumulates* (``_ZOOM_COALESCE_MS``), because a burst rebuilt the scene once
    per event; tests send a detent and assert on the next line, with no event loop to let a 16 ms
    timer expire. ``test_zoom_coalescing.py`` restores the real interval to test the coalescing
    itself — the same arrangement ``_instant_search`` has with ``test_search_perf.py``.

    Skipped when the toolkit is absent, for the same reason as ``_instant_search``."""
    if not GUI_INSTALLED:
        return
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


# ---- forms as they arrive in the wild (M94, modelled on the SSA-3 of TC-002) ----


@pytest.fixture
def awkward_form_pdf(tmp_path) -> str:
    """A form with the three properties a caller cannot guess and could not previously read.

    Everything here is copied from a real federal form rather than invented: a checkbox whose
    ticked value is ``"2"`` and not ``"Yes"``; a text field marked read-only that is form plumbing
    rather than something to offer a person; and a length-capped multiline field.
    """
    path = str(tmp_path / "awkward.pdf")
    doc = fitz.open()
    page = doc.new_page()
    for name, kind, rect, flags in (
        ("married", fitz.PDF_WIDGET_TYPE_CHECKBOX, (72, 72, 92, 92), 0),
        ("remarks", fitz.PDF_WIDGET_TYPE_TEXT, (72, 110, 500, 300),
         fitz.PDF_TX_FIELD_IS_MULTILINE),
        ("plumbing", fitz.PDF_WIDGET_TYPE_TEXT, (72, 320, 75, 324),
         fitz.PDF_FIELD_IS_READ_ONLY),
        ("ssn", fitz.PDF_WIDGET_TYPE_TEXT, (72, 340, 272, 360), fitz.PDF_FIELD_IS_REQUIRED),
    ):
        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = kind
        widget.rect = fitz.Rect(*rect)
        widget.field_flags = flags
        if name == "ssn":
            widget.text_maxlen = 9
        page.add_widget(widget)
    doc.save(path)
    doc.close()

    # The checkbox's on-state: PyMuPDF writes "Yes", real forms write whatever they like, and it is
    # the appearance-state name that decides. Renaming it in /AP/N is what makes this a fixture for
    # the bug rather than for the happy path.
    doc = fitz.open(path)
    page = doc[0]
    for widget in page.widgets():
        if widget.field_name == "married":
            _kind, appearances = doc.xref_get_key(widget.xref, "AP/N")
            doc.xref_set_key(widget.xref, "AP/N", appearances.replace("/Yes", "/2"))
    doc.save(path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()
    return path


def _xfa_packets(path: str, *, dynamic: bool) -> None:
    """Graft an XFA packet set onto ``path`` in place — an AcroForm form becomes an XFA one.

    Two packets is enough for what is being tested: ``config``, which is where ``dynamicRender``
    decides static vs dynamic, and ``datasets``, the value store that a fill leaves stale.
    """
    doc = fitz.open(path)
    render = "required" if dynamic else "forbidden"
    packets = {
        "config": (
            "<config xmlns='http://www.xfa.org/schema/xci/3.0/'><acrobat><acrobat7>"
            f"<dynamicRender>{render}</dynamicRender></acrobat7></acrobat></config>"
        ).encode(),
        "datasets": (
            b"<xfa:datasets xmlns:xfa='http://www.xfa.org/schema/xfa-data/1.0/'><xfa:data>"
            b"<topmostSubform><married/><remarks/></topmostSubform></xfa:data></xfa:datasets>"
        ),
    }
    references = []
    for name, data in packets.items():
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, data)
        references.append(f"({name}) {xref} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/XFA", "[" + " ".join(references) + "]")
    doc.save(path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()


@pytest.fixture
def static_xfa_pdf(awkward_form_pdf) -> str:
    """An XFA form Acrobat renders from its AcroForm appearances (``dynamicRender: forbidden``)."""
    _xfa_packets(awkward_form_pdf, dynamic=False)
    return awkward_form_pdf


@pytest.fixture
def dynamic_xfa_pdf(awkward_form_pdf) -> str:
    """An XFA form Acrobat builds from the XFA template (``dynamicRender: required``) — the case
    TC-002 flagged as untested and the likeliest hard failure."""
    _xfa_packets(awkward_form_pdf, dynamic=True)
    return awkward_form_pdf
