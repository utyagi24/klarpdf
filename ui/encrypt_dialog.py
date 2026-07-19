"""Password Protection dialog (PLAN.md §GUI feature roadmap, M54).

One save-path capability, four verbs. For an unprotected document the dialog is **Set
Password**: type twice + the unrecoverable-if-lost warning + the advisory restriction flags with
honest wording ("honored by most viewers; not cryptographically enforced" — only the password
is). For a protected document it offers **Change** (current + new twice) and **Remove** (current
password required — the Done-when). The dialog only validates and *stages*; the caller turns
:meth:`staged` into one undoable command, and the carry-through happens at save. Passwords stay
in widget memory only. Lazy-imported by ``main_window``.
"""

from __future__ import annotations

import pymupdf as fitz
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
)

# The advisory flag groups. Accessibility extraction stays always-allowed (never a checkbox —
# restricting screen readers hurts real people and stops no determined copier).
_BASE = fitz.PDF_PERM_ACCESSIBILITY
FLAG_GROUPS = (
    ("Allow printing", fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ),
    ("Allow copying text and images", fitz.PDF_PERM_COPY),
    (
        "Allow editing and form filling",
        fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ASSEMBLE,
    ),
)

_LOSS_WARNING = (
    "If you lose the password it cannot be recovered — the document becomes permanently "
    "unreadable. KlarPDF never stores it."
)
_FLAGS_HONESTY = (
    "Restrictions are honored by most viewers but are not cryptographically enforced; "
    "only the password itself is."
)


class PasswordDialog(QDialog):
    """Set (unprotected document) or Change / Remove (protected) the save password."""

    def __init__(self, vdoc, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Password Protection")
        self._current_password = vdoc.password  # memory-only; compared, never displayed
        self._staged: "tuple | None" = None
        form = QFormLayout(self)

        protected = self._current_password is not None
        self._remove = None
        self._current = None
        if protected:
            form.addRow(QLabel("This document is protected with a password (AES-256)."))
            self._current = QLineEdit()
            self._current.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Current password:", self._current)
            self._change = QRadioButton("Change password")
            self._remove = QRadioButton("Remove password")
            self._change.setChecked(True)
            form.addRow(self._change)
            form.addRow(self._remove)
            # The new-password rows only mean anything for Change; greyed under Remove.
            self._remove.toggled.connect(self._on_remove_toggled)

        self._new = QLineEdit()
        self._new.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password:" if protected else "Password:", self._new)
        form.addRow("Confirm:", self._confirm)

        self._flags: list[QCheckBox] = []
        for label, bits in FLAG_GROUPS:
            box = QCheckBox(label)
            box.setChecked(vdoc.permissions == -1 or bool(vdoc.permissions & bits))
            self._flags.append(box)
            form.addRow(box)
        flags_note = QLabel(_FLAGS_HONESTY)
        flags_note.setWordWrap(True)
        form.addRow(flags_note)

        warning = QLabel(_LOSS_WARNING)
        warning.setWordWrap(True)
        form.addRow(warning)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #c33;")
        form.addRow(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_remove_toggled(self, removing: bool) -> None:
        for widget in (self._new, self._confirm, *self._flags):
            widget.setEnabled(not removing)

    def _permissions(self) -> int:
        if all(box.isChecked() for box in self._flags):
            return -1  # nothing restricted → the plain everything-allowed encryption
        bits = _BASE
        for box, (_label, group) in zip(self._flags, FLAG_GROUPS):
            if box.isChecked():
                bits |= group
        return bits

    def _validate_and_accept(self) -> None:
        """OK: validate in place (the dialog stays open on an error) and stage the verb."""
        if self._current is not None and self._current.text() != self._current_password:
            self._error.setText("The current password is not correct.")
            return
        if self._remove is not None and self._remove.isChecked():
            self._staged = (None, -1)
            self.accept()
            return
        if not self._new.text():
            self._error.setText("Enter a password.")
            return
        if self._new.text() != self._confirm.text():
            self._error.setText("The passwords do not match. Type the same password twice.")
            return
        self._staged = (self._new.text(), self._permissions())
        self.accept()

    def staged(self) -> "tuple | None":
        """After an accepted exec: ``(password, permissions)`` — password ``None`` = remove."""
        return self._staged
