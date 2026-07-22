"""Signature / image stamp dialog (PLAN.md §R4, M63).

The sign-and-return workflow: place a scanned signature, seal or logo, then save and send it back.
Three things make it work offline in two clicks on the second use:

* **Recent signatures**, offered first — the second use is "pick the one from last time";
* **"Make white background transparent"**, so a *phone photo* of a signature on paper works without
  an image editor (a transparent PNG already works through its own alpha);
* **a live preview**, because the threshold is the one setting whose right value you can only see.

**Paths only, never pixels.** The recent list stores the file paths the user chose — KlarPDF keeps no
copy of a signature image anywhere. Moving or deleting the file revokes it; that is the whole
mechanism, and it is the same one Open Recent uses.

**Honesty (PLAN.md §Design budgets):** this is an *image* of a signature — ink-equivalent, exactly as
binding as a faxed one, and **not** a cryptographic digital signature. The dialog says so.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from model.content_marks import ImageStamp, render_mark_document

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)"

_NOT_CRYPTO = ("This places a picture of your signature — ink-equivalent, like a faxed one. "
               "It is not a cryptographic digital signature.")

# The preview panel, in device pixels. Big enough to judge whether the background dropped out
# cleanly, small enough that re-rendering on every slider step stays instant.
_PREVIEW = (320, 150)


class SignatureDialog(QDialog):
    """Choose an image, tune its background removal, and get back an :class:`ImageStamp` template."""

    def __init__(self, parent, recent: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Place Signature or Image")
        self._path: str | None = None

        self.recent = QListWidget()
        self.recent.setMaximumHeight(110)
        for path in recent:
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)                       # the full path is visible, never hidden
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.recent.addItem(item)
        self.recent.currentItemChanged.connect(self._on_recent_chosen)

        self.browse = QPushButton("Choose Image…")
        self.browse.clicked.connect(self._browse)

        self.transparent = QCheckBox("Make white background transparent")
        self.transparent.setToolTip(
            "For a photo or scan of a signature on paper. A PNG that is already transparent "
            "does not need this."
        )
        self.transparent.toggled.connect(self._sync_threshold)
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(50, 99)
        self.threshold.setValue(85)
        self.threshold.setEnabled(False)
        self.threshold.valueChanged.connect(self._refresh_preview)
        self.transparent.toggled.connect(self._refresh_preview)

        self.preview = QLabel("Choose an image to preview it here.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(*_PREVIEW)
        # A checkerboard would be nicer, but a plain mid-grey is enough to see whether the white
        # background actually went — and it needs no asset.
        self.preview.setStyleSheet("background: #808080; border: 1px solid #606060;")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._set_ok_enabled(False)

        layout = QVBoxLayout(self)
        if recent:
            layout.addWidget(QLabel("Recent:"))
            layout.addWidget(self.recent)
        else:
            self.recent.setVisible(False)   # no dead chrome on the first use (owner rule)
        row = QHBoxLayout()
        row.addWidget(self.browse)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self.preview)
        layout.addWidget(self.transparent)
        layout.addWidget(self.threshold)
        note = QLabel(_NOT_CRYPTO)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(self.buttons)

        if recent:
            self.recent.setCurrentRow(0)    # the second use is one click away

    # ---- choosing -------------------------------------------------------------

    def _set_ok_enabled(self, enabled: bool) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _on_recent_chosen(self, item) -> None:
        if item is not None:
            self.set_path(item.data(Qt.ItemDataRole.UserRole))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a signature or image", "", IMAGE_FILTER)
        if path:
            self.recent.setCurrentItem(None)
            self.set_path(path)

    def set_path(self, path: str) -> None:
        self._path = path
        self._set_ok_enabled(bool(path) and os.path.exists(path))
        self._refresh_preview()

    def path(self) -> str | None:
        return self._path

    def _sync_threshold(self, on: bool) -> None:
        self.threshold.setEnabled(on)

    # ---- preview --------------------------------------------------------------

    def _refresh_preview(self) -> None:
        """Render the *actual* mark and show it, so the threshold is judged on the real result.

        Reuses :func:`~model.content_marks.render_mark_document` — the same generator that bakes at
        save — rather than a Qt-side approximation, so what is judged here cannot drift from what
        lands in the file.
        """
        if not self._path:
            return
        mark = self.image_stamp((0, 0, _PREVIEW[0], _PREVIEW[1]))
        try:
            art = render_mark_document(mark)
        except Exception:
            self.preview.setText("Could not read that image.")
            self._set_ok_enabled(False)
            return
        try:
            pix = art[0].get_pixmap(alpha=True)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                           QImage.Format.Format_RGBA8888).copy()
        finally:
            art.close()
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                *_PREVIEW, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ---- result ---------------------------------------------------------------

    def image_stamp(self, rect=(0.0, 0.0, 1.0, 1.0)) -> ImageStamp:
        """The composed descriptor. ``rect`` is a placeholder — the placement drag supplies the real
        one (the preview passes its own panel size to render at a sensible aspect)."""
        return ImageStamp(
            rect=rect,
            image_path=self._path or "",
            white_to_alpha=self.transparent.isChecked(),
            white_threshold=self.threshold.value() / 100.0,
        )
