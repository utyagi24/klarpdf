"""Stamp + watermark dialogs (PLAN.md §R4, M62).

Two dialogs over **one** model descriptor (:class:`model.content_marks.Stamp`), because a watermark
*is* a stamp with ``under=True`` — the dialogs differ only in which knobs they surface and how the
result is placed:

* :class:`StampDialog` — text · colour · angle · opacity · frame, plus an optional **page range**
  ("initials on every page"). The result is *armed*, then dragged onto the page: a stamp goes
  somewhere specific, so the user picks where.
* :class:`WatermarkDialog` — preset/text · colour · translucency · diagonal · page range. Applied
  **immediately**, full-page, to the whole range: a watermark covers the page, so there is nothing
  to place and asking for a drag would be busywork.

Both are built lazily (the house lightness rule — nothing on the open-document path) and both state
the guarantee boundary in the dialog itself: these marks **bake into the page at save** and stop
being editable then, which is the R4 honesty item.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from model.content_marks import (
    STAMP_PRESETS,
    WATERMARK_DEFAULTS,
    WATERMARK_PRESETS,
    Stamp,
)
from util.page_range import PageRangeError, parse_page_range
from viewer.markup_style import swatch_icon

CUSTOM = "Custom…"

# Said in the dialog, not just the docs: a content mark is a point of no return at Save (PLAN.md
# §Design budgets → Honesty principle). The wording matches the save-time confirm.
_BAKE_NOTE = ("Stamps are drawn into the page when you save. Until then you can move, resize "
              "and undo them; afterwards they are part of the page.")


class _ColorButton(QToolButton):
    """A swatch that opens the system colour picker. Shares :func:`swatch_icon` with the markup
    style button, so a colour reads the same everywhere in the app."""

    def __init__(self, color: tuple[float, float, float], parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setAutoRaise(True)
        self._refresh()
        self.clicked.connect(self._pick)

    def color(self) -> tuple[float, float, float]:
        return self._color

    def set_color(self, color: tuple[float, float, float]) -> None:
        self._color = color
        self._refresh()

    def _refresh(self) -> None:
        self.setIcon(swatch_icon(self._color))

    def _pick(self) -> None:
        from PySide6.QtGui import QColor

        chosen = QColorDialog.getColor(QColor.fromRgbF(*self._color), self, "Stamp colour")
        if chosen.isValid():
            self.set_color((chosen.redF(), chosen.greenF(), chosen.blueF()))


class _PageRangeField(QWidget):
    """"All pages" / a range box — the shared scope control for both dialogs.

    Kept as one widget so the two dialogs cannot drift apart on what "pages" means, and so M64's
    search-&-redact scope can reuse it.
    """

    def __init__(self, page_count: int, current_page: int, parent=None) -> None:
        super().__init__(parent)
        self._page_count = page_count
        self._current_page = current_page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scope = QComboBox()
        self.scope.addItems(["This page", "All pages", "Pages…"])
        self.text = QLineEdit()
        self.text.setPlaceholderText(f"e.g. 1-3, 7, 10-  (of {page_count})")
        self.text.setVisible(False)
        layout.addWidget(self.scope)
        layout.addWidget(self.text)
        self.scope.currentIndexChanged.connect(
            lambda index: self.text.setVisible(index == 2)
        )

    def set_default_all(self) -> None:
        self.scope.setCurrentIndex(1)

    def pages(self) -> list[int]:
        """The selected 0-based page indices. Raises :class:`PageRangeError` on a bad range."""
        index = self.scope.currentIndex()
        if index == 0:
            return [self._current_page]
        if index == 1:
            return list(range(self._page_count))
        return parse_page_range(self.text.text(), self._page_count)


class _StampDialogBase(QDialog):
    """Shared plumbing: preset combo, text, colour, opacity, page range, and the bake note."""

    def __init__(self, parent, page_count: int, current_page: int, title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._form = QFormLayout()
        self.presets = QComboBox()
        self.text = QLineEdit()
        self.color = _ColorButton((0.80, 0.10, 0.10))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(100)
        self.opacity_label = QLabel("100%")
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.pages = _PageRangeField(page_count, current_page)
        self.presets.currentTextChanged.connect(self._on_preset)

        layout = QVBoxLayout(self)
        layout.addLayout(self._form)
        note = QLabel(_BAKE_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_preset(self, name: str) -> None:
        raise NotImplementedError

    def _opacity(self) -> float:
        return self.opacity.value() / 100.0

    def selected_pages(self) -> list[int]:
        """The chosen pages, or ``None`` after reporting a bad range to the user."""
        from PySide6.QtWidgets import QMessageBox

        try:
            return self.pages.pages()
        except PageRangeError as exc:
            QMessageBox.warning(self, "Pages", str(exc))
            return None


class StampDialog(_StampDialogBase):
    """Compose a stamp; the caller then arms placement and the user drags its box."""

    def __init__(self, parent, page_count: int, current_page: int) -> None:
        super().__init__(parent, page_count, current_page, "Add Stamp")
        self.presets.addItems([*STAMP_PRESETS, CUSTOM])
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-180.0, 180.0)
        self.angle.setSuffix("°")
        self.frame = QCheckBox("Draw a frame around the text")
        self.frame.setChecked(True)
        self._form.addRow("Preset", self.presets)
        self._form.addRow("Text", self.text)
        self._form.addRow("Colour", self.color)
        self._form.addRow("Angle", self.angle)
        self._form.addRow("Opacity", self._opacity_row())
        self._form.addRow("", self.frame)
        self._form.addRow("Apply to", self.pages)
        self._on_preset(self.presets.currentText())

    def _opacity_row(self) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.opacity)
        layout.addWidget(self.opacity_label)
        return row

    def _on_preset(self, name: str) -> None:
        """A preset just **prefills the fields** — it is not a separate kind of stamp (Way 2). So
        the text and colour stay editable afterwards, and ``Custom…`` simply stops overwriting them."""
        fields = STAMP_PRESETS.get(name)
        if not fields:
            return
        self.text.setText(fields["text"])
        self.color.set_color(fields["color"])

    def stamp(self) -> Stamp:
        """The composed descriptor, at a placeholder rect — the caller supplies the dragged box."""
        return Stamp(
            rect=(0.0, 0.0, 1.0, 1.0),
            text=self.text.text().strip() or "STAMP",
            color=self.color.color(),
            border_width=3.0 if self.frame.isChecked() else 0.0,
            angle=self.angle.value(),
            opacity=self._opacity(),
        )


class WatermarkDialog(_StampDialogBase):
    """Compose a watermark; the caller applies it full-page across the range immediately."""

    def __init__(self, parent, page_count: int, current_page: int) -> None:
        super().__init__(parent, page_count, current_page, "Add Watermark")
        self.presets.addItems([*WATERMARK_PRESETS, CUSTOM])
        self.color.set_color(WATERMARK_DEFAULTS["color"])
        self.opacity.setValue(int(WATERMARK_DEFAULTS["opacity"] * 100))
        self.diagonal = QCheckBox("Diagonal")
        self.diagonal.setChecked(True)
        self._form.addRow("Preset", self.presets)
        self._form.addRow("Text", self.text)
        self._form.addRow("Colour", self.color)
        self._form.addRow("Translucency", self._opacity_row())
        self._form.addRow("", self.diagonal)
        self._form.addRow("Pages", self.pages)
        self.pages.set_default_all()          # a watermark is a whole-document mark by default
        self._on_preset(self.presets.currentText())

    def _opacity_row(self) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.opacity)
        layout.addWidget(self.opacity_label)
        return row

    def _on_preset(self, name: str) -> None:
        fields = WATERMARK_PRESETS.get(name)
        if fields:
            self.text.setText(fields["text"])

    def watermark(self, rect: tuple[float, float, float, float]) -> Stamp:
        """The composed descriptor covering ``rect`` (the page). ``under=True`` is what makes it a
        watermark rather than a stamp — the page's own content paints on top of it."""
        return Stamp(
            rect=rect,
            text=self.text.text().strip() or "WATERMARK",
            color=self.color.color(),
            border_width=0.0,                              # a frame would read as a stamp
            angle=-45.0 if self.diagonal.isChecked() else 0.0,
            opacity=self._opacity(),
            under=True,
        )
