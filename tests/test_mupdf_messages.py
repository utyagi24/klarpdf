"""MuPDF console-noise suppression (launcher.silence_mupdf_console_noise).

MuPDF prints non-fatal diagnostics (e.g. "No common ancestor in structure tree") to the console;
the launcher disables that display at startup. This asserts the toggle is applied without affecting
the still-collected warnings.
"""

from __future__ import annotations

import pymupdf as fitz

from launcher import silence_mupdf_console_noise


def test_disables_mupdf_message_display():
    fitz.TOOLS.mupdf_display_errors(True)  # start from the noisy default
    fitz.TOOLS.mupdf_display_warnings(True)
    try:
        silence_mupdf_console_noise()
        assert fitz.TOOLS.mupdf_display_errors() is False
        assert fitz.TOOLS.mupdf_display_warnings() is False
    finally:
        # Restore Qt-default-ish state so we don't leak global config to other tests.
        fitz.TOOLS.mupdf_display_errors(True)
        fitz.TOOLS.mupdf_display_warnings(False)
