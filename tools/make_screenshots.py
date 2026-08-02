"""Regenerate the README app screenshots — `assets/screenshots/klarpdf-{light,dark}.png`.

    .venv/Scripts/python.exe tools/make_screenshots.py

**Why this file exists.** The v0.16 screenshots were captured by hand and the recipe was not kept,
so by v0.17 they showed a toolbar two milestones out of date (no M91.3 page counter, the pre-M91.2
rotate glyph) and nobody could tell without comparing pixel by pixel. `README.md` is the repo's shop
window and `CLAUDE.md` already names it as the one file that must be updated on every release; this
makes the screenshot part of that a command rather than an afternoon.

**The document is generated here, on purpose.** It is written for the shot — four pages of plausible
"field notes" about handling untrusted PDFs — so the screenshot never contains anything real. Never
point this at a document off the machine: whatever is in frame is published to a public repo.

**Theme is forced through `QStyleHints.setColorScheme()`**, not by changing a Windows setting. The
app already follows the platform palette, so this drives the same code path a real theme switch
does, and both shots come out of one run at identical geometry (`assets/brand/BRAND.md` §GitHub
assets).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf as fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from app import PdfApp

WIDTH, HEIGHT = 1360, 860          # what README.md has always been laid out against
ZOOM = 1.8
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "screenshots")

TITLE = "Handling PDFs You Did Not Make"
PAGES = [
    ("Handling PDFs You Did Not Make", [
        ("body",
         "A PDF is a container, not a document format you can trust by eye. Text you cannot see is "
         "still text: it lives in the content stream whether or not a black rectangle sits on top of "
         "it. This is why 'redaction' that draws a box is not redaction at all: the words "
         "survive a copy-paste, a text extractor, or any tool that reads the stream directly rather "
         "than the rendering."),
        ("head", "Why a second engine"),
        ("body",
         "KlarPDF removes the glyphs themselves and verifies the result against a second, "
         "independent engine before calling the page clean. The check is the point: an editor that "
         "trusts its own output has only proved that it is self-consistent."),
        ("head", "What the page does not show"),
        ("body",
         "The same reasoning applies to everything the file carries but never paints — earlier "
         "revisions, author metadata, internal links pointing at pages that once existed. None of it "
         "is visible on the page, and all of it travels with the file until something removes it on "
         "purpose."),
    ]),
    ("A viewer that is also an editor", [
        ("body",
         "Reading and editing are the same task, interrupted at different moments. KlarPDF keeps the "
         "reading surface calm — a resting toolbar, continuous scroll, a two-page thumbnail rail "
         "— and summons the markup kit only when you reach for it."),
        ("head", "Splice and split"),
        ("body",
         "Drag pages to reorder them, drag a second PDF in from Explorer to merge, extract a range to "
         "its own file, or copy and paste pages between two open documents. Every edit is lossless: "
         "the text layer, form fields, bookmarks and internal links all survive."),
        ("head", "Mark it up"),
        ("body",
         "Highlight, underline and strike through text; draw with a pen, lines, arrows and shapes; "
         "drop a stamped box, a stamp, or a photo of your signature. Re-marking merges instead of "
         "stacking, and every mark stays editable."),
    ]),
    ("Redaction that removes, not hides", [
        ("body",
         "Drag across a passage and the words are struck from the content stream, not painted over. "
         "Before the page is called clean a second, independent engine confirms the glyphs are gone "
         "— on a copy-paste, a text extractor, or a raw stream reader turns up nothing where the "
         "ink used to be."),
        ("head", "Find, then redact"),
        ("body",
         "Search the whole document, review every hit in the results panel, check the ones that "
         "matter, and redact them in a single, undoable step. Image-only pages are named rather than "
         "silently reported as zero matches."),
        ("head", "One point of no return"),
        ("body",
         "Because redaction is destructive by design, it is the one action the app will not let you "
         "fumble: it asks once, applies at save, and leaves a file you can hand to anyone."),
    ]),
    ("Offline, pinned, auditable", [
        ("body",
         "KlarPDF makes no network calls at all — no telemetry, no update check, no font or asset "
         "fetch. The installer is built from a hash-pinned lock against a vendored wheel cache, so a "
         "rebuild of the same tag produces the same dependency set."),
        ("head", "Your files stay yours"),
        ("body",
         "Documents are opened from disk and written back to disk. Nothing is uploaded, nothing is "
         "cached anywhere but your own machine, and the only thing the app remembers between sessions "
         "is which page you were reading."),
        ("head", "Open source"),
        ("body",
         "AGPL-3.0-or-later, with the corresponding source for every release tagged in this "
         "repository."),
    ]),
]


#: Characters the base-14 fonts render as "?" — normalised in one place rather than policed in the
#: prose above, because a single missed em-dash puts a question mark in the repo's shop window (it
#: did, on this tool's first run).
_SUBSTITUTIONS = {"—": " - ", "–": "-", "‘": "'", "’": "'",
                  "“": '"', "”": '"'}


def _plain(text: str) -> str:
    for bad, good in _SUBSTITUTIONS.items():
        text = text.replace(bad, good)
    return text


def build_document(path: str) -> None:
    doc = fitz.open()
    for number, (heading, blocks) in enumerate(PAGES, start=1):
        page = doc.new_page(width=612, height=792)
        y = 96
        page.insert_text((72, y), _plain(heading), fontsize=19, fontname="hebo",
                         color=(0.07, 0.09, 0.15))
        y += 22
        page.insert_text((72, y), f"Field notes   ·   page {number} of {len(PAGES)}",
                         fontsize=10.5, fontname="helv", color=(0.42, 0.45, 0.52))
        y += 12
        page.draw_line(fitz.Point(72, y), fitz.Point(540, y), color=(0.85, 0.87, 0.90), width=0.6)
        y += 26
        for kind, text in blocks:
            if kind == "head":
                y += 8
                page.insert_text((72, y), _plain(text), fontsize=13, fontname="hebo",
                                 color=(0.07, 0.09, 0.15))
                y += 20
                continue
            box = fitz.Rect(72, y - 12, 540, y + 200)
            used = page.insert_textbox(box, _plain(text), fontsize=11, fontname="helv",
                                       lineheight=1.55, color=(0.13, 0.16, 0.22))
            y += (200 - used) + 14
    doc.save(path)
    doc.close()


def capture(app, doc_path: str, scheme, name: str) -> None:
    """Open a **fresh window** under ``scheme`` and photograph it.

    **The scheme is set before the window exists, and that is the whole trick.** Setting it on a
    window already built leaves Qt's own text widgets unpainted: measured, the page field held "1"
    and reported itself visible, yet both it and the zoom box photographed **blank**, so the first
    pass of this tool produced a shop-window screenshot with a hollow toolbar. Widgets pick the
    palette up when they are polished, so a fresh window under the new scheme renders correctly.
    """
    QGuiApplication.styleHints().setColorScheme(scheme)
    for _ in range(10):
        app.processEvents()

    win = app.open_document(doc_path)
    win.resize(WIDTH, HEIGHT)
    win.show()
    app.processEvents()

    # The sidebar is what the shot is framed around; the zoom makes the body text legible at the
    # width README renders it. Both are set after show(), so the fit does not overwrite them.
    win.pages_dock.setVisible(True)
    win.view.set_zoom(ZOOM)
    win.view.goto_page(0)
    app.processEvents()
    # Both toolbar fields are driven by *signals* (`zoomChanged`, `currentPageChanged`), and neither
    # fires when the value it would announce is the one already held — `set_zoom` returns early on an
    # unchanged zoom, and page 0 was already current. So a scripted setup can leave the boxes
    # **blank**, which is exactly how the first run of this tool came out. Ask them to show their
    # value directly; they read it back from the view, which is the source of truth either way.
    win.zoom_widget.show_zoom(win.view.zoom)
    win.page_widget.show_page(win.view.current_page)
    win.page_widget.show_count()
    # Frame the page against the top of the strip rather than the scene's margin.
    win.view.verticalScrollBar().setValue(win.view.verticalScrollBar().minimum())
    app.processEvents()

    win.raise_()
    win.activateWindow()
    deadline = time.monotonic() + 2.0        # let the first render and the thumbnails land
    while time.monotonic() < deadline:
        app.processEvents()

    # **`QWidget.grab()` is not usable here** either: it re-renders the tree offscreen, and in that
    # path the same text widgets come out empty. Grabbing the composited window from the screen
    # captures what a reader actually sees, which is what a screenshot is for.
    screen = win.screen() or QGuiApplication.primaryScreen()
    image = screen.grabWindow(win.winId()).toImage()
    if image.width() != WIDTH or image.height() != HEIGHT:
        image = image.scaled(WIDTH, HEIGHT, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    out = os.path.join(OUT, name)
    image.save(out, "PNG")
    blank = not win.page_widget.field.text() or not win.zoom_widget.currentText()
    print(f"wrote {out}  {image.width()}x{image.height()}"
          f"{'   WARNING: a toolbar field is empty' if blank else ''}")

    win.undo_stack.setClean()
    win.close()
    app.processEvents()


def main() -> None:
    if "QT_QPA_PLATFORM" in os.environ:
        sys.exit("run this on a real display — an offscreen grab loses the native palette")
    os.makedirs(OUT, exist_ok=True)
    doc_path = os.path.join(OUT, "_demo.pdf")
    build_document(doc_path)

    app = PdfApp.instance() or PdfApp([])
    capture(app, doc_path, Qt.ColorScheme.Light, "klarpdf-light.png")
    capture(app, doc_path, Qt.ColorScheme.Dark, "klarpdf-dark.png")
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
    os.remove(doc_path)


if __name__ == "__main__":
    main()
