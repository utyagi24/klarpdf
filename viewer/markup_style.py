"""Shared markup / draw style — the colour · width · fill picker (PLAN.md, M59.5).

Every markup and draw descriptor already *carries* its style — :class:`~model.page_edits.Underline`
/ :class:`Strikeout` a ``color``; :class:`InkStroke` / :class:`Line` / :class:`Shape` a ``color`` +
``width`` (and ``Shape`` a ``fill_color``); it all round-trips through save→reopen. M56/M58 simply
baked the descriptor *defaults* (redline red at 2 pt) with no way to change them — "a bit
restricting". This module closes that gap without touching the model or the file format:

* :class:`MarkupStyle` — the immutable bundle the tools stamp onto the next mark: the shared
  **stroke** colour (line / outline / ink), the draw **width**, and an optional shape **fill**.
  Held sticky on :class:`~viewer.annotations.AnnotationOverlay`, so the last-used style carries
  forward exactly like the text-box :class:`~viewer.text_format_bar.TextBoxStyle`.
* :class:`MarkupStyleButton` — one toolbar slot (keeping the toolbar in budget, PLAN.md §Design
  budgets): a swatch face showing the current stroke colour, dropping a menu of preset colours +
  a custom picker, a width sub-menu, and a fill sub-menu.

**Applicability follows the model, not the button** — a knob a tool doesn't have is simply ignored,
the same way the text-box Fill only touches boxes:

* **colour · opacity** → pen · line · arrow · rect · ellipse;
* **width** → the five draw tools;
* **fill** → rect · ellipse only.

**Text markup is deliberately not on this button (M59.9).** Highlight / underline / strikeout are a
different domain from freehand drawing — a highlighter wants a few translucent brights, a proofing
underline a few opaque editorial colours, and neither wants width or fill. They get their own
curated palettes (:data:`HIGHLIGHT_COLORS`, :data:`TEXT_LINE_COLORS`) in the **Markup ▾** dropdown,
which leaves this button meaning exactly "pen & shapes". Redaction (opaque black, a destructive
semantic) keeps its own colour and is out of every palette.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QColorDialog, QMenu, QToolButton

# Stroke presets — the redline red default first, then a small spread that reads on white paper.
_STROKE_PRESETS = (
    ("Red", (0.86, 0.10, 0.10)),
    ("Orange", (0.95, 0.55, 0.15)),
    ("Yellow", (0.95, 0.80, 0.10)),
    ("Green", (0.13, 0.60, 0.20)),
    ("Blue", (0.13, 0.35, 0.85)),
    ("Black", (0.0, 0.0, 0.0)),
)
# Draw-tool stroke widths, in page points (the model's ``width``). Medium == the M58 default.
_WIDTHS = (("Thin", 1.0), ("Medium", 2.0), ("Thick", 4.0))
# Whole-mark opacity (PDF /CA). "Solid" first so the default reads as the no-op it is.
_OPACITIES = (("Solid", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25))
# ---- text-markup palettes (M59.9) -------------------------------------------
#
# Curated and small on purpose: these are the colours people actually reach for when marking up
# text, and a short list is faster than a colour wheel. Two sets, because the two jobs differ —
# a highlighter lays a translucent wash *behind* the words, while an underline / strikeout draws
# an opaque proofing line *through* them.
HIGHLIGHT_COLORS = (
    ("Yellow", (1.0, 0.86, 0.10)),      # the classic marker, and Highlight's own default
    ("Green", (0.55, 0.92, 0.45)),
    ("Blue", (0.55, 0.80, 1.00)),
    ("Pink", (1.00, 0.65, 0.85)),
    ("Orange", (1.00, 0.72, 0.30)),
)
TEXT_LINE_COLORS = (
    ("Red", (0.86, 0.10, 0.10)),        # redline red — the editing convention
    ("Blue", (0.13, 0.35, 0.85)),
    ("Green", (0.13, 0.60, 0.20)),
    ("Black", (0.0, 0.0, 0.0)),
)

# Fill presets for shapes — pale washes that sit under a stroke without swamping it.
_FILL_PRESETS = (
    ("Yellow", (1.0, 0.94, 0.60)),
    ("Green", (0.80, 0.92, 0.75)),
    ("Blue", (0.78, 0.86, 0.97)),
    ("Pink", (0.98, 0.82, 0.88)),
    ("Grey", (0.85, 0.85, 0.85)),
)


@dataclass(frozen=True)
class MarkupStyle:
    """The sticky style the markup / draw tools stamp onto the next mark (colour · width · fill).

    Defaults match the M56/M58 descriptor defaults, so a first mark drawn before the picker is
    ever touched looks exactly as it did before this milestone (redline red, 2 pt, no fill).
    """

    color: tuple[float, float, float] = (0.86, 0.10, 0.10)   # shared stroke — redline red
    width: float = 2.0                                       # draw-tool stroke width, page points
    fill_color: tuple[float, float, float] | None = None     # shapes only; None → no fill
    opacity: float = 1.0                                     # whole-mark alpha (PDF /CA), 0..1

    @classmethod
    def from_mark(cls, mark) -> "MarkupStyle | None":
        """The style of a selected drawn mark — loaded into the picker so a follow-up tweak edits
        *that* mark's colour/width/fill (M59.5, the twin of ``TextBoxStyle.from_textbox``). Returns
        ``None`` for a text box (its own format bar owns its style) or a non-drawn mark."""
        from model.page_edits import InkStroke, Line, Shape

        if isinstance(mark, Shape):
            return cls(mark.color, mark.width, mark.fill_color, mark.opacity)
        if isinstance(mark, (Line, InkStroke)):
            return cls(mark.color, mark.width, None, mark.opacity)
        return None


def _rgb(color: QColor) -> tuple[float, float, float]:
    return (color.redF(), color.greenF(), color.blueF())


def swatch_icon(color: tuple[float, float, float] | None, size: int = 16) -> QIcon:
    """A solid colour chip for a menu action / the button face. ``None`` → a hollow 'no fill' chip."""
    pix = QPixmap(size, size)
    if color is None:
        pix.fill(Qt.GlobalColor.transparent)
        return QIcon(pix)
    pix.fill(QColor.fromRgbF(*color))
    return QIcon(pix)


class MarkupStyleButton(QToolButton):
    """A single toolbar button: a stroke-colour swatch face + a menu for colour / width / fill.

    Emits :attr:`styleChanged` whenever the user picks a control; :meth:`set_style` loads a style
    into the menu ticks + face *without* emitting (so wiring it up on start looks like no edit).
    """

    styleChanged = Signal(object)  # the new MarkupStyle

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._style = MarkupStyle()
        self.setToolTip("Pen & shapes style — colour, width, opacity & fill "
                        "(text markup has its own colours in the Markup menu)")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIconSize(QSize(16, 16))

        menu = QMenu(self)
        self._stroke_group = QActionGroup(menu)
        self._stroke_actions: dict[tuple, object] = {}
        for label, rgb in _STROKE_PRESETS:
            act = menu.addAction(swatch_icon(rgb), label)
            act.setCheckable(True)
            act.setProperty("colorSwatch", True)  # a semantic colour chip — must NOT theme-retint
            self._stroke_group.addAction(act)
            act.triggered.connect(lambda _c, c=rgb: self._set_color(c))
            self._stroke_actions[rgb] = act
        self._custom_color_action = menu.addAction("Custom Colour…")
        self._custom_color_action.triggered.connect(self._pick_custom_color)
        menu.addSeparator()

        self._width_menu = menu.addMenu("Width")
        self._width_group = QActionGroup(self._width_menu)
        self._width_actions: dict[float, object] = {}
        for label, pt in _WIDTHS:
            act = self._width_menu.addAction(label)
            act.setCheckable(True)
            self._width_group.addAction(act)
            act.triggered.connect(lambda _c, w=pt: self._set_width(w))
            self._width_actions[pt] = act

        # Opacity (M59.9): PDF's /CA is whole-annotation, so this fades outline and fill together.
        # It is the answer to "my filled box hides the text" — annotations always paint above the
        # page content, so no amount of Send to Back can put one behind text; translucency can.
        self._opacity_menu = menu.addMenu("Opacity")
        self._opacity_group = QActionGroup(self._opacity_menu)
        self._opacity_actions: dict[float, object] = {}
        for label, value in _OPACITIES:
            act = self._opacity_menu.addAction(label)
            act.setCheckable(True)
            self._opacity_group.addAction(act)
            act.triggered.connect(lambda _c, v=value: self._set_opacity(v))
            self._opacity_actions[value] = act

        self._fill_menu = menu.addMenu("Fill")
        self._fill_group = QActionGroup(self._fill_menu)
        self._no_fill_action = self._fill_menu.addAction("No Fill")
        self._no_fill_action.setCheckable(True)
        self._fill_group.addAction(self._no_fill_action)
        self._no_fill_action.triggered.connect(lambda: self._set_fill(None))
        self._fill_actions: dict[tuple, object] = {}
        for label, rgb in _FILL_PRESETS:
            act = self._fill_menu.addAction(swatch_icon(rgb), label)
            act.setCheckable(True)
            act.setProperty("colorSwatch", True)  # a semantic colour chip — must NOT theme-retint
            self._fill_group.addAction(act)
            act.triggered.connect(lambda _c, c=rgb: self._set_fill(c))
            self._fill_actions[rgb] = act
        self._custom_fill_action = self._fill_menu.addAction("Custom…")
        self._custom_fill_action.triggered.connect(self._pick_custom_fill)

        self.setMenu(menu)
        self.set_style(self._style)

    # ---- state in / out ---------------------------------------------------------

    def style(self) -> MarkupStyle:
        return self._style

    def set_style(self, style: MarkupStyle) -> None:
        """Load ``style`` into the face + menu ticks without emitting :attr:`styleChanged`."""
        self._style = style
        self.setIcon(swatch_icon(style.color, 18))
        stroke_act = self._stroke_actions.get(style.color)
        if stroke_act is not None:
            stroke_act.setChecked(True)          # tick the matching preset (custom → none ticked)
        elif self._stroke_group.checkedAction() is not None:
            self._stroke_group.checkedAction().setChecked(False)
        width_act = self._width_actions.get(style.width)
        if width_act is not None:
            width_act.setChecked(True)
        opacity_act = self._opacity_actions.get(style.opacity)
        if opacity_act is not None:
            opacity_act.setChecked(True)
        elif self._opacity_group.checkedAction() is not None:
            self._opacity_group.checkedAction().setChecked(False)
        if style.fill_color is None:
            self._no_fill_action.setChecked(True)
        else:
            fill_act = self._fill_actions.get(style.fill_color)
            if fill_act is not None:
                fill_act.setChecked(True)
            elif self._fill_group.checkedAction() is not None:
                self._fill_group.checkedAction().setChecked(False)

    def _apply(self, **changes) -> None:
        self._style = replace(self._style, **changes)
        self.set_style(self._style)
        self.styleChanged.emit(self._style)

    # ---- control slots ----------------------------------------------------------

    def _set_color(self, color: tuple[float, float, float]) -> None:
        self._apply(color=color)

    def _set_width(self, width: float) -> None:
        self._apply(width=width)

    def _set_opacity(self, opacity: float) -> None:
        self._apply(opacity=opacity)

    def _set_fill(self, fill: tuple[float, float, float] | None) -> None:
        self._apply(fill_color=fill)

    def _pick_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor.fromRgbF(*self._style.color), self, "Stroke colour")
        if color.isValid():
            self._apply(color=_rgb(color))

    def _pick_custom_fill(self) -> None:
        start = self._style.fill_color or _FILL_PRESETS[0][1]
        color = QColorDialog.getColor(QColor.fromRgbF(*start), self, "Fill colour")
        if color.isValid():
            self._apply(fill_color=_rgb(color))
