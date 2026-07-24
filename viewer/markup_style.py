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
* :class:`LineStylingButton` · :class:`ColorsButton` · :class:`OpacityButton` — three markup-bar
  buttons over that one shared style (M78.6, splitting the former single ``MarkupStyleButton``):
  **Line Styling** (thickness · dash · arrowheads), **Colors** (a Border stroke row + a Fill row +
  custom + No Fill), and **Opacity** (a slider showing/accepting an exact %). The window keeps the
  three in sync by broadcasting the new style to all of them after any edit.

**Applicability follows the model, not the button** — a knob a tool doesn't have is simply ignored,
the same way the text-box Fill only touches boxes:

* **colour · opacity** → pen · line · rect · ellipse;
* **width · dash style** → the draw tools (the "Line Style" sub-menu — thickness + solid/dashed);
* **fill** → rect · ellipse only;
* **arrowheads** (M74) → lines only. Preview treats arrowheads as *line style*, and it is right:
  the Arrow tool is gone, and ``Line`` carries an ends attribute (none · start · end · both) set
  here — which is also what makes a **both-ended** arrow drawable for the first time.

**Text markup is deliberately not on this button (M59.9).** Highlight / underline / strikeout are a
different domain from freehand drawing — a highlighter wants a few translucent brights, a proofing
underline a few opaque editorial colours, and neither wants width or fill. They get their own
curated palettes (:data:`HIGHLIGHT_COLORS`, :data:`TEXT_LINE_COLORS`) in the **Markup ▾** dropdown,
which leaves this button meaning exactly "pen & shapes". Redaction (opaque black, a destructive
semantic) keeps its own colour and is out of every palette.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QGuiApplication, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

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
# Stroke dash styles (the model's ``dashed`` bool). Solid first — the no-op default.
_LINE_STYLES = (("Solid", False), ("Dashed", True))
# Whole-mark opacity (PDF /CA). "Solid" first so the default reads as the no-op it is.
_OPACITIES = (("Solid", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25))
# Since M78.6 opacity is a slider (an exact %) rather than those presets; the floor keeps a mark
# from being dragged to fully invisible (annotations always paint above the page).
_OPACITY_MIN_PCT = 10
# Line ends (M74): ``(arrow_start, arrow_end)``. "None" first — the plain-line default; "End" is
# what the retired Arrow tool drew; "Both" is new capability.
_LINE_ENDS = (
    ("None", (False, False)),
    ("Start", (True, False)),
    ("End", (False, True)),
    ("Both", (True, True)),
)
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
    line_ends: tuple[bool, bool] = (False, False)            # lines only (M74): arrowheads
    dashed: bool = False                                     # dashed vs solid stroke

    @classmethod
    def from_mark(cls, mark) -> "MarkupStyle | None":
        """The style of a selected drawn mark — loaded into the picker so a follow-up tweak edits
        *that* mark's colour/width/fill/ends/dash (M59.5, the twin of ``TextBoxStyle.from_textbox``).
        Returns ``None`` for a text box (its own format bar owns its style) or a non-drawn mark."""
        from model.page_edits import InkStroke, Line, Shape

        if isinstance(mark, Shape):
            return cls(mark.color, mark.width, mark.fill_color, mark.opacity, dashed=mark.dashed)
        if isinstance(mark, Line):
            return cls(mark.color, mark.width, None, mark.opacity,
                       (mark.arrow_start, mark.arrow_end), dashed=mark.dashed)
        if isinstance(mark, InkStroke):
            return cls(mark.color, mark.width, None, mark.opacity, dashed=mark.dashed)
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


def _close_colors(a: tuple, b: tuple) -> bool:
    """Tolerant colour equality — a save/reopen round-trips through PDF floats, so an exact ==
    against a palette tuple would miss (the same slack model-side merge uses)."""
    return len(a) == len(b) and all(abs(x - y) <= 0.01 for x, y in zip(a, b))


def dot_icon(color: tuple[float, float, float] | None, ring: bool = False,
             size: int = 18) -> QIcon:
    """A round colour dot for the M76.1 swatch rows. ``None`` draws the standard "no colour"
    glyph — a hollow dot with a red diagonal slash (Preview's own removal control) — so the row
    stays pure dots with no word to misread; the button tooltip carries the verb. ``ring`` marks
    the layer's *current* state (its colour, or slashed = absent) in the theme's text colour."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inner = QRectF(2.5, 2.5, size - 5.0, size - 5.0)
    if color is None:
        painter.setPen(QPen(QColor(128, 128, 128, 200), 1.2))
        painter.setBrush(QColor(255, 255, 255, 60))
        painter.drawEllipse(inner)
        painter.setPen(QPen(QColor(0xC0, 0x39, 0x2B), 1.6))  # the red slash reads "remove"
        offset = inner.width() * 0.2071  # chord inset: the slash spans the circle, not its box
        painter.drawLine(QPointF(inner.left() + offset, inner.bottom() - offset),
                         QPointF(inner.right() - offset, inner.top() + offset))
    else:
        painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
        painter.setBrush(QColor.fromRgbF(*color))
        painter.drawEllipse(inner)
    if ring:
        app = QGuiApplication.instance()
        ring_color = (app.palette().color(QPalette.ColorRole.WindowText)
                      if app is not None else QColor("#2b2b2b"))
        painter.setPen(QPen(ring_color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(0.8, 0.8, size - 1.6, size - 1.6))
    painter.end()
    return QIcon(pix)


def _button_text_color() -> QColor:
    app = QGuiApplication.instance()
    if app is None:
        return QColor("#2b2b2b")
    return app.palette().color(QPalette.ColorRole.ButtonText)


def line_style_icon(width: float, dashed: bool, size: int = 18) -> QIcon:
    """The Line Styling button's face (M78.6): a horizontal stroke at the current thickness + dash,
    in the theme's text colour, so the button shows the style it sets at a glance."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(_button_text_color(), max(1.0, min(width, 6.0)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    if dashed:
        pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    y = size / 2.0
    painter.drawLine(QPointF(3, y), QPointF(size - 3, y))
    painter.end()
    return QIcon(pix)


def opacity_icon(opacity: float, size: int = 18) -> QIcon:
    """The Opacity button's face (M78.6): a disc filled at the current opacity inside a ring, so the
    button hints how translucent the next mark will be."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = _button_text_color()
    inner = QRectF(2.5, 2.5, size - 5.0, size - 5.0)
    painter.setPen(QPen(base, 1.2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(inner)
    fill = QColor(base)
    fill.setAlphaF(max(0.0, min(1.0, opacity)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawEllipse(inner)
    painter.end()
    return QIcon(pix)


class SwatchRowAction(QWidgetAction):
    """A menu section in Preview's layout: a small header over a **horizontal row of colour
    dots** — the shared vocabulary of the M76.1 context menu and (M76.2) the Markup ▾ dropdown.
    Clicking a dot emits :attr:`picked` with its colour tuple; the ring marks the current state.

    Two configurations, two jobs:

    * the **context menu** (defaults): the row *acts on a mark* — it ends in the slashed remove
      dot (``picked`` emits ``None``) and a click closes the menu, like any menu action;
    * the **sticky-colour rows** (``include_remove=False, close_on_pick=False``): the row *sets
      state* — there is nothing to remove (a mark always has a colour), and the menu **stays
      open** so "pick a colour, then click the verb" is one menu visit; the caller moves the
      ring via :meth:`set_active` and the change is visible in place.

    Radio semantics either way: clicking the ringed dot is a harmless no-op, not dead chrome."""

    picked = Signal(object)  # (r, g, b) — or None from the remove dot, where one exists

    def __init__(self, menu: QMenu, title: str, palette, active: tuple | None,
                 close_on_pick: bool = True, include_remove: bool = True) -> None:
        super().__init__(menu)
        self.title = title
        self.active = active
        self.buttons: dict[str, QToolButton] = {}
        self._palette = tuple(palette)
        self._close_on_pick = close_on_pick
        self.remove_button = None
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 4, 12, 4)
        column.setSpacing(2)
        column.addWidget(QLabel(title))
        row = QHBoxLayout()
        row.setSpacing(2)
        for name, rgb in self._palette:
            button = self._dot(name)
            button.clicked.connect(lambda _c=False, c=rgb: self._pick(menu, c))
            row.addWidget(button)
            self.buttons[name] = button
        if include_remove:
            self.remove_button = self._dot(f"Remove {title.lower()}")
            self.remove_button.clicked.connect(lambda _c=False: self._pick(menu, None))
            row.addWidget(self.remove_button)
        row.addStretch(1)
        column.addLayout(row)
        self.setDefaultWidget(box)
        self.set_active(active)

    def set_active(self, active: tuple | None) -> None:
        """Move the state ring onto ``active`` (or the remove dot for ``None``) — re-rendered in
        place, so a stay-open row shows the change under the still-open menu."""
        self.active = active
        for name, rgb in self._palette:
            ringed = active is not None and _close_colors(rgb, active)
            self.buttons[name].setIcon(dot_icon(rgb, ring=ringed))
        if self.remove_button is not None:
            self.remove_button.setIcon(dot_icon(None, ring=active is None))

    @staticmethod
    def _dot(tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(True)
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(24, 24)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _pick(self, menu: QMenu, value) -> None:
        if self._close_on_pick:
            menu.close()  # a widget click doesn't auto-close the menu the way an action does
        self.picked.emit(value)


class _StyleButton(QToolButton):
    """Base for the three markup-style buttons (M78.6). They split the pen/shapes style controls —
    **Line Styling · Colors · Opacity** — across three toolbar slots but share one
    :class:`MarkupStyle`: each button edits only its own slice, and the window keeps all three in
    step by broadcasting :meth:`set_style` to every button after any :attr:`styleChanged`. So every
    mutator lives here (the one style is edited one way), while each subclass owns its menu + face.

    :meth:`set_style` loads a style into the controls + face **without** emitting (start-up and
    object-select), exactly as the old single button did.
    """

    styleChanged = Signal(object)  # the new MarkupStyle

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._style = MarkupStyle()
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIconSize(QSize(16, 16))

    def style(self) -> MarkupStyle:
        return self._style

    def set_style(self, style: MarkupStyle) -> None:
        """Load ``style`` into this button's controls + face without emitting :attr:`styleChanged`."""
        self._style = style
        self._sync()

    def _sync(self) -> None:  # overridden — refresh this button's own controls + face
        pass

    def _apply(self, **changes) -> None:
        self._style = replace(self._style, **changes)
        self._sync()
        self.styleChanged.emit(self._style)

    # ---- control slots (shared, so the one style is edited one way) --------------

    def _set_color(self, color: tuple[float, float, float]) -> None:
        self._apply(color=color)

    def _set_width(self, width: float) -> None:
        self._apply(width=width)

    def _set_dashed(self, dashed: bool) -> None:
        self._apply(dashed=dashed)

    def _set_ends(self, ends: tuple[bool, bool]) -> None:
        self._apply(line_ends=ends)

    def _set_opacity(self, opacity: float) -> None:
        self._apply(opacity=opacity)

    def _set_fill(self, fill: tuple[float, float, float] | None) -> None:
        self._apply(fill_color=fill)

    def _pick_custom_color(self) -> None:
        color = QColorDialog.getColor(QColor.fromRgbF(*self._style.color), self, "Border colour")
        if color.isValid():
            self._apply(color=_rgb(color))

    def _pick_custom_fill(self) -> None:
        start = self._style.fill_color or _FILL_PRESETS[0][1]
        color = QColorDialog.getColor(QColor.fromRgbF(*start), self, "Fill colour")
        if color.isValid():
            self._apply(fill_color=_rgb(color))


class LineStylingButton(_StyleButton):
    """Line Styling (M78.6): thickness · dash · arrowheads. Applicability follows the model — a tool
    without ends (everything but Line) just ignores them, the same rule as width and fill."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setToolTip("Line styling — thickness, dash & arrowheads (pen & shapes)")
        menu = QMenu(self)
        self._width_group = QActionGroup(menu)
        self._width_actions: dict[float, object] = {}
        for label, pt in _WIDTHS:
            act = menu.addAction(label)
            act.setCheckable(True)
            self._width_group.addAction(act)
            act.triggered.connect(lambda _c, w=pt: self._set_width(w))
            self._width_actions[pt] = act
        menu.addSeparator()
        self._dash_group = QActionGroup(menu)
        self._dash_actions: dict[bool, object] = {}
        for label, is_dashed in _LINE_STYLES:
            act = menu.addAction(label)
            act.setCheckable(True)
            self._dash_group.addAction(act)
            act.triggered.connect(lambda _c, d=is_dashed: self._set_dashed(d))
            self._dash_actions[is_dashed] = act
        menu.addSeparator()
        # Arrowheads (M74): line ends as style, Preview's model. Lines only.
        self._ends_menu = menu.addMenu("Arrowheads")
        self._ends_group = QActionGroup(self._ends_menu)
        self._ends_actions: dict[tuple, object] = {}
        for label, ends in _LINE_ENDS:
            act = self._ends_menu.addAction(label)
            act.setCheckable(True)
            self._ends_group.addAction(act)
            act.triggered.connect(lambda _c, e=ends: self._set_ends(e))
            self._ends_actions[ends] = act
        self.setMenu(menu)
        self._sync()

    def _sync(self) -> None:
        self.setIcon(line_style_icon(self._style.width, self._style.dashed, 18))
        width_act = self._width_actions.get(self._style.width)
        if width_act is not None:
            width_act.setChecked(True)
        elif self._width_group.checkedAction() is not None:
            self._width_group.checkedAction().setChecked(False)
        self._dash_actions[self._style.dashed].setChecked(True)
        ends_act = self._ends_actions.get(self._style.line_ends)
        if ends_act is not None:
            ends_act.setChecked(True)
        elif self._ends_group.checkedAction() is not None:
            self._ends_group.checkedAction().setChecked(False)


class ColorsButton(_StyleButton):
    """Colors (M78.6): a **Border** (stroke) swatch row + a **Fill** swatch row + custom pickers,
    clubbed under one menu (owner call). 'Border' is the pen/shape *stroke* — distinct from the
    text-markup colours, which live in the Markup ▾ menu (M78.5), a different domain. The Fill row's
    slashed dot is 'No Fill'."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setToolTip("Colors — border (stroke) & fill for pen & shapes")
        menu = QMenu(self)
        self._border_row = SwatchRowAction(menu, "Border", _STROKE_PRESETS, self._style.color,
                                           close_on_pick=True, include_remove=False)
        self._border_row.picked.connect(self._set_color)
        menu.addAction(self._border_row)
        custom_border = menu.addAction("Custom Border…")
        custom_border.triggered.connect(self._pick_custom_color)
        menu.addSeparator()
        self._fill_row = SwatchRowAction(menu, "Fill", _FILL_PRESETS, self._style.fill_color,
                                         close_on_pick=True, include_remove=True)
        self._fill_row.picked.connect(self._set_fill)  # the remove dot → None → No Fill
        menu.addAction(self._fill_row)
        custom_fill = menu.addAction("Custom Fill…")
        custom_fill.triggered.connect(self._pick_custom_fill)
        self.setMenu(menu)
        self._sync()

    def _sync(self) -> None:
        self.setIcon(swatch_icon(self._style.color, 18))
        self._border_row.set_active(self._style.color)
        self._fill_row.set_active(self._style.fill_color)  # None → the No-Fill (remove) dot rings


class OpacityButton(_StyleButton):
    """Opacity (M78.6): a slider that shows and accepts an **exact** percentage, replacing the old
    25/50/75/100 presets. PDF's /CA is whole-annotation, so it fades outline + fill together — the
    answer to "my filled box hides the text", since annotations always paint above the page."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        menu = QMenu(self)
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(12, 6, 12, 6)
        column.setSpacing(4)
        self._opacity_label = QLabel()
        column.addWidget(self._opacity_label)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(_OPACITY_MIN_PCT, 100)
        self._slider.setSingleStep(5)
        self._slider.setPageStep(10)
        self._slider.setMinimumWidth(180)
        self._slider.valueChanged.connect(self._on_slider)
        column.addWidget(self._slider)
        action = QWidgetAction(menu)
        action.setDefaultWidget(box)
        menu.addAction(action)
        self.setMenu(menu)
        self._sync()

    def _on_slider(self, pct: int) -> None:
        self._opacity_label.setText(f"Opacity: {pct}%")
        opacity = pct / 100.0
        if abs(opacity - self._style.opacity) > 1e-9:
            self._set_opacity(opacity)

    def _sync(self) -> None:
        pct = round(self._style.opacity * 100)
        self.setIcon(opacity_icon(self._style.opacity, 18))
        self.setToolTip(f"Opacity — {pct}% (fades border + fill together)")
        self._opacity_label.setText(f"Opacity: {pct}%")
        self._slider.blockSignals(True)  # loading a style must not re-emit through the slider
        self._slider.setValue(max(_OPACITY_MIN_PCT, min(100, pct)))
        self._slider.blockSignals(False)
