"""Undo/redo commands for page edits (PLAN.md, "Undo/redo").

Each mutating op is a ``QUndoCommand`` that snapshots ``VirtualDocument`` state before applying
and restores it on undo — cheap, because the state is just a tuple of small frozen ``PageRef``s
plus the dirty flag. ``QUndoStack`` (owned by the MainWindow) wires these to Ctrl+Z / Ctrl+Y and
supplies the "Undo *reorder*" labels for free.

Qt's ``QUndoStack``/``QUndoCommand`` live in ``QtGui`` but need **no** ``QApplication`` and no
display, so this module is headless-testable (verified in M1's tests).

Cross-window move is two independent commands on two stacks (delete in B, insert in A) — a known,
documented limitation: undoing the paste in A does not restore the page in B.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtGui import QUndoCommand

from model.virtual_document import PageRef, VirtualDocument


class _SnapshotCommand(QUndoCommand):
    """Base: snapshot-before / snapshot-after, restore either side on undo/redo.

    Subclasses implement :meth:`_apply` (the concrete mutation). The first ``redo`` (Qt calls it
    on push) captures the before/after states; subsequent redo/undo just restore them, so the
    behaviour is identical no matter how complex the op.
    """

    def __init__(self, vdoc: VirtualDocument, text: str) -> None:
        super().__init__(text)
        self._vdoc = vdoc
        self._before = None
        self._after = None

    def _apply(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def redo(self) -> None:
        if self._after is None:
            self._before = self._vdoc.snapshot()
            self._apply()
            self._after = self._vdoc.snapshot()
        else:
            self._vdoc.restore(self._after)

    def undo(self) -> None:
        self._vdoc.restore(self._before)


class MoveCommand(_SnapshotCommand):
    def __init__(self, vdoc: VirtualDocument, from_index: int, to_index: int) -> None:
        super().__init__(vdoc, f"Move page {from_index + 1} → {to_index + 1}")
        self._from, self._to = from_index, to_index

    def _apply(self) -> None:
        self._vdoc.move_page(self._from, self._to)


class MovePagesCommand(_SnapshotCommand):
    """Drag-reorder of one or more selected pages to a drop position."""

    def __init__(self, vdoc: VirtualDocument, src_indices: Iterable[int], before_index: int) -> None:
        self._src = sorted(set(src_indices))
        self._before_index = before_index  # NB: _before is the base class's snapshot slot
        label = "Move page" if len(self._src) == 1 else f"Move {len(self._src)} pages"
        super().__init__(vdoc, label)

    def _apply(self) -> None:
        self._vdoc.move_pages(self._src, self._before_index)


class DeleteCommand(_SnapshotCommand):
    def __init__(self, vdoc: VirtualDocument, indices: Iterable[int]) -> None:
        self._indices = sorted(set(indices))
        label = (
            f"Delete page {self._indices[0] + 1}"
            if len(self._indices) == 1
            else f"Delete {len(self._indices)} pages"
        )
        super().__init__(vdoc, label)

    def _apply(self) -> None:
        self._vdoc.delete_pages(self._indices)


class InsertCommand(_SnapshotCommand):
    """Insert/merge/paste refs at a position (refs' sources must already be registered)."""

    def __init__(
        self, vdoc: VirtualDocument, at_index: int, refs: Iterable[PageRef], text: str = "Insert pages"
    ) -> None:
        super().__init__(vdoc, text)
        self._at, self._refs = at_index, list(refs)

    def _apply(self) -> None:
        self._vdoc.insert_pages(self._at, self._refs)


class RotateCommand(_SnapshotCommand):
    def __init__(self, vdoc: VirtualDocument, index: int, angle: int | None) -> None:
        super().__init__(vdoc, f"Rotate page {index + 1}")
        self._index, self._angle = index, angle

    def _apply(self) -> None:
        self._vdoc.set_rotation(self._index, self._angle)


class RotatePagesCommand(_SnapshotCommand):
    """Rotate one or more pages by a relative ``delta`` (each from its own current angle)."""

    def __init__(self, vdoc: VirtualDocument, indices: Iterable[int], delta: int) -> None:
        self._indices = sorted(set(indices))
        self._delta = delta
        label = "Rotate page" if len(self._indices) == 1 else f"Rotate {len(self._indices)} pages"
        super().__init__(vdoc, label)

    def _apply(self) -> None:
        self._vdoc.rotate_pages(self._indices, self._delta)


class SetFieldValueCommand(_SnapshotCommand):
    """Fill an AcroForm field (M14). Snapshot-based, so undo/redo restores the prior value."""

    def __init__(self, vdoc: VirtualDocument, name: str, value: object) -> None:
        super().__init__(vdoc, f"Fill field “{name}”")
        self._name, self._value = name, value

    def _apply(self) -> None:
        self._vdoc.set_field_value(self._name, self._value)


class AddAnnotationCommand(_SnapshotCommand):
    """Add a highlight / text-box to a page (M20). Snapshot-based, so undo removes it."""

    def __init__(self, vdoc: VirtualDocument, index: int, annotation) -> None:
        super().__init__(vdoc, f"Add {type(annotation).__name__.lower()}")
        self._index, self._annotation = index, annotation

    def _apply(self) -> None:
        self._vdoc.add_annotation(self._index, self._annotation)


class RemoveAnnotationCommand(_SnapshotCommand):
    """Remove a highlight / text-box from a page (M20). Snapshot-based, so undo restores it."""

    def __init__(self, vdoc: VirtualDocument, index: int, annotation) -> None:
        super().__init__(vdoc, f"Remove {type(annotation).__name__.lower()}")
        self._index, self._annotation = index, annotation

    def _apply(self) -> None:
        self._vdoc.remove_annotation(self._index, self._annotation)


class ReplaceAnnotationCommand(_SnapshotCommand):
    """Swap one annotation descriptor for an updated one in place — moving a text box or editing
    its text (M21 follow-up). Snapshot-based, so it is one undo step and keeps z-order."""

    def __init__(self, vdoc: VirtualDocument, index: int, old, new, text: str | None = None) -> None:
        super().__init__(vdoc, text or f"Edit {type(old).__name__.lower()}")
        self._index, self._old, self._new = index, old, new

    def _apply(self) -> None:
        self._vdoc.replace_annotation(self._index, self._old, self._new)
