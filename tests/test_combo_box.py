"""A combo box's list is closed, not only hidden, however it goes away (M153, #388). Offscreen GUI.

On WSLg a list that was only hidden stayed on the desktop until the app quit: Qt keeps a hidden
window's Wayland surface, and WSLg keeps drawing it. Closing the window destroys the surface.
These tests watch for the window's native surface being destroyed, which is what the reader's
desktop depends on. The offscreen platform has no compositor to show a leftover list, so the
surface is the thing to check. See ``PLAN.md`` §M153.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QPlatformSurfaceEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from app import PdfApp
from klarpdf.model.virtual_document import VirtualDocument
from viewer.combo_box import ComboBox
from viewer.pdf_view import PdfView
from viewer.zoom_widget import ZoomWidget

ROOT = Path(__file__).resolve().parents[1]


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


@pytest.fixture
def host(qapp):
    widget = QWidget()
    widget.resize(300, 200)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def _open(combo) -> tuple[QWidget, _SurfaceWatch]:
    """Open the list and start watching its window."""
    combo.showPopup()
    container = combo.view().window()
    assert container.isVisible()
    watch = _SurfaceWatch()
    container.windowHandle().installEventFilter(watch)
    # Qt ignores a release on the list for one double-click interval after it opens under the
    # pointer, so the click that opened it cannot also choose from it.
    QTest.qWait(QApplication.doubleClickInterval() + 50)
    return container, watch


def _choose(combo, row: int) -> None:
    view = combo.view()
    # Near the item's left edge: a list not yet laid out reports items wider than itself, and Qt
    # ignores a release outside the list.
    point = QPoint(4, view.visualRect(view.model().index(row, 0)).center().y())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def _combo(host, cls=ComboBox) -> QComboBox:
    combo = cls(host)
    combo.addItems(["One", "Two", "Three"])
    combo.move(10, 10)
    combo.show()
    return combo


def test_choosing_a_value_closes_the_list(host):
    combo = _combo(host)
    chosen = []
    combo.activated.connect(chosen.append)
    container, watch = _open(combo)

    _choose(combo, 2)

    assert chosen == [2]
    assert combo.currentIndex() == 2
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1


def test_a_plain_qcombobox_only_hides_its_list(host):
    """The control: Qt's own combo box keeps the surface. If this ever fails, Qt has started
    closing the list itself and ``ComboBox`` can be retired."""
    combo = _combo(host, QComboBox)
    container, watch = _open(combo)

    _choose(combo, 1)

    assert not container.isVisible()
    assert watch.destroyed_surfaces == 0


def test_enter_and_a_click_outside_close_the_list_too(host):
    combo = _combo(host)
    container, watch = _open(combo)
    QTest.keyClick(combo.view(), Qt.Key.Key_Down)
    QTest.keyClick(combo.view(), Qt.Key.Key_Return)
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1

    # A click outside the list reaches Qt as a press on the list's window.
    container, watch = _open(combo)
    QTest.mousePress(container, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     container.rect().bottomRight() + container.rect().bottomRight())
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1


def test_the_list_opens_again_after_it_was_closed(host):
    combo = _combo(host)
    _open(combo)
    _choose(combo, 1)

    container, watch = _open(combo)
    assert container.isVisible()
    _choose(combo, 0)
    assert combo.currentIndex() == 0
    assert watch.destroyed_surfaces == 1


def test_choosing_a_zoom_closes_the_zoom_list(qapp, a_pdf):
    view = PdfView(VirtualDocument.from_path(a_pdf))
    view.resize(600, 800)
    widget = ZoomWidget(view)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    container, watch = _open(widget)

    _choose(widget, widget.findText("300%"))

    assert view.zoom == pytest.approx(3.0)
    assert not container.isVisible()
    assert watch.destroyed_surfaces == 1
    widget.close()
    widget.deleteLater()
    view.deleteLater()


def test_no_plain_qcombobox_in_the_app():
    """Every combo box in the app is a ``ComboBox``, so no list can be left on a WSLg desktop."""
    folders = ["viewer", "ui", "organize", "store"]
    files = [p for f in folders for p in (ROOT / f).rglob("*.py")] + sorted(ROOT.glob("*.py"))
    plain = re.compile(r"\bQComboBox\s*\(|\(\s*QComboBox\s*\)")
    offenders = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in files if p.name != "combo_box.py"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if plain.search(line)
    ]
    assert offenders == [], f"use viewer.combo_box.ComboBox instead of QComboBox: {offenders}"
