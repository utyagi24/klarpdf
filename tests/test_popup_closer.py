"""A list or menu that Qt only hides is closed (M153, #388). Offscreen GUI.

On WSLg a popup that was only hidden stayed on the desktop until the app quit: Qt keeps a hidden
window's Wayland surface, and WSLg keeps drawing it. Closing the window destroys the surface.
These tests watch for the popup's surface being destroyed, which is what the reader's desktop
depends on. The offscreen platform has no compositor to show a leftover popup, so the surface is
the thing to check. See ``ui/popup_closer.py`` and ``PLAN.md`` §M153.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPlatformSurfaceEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QMainWindow, QMenu, QMenuBar, QWidget

from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from store.settings import Settings
from ui.popup_closer import PopupCloser
from viewer.pdf_view import PdfView
from viewer.zoom_widget import ZoomWidget

_LEFT = Qt.MouseButton.LeftButton
_NO_KEYS = Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


class _SurfaceWatch(QObject):
    """Counts the native surfaces a window releases."""

    def __init__(self) -> None:
        super().__init__()
        self.destroyed_surfaces = 0

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QEvent.Type.PlatformSurface
                and event.surfaceEventType()
                == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed):
            self.destroyed_surfaces += 1
        return False


def _watch(popup: QWidget) -> _SurfaceWatch:
    watch = _SurfaceWatch()
    popup.windowHandle().installEventFilter(watch)
    return watch


def _settle() -> None:
    # The closer acts one event-loop turn after the hide.
    QTest.qWait(20)


@contextmanager
def _without_closer(qapp):
    closer = qapp.findChild(PopupCloser)
    assert closer is not None, "PdfApp did not install the popup closer"
    qapp.removeEventFilter(closer)
    try:
        yield
    finally:
        qapp.installEventFilter(closer)


# ---- combo box lists -------------------------------------------------------------------------


@pytest.fixture
def host(qapp):
    widget = QWidget()
    widget.resize(300, 200)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def _combo(host) -> QComboBox:
    combo = QComboBox(host)
    combo.addItems(["One", "Two", "Three"])
    combo.move(10, 10)
    combo.show()
    return combo


def _open(combo) -> tuple[QWidget, _SurfaceWatch]:
    """Open the list and start watching its window."""
    combo.showPopup()
    container = combo.view().window()
    assert container.isVisible()
    watch = _watch(container)
    # Qt ignores a release on the list for one double-click interval after it opens under the
    # pointer, so the click that opened it cannot also choose from it.
    QTest.qWait(QApplication.doubleClickInterval() + 50)
    return container, watch


def _choose(combo, row: int) -> None:
    view = combo.view()
    # Near the item's left edge: a list not yet laid out reports items wider than itself, and Qt
    # ignores a release outside the list.
    point = QPoint(4, view.visualRect(view.model().index(row, 0)).center().y())
    QTest.mouseClick(view.viewport(), _LEFT, _NO_KEYS, point)


def test_choosing_a_value_closes_the_list(host):
    combo = _combo(host)
    chosen = []
    combo.activated.connect(chosen.append)
    container, watch = _open(combo)

    _choose(combo, 2)
    _settle()

    assert chosen == [2]
    assert combo.currentIndex() == 2
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1


def test_enter_and_a_click_outside_close_the_list_too(host):
    combo = _combo(host)
    container, watch = _open(combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    _settle()
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1

    # A click outside the list reaches Qt as a press on the list's window.
    container, watch = _open(combo)
    QTest.mousePress(container, _LEFT, _NO_KEYS,
                     container.rect().bottomRight() + container.rect().bottomRight())
    _settle()
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1


def test_the_list_opens_again_after_it_was_closed(host):
    combo = _combo(host)
    _open(combo)
    _choose(combo, 1)
    _settle()

    container, watch = _open(combo)
    assert container.isVisible()
    _choose(combo, 0)
    _settle()
    assert combo.currentIndex() == 0
    assert watch.destroyed_surfaces == 1


def test_a_list_opened_again_at_once_is_left_open(host):
    """The closer acts a turn later, and only on a popup that is still hidden then."""
    combo = _combo(host)
    container, watch = _open(combo)

    combo.hidePopup()
    combo.showPopup()
    _settle()

    assert container.isVisible()
    assert watch.destroyed_surfaces == 0
    combo.hidePopup()


def test_choosing_a_zoom_closes_the_zoom_list(qapp, a_pdf):
    view = PdfView(VirtualDocument.from_path(a_pdf))
    view.resize(600, 800)
    widget = ZoomWidget(view)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    container, watch = _open(widget)

    _choose(widget, widget.findText("300%"))
    _settle()

    assert view.zoom == pytest.approx(3.0)
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1
    widget.close()
    widget.deleteLater()
    view.deleteLater()


def test_a_list_inside_a_qt_dialog_closes_too(qapp, tmp_path):
    """Qt builds the combo boxes in its own dialogs. On Linux, the Open and Save dialogs are
    Qt's own, and nothing the app creates could reach their lists."""
    dialog = QFileDialog(None, "Open PDF", str(tmp_path), "PDF files (*.pdf);;All files (*)")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)
    combo = dialog.findChild(QComboBox, "fileTypeCombo")
    container, watch = _open(combo)

    _choose(combo, 1)
    _settle()

    assert combo.currentText() == "All files (*)"
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1
    dialog.close()
    dialog.deleteLater()


# ---- menu bar menus --------------------------------------------------------------------------


@pytest.fixture
def bar(qapp):
    window = QMainWindow()
    window.resize(400, 300)
    for name in ("File", "Edit", "View"):
        window.menuBar().addMenu(name).addAction(f"{name} item")
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window.menuBar()
    window.close()
    window.deleteLater()


def _menus(bar: QMenuBar) -> list[QMenu]:
    # Not through ``action.menu()``: measured, a menu reached through a temporary action object
    # can raise "already deleted" once that action object is dropped. The bar also owns a menu
    # of its own that is not on it, for titles that do not fit.
    on_bar = bar.actions()
    children = bar.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly)
    return [menu for menu in children if menu.menuAction() in on_bar]


def _title(bar: QMenuBar, menu: QMenu) -> QPoint:
    return bar.actionGeometry(menu.menuAction()).center()


def _open_menu(bar: QMenuBar, menu: QMenu) -> _SurfaceWatch:
    QTest.mouseClick(bar, _LEFT, _NO_KEYS, _title(bar, menu))
    assert menu.isVisible(), f"{menu.title()!r} did not open"
    return _watch(menu)


def _mouse_over_title(bar, open_menu, title_of, kind, button=Qt.MouseButton.NoButton) -> None:
    """Give the open menu a mouse event over a title, as its mouse grab would."""
    at = QPointF(bar.mapToGlobal(_title(bar, title_of)))
    event = QMouseEvent(kind, open_menu.mapFromGlobal(at), at, button, button, _NO_KEYS)
    QApplication.sendEvent(open_menu, event)


def _click_title_again(bar, menu) -> None:
    # The open menu hands the press to the menu bar, which then takes the release itself.
    _mouse_over_title(bar, menu, menu, QEvent.Type.MouseButtonPress, _LEFT)
    QTest.mouseRelease(bar, _LEFT, _NO_KEYS, _title(bar, menu))


def test_clicking_an_open_menus_title_again_closes_the_menu(bar):
    file = _menus(bar)[0]
    watch = _open_menu(bar, file)

    _click_title_again(bar, file)
    _settle()

    assert not file.isVisible()
    assert watch.destroyed_surfaces == 1


@pytest.mark.parametrize("how", ["pointer", "arrow key"])
def test_moving_to_the_next_menu_closes_the_first(bar, how):
    file, edit = _menus(bar)[:2]
    watch = _open_menu(bar, file)

    if how == "pointer":
        _mouse_over_title(bar, file, edit, QEvent.Type.MouseMove)
    else:
        QTest.keyClick(file, Qt.Key.Key_Right)
    _settle()

    assert edit.isVisible()
    assert not file.isVisible()
    assert watch.destroyed_surfaces == 1
    QTest.keyClick(edit, Qt.Key.Key_Escape)


def test_a_closed_menu_opens_again_and_works(bar):
    file = _menus(bar)[0]
    _open_menu(bar, file)
    _click_title_again(bar, file)
    _settle()
    chosen = []
    file.triggered.connect(chosen.append)

    _open_menu(bar, file)
    QTest.keyClick(file, Qt.Key.Key_Down)
    QTest.keyClick(file, Qt.Key.Key_Return)

    assert [action.text() for action in chosen] == ["File item"]


@pytest.fixture
def app_window(qapp, a_pdf, tmp_path):
    settings, qapp.settings = qapp.settings, Settings(tmp_path / "settings.json")
    window = qapp.open_document(a_pdf)
    QTest.qWaitForWindowExposed(window)
    yield window
    window.undo_stack.setClean()
    window.close()
    qapp._windows.clear()
    qapp.settings = settings


def test_every_menu_of_the_app_closes_when_its_title_is_clicked_again(app_window):
    """The reader's report: File, Edit, View, Tools and Help all stayed on the desktop."""
    bar = app_window.menuBar()
    menus = _menus(bar)
    assert len(menus) >= 5
    for menu in menus:
        watch = _open_menu(bar, menu)
        _click_title_again(bar, menu)
        _settle()
        assert not menu.isVisible(), menu.title()
        assert watch.destroyed_surfaces == 1, menu.title()


# ---- the control -----------------------------------------------------------------------------


def test_without_the_closer_qt_only_hides_them(qapp, host, bar):
    """If this fails, Qt has started closing these popups itself, and the closer can go."""
    with _without_closer(qapp):
        combo = _combo(host)
        container, watch = _open(combo)
        _choose(combo, 1)
        _settle()
        assert not container.isVisible()
        assert watch.destroyed_surfaces == 0

        file = _menus(bar)[0]
        watch = _open_menu(bar, file)
        _click_title_again(bar, file)
        _settle()
        assert not file.isVisible()
        assert watch.destroyed_surfaces == 0
