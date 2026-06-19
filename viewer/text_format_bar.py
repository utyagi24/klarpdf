"""The small formatting bar that floats over the inline text-box editor (PLAN.md, M27).

A text box's *style* — font family / size, text colour, box fill, box outline — is edited through
this bar while the inline editor is open, then carried onto the committed
:class:`~model.page_edits.TextBox`. The bar is deliberately built from **focus-less**
``QToolButton``s with pop-up menus rather than combo boxes / spin boxes: the editor commits on
focus-out, so a control that stole keyboard focus would close the editor the instant it was
clicked. Only the two colour pickers are modal (they *do* deactivate the editor) — those are wrapped
by the overlay's ``before_modal`` / ``after_modal`` hooks, which suspend the focus-out commit and
hand focus back to the editor afterwards.

:class:`TextBoxStyle` is the immutable bundle the bar reads and writes; the overlay merges it with a
box's geometry + text to build the descriptor. :func:`qt_font` maps a base-14 family name to a Qt
font (with a style hint, so it substitutes sensibly where the named face is absent, e.g. WSLg).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QMenu,
    QToolButton,
    QWidget,
)

# base-14 family selector -> (Qt family, style hint, menu label). The hint drives substitution when
# the exact face is missing (common on Linux/WSLg), so the on-screen preview stays sans/serif/mono.
_FAMILIES = (
    ("helv", "Helvetica", QFont.StyleHint.SansSerif, "Helvetica"),
    ("tiro", "Times New Roman", QFont.StyleHint.Serif, "Times"),
    ("cour", "Courier New", QFont.StyleHint.TypeWriter, "Courier"),
)
_SIZES = (8, 9, 10, 11, 12, 14, 16, 18, 24, 36, 48)
_DEFAULT_FILL = (1.0, 0.94, 0.6)  # pale yellow — the colour a freshly-enabled fill starts at
_OUTLINE_WIDTH = 1.0              # the (black) width a freshly-enabled outline bakes at, in points


@dataclass(frozen=True)
class TextBoxStyle:
    """The styling carried by a :class:`~model.page_edits.TextBox` (everything but rect + text)."""

    fontname: str = "helv"
    fontsize: float = 11.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fill_color: tuple[float, float, float] | None = None
    border_width: float = 0.0

    @classmethod
    def from_textbox(cls, box) -> "TextBoxStyle":
        return cls(box.fontname, box.fontsize, box.color, box.fill_color, box.border_width)


def _qt_family(fontname: str) -> tuple[str, QFont.StyleHint]:
    for key, family, hint, _label in _FAMILIES:
        if key == fontname:
            return family, hint
    return _FAMILIES[0][1], _FAMILIES[0][2]


def qt_font(fontname: str, pixel_size: float) -> QFont:
    """A Qt font for ``fontname`` at ``pixel_size`` px — used by the editor and the scene preview so
    both render in the same family the saved annotation will."""
    family, hint = _qt_family(fontname)
    font = QFont(family)
    font.setStyleHint(hint)
    font.setPixelSize(max(1, int(round(pixel_size))))
    return font


def _rgb(color: QColor) -> tuple[float, float, float]:
    return (color.redF(), color.greenF(), color.blueF())


class TextFormatBar(QWidget):
    """A floating row of focus-less buttons that edits a :class:`TextBoxStyle`.

    Emits :attr:`styleChanged` whenever the user changes any control. :meth:`set_style` loads a
    style into the controls *without* emitting (so re-opening the bar on an existing box doesn't
    look like an edit).
    """

    styleChanged = Signal(object)  # the new TextBoxStyle

    def __init__(self, parent, before_modal=None, after_modal=None) -> None:
        super().__init__(parent)
        self._before_modal = before_modal or (lambda: None)
        self._after_modal = after_modal or (lambda: None)
        self._loading = False
        self._style = TextBoxStyle()
        self._fill_rgb = _DEFAULT_FILL  # remembered across toggles, so re-enabling restores it

        # A child of the viewport that should never grab the editor's keyboard focus.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "QWidget { background: palette(window); }"
            "QToolButton { border: 1px solid rgba(128,128,128,90); border-radius: 4px;"
            " padding: 2px 6px; margin: 0; }"
            "QToolButton:hover { background: rgba(128,128,128,46); }"
            "QToolButton:checked { background: rgba(128,128,128,90); }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 2, 3, 2)
        row.setSpacing(2)

        self._family_btn = self._menu_button("Helvetica", "Font family")
        self._family_menu = QMenu(self._family_btn)
        group = QActionGroup(self._family_menu)
        for key, _family, _hint, label in _FAMILIES:
            act = self._family_menu.addAction(label)
            act.setCheckable(True)
            act.setData(key)
            group.addAction(act)
            act.triggered.connect(lambda _checked, k=key: self._set_family(k))
        self._family_btn.setMenu(self._family_menu)
        row.addWidget(self._family_btn)

        self._size_btn = self._menu_button("11", "Font size")
        self._size_menu = QMenu(self._size_btn)
        for pt in _SIZES:
            act = self._size_menu.addAction(str(pt))
            act.triggered.connect(lambda _checked, p=pt: self._set_size(p))
        self._size_btn.setMenu(self._size_menu)
        row.addWidget(self._size_btn)

        self._text_btn = self._tool_button("A", "Text colour")
        self._text_btn.clicked.connect(self._pick_text_color)
        row.addWidget(self._text_btn)

        self._fill_btn = self._tool_button("Fill", "Box fill (off / pick a colour)")
        self._fill_btn.setCheckable(True)
        self._fill_btn.clicked.connect(self._toggle_fill)
        row.addWidget(self._fill_btn)

        self._outline_btn = self._tool_button("Outline", "Box outline (black, on/off)")
        self._outline_btn.setCheckable(True)
        self._outline_btn.clicked.connect(self._toggle_outline)
        row.addWidget(self._outline_btn)

        self.adjustSize()
        self._refresh_swatches()

    # ---- construction helpers ---------------------------------------------------

    def _tool_button(self, text: str, tip: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(text)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # never steal the editor's focus
        return btn

    def _menu_button(self, text: str, tip: str) -> QToolButton:
        btn = self._tool_button(text, tip)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return btn

    # ---- state in / out ---------------------------------------------------------

    def style(self) -> TextBoxStyle:
        return self._style

    def set_style(self, style: TextBoxStyle) -> None:
        """Load ``style`` into the controls without emitting :attr:`styleChanged`."""
        self._loading = True
        self._style = style
        if style.fill_color is not None:
            self._fill_rgb = style.fill_color
        for _key, _family, _hint, label in _FAMILIES:
            if _key == style.fontname:
                self._family_btn.setText(label)
        self._size_btn.setText(str(int(round(style.fontsize))))
        self._fill_btn.setChecked(style.fill_color is not None)
        self._outline_btn.setChecked(style.border_width > 0)
        self._refresh_swatches()
        self._loading = False

    def _emit(self) -> None:
        if self._loading:
            return
        self._style = self._build()
        self.styleChanged.emit(self._style)

    def _build(self) -> TextBoxStyle:
        return TextBoxStyle(
            fontname=self._style.fontname,
            fontsize=self._style.fontsize,
            color=self._style.color,
            fill_color=self._fill_rgb if self._fill_btn.isChecked() else None,
            border_width=_OUTLINE_WIDTH if self._outline_btn.isChecked() else 0.0,
        )

    # ---- control slots ----------------------------------------------------------

    def _set_family(self, key: str) -> None:
        for _key, _family, _hint, label in _FAMILIES:
            if _key == key:
                self._family_btn.setText(label)
        self._style = TextBoxStyle(key, self._style.fontsize, self._style.color,
                                   self._style.fill_color, self._style.border_width)
        self._emit()

    def _set_size(self, pt: int) -> None:
        self._size_btn.setText(str(pt))
        self._style = TextBoxStyle(self._style.fontname, float(pt), self._style.color,
                                   self._style.fill_color, self._style.border_width)
        self._emit()

    def _pick_text_color(self) -> None:
        self._before_modal()
        color = QColorDialog.getColor(QColor.fromRgbF(*self._style.color), self, "Text colour")
        self._after_modal()
        if color.isValid():
            self._style = TextBoxStyle(self._style.fontname, self._style.fontsize, _rgb(color),
                                       self._style.fill_color, self._style.border_width)
            self._refresh_swatches()
            self._emit()

    def _toggle_fill(self) -> None:
        if self._fill_btn.isChecked():
            self._before_modal()
            color = QColorDialog.getColor(QColor.fromRgbF(*self._fill_rgb), self, "Box fill colour")
            self._after_modal()
            if not color.isValid():
                self._fill_btn.setChecked(False)  # cancelled → leave fill off
                return
            self._fill_rgb = _rgb(color)
        self._refresh_swatches()
        self._emit()

    def _toggle_outline(self) -> None:
        self._emit()

    # ---- swatches ---------------------------------------------------------------

    def _refresh_swatches(self) -> None:
        """Tint the 'A' button in the text colour and the Fill button in its colour, so the bar
        shows the current choices at a glance."""
        tc = QColor.fromRgbF(*self._style.color).name()
        self._text_btn.setStyleSheet(f"QToolButton {{ color: {tc}; font-weight: bold; }}")
        if self._fill_btn.isChecked():
            fc = QColor.fromRgbF(*self._fill_rgb)
            # readable label over the fill swatch
            ink = "#000000" if fc.lightnessF() > 0.5 else "#ffffff"
            self._fill_btn.setStyleSheet(f"QToolButton {{ background: {fc.name()}; color: {ink}; }}")
        else:
            self._fill_btn.setStyleSheet("")
