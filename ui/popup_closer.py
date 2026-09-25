"""Close every popup that Qt only hid, so none stays on a WSLg desktop (M153, #388).

On WSL, a list or menu that Qt hides instead of closing stays on the desktop until the app quits.
Qt 6.11 on Wayland hides a window by taking its picture away but keeps its surface (the display
server's handle for the window); closing destroys the surface. WSLg keeps drawing a surface that
still exists.

Qt closes most popups, but it only hides these two:

* a combo box's list, when a value is chosen, on Enter, or on a click outside the list
  (``QComboBox.hidePopup``);
* a menu bar's menu, when its title is clicked again, when the pointer moves to another title,
  or when an arrow key moves to the next menu (``QMenuBar``).

Esc, choosing a menu item and clicking elsewhere all close the popup. A toolbar button's menu is
always closed, which is why those never stayed.

:class:`PopupCloser` sees every event in the app. When a popup is hidden, it closes the popup one
event-loop turn later, if it is still hidden. Closing a popup that is already closed does
nothing, and the next time the popup opens Qt makes a new surface. ``PdfApp`` installs it. It is
not WSL-only code: closing a hidden popup is what Esc already does on every platform. See
``PLAN.md`` §M153 and ``tests/test_popup_closer.py``.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget

_HIDE = QEvent.Type.Hide
_POPUP = Qt.WindowType.Popup


class PopupCloser(QObject):
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Every event in the app passes through here, so the cheapest test goes first. Measured:
        # this adds 0.4 µs to an event.
        if event.type() == _HIDE and obj.isWidgetType() and obj.windowType() == _POPUP:
            # Not now: Qt is still hiding it, and the menu bar opens the next menu straight
            # after. The timer belongs to the popup, so it is dropped if the popup is deleted
            # first.
            QTimer.singleShot(0, obj, partial(_close_if_hidden, obj))
        return False


def _close_if_hidden(popup: QWidget) -> None:
    if not popup.isVisible():
        popup.close()


def install(app: QApplication) -> PopupCloser:
    """Start closing hidden popups in ``app``. Calling it again changes nothing."""
    closer = app.findChild(PopupCloser)
    if closer is None:
        closer = PopupCloser(app)
        app.installEventFilter(closer)
    return closer
