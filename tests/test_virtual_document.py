"""VirtualDocument list-edit ops + undo/redo round-trips (headless, no Qt display)."""

from __future__ import annotations

from PySide6.QtGui import QUndoStack

from model.edit_commands import DeleteCommand, InsertCommand, MoveCommand, RotateCommand
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
