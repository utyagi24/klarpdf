"""The bookkeeping for drawing a slow page in pieces (PLAN.md §M152, M152.2). No Qt.

``viewer/pieces.py`` decides which piece of a page to draw next and how big it should be. The
sizes are checked against the two pages the design was measured on: the NADA cover, whose cost
grows with a piece's area, and ``IAS_CaseStudy.pdf`` page 6, where every piece pays a large fixed
part. The costs below are made up in those shapes.
"""

from __future__ import annotations

from viewer.pieces import TILE, Sizer, Tiles

BUDGET = 0.030
ONE = TILE * TILE


# ---- the tile grid -------------------------------------------------------------


def test_the_grid_covers_the_page_and_cuts_the_last_tiles():
    tiles = Tiles(300, 200)
    assert (tiles.cols, tiles.rows) == (3, 2)
    assert tiles.box((2, 1, 2, 1)) == (256, 128, 300, 200)


def test_a_tile_counts_as_touched_only_with_a_whole_pixel():
    tiles = Tiles(1000, 1000)
    assert tiles.tiles_in((0, 0, 128, 128)) == {(0, 0)}
    assert tiles.tiles_in((0, 0, 129, 128)) == {(0, 0), (1, 0)}
    assert tiles.tiles_in((10, 10, 10.5, 50)) == set()


def test_the_nearest_undrawn_tile_comes_first():
    tiles = Tiles(1024, 1024)
    wanted = tiles.tiles_in((0, 0, 1024, 1024))
    centre = (512, 512)
    first, _ = tiles.nearest(wanted, centre)
    assert first in {(3, 3), (3, 4), (4, 3), (4, 4)}
    tiles.mark((3, 3, 4, 4))
    second, _ = tiles.nearest(wanted, centre)
    assert second not in {(3, 3), (3, 4), (4, 3), (4, 4)}


def test_a_piece_grows_only_over_undrawn_wanted_tiles():
    tiles = Tiles(1024, 1024)
    wanted = tiles.tiles_in((0, 0, 512, 512))          # the top-left 4 x 4
    tiles.mark((1, 0, 1, 3))                            # column 1 is drawn
    piece = tiles.grow((0, 0), wanted, (0, 0), 16)
    assert piece == (0, 0, 0, 3)                        # blocked on the right by the drawn column


def test_a_piece_stops_at_its_size():
    tiles = Tiles(4096, 4096)
    wanted = tiles.tiles_in((0, 0, 4096, 4096))
    c0, r0, c1, r1 = tiles.grow((10, 10), wanted, (1344, 1344), 6)
    assert 6 <= (c1 - c0 + 1) * (r1 - r0 + 1) <= 9


def test_the_page_is_complete_when_every_tile_is_drawn():
    tiles = Tiles(300, 200)
    tiles.mark((0, 0, 2, 0))
    assert not tiles.complete()
    tiles.mark((0, 1, 2, 1))
    assert tiles.complete()
    tiles.mark((0, 1, 0, 1), drawn=False)
    assert not tiles.complete()


# ---- how big a piece is ----------------------------------------------------------


def _cover(pixels: int) -> float:
    """The NADA cover at 375%: 3.5 ms fixed, 1.6 µs a pixel."""
    return 0.0035 + 1.6e-6 * pixels


def _photo(pixels: int) -> float:
    """``IAS_CaseStudy.pdf`` page 6 at 300%: 200 ms fixed, 0.1 µs a pixel."""
    return 0.200 + 0.1e-6 * pixels


def _run(sizer: Sizer, cost, pieces: int) -> list[int]:
    sizes = []
    for _ in range(pieces):
        n = sizer.tiles()
        sizes.append(n)
        sizer.record(n * ONE, cost(n * ONE))
    return sizes


def test_the_first_piece_is_sized_from_the_last_whole_drawing():
    assert Sizer(BUDGET, rate=0.85e-6).tiles() == 2         # 30 ms at 0.85 µs a pixel
    assert Sizer(BUDGET).tiles() == 1                       # never drawn: the smallest


def test_a_page_whose_cost_grows_with_area_gets_pieces_that_take_the_budget():
    sizes = _run(Sizer(BUDGET, rate=0.85e-6), _cover, 12)
    assert sizes[-6:] == [1] * 6                            # one tile, ~30 ms


def test_a_page_with_a_large_fixed_part_gets_large_pieces():
    """One tile costs 200 ms there, and so do a hundred. Small pieces would pay it every time:
    21 s for the window in 128 px pieces, measured, against 4.8 s in 512 px ones."""
    sizes = _run(Sizer(BUDGET, rate=2.4e-6), _photo, 6)
    assert sizes[:2] == [1, 2]                              # too slow at one tile: try two
    assert sizes[-1] > 100                                  # then as big as the window allows
    assert _photo(sizes[-1] * ONE) < 3 * _photo(ONE)        # the fixed part is still most of it


def test_one_piece_that_unpacked_a_photo_does_not_shrink_the_rest():
    sizer = Sizer(BUDGET)
    sizer.record(ONE, 2.4)                                  # the first piece unpacked the photos
    for _ in range(4):
        sizer.record(4 * ONE, _cover(4 * ONE) / 4)          # then quick pieces
    assert sizer.tiles() >= 4


def test_cheap_pieces_over_a_margin_do_not_grow_the_next_piece_into_the_dense_middle():
    """The page's average across its pieces holds the size down. Without it the NADA cover got a
    piece of 11 x 7 tiles that took 1.7 s."""
    sizer = Sizer(BUDGET)
    for _ in range(20):
        sizer.record(ONE, _cover(ONE))                      # the dense middle
    for _ in range(8):
        sizer.record(8 * ONE, 0.004)                        # then a blank margin
    assert sizer.tiles() * ONE * 1.6e-6 < 10 * BUDGET
