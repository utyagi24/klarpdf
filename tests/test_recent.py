"""Recent-documents MRU in the settings store (PLAN.md, M13). Pure — no Qt display needed."""

from __future__ import annotations

import os

from store.settings import _MAX_RECENT, Settings


def _touch(tmp_path, name: str) -> str:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4\n")  # just needs to exist (recent_files prunes missing)
    return str(p)


def test_most_recent_first(tmp_path):
    s = Settings(tmp_path / "vs.json")
    a, b = _touch(tmp_path, "a.pdf"), _touch(tmp_path, "b.pdf")
    s.add_recent(a)
    s.add_recent(b)
    assert s.recent_files() == [b, a]


def test_reopen_dedupes_and_moves_to_front(tmp_path):
    s = Settings(tmp_path / "vs.json")
    a, b = _touch(tmp_path, "a.pdf"), _touch(tmp_path, "b.pdf")
    s.add_recent(a)
    s.add_recent(b)
    s.add_recent(a)  # reopening a
    assert s.recent_files() == [a, b]  # promoted, not duplicated


def test_dedupe_is_identity_based(tmp_path):
    # normalize_path collapses case (Windows) / symlinks / .. — same file, one entry.
    s = Settings(tmp_path / "vs.json")
    a = _touch(tmp_path, "Doc.pdf")
    s.add_recent(a)
    s.add_recent(os.path.join(tmp_path, ".", "Doc.pdf"))  # same file, different spelling
    assert len(s.recent_files()) == 1


def test_capped_at_max(tmp_path):
    s = Settings(tmp_path / "vs.json")
    paths = [_touch(tmp_path, f"f{i}.pdf") for i in range(_MAX_RECENT + 3)]
    for p in paths:
        s.add_recent(p)
    recent = s.recent_files()
    assert len(recent) == _MAX_RECENT
    assert recent[0] == paths[-1]  # newest first
    assert paths[0] not in recent  # oldest evicted


def test_missing_files_are_pruned_and_persisted(tmp_path):
    path = tmp_path / "vs.json"
    s = Settings(path)
    a, b = _touch(tmp_path, "a.pdf"), _touch(tmp_path, "b.pdf")
    s.add_recent(a)
    s.add_recent(b)
    os.remove(a)
    assert s.recent_files() == [b]  # vanished file dropped
    assert Settings(path).recent_files() == [b]  # prune was persisted


def test_clear_recent(tmp_path):
    s = Settings(tmp_path / "vs.json")
    s.add_recent(_touch(tmp_path, "a.pdf"))
    s.clear_recent()
    assert s.recent_files() == []


def test_persists_across_instances(tmp_path):
    path = tmp_path / "vs.json"
    a = _touch(tmp_path, "a.pdf")
    Settings(path).add_recent(a)
    assert Settings(path).recent_files() == [a]


def test_recent_and_doc_state_coexist(tmp_path):
    """Adding recents must not clobber per-document view state in the same file."""
    path = tmp_path / "vs.json"
    s = Settings(path)
    a = _touch(tmp_path, "a.pdf")
    s.set_doc_state(a, {"page": 2, "zoom": 1.5})
    s.add_recent(a)
    reloaded = Settings(path)
    assert reloaded.recent_files() == [a]
    assert reloaded.get_doc_state(a) == {"page": 2, "zoom": 1.5}
