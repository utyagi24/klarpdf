"""Editable zoom-percentage combo, two-way bound to a :class:`~viewer.pdf_view.PdfView` (M11).

Shows the live magnification (``150%``) and lets the user pick a preset or type a percentage. The
binding is one source of truth — the view's zoom: selecting/typing calls ``view.set_zoom`` and the
view's ``zoomChanged`` signal drives the displayed text, so fit-width/zoom-in/keyboard all keep the
indicator in sync. Programmatic text updates use ``setEditText`` (which does not emit ``activated``),
so there is no feedback loop.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

# Preset zoom factors offered in the dropdown (1.0 == 100%).
_PRESETS = (0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00, 3.00, 4.00)


class ZoomWidget(QComboBox):
    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self._view = view
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # typed values never grow the list
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setToolTip("Zoom level — pick a preset or type a percentage")
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(72)
        for factor in _PRESETS:
            self.addItem(f"{round(factor * 100)}%", factor)

        self.activated.connect(self._apply_index)              # user chose a preset
        self.lineEdit().editingFinished.connect(self._apply_text)  # user typed a value
        view.zoomChanged.connect(self.show_zoom)
        self.show_zoom(view.zoom)

    def show_zoom(self, zoom: float) -> None:
        """Reflect the view's current zoom in the edit field (no signal feedback)."""
        text = f"{round(zoom * 100)}%"
        if self.lineEdit().text() != text:
            self.setEditText(text)

    def _apply_index(self, index: int) -> None:
        factor = self.itemData(index)
        if factor is not None:
            self._view.set_zoom(float(factor))

    def _apply_text(self) -> None:
        raw = self.lineEdit().text().strip().rstrip("%").strip()
        try:
            percent = float(raw)
        except ValueError:
            self.show_zoom(self._view.zoom)  # ignore garbage, restore the live value
            return
        self._view.set_zoom(percent / 100.0)
        self.show_zoom(self._view.zoom)  # echo back the clamped result
