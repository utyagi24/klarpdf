"""The mark dialog — stamps and watermarks, one feature (PLAN.md §R4, M62 + M69.3).

**Why one dialog.** A watermark was never a second feature: :mod:`model.content_marks` has always
had exactly two descriptors (:class:`~model.content_marks.Stamp`,
:class:`~model.content_marks.ImageStamp`) and a watermark is one of them with ``under=True``. The UI
was the only place the two were separate, and it cost more than it bought — two dialogs over one
descriptor drifted (each new field had to be added twice), and the two preset lists both contained
"Draft" and "Confidential", the same word producing different results with nothing on screen to
explain why.

**What actually differed.** Of the seven axes between the old dialogs, six were defaults — ``under``,
angle, frame, opacity, scope, preset list (``under`` has since been dropped from the UI entirely,
§M69.6). Exactly one was structural: **how the mark is placed**.
So that is the one control the merged dialog adds, and everything else follows from it:

* **"Where I drag it"** — the mark is *armed*, then the user drags or clicks its box. A stamp goes
  somewhere specific, so the user picks where.
* **"Over the whole page"** — the mark is applied immediately, full-page, across the range. A
  watermark covers the page, so there is nothing to place and asking for a drag would be busywork.

Switching between them rewrites the style fields **in front of the user** (colour, opacity, angle,
frame) rather than silently applying hidden defaults, and hides the two controls that mean nothing
for a page-covering mark — the house "no dead chrome" rule, not greyed-out placeholders.

**There is no "behind the page content" control** (§M69.6). ``Stamp.under`` is still an engine
capability, but the UI does not offer it: "behind" means behind everything the page draws, and most
real PDFs paint an opaque full-page background, so the usual result was a mark that saved correctly
and was invisible. **Opacity already gives the watermark look** — a translucent mark over the
content, page text legible through it — which is what ``under`` was reached for in the first place.

**Presets are words, not modes.** One list, prefilling text + colour only. Whether "Confidential"
is a stamp or a watermark is now the visible Place choice rather than which menu you happened to
open. That is the "Way 2" rule the presets themselves already follow — a preset is a prefill of the
custom generator, never a separate code path — applied one level up.

**The composed style is sticky across sessions**; the **page range deliberately is not**, because a
persisted "All pages" would silently re-scope the next mark to a whole document. Scope is the one
field where a stale value is destructive rather than merely wrong.
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

from model.content_marks import MARK_PRESETS, WHOLE_PAGE_DEFAULTS, Stamp
from util.page_range import PageRangeError, parse_page_range
from viewer.markup_style import swatch_icon

CUSTOM = "Custom…"

_MIN_POINT_SIZE = 6.0
_MAX_POINT_SIZE = 288.0     # 4 inches of cap height; past this a stamp is a watermark
_DEFAULT_POINT_SIZE = 36.0

# **The two use cases, and nothing else** (M69.7, owner). There is no third "drag a box" mode: a
# stamp is sized by its font size and placed by a click, a watermark covers the page. Dragging a
# rectangle was a way of *sizing* a stamp, and once a point size is on the dialog it is a second
# answer to a question already answered — with the worse ergonomics of the two, since a dragged box
# only sets the size indirectly, through the padding the auto-fit leaves.
PLACE_CLICK = "Stamp (click to place)"
PLACE_PAGE = "Watermark (whole page)"

# Style defaults for a stamp: opaque, framed, upright, stamp red, at a legible size.
_STAMP_DEFAULTS = {"color": (0.80, 0.10, 0.10), "opacity": 1.0, "angle": 0.0, "frame": True,
                   "fontsize": _DEFAULT_POINT_SIZE}

# Said in the dialog, not just the docs: a content mark is a point of no return at Save (PLAN.md
# §Design budgets → Honesty principle). The wording matches the save-time confirm.
_BAKE_NOTE = ("Marks are drawn into the page when you save. Until then you can move, resize "
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

        chosen = QColorDialog.getColor(QColor.fromRgbF(*self._color), self, "Mark colour")
        if chosen.isValid():
            self.set_color((chosen.redF(), chosen.greenF(), chosen.blueF()))


class _PageRangeField(QWidget):
    """"All pages" / a range box — the scope control, shared with M64's search-&-redact scope."""

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


class MarkDialog(QDialog):
    """Compose a stamp or a watermark — one dialog, the Place control deciding which."""

    def __init__(self, parent, page_count: int, current_page: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Stamp or Watermark")
        form = QFormLayout()

        self.presets = QComboBox()
        self.presets.addItems([*MARK_PRESETS, CUSTOM])
        self.place = QComboBox()
        self.place.addItems([PLACE_CLICK, PLACE_PAGE])
        self.place.setToolTip(
            "Stamp — compose it at a font size, then click where you want it on the page.\n"
            "Watermark — applied straight away, covering every page in the range."
        )
        self.text = QLineEdit()
        self.color = _ColorButton(_STAMP_DEFAULTS["color"])
        # **The one sizing control.** No "fit to box" any more — there is no box to fit, because
        # there is no drag. The stamp is exactly as big as its text at this size, so a click drops a
        # correctly-proportioned mark and there is no padding to fight.
        self.fontsize = QDoubleSpinBox()
        self.fontsize.setRange(_MIN_POINT_SIZE, _MAX_POINT_SIZE)
        self.fontsize.setDecimals(0)
        self.fontsize.setSingleStep(2.0)
        self.fontsize.setSuffix(" pt")
        self.fontsize.setValue(_DEFAULT_POINT_SIZE)
        self.fontsize.setToolTip("The stamp is sized to its text at this size; click to place it")
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-180.0, 180.0)
        self.angle.setSuffix("°")
        self.angle.setToolTip("Counter-clockwise, so −45° tilts the mark bottom-left to top-right")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(100)
        self.opacity_label = QLabel("100%")
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.frame = QCheckBox("Draw a frame around the text")
        self.frame.setChecked(True)
        # **No "behind the page content" control** (M69.6, owner call). `Stamp.under` remains an
        # engine capability, but the UI does not offer it: "behind" means behind *everything the page
        # draws*, and most real-world PDFs paint an opaque full-page background — so the usual result
        # is a mark that saves correctly and is completely invisible. A control whose ordinary
        # outcome is "nothing appears" is worse than dead chrome.
        #
        # Nothing is lost, because **Opacity already delivers the watermark look** (owner): a
        # translucent mark over the content reads exactly as a watermark should, with the page's own
        # text legible through it — which is what `under` was reached for in the first place. The
        # alternative fix (bake `under` as an over-content `/BM /Multiply` draw, so the file finally
        # matches the multiply-composited preview) was rejected: it would not restore the one thing
        # true under-print uniquely gives — page images *covering* the mark — and it means hand-built
        # `/ExtGState` PDF code in the save path, adding exactly the cross-renderer variability
        # §M61's "no cross-renderer calibration" owner call exists to avoid.
        self.pages = _PageRangeField(page_count, current_page)

        form.addRow("Preset", self.presets)
        form.addRow("Place", self.place)
        form.addRow("Text", self.text)
        form.addRow("Colour", self.color)
        self._size_label = QLabel("Size")
        form.addRow(self._size_label, self.fontsize)
        form.addRow("Angle", self.angle)
        form.addRow("Opacity", self._opacity_row())
        self._frame_filler = QLabel("")
        form.addRow(self._frame_filler, self.frame)
        form.addRow("Apply to", self.pages)
        self._form = form

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        note = QLabel(_BAKE_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.presets.currentTextChanged.connect(self._on_preset)
        self.place.currentTextChanged.connect(self._on_place)
        self._on_preset(self.presets.currentText())
        self._on_place(self.place.currentText())

    def _opacity_row(self) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.opacity)
        layout.addWidget(self.opacity_label)
        return row

    # ---- the two live behaviours -------------------------------------------------

    def _on_preset(self, name: str) -> None:
        """A preset **prefills the fields** — it is not a separate kind of mark (Way 2). So the text
        and colour stay editable afterwards, and ``Custom…`` simply stops overwriting them."""
        fields = MARK_PRESETS.get(name)
        if not fields:
            return
        self.text.setText(fields["text"])
        self.color.set_color(fields["color"])

    def _on_place(self, mode: str) -> None:
        """Switch the placement gesture, rewriting the style fields **visibly**.

        The defaults move in front of the user rather than being applied silently at OK, so the
        dialog never bakes something the fields did not say. Size and Frame are *hidden* rather than
        disabled for a page-covering mark: it auto-fits the page and a frame around the page edge
        reads as a border, so neither knob means anything (house rule — no dead chrome).
        """
        whole_page = mode == PLACE_PAGE
        defaults = WHOLE_PAGE_DEFAULTS if whole_page else _STAMP_DEFAULTS
        self.color.set_color(defaults["color"])
        self.opacity.setValue(int(defaults["opacity"] * 100))
        self.angle.setValue(defaults["angle"])
        self.frame.setChecked(bool(defaults.get("frame", False)))
        if not whole_page:
            self.fontsize.setValue(defaults["fontsize"])
        for widget in (self._size_label, self.fontsize, self._frame_filler, self.frame):
            widget.setVisible(not whole_page)
        if whole_page:
            self.pages.set_default_all()      # a watermark is a whole-document mark by default

    # ---- results -----------------------------------------------------------------

    @property
    def covers_page(self) -> bool:
        """True when OK should **apply** the mark full-page rather than arm a placement gesture."""
        return self.place.currentText() == PLACE_PAGE

    def mark(self, rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> Stamp:
        """The composed descriptor at ``rect`` — the page box for a whole-page mark, a placeholder
        for a dragged one (the placement gesture supplies the real box)."""
        whole_page = self.covers_page
        return Stamp(
            rect=rect,
            text=self.text.text().strip() or "MARK",
            color=self.color.color(),
            # A watermark auto-fits the page it covers (0.0); a stamp is the size you typed.
            fontsize=0.0 if whole_page else self.fontsize.value(),
            border_width=0.0 if whole_page or not self.frame.isChecked() else 3.0,
            angle=self.angle.value(),
            opacity=self.opacity.value() / 100.0,
        )

    def selected_pages(self) -> list[int] | None:
        """The chosen pages, or ``None`` after reporting a bad range to the user."""
        from PySide6.QtWidgets import QMessageBox

        try:
            return self.pages.pages()
        except PageRangeError as exc:
            QMessageBox.warning(self, "Pages", str(exc))
            return None

    # ---- sticky style (remembered across sessions) -------------------------------

    def style_state(self) -> dict:
        """The composed look, as JSON-able primitives for :class:`~store.settings.Settings`.

        The preset name rides along: reopening on ``Custom…`` is what stops :meth:`restore` from
        being immediately overwritten by a preset's prefill. So does the Place mode, since it drives
        the style defaults — restoring the fields without it would have ``_on_place`` overwrite them.
        """
        return {
            "preset": self.presets.currentText(),
            "place": self.place.currentText(),
            "text": self.text.text(),
            "color": list(self.color.color()),
            "fontsize": self.fontsize.value(),
            "angle": self.angle.value(),
            "opacity": self.opacity.value(),
            "frame": self.frame.isChecked(),
        }

    def restore(self, state: dict) -> None:
        """Reinstate a :meth:`style_state` mapping. Anything missing or malformed is *skipped*, not
        defaulted — a settings file from an older build must degrade to "some fields remembered",
        never to a dialog that refuses to open.

        Order matters: Place and Preset both rewrite other fields when they change, so they are
        applied first and the remembered values land on top.
        """
        if not isinstance(state, dict):
            return
        place = state.get("place")
        if isinstance(place, str) and self.place.findText(place) >= 0:
            self.place.setCurrentText(place)
        preset = state.get("preset")
        if isinstance(preset, str) and self.presets.findText(preset) >= 0:
            self.presets.setCurrentText(preset)
        if isinstance(state.get("text"), str):
            self.text.setText(state["text"])
        color = state.get("color")
        if isinstance(color, (list, tuple)) and len(color) == 3:
            try:
                self.color.set_color(tuple(float(c) for c in color))
            except (TypeError, ValueError):
                pass
        for key, setter in (
            ("fontsize", lambda v: self.fontsize.setValue(float(v))),
            ("angle", lambda v: self.angle.setValue(float(v))),
            ("opacity", lambda v: self.opacity.setValue(int(v))),
        ):
            if isinstance(state.get(key), (int, float)) and not isinstance(state[key], bool):
                setter(state[key])
        if isinstance(state.get("frame"), bool):
            self.frame.setChecked(state["frame"])
