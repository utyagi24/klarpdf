"""New form-field properties (PLAN.md §R5, M69).

A small dialog: type, name, default value, and — for a dropdown — its choices. Composed first, then
the field's box is dragged on the page, reusing M62's placement gesture.

**The name is the field's identity**, not decoration: AcroForm keys values by field name, so two
fields sharing a name share a value. The dialog therefore requires one and warns when it collides
with a field the document already has — a collision is occasionally deliberate (the same value on
several pages) and often a mistake, so it informs rather than blocks.

**Radio-button groups are absent** by owner decision (2026-07-18): a group is several widgets
sharing a field name with one export value each, needing group-aware placement and editing UI the
other three types do not, for a control a checkbox usually replaces.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from model.form_fields import FIELD_KINDS, NewField, kind_label


class FieldDialog(QDialog):
    """Compose a :class:`NewField`; the caller then arms the placement drag."""

    def __init__(self, parent, kind: str = "text", existing_names=()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Form Field")
        self._existing = {name for name in existing_names}

        self.kind = QComboBox()
        for value in FIELD_KINDS:
            self.kind.addItem(kind_label(value), value)
        self.kind.setCurrentIndex(max(0, FIELD_KINDS.index(kind) if kind in FIELD_KINDS else 0))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. full_name")
        self.value = QLineEdit()
        self.value.setPlaceholderText("optional")
        self.options = QPlainTextEdit()
        self.options.setPlaceholderText("One choice per line")
        self.options.setMaximumHeight(90)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)

        form = QFormLayout()
        form.addRow("Type", self.kind)
        form.addRow("Name", self.name)
        form.addRow("Default", self.value)
        self._options_row = self.options
        form.addRow("Choices", self.options)
        self._form = form

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.warning)
        layout.addWidget(QLabel(
            "The field is created when you save. Until then you can move, resize and undo it."
        ))
        layout.addWidget(self.buttons)

        self.kind.currentIndexChanged.connect(self._sync)
        self.name.textChanged.connect(self._sync)
        self._sync()

    # ---- state ----------------------------------------------------------------

    def selected_kind(self) -> str:
        return self.kind.currentData()

    def _sync(self) -> None:
        """Choices only exist for a dropdown (no dead chrome); OK needs a name; warn on a clash."""
        is_dropdown = self.selected_kind() == "dropdown"
        self.options.setVisible(is_dropdown)
        label = self._form.labelForField(self.options)
        if label is not None:
            label.setVisible(is_dropdown)

        name = self.name.text().strip()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(name))
        if name and name in self._existing:
            self.warning.setText(
                f"“{name}” already exists in this document. Fields sharing a name share a value — "
                "which is sometimes what you want, and sometimes a mistake."
            )
            self.warning.setVisible(True)
        else:
            self.warning.setVisible(False)

    def field(self, rect=(0.0, 0.0, 1.0, 1.0)) -> NewField:
        """The composed descriptor at a placeholder rect — the placement drag supplies the real one."""
        options = tuple(
            line.strip() for line in self.options.toPlainText().splitlines() if line.strip()
        )
        return NewField(
            rect=rect,
            name=self.name.text().strip(),
            kind=self.selected_kind(),
            value=self.value.text().strip(),
            options=options if self.selected_kind() == "dropdown" else (),
        )
