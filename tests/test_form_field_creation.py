"""Form-field creation (PLAN.md §R5, M69). Headless + offscreen GUI.

Place **text · checkbox · dropdown** fields on a page. The milestone's done-when is deliberately not
about new machinery: *"fill / print / flatten work on them like any AcroForm field"* — because what
materialises is an ordinary ``page.add_widget`` widget, with no KlarPDF concept attached. These
tests assert exactly that, by running the **existing** form, print and flatten paths over a created
field rather than anything written for M69.

Radio-button groups are deliberately absent (owner, 2026-07-18).
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from app import PdfApp
from main_window import MainWindow
from model.edit_engine import PyMuPDFEngine
from model.export import export_flattened_pdf
from model.form_fields import FIELD_KINDS, NewField, apply_new_fields, kind_label
from model.page_edits import read_form_fields
from model.virtual_document import VirtualDocument
from store.settings import Settings
from viewer.tools import ArmedTool

RECT = (100.0, 300.0, 300.0, 330.0)


@pytest.fixture
def blank_pdf(tmp_path) -> str:
    path = str(tmp_path / "blank.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Please complete:", fontsize=12)
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def vdoc(blank_pdf):
    v = VirtualDocument.from_path(blank_pdf)
    yield v
    v.close()


def _materialize(vdoc, tmp_path, name="out.pdf") -> str:
    out = str(tmp_path / name)
    PyMuPDFEngine().materialize(vdoc, out)
    return out


def _widgets(path, page=0):
    doc = fitz.open(path)
    try:
        return [(w.field_name, w.field_type, w.field_value, tuple(w.rect))
                for w in (doc[page].widgets() or [])]
    finally:
        doc.close()


# ---- the three types materialise as real AcroForm widgets -----------------------


def test_a_text_field_is_created(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text", "Ada"))
    made = _widgets(_materialize(vdoc, tmp_path))
    assert len(made) == 1
    name, kind, value, rect = made[0]
    assert (name, kind, value) == ("full_name", fitz.PDF_WIDGET_TYPE_TEXT, "Ada")
    assert rect == pytest.approx(RECT, abs=1.0)


def test_a_checkbox_is_created(vdoc, tmp_path):
    """A ticked checkbox reads back as its PDF **on-state name** (conventionally ``Yes``), not as a
    Python ``True`` — the value of a checkbox field is a name in the file format."""
    vdoc.add_annotation(0, NewField(RECT, "agreed", "checkbox", "yes"))
    name, kind, value, _rect = _widgets(_materialize(vdoc, tmp_path))[0]
    assert (name, kind) == ("agreed", fitz.PDF_WIDGET_TYPE_CHECKBOX)
    assert value not in (False, "Off", "", None)


def test_an_unchecked_checkbox_is_created(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "agreed", "checkbox", ""))
    _name, _kind, value, _rect = _widgets(_materialize(vdoc, tmp_path))[0]
    assert value in (False, "Off")


def test_a_dropdown_is_created_with_its_choices(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "colour", "dropdown", "", ("Red", "Green", "Blue")))
    out = _materialize(vdoc, tmp_path)
    doc = fitz.open(out)
    try:
        widget = next(iter(doc[0].widgets()))
        assert widget.field_type == fitz.PDF_WIDGET_TYPE_COMBOBOX
        assert list(widget.choice_values) == ["Red", "Green", "Blue"]
        assert widget.field_value == "Red"            # first choice when no default given
    finally:
        doc.close()


def test_a_dropdown_honours_its_default(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "colour", "dropdown", "Blue", ("Red", "Green", "Blue")))
    _name, _kind, value, _rect = _widgets(_materialize(vdoc, tmp_path))[0]
    assert value == "Blue"


def test_fields_land_on_their_own_pages(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "first", "text"))
    vdoc.add_annotation(1, NewField(RECT, "second", "text"))
    out = _materialize(vdoc, tmp_path)
    assert [n for n, *_ in _widgets(out, 0)] == ["first"]
    assert [n for n, *_ in _widgets(out, 1)] == ["second"]


def test_an_unnamed_field_still_gets_a_name(vdoc, tmp_path):
    """AcroForm keys values by name, so a nameless widget is unusable — never write one."""
    vdoc.add_annotation(0, NewField(RECT, "", "text"))
    name, *_ = _widgets(_materialize(vdoc, tmp_path))[0]
    assert name


def test_apply_new_fields_reports_its_count():
    doc = fitz.open()
    page = doc.new_page()
    try:
        assert apply_new_fields(page, (NewField(RECT, "a", "text"),
                                       NewField(RECT, "b", "checkbox"))) == 2
        assert apply_new_fields(page, ()) == 0
    finally:
        doc.close()


# ---- it rides the PageRef like every other page edit ----------------------------


def test_a_new_field_rides_the_pageref(vdoc):
    field = NewField(RECT, "x", "text")
    vdoc.add_annotation(0, field)
    assert vdoc.page_annotations(0) == (field,)
    assert vdoc.dirty is True


def test_a_new_field_follows_a_reorder(vdoc, tmp_path):
    vdoc.add_annotation(0, NewField(RECT, "moved", "text"))
    vdoc.move_page(0, 1)
    out = _materialize(vdoc, tmp_path)
    assert _widgets(out, 0) == []
    assert [n for n, *_ in _widgets(out, 1)] == ["moved"]


def test_new_fields_are_hashable_for_undo_snapshots():
    assert len({NewField(RECT, "a"), NewField(RECT, "a"), NewField(RECT, "b")}) == 2


def test_a_new_field_moves_and_scales_with_the_object_primitives():
    """It is a free-placed rect, so M59's move/resize work on it — the same reuse M62 relies on."""
    from model.page_edits import mark_bounds, scale_mark, translate_mark

    field = NewField(RECT, "x", "text")
    assert mark_bounds(translate_mark(field, 10, 5)) == (110.0, 305.0, 310.0, 335.0)
    assert mark_bounds(scale_mark(field, 2.0, 1.0, 100.0, 300.0)) == (100.0, 300.0, 500.0, 330.0)


# ---- "like any AcroForm field": the existing paths, unchanged -------------------


def test_a_created_field_is_fillable_before_it_is_saved(vdoc):
    """The inline filler reads ``read_form_fields``, which includes placed-but-unsaved fields — so
    a field you just drew is immediately usable rather than only after a save."""
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text"))
    names = [f.name for f in read_form_fields(vdoc)]
    assert "full_name" in names


def test_filling_a_created_field_persists_through_the_save(vdoc, tmp_path):
    """The fill pass runs *after* field creation, so a value typed into a field made in the same
    session lands on the widget like any other."""
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text"))
    vdoc.set_field_value("full_name", "Grace Hopper")
    _name, _kind, value, _rect = _widgets(_materialize(vdoc, tmp_path))[0]
    assert value == "Grace Hopper"


def test_a_created_field_reopens_as_an_ordinary_field(vdoc, tmp_path):
    """Nothing KlarPDF-specific survives — reopening finds a plain AcroForm field, which is the
    whole design claim."""
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text", "Ada"))
    out = _materialize(vdoc, tmp_path)
    reopened = VirtualDocument.from_path(out)
    try:
        fields = read_form_fields(reopened)
        assert [f.name for f in fields] == ["full_name"]
        assert fields[0].current_value == "Ada"
        assert reopened.page_annotations(0) == ()      # no lingering descriptor
    finally:
        reopened.close()


def test_a_created_field_flattens(vdoc, tmp_path):
    """Flatten (M31.5) bakes widgets into page content — it must work on a created field, and the
    value must survive as text."""
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text", "Ada Lovelace"))
    out = str(tmp_path / "flat.pdf")
    export_flattened_pdf(vdoc, out)
    doc = fitz.open(out)
    try:
        assert list(doc[0].widgets() or []) == []      # no widgets left
        assert "Ada" in doc[0].get_text()              # the value became page content
    finally:
        doc.close()


def test_a_created_field_renders_in_the_print_export_path(vdoc):
    """Print / image export / live thumbnails all read ``render_output``."""
    vdoc.add_annotation(0, NewField(RECT, "full_name", "text", "Ada"))
    with PyMuPDFEngine().render_output(vdoc) as rendered:
        assert [w.field_name for w in (rendered[0].widgets() or [])] == ["full_name"]


# ---- the placement UI (offscreen GUI) -------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return PdfApp.instance() or PdfApp([])


@pytest.fixture
def app(qapp, tmp_path):
    qapp.settings = Settings(tmp_path / "vs.json")
    qapp.page_clipboard = []
    return qapp


@pytest.fixture
def win(app, blank_pdf):
    w = MainWindow(app, blank_pdf, app.settings)
    yield w
    w.undo_stack.setClean()
    w.close()


def _scene(win, x: float, y: float):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _place(win, field, start=(100, 300), end=(300, 330)):
    win.view.annotations.pending_field = field
    win.view.arm(ArmedTool.FIELD)
    overlay = win.view.annotations
    from PySide6.QtCore import Qt

    assert overlay.begin_draw(ArmedTool.FIELD, _scene(win, *start)) is True
    overlay.update_draw(_scene(win, *end), Qt.KeyboardModifier.NoModifier)
    overlay.finish_draw()


def _fields(win, page=0):
    return [a for a in win.vdoc.page_annotations(page) if isinstance(a, NewField)]


def test_placing_a_field_puts_it_where_it_was_dragged(win):
    _place(win, NewField((0, 0, 1, 1), "full_name", "text"))
    assert len(_fields(win)) == 1
    assert _fields(win)[0].rect == pytest.approx(RECT, abs=1.0)
    assert _fields(win)[0].name == "full_name"


def test_the_field_placement_is_one_shot(win):
    _place(win, NewField((0, 0, 1, 1), "a", "text"))
    assert win.view.annotations.pending_field is None
    _place_again = win.view.annotations
    from PySide6.QtCore import Qt

    _place_again.begin_draw(ArmedTool.FIELD, _scene(win, 100, 400))
    _place_again.update_draw(_scene(win, 200, 430), Qt.KeyboardModifier.NoModifier)
    _place_again.finish_draw()
    assert len(_fields(win)) == 1


def test_placing_a_field_is_undoable(win):
    _place(win, NewField((0, 0, 1, 1), "a", "text"))
    win.undo_stack.undo()
    assert _fields(win) == []


def test_a_placed_field_shows_in_the_form_overlay(win):
    """It gets the same faint wash every fillable field gets, immediately — the affordance comes
    from the existing overlay, not from anything M69 added."""
    before = len(win.view.form._items)
    _place(win, NewField((0, 0, 1, 1), "full_name", "text"))
    win.view.form.repaint()
    assert len(win.view.form._items) > before


def test_the_dialog_composes_each_type(win):
    from ui.field_dialog import FieldDialog

    for kind in FIELD_KINDS:
        dialog = FieldDialog(win, kind)
        try:
            dialog.name.setText("thing")
            assert dialog.field().kind == kind
            assert dialog.field().name == "thing"
        finally:
            dialog.deleteLater()


def test_the_dialog_requires_a_name(win):
    from PySide6.QtWidgets import QDialogButtonBox

    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "text")
    try:
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False        # AcroForm keys by name; a nameless field is unusable
        dialog.name.setText("x")
        assert ok.isEnabled() is True
    finally:
        dialog.deleteLater()


def test_the_dialog_warns_about_a_name_collision(win):
    """Fields sharing a name share a value — sometimes deliberate, often a mistake, so it informs
    rather than blocks."""
    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "text", existing_names={"taken"})
    try:
        dialog.name.setText("free")
        assert dialog.warning.isHidden() is True
        dialog.name.setText("taken")
        assert dialog.warning.isHidden() is False
        assert dialog.buttons.button(
            __import__("PySide6.QtWidgets", fromlist=["QDialogButtonBox"])
            .QDialogButtonBox.StandardButton.Ok
        ).isEnabled() is True                 # informs, does not block
    finally:
        dialog.deleteLater()


def test_choices_only_exist_for_a_dropdown(win):
    """No dead chrome: a text field has no choice list to show."""
    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "text")
    try:
        assert dialog.options.isHidden() is True
        dialog.kind.setCurrentIndex(FIELD_KINDS.index("dropdown"))
        assert dialog.options.isHidden() is False
    finally:
        dialog.deleteLater()


def test_dropdown_choices_are_parsed_per_line(win):
    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "dropdown")
    try:
        dialog.name.setText("colour")
        dialog.options.setPlainText("Red\n Green \n\nBlue\n")
        assert dialog.field().options == ("Red", "Green", "Blue")
    finally:
        dialog.deleteLater()


def test_choices_are_dropped_for_a_non_dropdown(win):
    from ui.field_dialog import FieldDialog

    dialog = FieldDialog(win, "dropdown")
    try:
        dialog.name.setText("x")
        dialog.options.setPlainText("Red\nGreen")
        dialog.kind.setCurrentIndex(FIELD_KINDS.index("text"))
        assert dialog.field().options == ()
    finally:
        dialog.deleteLater()


def test_kind_labels_are_human():
    assert kind_label("dropdown") == "Dropdown"
    assert kind_label("checkbox") == "Checkbox"
    assert kind_label("text") == "Text Field"


def test_radio_groups_are_not_offered():
    """Rejected by the owner (2026-07-18) — pinned so it cannot creep back in unnoticed."""
    assert "radio" not in FIELD_KINDS
    assert set(FIELD_KINDS) == {"text", "checkbox", "dropdown"}


# ---- a created field is an ordinary object (M69.14) ------------------------------
#
# Owner-reported: a placed field could not be moved, even before saving. The model had always been
# ready — `PLACEABLE_TYPES` lists NewField, `translate_mark` and `scale_mark` both handle it, and
# its `bounding_rect` docstring says it exists "so the viewer's shared hit-test / outline helpers
# work on it unchanged" — but the viewer's OBJECT_TYPES tuple was never told, so the field was
# invisible to select / move / resize / marquee. It is drawn by the *form* overlay rather than the
# annotation overlay, which is what let the omission go unnoticed.


def _field_win(win, rect=(100, 300, 300, 330)):
    from model.form_fields import NewField

    field = NewField(kind="text", name="who", rect=rect)
    win.vdoc.add_annotation(0, field)
    win.view.annotations.repaint()
    return field


def _at(win, x, y):
    return win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()


def _fields(win):
    from model.form_fields import NewField

    return [a for a in win.vdoc.page_annotations(0) if isinstance(a, NewField)]


def test_a_placed_field_is_hit_testable(win):
    _field_win(win)
    assert win.view.annotations.drawn_mark_at(_at(win, 200, 315)) is not None


def test_a_placed_field_selects_and_moves(win):
    _field_win(win)
    overlay = win.view.annotations
    assert overlay.select_object_at(_at(win, 200, 315)) is True
    assert overlay.begin_move(_at(win, 200, 315)) is True
    overlay.update_move(_at(win, 250, 360))
    overlay.finish_move()
    assert _fields(win)[0].rect[:2] == pytest.approx((150, 345), abs=1.5)


def test_a_placed_field_resizes_by_its_handles(win):
    from PySide6.QtCore import Qt as _Qt

    _field_win(win)
    overlay = win.view.annotations
    overlay.select_object(0, _fields(win)[0])
    assert overlay.begin_resize("se", _at(win, 300, 330)) is True
    overlay.update_resize(_at(win, 360, 380), _Qt.KeyboardModifier.NoModifier)
    overlay.finish_resize()
    assert _fields(win)[0].rect == pytest.approx((100, 300, 360, 380), abs=1.5)


def test_a_marquee_catches_a_field(win):
    _field_win(win)
    win.view.annotations.select_in_rect(0, (50, 250, 400, 400))
    assert len(win.view.annotations.selected_objects) == 1


def test_a_field_is_named_in_its_remove_verb(win):
    """"Remove newfield" was the class-name fallback."""
    _field_win(win)
    menu = win._view_context_menu(_at(win, 200, 315))
    labels = [a.text() for a in menu.actions()]
    assert "Remove form field" in labels
    menu.deleteLater()


def test_clicking_a_field_in_select_mode_still_fills_it(win, qapp):
    """The deliberate split: Select mode fills a field (M69's "type into one you just created"),
    Objects mode moves it. Pinned so making fields movable cannot quietly cost the filling."""
    from PySide6.QtCore import QEvent, QPointF, Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    from model.edit_commands import AddAnnotationCommand
    from model.form_fields import NewField
    from viewer.tools import InteractionMode

    win.undo_stack.push(AddAnnotationCommand(
        win.vdoc, 0, NewField(kind="text", name="who", rect=(100, 300, 300, 330))))
    qapp.processEvents()
    win.view.set_mode(InteractionMode.SELECT)
    point = win.view.mapFromScene(_at(win, 200, 315))
    win.view.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(point), win.view.viewport().mapToGlobal(point),
        _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton, _Qt.KeyboardModifier.NoModifier))
    assert win.view.annotations._move_grabbed is None, \
        "Select mode grabbed the field to move it instead of letting the form overlay fill it"


# ---- a freshly placed mark is selected (M69.15) ----------------------------------
#
# Owner-reported: a form field could not be selected right after creating it. Nothing was selected
# when placement committed, so the next click went to the *form* overlay to be filled — which, to
# someone who had just drawn the box, looked like the field could not be selected at all. Paste has
# selected-after-add since M59.7 for exactly this reason; placement never did.


def _send(win, kind, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt as _Qt
    from PySide6.QtGui import QMouseEvent

    scene = win.view.scene_rect_for_box(0, (x, y, x + 0.01, y + 0.01)).center()
    point = win.view.mapFromScene(scene)
    event = QMouseEvent(kind, QPointF(point), win.view.viewport().mapToGlobal(point),
                        _Qt.MouseButton.LeftButton, _Qt.MouseButton.LeftButton,
                        _Qt.KeyboardModifier.NoModifier)
    {QEvent.Type.MouseButtonPress: win.view.mousePressEvent,
     QEvent.Type.MouseMove: win.view.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: win.view.mouseReleaseEvent}[kind](event)


def _draw_field(win, start=(100, 300), end=(300, 330)):
    """Create a field through the real event path — press, move, release. Driving `finish_draw`
    directly would leave the tool armed (the view disarms on *release*), which is exactly the
    artifact that made this look unreproducible the first time."""
    from PySide6.QtCore import QEvent

    from model.form_fields import NewField
    from viewer.tools import ArmedTool

    win.view.annotations.pending_field = NewField(kind="text", name="who", rect=(0, 0, 1, 1))
    win.view.arm(ArmedTool.FIELD)
    _send(win, QEvent.Type.MouseButtonPress, *start)
    _send(win, QEvent.Type.MouseMove, *end)
    _send(win, QEvent.Type.MouseButtonRelease, *end)


def test_a_freshly_placed_field_is_selected(win, qapp):
    from model.form_fields import NewField

    _draw_field(win)
    qapp.processEvents()
    assert win.view.armed is None                       # the one-shot arm was consumed
    selected = win.view.annotations.selected_objects
    assert [type(m).__name__ for _p, m in selected] == ["NewField"]


def test_a_freshly_placed_field_can_be_dragged_straight_away(win, qapp):
    """The point of selecting it: no mode switch, no marquee — just drag it."""
    from PySide6.QtCore import QEvent

    from model.form_fields import NewField

    _draw_field(win)
    qapp.processEvents()
    _send(win, QEvent.Type.MouseButtonPress, 200, 315)
    assert win.view.annotations.moving is True
    _send(win, QEvent.Type.MouseMove, 250, 360)
    _send(win, QEvent.Type.MouseButtonRelease, 250, 360)
    qapp.processEvents()
    field = [a for a in win.vdoc.page_annotations(0) if isinstance(a, NewField)][0]
    assert field.rect[:2] == pytest.approx((150, 345), abs=2.0)


def test_an_unselected_field_is_still_filled_not_moved(win, qapp):
    """The other half stays true: Select mode on an *unselected* field means "type into it", which
    is M69's "a value typed into a field made this session persists"."""
    from PySide6.QtCore import QEvent

    _draw_field(win)
    qapp.processEvents()
    win.view.annotations.clear_object_selection()
    qapp.processEvents()
    _send(win, QEvent.Type.MouseButtonPress, 200, 315)
    assert win.view.annotations.moving is False
