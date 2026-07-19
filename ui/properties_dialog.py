"""Document Properties dialog (PLAN.md §GUI feature roadmap, M53).

One dialog, three verbs: **view** (title/author/… plus read-only provenance and file facts),
**edit** (the four user-facing fields), **remove all** (staged by the button, applied on OK —
clears the Info dict *and* the XMP packet at save, or the strip would be a false promise; the
wording says exactly that). The dialog only *stages* — the caller turns :meth:`staged_override`
into an undoable command, so metadata edits ride the undo stack like every other edit.
Lazy-imported by ``main_window`` so no document pays for it on open.
"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from model.metadata import EDITABLE_KEYS

_FIELD_LABELS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
}
_PROVENANCE = (("creator", "Creator"), ("producer", "Producer"),
               ("creationDate", "Created"), ("modDate", "Modified"))


def pdf_date_display(value: str) -> str:
    """A PDF ``D:YYYYMMDDHHmmSS…`` date as a readable string; anything unparseable verbatim."""
    raw = value[2:] if value.startswith("D:") else value
    if len(raw) >= 8 and raw[:8].isdigit():
        text = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if len(raw) >= 14 and raw[8:14].isdigit():
            text += f" {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
        return text
    return value


def _human_file_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / (1024 * 1024):.1f} MB"


class PropertiesDialog(QDialog):
    """View / edit / remove-all over the document's effective metadata."""

    def __init__(self, vdoc, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self._initial = vdoc.effective_metadata()
        self._removed = False

        form = QFormLayout(self)
        self._edits: dict[str, QLineEdit] = {}
        for key in EDITABLE_KEYS:
            edit = QLineEdit(self._initial.get(key, ""))
            # Typing into a field after Remove All turns the stage back into an ordinary edit.
            edit.textEdited.connect(self._unstage_removal)
            self._edits[key] = edit
            form.addRow(_FIELD_LABELS[key] + ":", edit)

        self._provenance: dict[str, QLabel] = {}
        for key, label in _PROVENANCE:
            value = self._initial.get(key, "")
            display = pdf_date_display(value) if key.endswith("Date") else value
            row = QLabel(display or "—")
            self._provenance[key] = row
            form.addRow(label + ":", row)

        # File facts — pure view, never staged: they describe the file, not the metadata.
        path = vdoc.path
        form.addRow("Location:", QLabel(path or "—"))
        size = _human_file_size(os.path.getsize(path)) if path and os.path.exists(path) else "—"
        form.addRow("Size:", QLabel(size))
        form.addRow("Pages:", QLabel(str(vdoc.page_count)))

        self._remove_button = QPushButton("Remove All Metadata")
        self._remove_button.setToolTip(
            "Clears every field above from both metadata stores (the Info dictionary and the "
            "XMP packet) when you click OK — other viewers see them cleared too."
        )
        self._remove_button.clicked.connect(self._stage_removal)
        form.addRow(self._remove_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _stage_removal(self) -> None:
        """Stage remove-all: blank every field so the dialog shows exactly what OK will apply."""
        for edit in self._edits.values():
            edit.clear()
        for row in self._provenance.values():
            row.setText("—")
        self._removed = True  # after the clears — clear() fires textChanged, not textEdited

    def _unstage_removal(self, _text: str) -> None:
        self._removed = False

    def staged_override(self) -> "dict | None":
        """What OK staged: ``{}`` = remove all, a dict = edited values, ``None`` = no change."""
        if self._removed:
            return {}
        values = dict(self._initial)
        for key, edit in self._edits.items():
            values[key] = edit.text()
        return None if values == self._initial else values
