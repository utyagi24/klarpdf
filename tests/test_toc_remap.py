"""Pure-function tests for outline remap + level repair (no PyMuPDF needed)."""

from __future__ import annotations

from model.toc_remap import remap_toc, repair_levels


def test_repair_levels_starts_at_one_and_no_jumps():
    assert repair_levels([1, 2, 1]) == [1, 2, 1]
    assert repair_levels([2, 3]) == [1, 2]  # never starts above 1
    assert repair_levels([1, 3]) == [1, 2]  # a +2 jump is clamped to +1


def test_repair_levels_promotes_orphaned_child():
    # Parent (level 1) dropped, its child (level 2) and a following level-1 survive.
    assert repair_levels([2, 1]) == [1, 1]


def test_remap_drops_dangling_and_renumbers():
    toc = [[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 3]]
    # A1 (page 2 -> old0=1) deleted; A0->0, A2->1.
    index_map = {0: 0, 2: 1}
    assert remap_toc(toc, index_map) == [[1, "Chapter 1", 1], [1, "Chapter 2", 2]]


def test_remap_updates_explicit_destination_page():
    toc = [[1, "Chapter 1", 3, {"kind": 1, "page": 2, "to": (0, 700)}]]
    index_map = {2: 0}  # origin page index 2 lands at output index 0
    out = remap_toc(toc, index_map)
    assert out[0][0] == 1
    assert out[0][2] == 1  # 1-based page
    assert out[0][3]["page"] == 0  # dest carries the new 0-based page
    assert out[0][3]["to"] == (0, 700)  # other dest keys untouched
