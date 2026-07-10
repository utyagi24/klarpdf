"""Help ▸ About and Help ▸ Open-Source Licenses (PLAN.md §Public-release readiness, G4).

Two dialogs an AGPL release owes its users:

* **About** — what this is, which version, the licence, the **no-warranty** notice AGPL §15-16
  require be shown prominently, and a link to the *corresponding source* at the matching tag.
* **Open-Source Licenses** — the bundled licence texts themselves, offline. AGPL §5 says the binary
  must ship the licence; showing it in-app is how a GUI honours that without a terminal.

Everything renders from files bundled by ``packaging/klarpdf.spec`` and resolved through
``util.resources`` — no network. The only outbound action is the user *clicking* a link, which hands
off to the system browser via ``QDesktopServices``; the app itself opens no socket, so the
offline / no-telemetry guarantee in PLAN.md still holds.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)

from ui import icons
from util.resources import LICENSE_FILES, read_text_resource
from version import __version__

APP_NAME = "KlarPDF"  # display spelling (assets/brand/BRAND.md §Type); `klarpdf` is the identifier
REPO_URL = "https://github.com/utyagi24/klarpdf"

#: Link to the *exact* source this binary was built from — what AGPL §13 means by "corresponding
#: source". A bare link to `main` would be wrong: main moves, the shipped binary does not.
SOURCE_URL = f"{REPO_URL}/tree/v{__version__}"


def _open_url(url: str) -> None:
    """Hand a URL to the system browser. User-initiated only — never called on a timer or at start."""
    QDesktopServices.openUrl(QUrl(url))


class AboutDialog(QDialog):
    """Name, mark, version, licence, no-warranty notice, and the corresponding-source link."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)

        mark = QLabel()
        mark.setPixmap(icons.app_icon().pixmap(64, 64))
        mark.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(mark)

        title = QLabel(f"<h2 style='margin:6px 0 0 0'>{APP_NAME}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)

        subtitle = QLabel(f"Version {__version__}")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(subtitle)

        blurb = QLabel(
            "A local, offline PDF viewer and page editor for Windows.<br>"
            "It opens no network connection of its own."
        )
        blurb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        # AGPL §15-16: the warranty disclaimer must be conveyed to the user, not buried in a file.
        notice = QLabel(
            f"<p>Copyright © 2026 {APP_NAME} contributors</p>"
            "<p>Licensed under the <b>GNU Affero General Public License v3 or later</b>. "
            "This program comes with <b>ABSOLUTELY NO WARRANTY</b>, to the extent permitted by "
            "law. It is free software, and you are welcome to redistribute it under the terms of "
            "the AGPL.</p>"
            f"<p>Source for this exact build: <a href='{SOURCE_URL}'>v{__version__}</a><br>"
            f"Project: <a href='{REPO_URL}'>{REPO_URL}</a></p>"
        )
        notice.setWordWrap(True)
        notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        notice.setOpenExternalLinks(False)  # route through _open_url so the policy stays in one place
        notice.linkActivated.connect(_open_url)
        layout.addWidget(notice)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class LicensesDialog(QDialog):
    """The bundled licence texts, one tab each, read offline from the app bundle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open-Source Licenses")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        for name in LICENSE_FILES:
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setPlainText(read_text_resource(name))
            # Licence texts are hard-wrapped at ~75 cols; a proportional font ruins the layout.
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            font = view.font()
            font.setStyleHint(font.StyleHint.Monospace)
            font.setFamily("Consolas")
            view.setFont(font)
            self.tabs.addTab(view, name)
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
