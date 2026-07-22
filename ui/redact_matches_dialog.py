"""Search & redact — find every occurrence of a string and redact the ones you keep (PLAN.md §R4, M64).

The flow is **mark-all → review → redact-checked**, and the review step is not optional decoration:
a search for ``Smith`` also finds ``Smithsonian``, and redaction is destructive. So the dialog drives
the *real* :class:`~viewer.search.SearchController` — the hits highlight on the page as you type —
and reviews them in the **M47 results panel** with checkboxes, where a doubtful row can be clicked to
jump to it before deciding.

Nothing here is destructive. Checked hits become ordinary :class:`~model.page_edits.Redaction`
descriptors, which stay editable and undoable until the existing confirmed Save applies them. That
keeps one destructive path in the app rather than a second one that would need its own guarantees.

**What it cannot do**, said in the dialog rather than only here (PLAN.md §Design budgets → Honesty):

* **Text layer only.** A scanned page with no text layer has nothing to search; the dialog counts
  those pages and warns, because "0 matches" on an image-only document otherwise reads as "the word
  isn't there" rather than "I cannot see the words at all".
* **Form-field values are not searched.** They live in widget objects, not the page text.
* **A redaction box is as wide as the string it covers**, so a reader can infer the length of what
  was removed. Unavoidable for in-place redaction; worth knowing.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from viewer.search import SearchResultsPanel

_LIMITS = ("Searches the page text layer only — not scanned images without OCR, and not form-field "
           "values. Each box is as wide as the text it covers, which hints at its length.")


def image_only_pages(vdoc) -> list[int]:
    """Pages that carry images but **no text layer** — nothing here can find anything on them.

    The honest counterpart to a "0 matches" result: on a scanned document that is not a statement
    about the word, it is a statement about the page. A page with neither text nor images is simply
    blank and not worth warning about.
    """
    flagged = []
    for index in range(vdoc.page_count):
        ref = vdoc.ordered[index]
        page = vdoc.sources[ref.source_id][ref.source_page_index]
        if not page.get_text("text").strip() and page.get_images():
            flagged.append(index)
    return flagged


class RedactMatchesDialog(QDialog):
    """Search the document, review the hits, and mark the checked ones for redaction."""

    def __init__(self, parent, view) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find and Redact")
        self._view = view

        self.query = QLineEdit()
        self.query.setPlaceholderText("Text to redact")
        self.case_sensitive = QCheckBox("Match case")
        self.whole_word = QCheckBox("Whole words only")
        self.results = SearchResultsPanel(view, checkable=True)
        self.results.setMaximumHeight(240)
        self.results.show()          # inside the dialog it is the content, not an optional band
        self.count_label = QLabel("")
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)

        select_all = QPushButton("Select All")
        select_none = QPushButton("Select None")
        select_all.clicked.connect(lambda: self.results.set_all_checked(True))
        select_none.clicked.connect(lambda: self.results.set_all_checked(False))

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.redact_button = self.buttons.addButton(
            "Mark for Redaction", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.redact_button.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.query)
        toggles = QHBoxLayout()
        toggles.addWidget(self.case_sensitive)
        toggles.addWidget(self.whole_word)
        toggles.addStretch(1)
        toggles.addWidget(select_all)
        toggles.addWidget(select_none)
        layout.addLayout(toggles)
        layout.addWidget(self.results)
        layout.addWidget(self.count_label)
        layout.addWidget(self.warning)
        note = QLabel("Marked text is removed permanently when you save — until then you can undo "
                      "it.\n" + _LIMITS)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(self.buttons)

        self.query.textChanged.connect(self._run_search)
        self.case_sensitive.toggled.connect(self._run_search)
        self.whole_word.toggled.connect(self._run_search)
        self._warn_about_image_pages()

    # ---- searching ------------------------------------------------------------

    def _run_search(self) -> None:
        text = self.query.text()
        count = self._view.search.search(
            text,
            case_sensitive=self.case_sensitive.isChecked(),
            whole_word=self.whole_word.isChecked(),
        )
        self.results.refresh()
        self.redact_button.setEnabled(count > 0)
        if not text:
            self.count_label.setText("")
        elif count:
            self.count_label.setText(f"{count} match{'es' if count != 1 else ''} — "
                                     "untick any you want to keep.")
        else:
            self.count_label.setText("No matches.")

    def _warn_about_image_pages(self) -> None:
        pages = image_only_pages(self._view._vdoc)
        if not pages:
            return
        listed = ", ".join(str(p + 1) for p in pages[:8])
        more = "…" if len(pages) > 8 else ""
        self.warning.setText(
            f"⚠ {len(pages)} page{'s' if len(pages) != 1 else ''} "
            f"({listed}{more}) contain images with no text layer. "
            "Nothing on those pages can be found or redacted here — use Redact Block instead."
        )
        self.warning.setVisible(True)

    # ---- result ---------------------------------------------------------------

    def checked_hits(self) -> list[tuple[int, tuple]]:
        """``(page_index, box)`` for every hit still ticked when the dialog was accepted."""
        return self.results.checked_hits()
