"""Entry point: launch pdfproj on a PDF path (or prompt for one).

Explorer invokes this as ``pdfproj.exe "%1"`` (frozen) / ``pythonw launcher.py "%1"`` (dev). In
M5 this grows the single-instance guard (normalize ``%1``, hand off to the resident instance via
``QLocalServer`` if one exists, else become it — PLAN.md, Single-instance). For M2 it just starts
the app and opens the requested document.

    python launcher.py path/to/file.pdf
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QFileDialog

from app import PdfApp


def main(argv: list[str]) -> int:
    app = PdfApp(argv)

    path = argv[1] if len(argv) > 1 else None
    if not path:
        path, _ = QFileDialog.getOpenFileName(None, "Open PDF", "", "PDF files (*.pdf)")
    if not path:
        return 0  # user cancelled with no document to show

    app.open_document(path)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
