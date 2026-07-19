"""Reduce File Size dialog (PLAN.md §GUI feature roadmap, M52).

Presets are named by intent but **show their true values** — "Screen — 150 dpi, JPEG 75", not a
synthetic "% compression" slider — and Custom exposes exactly the two real knobs the reduction
has (target dpi, JPEG quality). The honesty wording states what the operation actually does:
images above the target are downsampled + re-encoded (detail permanently gone *in the copy*),
fonts are subset. Lazy-imported by ``main_window`` so no document pays for it on open.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

# (name, target dpi, JPEG quality) — the *real* values, shown verbatim in the labels.
PRESETS = (
    ("Screen", 150, 75),
    ("Print", 300, 85),
)

_HONESTY = (
    "Images above the target resolution are downsampled and re-encoded as JPEG, and fonts are "
    "subset to the glyphs used. The removed detail is permanently gone in the reduced copy; "
    "the open document and its file are not changed."
)


def preset_label(name: str, dpi: int, quality: int) -> str:
    """The intent name with its true values — the no-synthetic-numbers rule, single-sourced."""
    return f"{name} — {dpi} dpi, JPEG {quality}"


def human_size(n: int) -> str:
    """A byte count for the actual before → after report (whole KB under 1 MB, one decimal up)."""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


class ReduceSizeDialog(QDialog):
    """Choose the reduction: a true-value preset, or Custom's two real knobs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reduce File Size")
        col = QVBoxLayout(self)

        self._preset_buttons: list[QRadioButton] = []
        for name, dpi, quality in PRESETS:
            button = QRadioButton(preset_label(name, dpi, quality))
            self._preset_buttons.append(button)
            col.addWidget(button)
        self._preset_buttons[0].setChecked(True)  # Screen — the everyday share-by-mail intent

        self._custom = QRadioButton("Custom:")
        col.addWidget(self._custom)
        row = QHBoxLayout()
        row.setContentsMargins(24, 0, 0, 0)  # indented under its radio
        self._dpi = QSpinBox()
        self._dpi.setRange(36, 600)
        self._dpi.setValue(PRESETS[0][1])
        self._dpi.setSuffix(" dpi")
        self._quality = QSpinBox()
        self._quality.setRange(10, 95)
        self._quality.setValue(PRESETS[0][2])
        row.addWidget(QLabel("Target resolution"))
        row.addWidget(self._dpi)
        row.addSpacing(12)
        row.addWidget(QLabel("JPEG quality"))
        row.addWidget(self._quality)
        row.addStretch(1)
        col.addLayout(row)
        # The knobs only mean anything in Custom — enabled with it, greyed under a preset (inside
        # a dialog, disabling keeps the layout stable; the hide-entirely rule is for chrome).
        for widget in (self._dpi, self._quality):
            widget.setEnabled(False)
        self._custom.toggled.connect(self._dpi.setEnabled)
        self._custom.toggled.connect(self._quality.setEnabled)

        honesty = QLabel(_HONESTY)
        honesty.setWordWrap(True)
        col.addWidget(honesty)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

    def chosen(self) -> tuple[int, int]:
        """The selected ``(target dpi, JPEG quality)`` — a preset's true values, or the knobs."""
        if self._custom.isChecked():
            return self._dpi.value(), self._quality.value()
        for button, (_name, dpi, quality) in zip(self._preset_buttons, PRESETS):
            if button.isChecked():
                return dpi, quality
        return PRESETS[0][1], PRESETS[0][2]
