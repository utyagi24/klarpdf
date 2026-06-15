"""Settings store round-trips per-document view state (no Qt display / no QApplication)."""

from __future__ import annotations

from store.settings import Settings


def test_get_missing_returns_empty(tmp_path):
    s = Settings(tmp_path / "view_state.json")
    assert s.get_doc_state("/no/such.pdf") == {}


def test_set_then_get_roundtrip(tmp_path, a_pdf):
    s = Settings(tmp_path / "view_state.json")
    s.set_doc_state(a_pdf, {"page": 2, "zoom": 1.5, "rotation": 90})
    assert s.get_doc_state(a_pdf) == {"page": 2, "zoom": 1.5, "rotation": 90}


def test_persists_across_instances(tmp_path, a_pdf):
    path = tmp_path / "view_state.json"
    Settings(path).set_doc_state(a_pdf, {"page": 1, "zoom": 2.0})
    # A fresh instance reads the same file back.
    assert Settings(path).get_doc_state(a_pdf)["page"] == 1


def test_corrupt_file_is_tolerated(tmp_path, a_pdf):
    path = tmp_path / "view_state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    s = Settings(path)  # must not raise
    assert s.get_doc_state(a_pdf) == {}
    s.set_doc_state(a_pdf, {"page": 0})  # and recovers on next write
    assert Settings(path).get_doc_state(a_pdf) == {"page": 0}
