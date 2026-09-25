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
- :func:`acquire_app_mutex` — a named Windows mutex the **installer and uninstaller** watch, so
  neither runs while the app is open. See :data:`APP_MUTEX_NAME`.
- :func:`quiet_harmless_qt_lines` — on Wayland, stop Qt printing the console lines in
  :data:`HARMLESS_WAYLAND_LINES`. Every other Qt message still prints.

Windows impl now (focus shim) / Linux stub later — but written so today's WSLg path works.
"""

from __future__ import annotations

import getpass
import sys

#: Name of the Windows mutex held for the lifetime of a *running* KlarPDF.
#:
#: ``packaging/app/installer.iss`` names this same string in its ``AppMutex`` directive. Inno's Setup
#: **and** uninstaller check for it and refuse to proceed while it exists, telling the user to close
#: the app — which is the only thing that prevents the two artifacts seen at v0.10.0 (see RELEASE.md):
#: Windows will not let the uninstaller delete a running ``.exe``, and a still-live process rewrites
#: ``view_state.json`` on shutdown, *recreating* the config directory ``[UninstallDelete]`` just wiped.
#:
#: The single-instance guard cannot serve here: it is a ``QLocalServer`` named pipe, which Inno cannot
#: see. ``tests/test_app_mutex.py`` asserts this constant and the ``.iss`` never drift apart — a rename
#: on one side would silently disable the guard on the other, and nothing else would notice.
#:
#: Session namespace (no ``Global\``) on purpose: the install is per-user, so one user's running app
#: must not block another user's installer.
APP_MUTEX_NAME = "KlarPDF-AppMutex"

# Held for the process lifetime. Module-level so the handle is never garbage-collected — closing it
# would release the mutex and silently re-open the window the installer is meant to be locked out of.
_app_mutex_handle: int | None = None


def single_instance_server_name() -> str:
    """A stable, per-user IPC name so two users on one machine don't collide."""
    try:
        user = getpass.getuser()
    except Exception:  # getuser can raise if the environment has no user info
        user = "default"
    return f"klarpdf-singleton-{user}"


def acquire_app_mutex(name: str = APP_MUTEX_NAME) -> bool:
    """Hold :data:`APP_MUTEX_NAME` for this process's lifetime. No-op off Windows.

    Call it only from a launch that is going to **stay running** (the resident instance, or the
    degraded no-server fallback) — never from a forwarding launch, which exits in milliseconds and
    would take the mutex down with it, briefly unlocking the installer.

    Returns True if a handle is held. Owning the mutex vs. merely opening an existing one does not
    matter: Inno's ``AppMutex`` tests for *existence*, so any live handle keeps the door shut. The
    OS closes the handle when the process dies, which is exactly the lifetime we want, so there is
    deliberately no release function — an app that could drop the mutex while still showing a window
    would be worse than none.

    Never raises: a missing mutex degrades to today's behaviour (installer proceeds, files stay
    locked, config dir gets recreated). That is a bad outcome, not a crashed app.
    """
    global _app_mutex_handle
    if sys.platform != "win32":
        return False
    if _app_mutex_handle is not None:
        return True  # idempotent: a second call must not leak a second handle
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return False
        _app_mutex_handle = handle
        return True
    except Exception:  # pragma: no cover - ctypes/kernel32 unavailable
        return False


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


#: Qt console lines known to be harmless on Wayland. :func:`quiet_harmless_qt_lines` drops a
#: message only when its whole text is one of these, so every other message still prints.
#:
#: * Clicking an open menu's title again makes Qt's menu bar ask to hold the mouse until the button
#:   comes up, then let go. Qt's Wayland support allows that only for a popup, and the menu bar is
#:   part of the main window, so each such click printed this line twice. The menus work normally
#:   (``PLAN.md`` §M153).
HARMLESS_WAYLAND_LINES = frozenset({
    "This plugin supports grabbing the mouse only for popup windows",
})

# The message handler that was in place before ours. None is Qt's own printer.
_passed_on = None


def quiet_harmless_qt_lines(app) -> bool:
    """On Wayland, stop Qt printing :data:`HARMLESS_WAYLAND_LINES`. No-op elsewhere.

    Returns whether the filter is on. PySide takes Python's lock to run the filter, on whichever
    thread printed the message. A Qt thread that printed while the main thread held that lock and
    waited for it would stall both. So the filter stays off on Windows, where the app ships and
    these lines never appear.
    """
    if not app.platformName().startswith("wayland"):
        return False
    _install_line_filter()
    return True


def _install_line_filter() -> None:
    global _passed_on
    from PySide6.QtCore import qInstallMessageHandler

    previous = qInstallMessageHandler(_filter_qt_line)
    if previous is not _filter_qt_line:  # a second install must not make it call itself
        _passed_on = previous


def _filter_qt_line(msg_type, context, message: str) -> None:
    if message in HARMLESS_WAYLAND_LINES:
        return
    if _passed_on is not None:
        _passed_on(msg_type, context, message)
    elif sys.stderr is not None:
        # What Qt's own printer does: format the line as Qt would, then write it.
        from PySide6.QtCore import qFormatLogMessage

        print(qFormatLogMessage(msg_type, context, message), file=sys.stderr, flush=True)
