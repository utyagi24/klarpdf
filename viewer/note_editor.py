"""The popup a mark's note is typed in (PLAN.md §GUI feature roadmap → M90.1).

A note is a **field of its host mark** (M81), so there is no descriptor to place and nothing to
drag: this editor exists only to collect text and hand it back. That makes it a different animal
from the text-box editor in :mod:`viewer.annotations` — which is WYSIWYG, sized to a page rect at
the current zoom, and carries a formatting bar — even though it reuses that editor's **idiom**: a
``QPlainTextEdit`` child of the view's viewport that commits on focus-out. Focus, clipboard
routing and the reading-key guards of M89 therefore behave here exactly as they already do there,
because they are properties of that idiom rather than of either editor.

Two consequences of "collect text, hand it back" shape the widget:

* **It is anchored, not placed.** The popup sits just under the marked passage in *viewport*
  coordinates at a fixed pixel size, so it stays legible at any zoom instead of shrinking with the
  page — and it never covers the text being annotated (it is flipped above the passage when there
  is no room below).
* **It wears its host's colour** (owner rule 3), washed pale enough to type on. A note on a red
  underline is red; the colour is the one visible tie between the popup and the mark it belongs to.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPlainTextEdit

# Fixed viewport-pixel size: a note is a remark, not a document, and a popup that scaled with zoom
# would be unreadable on a fit-page view and absurd at 400%.
_W, _H = 240, 96
_GAP = 6            # between the passage and the popup, so the mark stays visible while typing


def wash(color, factor: float = 0.28) -> QColor:
    """``color`` (an r,g,b float triple) mixed towards white — the popup's background.

    The host's own colour at full strength is a highlighter wash meant to sit *under* text; typed
    into directly it fights the text. Mixing towards white keeps the tie to the host visible while
    leaving black text legible on every palette colour, including the dark proofing reds and blues
    that underline / strikeout use.
    """
    r, g, b = (max(0.0, min(1.0, c)) for c in color[:3])
    mix = lambda c: int(round(255 * (c * factor + (1.0 - factor))))  # noqa: E731
    return QColor(mix(r), mix(g), mix(b))


class _NotePopup(QPlainTextEdit):
    """The widget itself. Esc cancels, Ctrl+Enter and focus-out commit.

    ``Esc`` is consumed here rather than left to the view: with a popup open it means "abandon what
    I typed", which is not the view's meaning for it (disarm the tool).

    **Every callback passes ``self``**, and the controller ignores one from a popup that is no
    longer current. Opening a second note while the first is still alive gives the outgoing widget
    its focus-out *during* the incoming one's ``setFocus()`` — so an unscoped callback commits and
    closes the popup that just opened. (The text-box editor guards the same trap by capturing its
    editor; this is that guard, moved into the call.)
    """

    def __init__(self, parent, on_commit, on_cancel) -> None:
        super().__init__(parent)
        self._on_commit = on_commit
        self._on_cancel = on_cancel

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._on_cancel(self)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.accept()
            self._on_commit(self)
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._on_commit(self, from_focus_out=True)


class NoteEditor:
    """Opens, positions and closes the note popup; hands the committed text to a callback.

    Owned by :class:`~viewer.annotations.AnnotationOverlay` (as ``.notes``) so it is repositioned
    by the same zoom / scroll hook that already follows the text-box editor, and so a caller that
    has the overlay has the notes UI without a second wiring seam.
    """

    def __init__(self, view) -> None:
        self._view = view
        self._popup: _NotePopup | None = None
        self._page = 0
        self._anchor: tuple = (0.0, 0.0, 0.0, 0.0)   # the marked passage, in page points
        self._on_commit = None
        self._committing = False    # re-entry guard: committing drops focus, which commits again
        self._target = None         # the mark this popup belongs to, for the click-to-toggle check
        # Where the popup that closed during the **current** event dispatch was anchored, as
        # ``(page_index, anchor)``. Clicking an open note's badge takes focus off the popup, so the
        # focus-out commit lands *before* the view's mousePressEvent (measured) — by the time the
        # click is handled the popup is already gone, and a naive handler reopens it, which is why
        # a second click looked like a no-op. Cleared on the next event-loop pass, so it can only
        # ever describe the click being handled right now.
        #
        # **Keyed on where and what, not on the mark object**: descriptors are frozen values, so
        # committing an edited note *replaces* the host with a new object. Identity therefore held
        # only while the text was unchanged — type something and the toggle silently reverted to
        # reopening. The key is ``(page, bounds, type name)``, none of which a note edit touches.
        # The type is not padding: layered marks are the case this whole fan exists for, and a
        # highlight and an underline on one passage have **identical bounds** — on anchor alone,
        # clicking one while the other was open closed it instead of switching. M59.10 scopes
        # merging per type, so one mark per type per span is an invariant, which makes the key
        # exact for our own marks.
        self._just_closed: tuple | None = None

    # ---- state ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._popup is not None

    @property
    def is_read_only(self) -> bool:
        return self._popup is not None and self._popup.isReadOnly()

    @property
    def target(self):
        """The mark the open popup belongs to, or ``None``."""
        return self._target if self._popup is not None else None

    @staticmethod
    def key_for(page_index: int, mark) -> tuple:
        """The toggle key for a mark — see :attr:`_just_closed` for why it is this and not the
        object. Built here so the close side and the click side cannot derive it differently."""
        from klarpdf.model.page_edits import mark_bounds

        return (page_index, tuple(mark_bounds(mark)), type(mark).__name__)

    def consume_just_closed(self, key: tuple) -> bool:
        """Did the popup identified by ``key`` close during *this* event dispatch?
        Answers once, then forgets.

        This is what makes clicking a note glyph a **toggle**: the click's own focus-out has
        already committed and closed the popup by the time the click is handled, so "is it open?"
        is always False and cannot distinguish opening from closing. Consuming the answer keeps a
        stale flag from swallowing a later, unrelated click, and matching on the key keeps a
        neighbouring badge opening normally instead of being eaten as a toggle-off.
        """
        if self._just_closed != key:
            return False
        self._just_closed = None
        return True

    @property
    def text(self) -> str:
        return self._popup.toPlainText() if self._popup is not None else ""

    @property
    def page(self) -> int:
        return self._page

    # ---- open / close -----------------------------------------------------------

    def open_on(self, page_index: int, anchor: tuple, text: str, color, on_commit=None,
                *, read_only: bool = False, placeholder: str = "Note…", target=None) -> None:
        """Open the popup over the passage at ``anchor`` (page points), pre-filled with ``text``.

        ``on_commit(text)`` is called once, with the text as typed — including empty, which is how
        a note is removed (clearing the text drops the note and **leaves the mark**, owner rule).

        ``read_only`` shows a comment that cannot be written back — a foreign annotation's
        ``/Contents`` until the mark is adopted (M90.4). It is the *same* popup deliberately: one
        surface for reading a note means a reader never has to learn where a remark will appear
        based on who wrote it. There is no commit callback, so nothing can be saved by accident.
        """
        self.close()
        self._page = page_index
        self._anchor = tuple(anchor)
        self._target = target
        self._on_commit = None if read_only else on_commit
        popup = _NotePopup(self._view.viewport(), self._commit, self.close)
        popup.setPlaceholderText(placeholder)
        popup.setPlainText(text)
        popup.setReadOnly(read_only)
        # The braces live in **plain** literals and the interpolation in an f-string of its own, so
        # there is nothing to escape. Written as one f-string spanning both lines it wasn't: only
        # the first literal was an f-string, so the second's `}}` stayed *two* closing braces, Qt
        # rejected the rule with "Could not parse stylesheet of object _NotePopup", and the popup
        # kept whatever of it Qt salvaged. Doubling a brace is only escaping inside an f-string —
        # implicit concatenation does not carry the prefix along.
        popup.setStyleSheet(
            "QPlainTextEdit {"
            f" background: {wash(color).name()}; color: #111111;"
            " border: 1px solid rgba(0,0,0,0.35); border-radius: 3px; padding: 3px; }"
        )
        self._popup = popup
        self.reposition()
        popup.show()
        popup.setFocus()
        popup.moveCursor(popup.textCursor().MoveOperation.End)

    def close(self, popup=None, *, from_focus_out: bool = False) -> None:
        """Drop the popup without committing (Esc, or a caller tearing the view down).

        ``popup`` is the widget asking, when the ask came from a widget; a stale one is ignored.
        """
        if popup is not None and popup is not self._popup:
            return
        popup, self._popup = self._popup, None
        self._on_commit = None
        if popup is not None:
            # Remember which note this was, just long enough for the click that caused the close
            # to be handled — see :meth:`consume_just_closed`.
            #
            # **Only a focus-out arms this.** A click on the badge reaches us as a focus-out first
            # and the press second, which is the whole reason the flag exists; a *programmatic*
            # close (Esc, a caller committing directly, the view being torn down) is nobody's
            # click, and arming it there made the flag outlive its dispatch whenever no event-loop
            # turn followed — swallowing the next legitimate open. The timer below is then only a
            # backstop, for a focus-out that no badge click follows.
            if from_focus_out and self._target is not None:
                self._just_closed = self.key_for(self._page, self._target)
                QTimer.singleShot(0, self._forget_just_closed)
            popup.hide()
            popup.deleteLater()

    def _forget_just_closed(self) -> None:
        self._just_closed = None

    def commit(self) -> None:
        """Save and close, as a focus-out would — the toggle-shut path."""
        self._commit()

    def _commit(self, popup=None, *, from_focus_out: bool = False) -> None:
        text = self.text
        on_commit = self._on_commit
        if self._committing or on_commit is None:
            return
        if popup is not None and popup is not self._popup:
            return          # an outgoing widget's focus-out — not this popup's commit
        self._committing = True
        try:
            self.close(from_focus_out=from_focus_out)
            on_commit(text)
        finally:
            self._committing = False

    # ---- geometry ---------------------------------------------------------------

    def reposition(self) -> None:
        """Put the popup back under its passage after a zoom or scroll.

        Below the passage by preference, flipped above it when the viewport bottom is closer than
        the popup is tall — either way the marked text stays visible, which is the point of
        annotating it. Then clamped into the viewport, so a note on a page edge is never half
        off-screen.
        """
        popup = self._popup
        if popup is None:
            return
        scene_rect = self._view.scene_rect_for_box(self._page, self._anchor)
        top_left = self._view.mapFromScene(scene_rect.topLeft())
        bottom_right = self._view.mapFromScene(scene_rect.bottomRight())
        viewport = self._view.viewport().rect()
        x = top_left.x()
        y = bottom_right.y() + _GAP
        if y + _H > viewport.bottom():
            above = top_left.y() - _GAP - _H
            y = above if above >= viewport.top() else max(viewport.top(), viewport.bottom() - _H)
        x = max(viewport.left(), min(x, viewport.right() - _W))
        popup.setGeometry(QRect(int(x), int(y), _W, _H))
