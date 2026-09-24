"""A combo box whose list is closed, not only hidden, when it goes away (M153, #388).

Qt takes a combo box's list away in two ways. **Esc closes it**: the list's window is destroyed.
**Choosing a value, Enter, or a click outside the list hides it**: ``QComboBox.hidePopup`` calls
``hide()``, and the window is kept for the next time. On Wayland, Qt 6.11 hides a window by
removing its role and attaching no buffer, and keeps the surface; closing destroys the surface.
WSLg keeps drawing a surface that is hidden but still alive, so a chosen value left the list's
picture on the desktop until the app quit. Menus and tooltips close, which is why only the lists
showed it.

:class:`ComboBox` closes the list after Qt hides it, so every way out ends the way Esc does. It
is not WSL-only code: closing a hidden window is what Esc already does on every platform, and
the next open creates the window again. Every combo box in the app is one of these;
``tests/test_combo_box.py`` fails on a plain ``QComboBox``. See ``PLAN.md`` §M153.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class ComboBox(QComboBox):
    def hidePopup(self) -> None:
        # Qt calls this before it reports the choice (``activated``), so the list is gone before
        # anything the choice starts.
        container = self.view().window()
        was_open = container.isVisible()
        super().hidePopup()
        if was_open and not container.isVisible():
            container.close()
