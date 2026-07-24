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
                 close_on_pick: bool = True, include_remove: bool = True,
                 show_title: bool = True) -> None:
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
        # ``show_title=False`` drops the header label (``title`` is still kept as the row's
        # identity) — used when a menu entry right above already names the row (M78.5's markup
        # rows sit under their verb action, so a title here would just repeat the verb).
        if show_title:
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

        # Line Style: thickness + solid/dashed in one sub-menu — two radio groups separated by a
        # divider, because a stroke has both a width and a dash style at once. (Was "Width" before
        # the dash style joined it; PyMuPDF bakes the dash as a /BS /D border and reads it back.)
        self._width_menu = menu.addMenu("Line Style")
        self._width_group = QActionGroup(self._width_menu)
        self._width_actions: dict[float, object] = {}
        for label, pt in _WIDTHS:
            act = self._width_menu.addAction(label)
            act.setCheckable(True)
            self._width_group.addAction(act)
            act.triggered.connect(lambda _c, w=pt: self._set_width(w))
            self._width_actions[pt] = act
        self._width_menu.addSeparator()
        self._dash_group = QActionGroup(self._width_menu)
        self._dash_actions: dict[bool, object] = {}
        for label, is_dashed in _LINE_STYLES:
            act = self._width_menu.addAction(label)
            act.setCheckable(True)
            self._dash_group.addAction(act)
            act.triggered.connect(lambda _c, d=is_dashed: self._set_dashed(d))
            self._dash_actions[is_dashed] = act

        # Arrowheads (M74): line ends as style, Preview's model. Lines only — the other tools
        # simply ignore it, the same applicability-follows-the-model rule as width and fill.
        self._ends_menu = menu.addMenu("Arrowheads")
        self._ends_group = QActionGroup(self._ends_menu)
        self._ends_actions: dict[tuple, object] = {}
        for label, ends in _LINE_ENDS:
            act = self._ends_menu.addAction(label)
            act.setCheckable(True)
            self._ends_group.addAction(act)
            act.triggered.connect(lambda _c, e=ends: self._set_ends(e))
            self._ends_actions[ends] = act

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
        self._dash_actions[style.dashed].setChecked(True)
        ends_act = self._ends_actions.get(style.line_ends)
        if ends_act is not None:
            ends_act.setChecked(True)
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

    def _set_dashed(self, dashed: bool) -> None:
        self._apply(dashed=dashed)

    def _set_ends(self, ends: tuple[bool, bool]) -> None:
        self._apply(line_ends=ends)

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
