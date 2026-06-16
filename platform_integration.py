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
    return f"pdfproj-singleton-{user}"


def activate_window(window) -> None:
    """Bring ``window`` to the front and give it focus (one-window-per-document raise)."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()

    if sys.platform == "win32":
        # A background process can't always steal focus on Windows; a brief always-on-top
        # toggle reliably pulls the window forward. Validated on real Windows in M7.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        window.show()
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        window.show()
        QApplication.alert(window)


def register_file_association(exe_path: str | None = None) -> None:
    """Register pdfproj as a .pdf handler.

    On Windows this is a no-op: the installer writes the HKCU ProgID + association (PLAN.md,
    Packaging). It exists for a future Linux ``xdg-mime`` path and an optional dev convenience.
    """
    return None
