"""VirtualDocument list-edit ops + undo/redo round-trips (headless, no Qt display)."""

from __future__ import annotations

from PySide6.QtGui import QUndoStack

from model.edit_commands import (
    DeleteCommand,
    InsertCommand,
    MoveCommand,
    RotateCommand,
    RotatePagesCommand,
)
from model.virtual_document import PageRef, VirtualDocument
from util.paths import normalize_path


def _indices(vd: VirtualDocument) -> list[int]:
    """The source page indices in current order (all from the origin in these cases)."""
    return [r.source_page_index for r in vd.ordered]


def test_from_path_seeds_ordered(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    assert vd.page_count == 3
    assert _indices(vd) == [0, 1, 2]
    assert all(r.source_id == normalize_path(a_pdf) for r in vd.ordered)
    assert vd.dirty is False  # opening is not an edit


def test_move_delete_insert_rotate(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    vd.move_page(0, 2)
    assert _indices(vd) == [1, 2, 0]
    vd.delete_page(1)
    assert _indices(vd) == [1, 0]
    vd.set_rotation(0, 90)
    assert vd.ordered[0].rotation_override == 90
    assert vd.dirty is True


def test_set_rotation_rejects_non_multiples(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    try:
        vd.set_rotation(0, 45)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-multiple-of-90 rotation")


def test_rotate_pages_is_relative_to_current_angle(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)  # fixture pages have native rotation 0
    vd.rotate_pages([0, 2], 90)
    assert vd.ordered[0].rotation_override == 90
    assert vd.ordered[2].rotation_override == 90
    assert vd.ordered[1].rotation_override is None  # untouched
    vd.rotate_pages([0], 90)  # 90 -> 180 (relative to current)
    assert vd.ordered[0].rotation_override == 180
    vd.rotate_pages([0], -90)  # 180 -> 90, and wraps via %360
    assert vd.ordered[0].rotation_override == 90
    assert vd.dirty is True


def test_rotate_pages_command_roundtrip(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    stack = QUndoStack()
    stack.push(RotatePagesCommand(vd, [0, 1], 90))
    assert [vd.ordered[i].rotation_override for i in (0, 1, 2)] == [90, 90, None]
    stack.undo()
    assert all(r.rotation_override is None for r in vd.ordered)
    stack.redo()
    assert [vd.ordered[i].rotation_override for i in (0, 1, 2)] == [90, 90, None]


def test_merge_requires_registered_source(a_pdf, b_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    bogus = PageRef("not-registered", 0)
    try:
        vd.insert_pages(0, [bogus])
    except KeyError:
        pass
    else:
        raise AssertionError("inserting an unregistered source should raise")


def test_cross_window_copy_via_import(a_pdf, b_pdf):
    dst = VirtualDocument.from_path(a_pdf)
    src = VirtualDocument.from_path(b_pdf)
    dst.import_pages(dst.page_count, src, [0])
    assert dst.page_count == 4
    assert dst.ordered[-1].source_id == normalize_path(b_pdf)
    # Copy leaves the source untouched.
    assert src.page_count == 2


# ---- undo/redo via the real QUndoStack --------------------------------------


def test_move_command_undo_redo(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    stack = QUndoStack()
    before = vd.snapshot()

    stack.push(MoveCommand(vd, 0, 2))
    assert _indices(vd) == [1, 2, 0]
    assert vd.dirty is True

    stack.undo()
    assert vd.snapshot() == before  # ordered AND dirty restored exactly
    assert vd.dirty is False

    stack.redo()
    assert _indices(vd) == [1, 2, 0]
    assert vd.dirty is True


def test_delete_and_insert_command_undo_redo(a_pdf, b_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    b_id = vd.open_source(b_pdf)
    stack = QUndoStack()

    stack.push(DeleteCommand(vd, [1]))
    assert _indices(vd) == [0, 2]

    stack.push(InsertCommand(vd, 1, [PageRef(b_id, 0)], text="Merge B p1"))
    assert vd.page_count == 3
    assert vd.ordered[1].source_id == b_id

    stack.undo()  # undo insert
    assert vd.page_count == 2
    stack.undo()  # undo delete
    assert _indices(vd) == [0, 1, 2]


def test_rotate_command_label_and_roundtrip(a_pdf):
    vd = VirtualDocument.from_path(a_pdf)
    stack = QUndoStack()
    cmd = RotateCommand(vd, 1, 270)
    assert cmd.text() == "Rotate page 2"
    stack.push(cmd)
    assert vd.ordered[1].rotation_override == 270
    stack.undo()
    assert vd.ordered[1].rotation_override is None


# ---- annotation edits: identity first, value as a safety net -----------------
#
# Descriptors are frozen value objects, so a caller can hand back an equal-but-*distinct* copy of
# a mark it is holding. Identity-only matching made that fail silently (a moved object's
# re-selection once held such a copy, so the next resize no-opped) — these lock the fallback in.


def test_replace_annotation_accepts_an_equal_but_distinct_descriptor(a_pdf):
    from model.page_edits import Line

    vd = VirtualDocument.from_path(a_pdf)
    vd.add_annotation(0, Line((10.0, 10.0), (50.0, 50.0)))
    stale = Line((10.0, 10.0), (50.0, 50.0))          # equal by value, a different instance
    assert stale is not vd.page_annotations(0)[0] and stale == vd.page_annotations(0)[0]
    vd.replace_annotation(0, stale, Line((10.0, 10.0), (99.0, 99.0)))
    assert vd.page_annotations(0)[0].end == (99.0, 99.0)   # the edit landed, not a silent no-op


def test_remove_annotation_accepts_an_equal_but_distinct_descriptor(a_pdf):
    from model.page_edits import Line

    vd = VirtualDocument.from_path(a_pdf)
    vd.add_annotation(0, Line((10.0, 10.0), (50.0, 50.0)))
    vd.remove_annotation(0, Line((10.0, 10.0), (50.0, 50.0)))
    assert vd.page_annotations(0) == ()


def test_replace_annotation_ignores_a_mark_the_page_does_not_have(a_pdf):
    from model.page_edits import Line

    vd = VirtualDocument.from_path(a_pdf)
    vd.add_annotation(0, Line((10.0, 10.0), (50.0, 50.0)))
    vd.replace_annotation(0, Line((0.0, 0.0), (1.0, 1.0)), Line((5.0, 5.0), (6.0, 6.0)))
    assert vd.page_annotations(0)[0].end == (50.0, 50.0)   # untouched
