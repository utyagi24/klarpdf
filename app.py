"""PdfApp — the resident QApplication that owns the open document windows.

PLAN.md, Critical files / Single-instance: ``PdfApp`` holds the ``dict[normalized_path -> window]``
(one window per document), the shared settings store, the page clipboard, and the
``QLocalServer``. A second launch hands its path to this resident instance (see
:func:`send_path_to_running_instance` + ``launcher.py``); the instance then raises the existing
window for that path or opens a new one. Because every document lives in this one process, the
page clipboard works across all document windows.
"""

from __future__ import annotations

import time

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

import platform_integration
from store.settings import Settings
from util.paths import normalize_path

_HANDOFF_TIMEOUT_MS = 400


def send_path_to_running_instance(name: str, path: str, retries: int = 1) -> bool:
    """Try to hand ``path`` to a resident instance listening on ``name``.

    Returns True if a running instance accepted it (this process should then exit with no UI),
    False if no instance is listening. Retries once to absorb the near-simultaneous
    double-click race (PLAN.md).
    """
    for _ in range(retries + 1):
        sock = QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(_HANDOFF_TIMEOUT_MS):
            sock.write(path.encode("utf-8") + b"\n")
            sock.flush()  # push the bytes onto the pipe now; the pump below drives any remainder
            # Keep our end open until the resident instance has read the path and closes the
            # socket from its side. Closing first would let a Windows named pipe discard the
            # still-unread bytes (Unix domain sockets preserve them after the writer closes — a
            # WSL/Windows divergence we must not depend on). We *pump* the event loop rather than
            # block on waitForDisconnected so the handoff also completes when the server shares
            # our thread (the headless tests drive both ends in one process, where the server's
            # slots only run when events are processed); in the real two-process launch this just
            # drains this short-lived launcher's own events while the resident server reads.
            qapp = QApplication.instance()
            deadline = time.monotonic() + _HANDOFF_TIMEOUT_MS / 1000
            while (
                sock.state() != QLocalSocket.LocalSocketState.UnconnectedState
                and time.monotonic() < deadline
            ):
                if qapp is not None:
                    qapp.processEvents()
                # processEvents() above may have completed the disconnect; re-check before
                # waiting so we don't call waitForDisconnected() on an already-unconnected socket
                # (Qt warns: "not allowed in UnconnectedState").
                if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                    sock.waitForDisconnected(10)
            sock.close()
            return True
    return False


class PdfApp(QApplication):
    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        # Set early: QStandardPaths.AppConfigLocation derives from the application name, so the
        # settings dir resolves to .config/pdfproj (Linux) / %APPDATA%\pdfproj (Windows).
        self.setApplicationName("pdfproj")
        self.setOrganizationName("pdfproj")
        self.settings = Settings()
        self._windows: dict[str, object] = {}
        # Page clipboard for cross-window cut/copy/paste (PLAN.md): each entry is
        # (source_id, source fitz.Document, source_page_index, rotation_override). Holding the
        # source doc lets the paste target register it and splice the PageRef losslessly.
        self.page_clipboard: list[tuple] = []
        self._server: QLocalServer | None = None
        self._incoming: dict[QLocalSocket, bytes] = {}

    # ---- single-instance server -------------------------------------------------

    def start_server(self, name: str) -> bool:
        """Become the resident instance by listening on ``name``. Returns False if another
        live instance already holds it (caller should hand off instead)."""
        server = QLocalServer(self)
        if not server.listen(name):
            # No live server answered the caller's connect attempt, so a leftover socket from a
            # crashed instance is stale: clear it and retry once.
            QLocalServer.removeServer(name)
            if not server.listen(name):
                return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        assert self._server is not None
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        self._incoming[sock] = b""
        sock.readyRead.connect(lambda: self._read_incoming(sock))
        sock.disconnected.connect(lambda: self._read_incoming(sock, final=True))
        # The path may already be buffered by the time we attach the handlers — on Windows named
        # pipes the bytes can land before readyRead is connected (the peer holds the connection
        # open until we read, so they are never discarded). Drain whatever is present now rather
        # than waiting for an edge that has already passed.
        if sock.bytesAvailable():
            self._read_incoming(sock)

    def _read_incoming(self, sock: QLocalSocket, final: bool = False) -> None:
        if sock not in self._incoming:
            return
        self._incoming[sock] += bytes(sock.readAll().data())
        buffer = self._incoming[sock]
        if b"\n" in buffer or final:
            del self._incoming[sock]
            line = buffer.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
            # Close from our side so the peer's waitForDisconnected returns promptly (it is still
            # holding the connection open). Do this before the potentially slow open_document.
            sock.disconnectFromServer()
            sock.deleteLater()
            if line:
                self.open_document(line)  # dedupes + raises via _raise

    # ---- window registry --------------------------------------------------------

    def open_document(self, path: str):
        """Open ``path``, or raise its existing window if already open (no duplicate)."""
        key = normalize_path(path)
        existing = self._windows.get(key)
        if existing is not None:
            self._raise(existing)
            return existing

        from main_window import MainWindow  # local import avoids a cycle at module load

        window = MainWindow(self, path, self.settings)
        self._windows[key] = window
        window.show()
        self._raise(window)
        return window

    def forget_window(self, path: str) -> None:
        self._windows.pop(normalize_path(path), None)

    def rename_window(self, old_path: str, new_path: str, window) -> None:
        """Re-key a window after Save As, so one-window-per-document tracks the new identity."""
        self._windows.pop(normalize_path(old_path), None)
        self._windows[normalize_path(new_path)] = window

    @staticmethod
    def _raise(window) -> None:
        platform_integration.activate_window(window)
