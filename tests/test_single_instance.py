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


def test_launcher_hands_off_raw_path_not_normalized(qapp, monkeypatch):
    r"""A second launch hands the resident instance the path **as given** — not normalize_path(it).

    normalize_path lower-cases (Windows case-fold); on a case-sensitive share such as a
    ``\\wsl.localhost\`` (WSL) mount the lower-cased path names a non-existent file, so a normalised
    hand-off failed to open it. The resident instance computes the identity key itself, so it only
    needs the original path. Regression guard for the v0.8.0 WSL-folder bug."""
    import launcher

    captured: list[str] = []
    monkeypatch.setattr(launcher, "PdfApp", lambda argv: qapp)  # reuse the one QApplication
    monkeypatch.setattr(
        launcher,
        "send_path_to_running_instance",
        lambda name, path, *a, **k: (captured.append(path), True)[1],
    )
    raw = r"\\wsl.localhost\Ubuntu-24.04\home\umesh\Payslip_2026-06-05.pdf"  # mixed-case, case-sensitive
    assert launcher.main(["pdfproj", raw]) == 0
    assert captured == [raw]                     # handed off verbatim (original case preserved)
    assert captured[0] != normalize_path(raw)    # specifically NOT the lower-cased identity key


def test_activate_window_does_not_crash(app):
    win = QMainWindow()
    win.show()
    activate_window(win)  # raise/focus path must be exception-free on this platform
    win.close()


def test_allow_foreground_handoff_does_not_crash():
    """A best-effort Win32 call (AllowSetForegroundWindow) on Windows, a no-op elsewhere — never
    raises on this platform."""
    from platform_integration import allow_foreground_handoff

    allow_foreground_handoff()


def test_handoff_passes_the_foreground_right(app, a_pdf, monkeypatch):
    """A second launch hands its foreground right to the resident instance as part of forwarding the
    path, so the resident (a background process) can actually raise the window it opens. Windows
    otherwise refuses a background process the focus — the bug where only the first file opened from
    Explorer came to the front and every later one stayed behind."""
    import platform_integration

    calls: list[bool] = []
    monkeypatch.setattr(platform_integration, "allow_foreground_handoff", lambda: calls.append(True))

    name = "pdfproj-test-foreground"
    QLocalServer.removeServer(name)
    assert app.start_server(name) is True
    assert send_path_to_running_instance(name, normalize_path(a_pdf)) is True
    assert calls  # the right was passed exactly once, as part of a successful handoff


def test_no_foreground_handoff_when_no_instance(monkeypatch):
    """With no resident instance to hand off to, the connection never opens, so there is nothing to
    raise and no foreground right is passed (the grant would just leak to some other app)."""
    import platform_integration

    calls: list[bool] = []
    monkeypatch.setattr(platform_integration, "allow_foreground_handoff", lambda: calls.append(True))

    assert send_path_to_running_instance("pdfproj-test-absent-xyz", "/tmp/none.pdf") is False
    assert not calls
