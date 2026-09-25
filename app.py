"""PdfApp — the resident QApplication that owns the open document windows.

PLAN.md, Critical files / Single-instance: ``PdfApp`` holds the ``dict[normalized_path -> window]``
(one window per document), the shared settings store, the page clipboard, and the
``QLocalServer``. A second launch hands its path to this resident instance (see
:func:`send_path_to_running_instance` + ``launcher.py``); the instance then raises the existing
window for that path or opens a new one. Because every document lives in this one process, the
page clipboard works across all document windows.
"""

from __future__ import annotations

import os
import time

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

import platform_integration
from store.settings import Settings
from klarpdf.util.paths import normalize_path

_HANDOFF_TIMEOUT_MS = 400


def unopenable_reason(exc: BaseException) -> str:
    """One line saying why a document would not open — written for the reader, not for a log.

    Each arm names a failure the open path genuinely produces, measured for M149 (#332):
    ``Path.read_bytes`` in ``VirtualDocument.open_source`` raises ``OSError`` for a file that
    cannot be read at all, and ``fitz.open`` then raises ``EmptyFileError`` for a 0-byte one and
    ``FileDataError`` for bytes that are not a PDF — a **truncated** file included, since MuPDF
    does not try to repair a stream it cannot find a trailer in. Anything unanticipated falls
    through to the exception's own text, so a new cause still reaches the reader as words rather
    than as a traceback on a console they never see.

    ``pymupdf`` defines its own ``FileNotFoundError`` (a ``RuntimeError``, not the builtin), raised
    when it opens **by path**; we open from a stream, so the builtin is the one that actually
    arrives. Both are named, because which one appears is PyMuPDF's business, not the reader's.
    """
    import pymupdf as fitz

    if isinstance(exc, fitz.EmptyFileError):
        return "The file is empty — there is nothing in it to show."
    if isinstance(exc, fitz.FileDataError):
        return "It is damaged, or it is not a PDF."
    if isinstance(exc, (FileNotFoundError, fitz.FileNotFoundError)):
        return "The file is no longer there."
    if isinstance(exc, IsADirectoryError):
        return "That is a folder, not a file."
    if isinstance(exc, PermissionError):
        return "It could not be read — another program may have it open, or its permissions deny it."
    if isinstance(exc, OSError) and exc.strerror:
        return f"It could not be read: {exc.strerror}."
    return str(exc) or exc.__class__.__name__


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
            # Hand our foreground right to the resident instance before forwarding the path, so the
            # window IT opens for this file is actually raised to the front. A background process is
            # refused focus on Windows — which is why, before this, only the first file opened from
            # Explorer came forward and every later one stayed behind (see allow_foreground_handoff).
            platform_integration.allow_foreground_handoff()
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
        # On WSL, drop Qt console lines known to be harmless. Does nothing elsewhere (M153).
        platform_integration.quiet_harmless_qt_lines(self)
        # Set early: QStandardPaths.AppConfigLocation derives from the application name, so the
        # settings dir resolves to .config/klarpdf (Linux) / %LOCALAPPDATA%\klarpdf (Windows).
        self.setApplicationName("klarpdf")
        self.setOrganizationName("klarpdf")
        # App-level icon: taskbar grouping + the default for every window/dialog.
        from ui import icons, popup_closer

        self.setWindowIcon(icons.app_icon())
        # A list or menu that Qt only hides would stay on a WSLg desktop; this closes it (M153).
        popup_closer.install(self)
        self.settings = Settings()
        self._windows: dict[str, object] = {}
        # Page clipboard for cross-window cut/copy/paste (PLAN.md): each entry is
        # (source_id, source fitz.Document, source_page_index, rotation_override, annotations,
        # crop_override) — the page plus its per-page edits. Holding the source doc lets the
        # paste target register it and splice the PageRef losslessly.
        self.page_clipboard: list[tuple] = []
        # Object clipboard (M59; a **list** since M59.12): the copied free-placed marks (frozen
        # descriptor value objects — TextBox / InkStroke / Line / Shape), pasteable into any window
        # (single process, same pattern as the page clipboard but with no source document to carry).
        # A whole multi-selection copies at once and pastes as a group, keeping its arrangement, so
        # this mirrors `page_clipboard`: a list, empty when there is nothing to paste.
        self.object_clipboard: list = []
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
        """Open ``path``, or raise its existing window if already open (no duplicate).

        Returns ``None`` when **no window opened** — an encrypted document's password prompt was
        cancelled, or the file could not be opened at all and the reader has been told so (M149).
        Callers must check: ``launcher.main`` would otherwise run an event loop with no window in
        it, a process alive with nothing on screen (#374).
        """
        key = normalize_path(path)
        existing = self._windows.get(key)
        if existing is not None:
            self.settings.add_recent(path)  # bump the MRU for the re-raised doc
            self._raise(existing)
            return existing

        from main_window import MainWindow  # local import avoids a cycle at module load
        from klarpdf.model.virtual_document import PasswordRequired

        try:
            window = MainWindow(self, path, self.settings)
        except PasswordRequired:
            return None  # encrypted + the password prompt was cancelled → open nothing
        except (OSError, RuntimeError) as exc:
            # Every *other* way an open fails (M149, #332): a 0-byte, damaged, vanished or
            # unreadable file used to let `pymupdf.EmptyFileError` & co. straight out of here —
            # a traceback on a console nobody sees, and at a cold start the launch died before a
            # window ever existed. Caught at this one chokepoint because every route into the app
            # arrives here: the launcher's command line, File ▸ Open, Open Recent, and the path a
            # second launch hands to this resident instance.
            #
            # Narrow on purpose. `OSError` is `read_bytes` failing and `RuntimeError` is every
            # PyMuPDF error (`FileDataError` and friends subclass it); a `TypeError` or
            # `AttributeError` from our own construction code is a bug in KlarPDF, and must keep
            # crashing loudly instead of being reported to the reader as a broken document.
            self._report_unopenable(path, exc)
            return None

        self.settings.add_recent(path)  # record only a document we actually opened
        self._windows[key] = window
        window.show()
        self._raise(window)
        return window

    def _report_unopenable(self, path: str, exc: BaseException) -> None:
        """Tell the reader this document would not open, and stay running.

        Parented to whatever window is in front, so the dialog belongs to the document they were
        looking at. At a cold start there is no window yet and ``activeWindow()`` is ``None`` —
        which is what we want: a parentless ``QMessageBox`` still shows, and still spins its own
        event loop, so this works *before* ``app.exec()`` has been entered (#332's cold-start half).
        """
        QMessageBox.warning(
            self.activeWindow(),
            "Can't open document",
            f"“{os.path.basename(path)}” can't be opened.\n\n{unopenable_reason(exc)}",
        )

    def window_for_key(self, key: str | None):
        """Return the open window registered under a normalized identity key, or None.

        Used by cross-window drag/drop to resolve the source window from the dragged payload.
        """
        return self._windows.get(key) if key else None

    def forget_window(self, path: str) -> None:
        self._windows.pop(normalize_path(path), None)

    def rename_window(self, old_path: str, new_path: str, window) -> None:
        """Re-key a window after Save As, so one-window-per-document tracks the new identity."""
        self._windows.pop(normalize_path(old_path), None)
        self._windows[normalize_path(new_path)] = window
        self.settings.add_recent(new_path)  # the Save As target is now a recent document

    @staticmethod
    def _raise(window) -> None:
        platform_integration.activate_window(window)
