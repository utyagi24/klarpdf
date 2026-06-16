"""Headless IPC tests for the single-instance handoff (M5). WSL smoke; Windows-validated in M7.

These run a real QLocalServer + QLocalSocket in one process (offscreen), exercising the handoff
the launcher performs between a second launch and the resident instance.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QMainWindow

from app import PdfApp, send_path_to_running_instance
from platform_integration import activate_window, single_instance_server_name
from store.settings import Settings
from util.paths import normalize_path


@pytest.fixture(scope="session")
def qapp():
    app = PdfApp.instance() or PdfApp([])
    yield app


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "view_state.json")
    qapp.page_clipboard = []
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()
    yield qapp
    if qapp._server is not None:
        name = qapp._server.serverName()
        qapp._server.close()
        QLocalServer.removeServer(name)
        qapp._server = None
    for w in list(qapp._windows.values()):
        w.close()
    qapp._windows.clear()


def _pump(app, cond, tries=400) -> bool:
    for _ in range(tries):
        if cond():
            return True
        app.processEvents()
        time.sleep(0.003)
    return cond()


def test_server_name_is_stable_and_nonempty():
    assert single_instance_server_name()
    assert single_instance_server_name() == single_instance_server_name()


def test_handoff_opens_window_in_resident_instance(app, a_pdf):
    name = "pdfproj-test-handoff"
    QLocalServer.removeServer(name)
    assert app.start_server(name) is True

    assert send_path_to_running_instance(name, normalize_path(a_pdf)) is True
    key = normalize_path(a_pdf)
    assert _pump(app, lambda: key in app._windows)


def test_handoff_for_open_doc_does_not_duplicate(app, a_pdf):
    name = "pdfproj-test-dedupe"
    QLocalServer.removeServer(name)
    app.start_server(name)
    w1 = app.open_document(a_pdf)
    count_before = len(app._windows)

    assert send_path_to_running_instance(name, normalize_path(a_pdf)) is True
    _pump(app, lambda: False, tries=40)  # let any new-connection events drain
    assert len(app._windows) == count_before
    assert app._windows[normalize_path(a_pdf)] is w1  # raised, not duplicated


def test_send_returns_false_when_no_instance():
    assert send_path_to_running_instance("pdfproj-test-absent-xyz", "/tmp/none.pdf") is False


def test_start_server_clears_stale_socket(app):
    name = "pdfproj-test-stale"
    QLocalServer.removeServer(name)
    leftover = QLocalServer()  # simulate a socket left behind by a prior instance
    assert leftover.listen(name)
    # The caller already failed to connect, so start_server treats the name as stale: remove + retry.
    assert app.start_server(name) is True
    leftover.close()


def test_activate_window_does_not_crash(app):
    win = QMainWindow()
    win.show()
    activate_window(win)  # raise/focus path must be exception-free on this platform
    win.close()
