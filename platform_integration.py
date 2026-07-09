"""OS-integration seam (PLAN.md, Portability hedge #2).

The *only* place OS-specific app behaviours live, so ``app.py``/``launcher.py`` stay portable:

- :func:`single_instance_server_name` — the per-user ``QLocalServer`` name (a named pipe on
  Windows, a socket on Linux).
- :func:`activate_window` — raise/focus an existing window. Windows can refuse focus to a
  background process, so it also does the documented ``WindowStaysOnTopHint`` nudge + ``alert``;
  that branch is gated to Windows and is validated there in M7 (WSLg only smoke-tests the rest).
- :func:`register_file_association` — a slot kept for a future Linux ``xdg-mime`` path and a
  dev/source-run convenience. On Windows the Inno Setup installer writes the ``.pdf``/ProgID
  association, so this is effectively unused there.

Windows impl now (focus shim) / Linux stub later — but written so today's WSLg path works.
"""

from __future__ import annotations

import getpass
import sys


def single_instance_server_name() -> str:
    """A stable, per-user IPC name so two users on one machine don't collide."""
    try:
        user = getpass.getuser()
    except Exception:  # getuser can raise if the environment has no user info
        user = "default"
    return f"klarpdf-singleton-{user}"


def activate_window(window) -> None:
    """Bring ``window`` to the front and give it focus (one-window-per-document raise)."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()

    if sys.platform == "win32":
        # A background process (the resident instance opening a window from an Explorer double-click)
        # can't always steal focus on Windows. Nudge the window to the top of the z-order with
        # SetWindowPos TOPMOST→NOTOPMOST + flash the taskbar.
        #
        # NB: do NOT toggle the Qt WindowStaysOnTopHint flag to do this — changing a window flag on
        # Windows *destroys and recreates the native window*, a visible flash/flicker every raise
        # (the previous approach; the cause of the open-from-Explorer flicker). SetWindowPos only
        # reorders the existing window, so there's no recreation and no flicker.
        from PySide6.QtWidgets import QApplication

        _raise_to_front_win32(window)
        QApplication.alert(window)


def _raise_to_front_win32(window) -> None:
    """Raise ``window`` to the top of the z-order via the Win32 ``SetWindowPos`` TOPMOST→NOTOPMOST
    nudge — no native-window recreation (hence no flicker). Best-effort: any failure (e.g. an
    offscreen window with no real HWND) is ignored, leaving the portable ``raise_``/``activateWindow``
    above as the result."""
    try:
        import ctypes

        hwnd = int(window.winId())
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
        flags = SWP_NOMOVE | SWP_NOSIZE
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    except Exception:
        pass


def allow_foreground_handoff() -> None:
    """Pass this process's foreground right on to the resident instance (Windows).

    When a second launch hands its file to the already-running instance and exits, it is that
    *resident* — a **background** process — that opens and raises the new window. But Windows lets
    only a process currently holding the foreground right call ``SetForegroundWindow`` (what Qt's
    ``activateWindow`` uses); a background process is refused, so the window opens *behind* the others.
    This process, however, was started by Explorer (the foreground app) to service the double-click,
    so it *does* hold the right — ``AllowSetForegroundWindow(ASFW_ANY)`` delegates it to whichever
    process next activates a window, i.e. the resident instance. The sender calls this just before it
    forwards the path (while it still holds the right); :func:`activate_window` in the resident then
    succeeds. Without it, only the very first file — opened by this same foreground-privileged process,
    before it became resident — was brought to the front.

    Best-effort and a no-op off Windows (or if this process doesn't currently hold the right, in which
    case the call simply returns FALSE and we are no worse off than before)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        ASFW_ANY = 0xFFFFFFFF  # (DWORD)-1 — grant to any process (the resident instance is unknown here)
        user32 = ctypes.windll.user32
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
        user32.AllowSetForegroundWindow.restype = wintypes.BOOL
        user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass


def register_file_association(exe_path: str | None = None) -> None:
    """Register KlarPDF as a .pdf handler.

    On Windows this is a no-op: the installer writes the HKCU ProgID + association (PLAN.md,
    Packaging). It exists for a future Linux ``xdg-mime`` path and an optional dev convenience.
    """
    return None
