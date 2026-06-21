"""Entry point with the single-instance guard (PLAN.md, Single-instance).

Explorer invokes this as ``pdfproj.exe "%1"`` (frozen) / ``pythonw launcher.py "%1"`` (dev).
Each launch:
 1. normalize ``%1`` and try to hand it to a resident instance (``QLocalSocket``);
 2. if one accepted it → exit with no UI (it raised/opened the window);
 3. otherwise become the resident instance (``QLocalServer.listen``) — re-trying a hand-off if we
    lost a startup race — then open the document and run the event loop.

Because the resident instance owns every document window, re-opening an already-open file just
raises its window (one window per document) and the page clipboard spans all windows.

    python launcher.py path/to/file.pdf
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QFileDialog

from app import PdfApp, send_path_to_running_instance
from platform_integration import single_instance_server_name


def silence_mupdf_console_noise() -> None:
    """Stop MuPDF printing non-fatal diagnostics to the console.

    MuPDF writes messages like ``MuPDF error: format error: No common ancestor in structure tree``
    straight to stderr when it opens/renders some tagged PDFs. They are harmless — the document
    still opens and renders — but they clutter the console. Disable the *display* only; genuine
    failures still raise Python exceptions (surfaced as dialogs), and the messages remain
    retrievable via ``fitz.TOOLS.mupdf_warnings()`` for debugging.
    """
    import pymupdf as fitz

    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)


def main(argv: list[str]) -> int:
    silence_mupdf_console_noise()
    app = PdfApp(argv)
    name = single_instance_server_name()

    raw_path = argv[1] if len(argv) > 1 else None

    # 1) A resident instance already running? Hand off the path **as given** and exit (no UI here).
    #    We hand off the *raw* path, NOT normalize_path(it): the resident instance computes the
    #    normalised identity key itself, but it must OPEN the original path. normalize_path lower-cases
    #    (Windows case-fold), which names a non-existent file on a **case-sensitive** share such as a
    #    `\\wsl.localhost\` (WSL) mount — so handing off a normalised path opened the first file (this
    #    process opens raw_path directly) but failed for every subsequent file routed via the server.
    if raw_path and send_path_to_running_instance(name, raw_path):
        return 0

    # 2) Become the resident instance. If listen fails we lost a race to another launch that
    #    just became the server — hand off to it; only as a last resort run degraded.
    if not app.start_server(name):
        if raw_path and send_path_to_running_instance(name, raw_path):
            return 0

    path = raw_path
    if not path:
        path, _ = QFileDialog.getOpenFileName(None, "Open PDF", "", "PDF files (*.pdf)")
    if not path:
        return 0  # nothing to show

    app.open_document(path)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
