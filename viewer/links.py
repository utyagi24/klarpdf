"""In-viewer internal-link navigation (PLAN.md, M33).

Clicking an internal link (a GoTo or named-destination link) jumps to the page its target currently
sits on; hovering one shows a pointing-hand cursor. The target is resolved with the same
``(source_id, source_page) -> display index`` map the materialise remap uses (``links_remap``), so
navigation lands on the page exactly where Save would repoint the link — and it follows reorders /
deletes live, since the map is rebuilt from ``ordered`` (and invalidated on every edit).

Hit-testing reuses the view's rotation-aware box mapping (``page_and_local_at`` /
``scene_rect_for_box``), the same one the text-selection and annotation overlays use, so link rects
land correctly on rotated pages too. URI / external links are ignored (offline app; no browser
launch).
"""

from __future__ import annotations

import pymupdf as fitz

from model.links_remap import internal_link_target, link_target_map


class LinkNavigator:
    def __init__(self, view) -> None:
        self._view = view
        self._links: dict[int, list[tuple[tuple, int]]] = {}  # display page -> [(box, target display)]

    def _links_for(self, page_index: int) -> list[tuple[tuple, int]]:
        cached = self._links.get(page_index)
        if cached is None:
            cached = self._build(page_index)
            self._links[page_index] = cached
        return cached

    def _build(self, page_index: int) -> list[tuple[tuple, int]]:
        vdoc = self._view._vdoc
        ref = vdoc.ordered[page_index]
        page = vdoc.sources[ref.source_id][ref.source_page_index]
        target_map = link_target_map(vdoc.ordered)
        boxes: list[tuple[tuple, int]] = []
        for link in page.get_links():
            target_src = internal_link_target(link)
            if target_src is None:
                continue
            dest = target_map.get((ref.source_id, target_src))
            if dest is None:
                continue  # target page isn't in the current document (deleted)
            r = link["from"]
            boxes.append(((r.x0, r.y0, r.x1, r.y1), dest))
        return boxes

    def link_at(self, scene_pt) -> int | None:
        """The target **display index** of the internal link under ``scene_pt``, else ``None``."""
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        for box, dest in self._links_for(page_index):
            if self._view.scene_rect_for_box(page_index, box).contains(scene_pt):
                return dest
        return None

    def navigate_at(self, scene_pt) -> bool:
        """If an internal link is under ``scene_pt``, jump to its target page. Returns True if it
        consumed the click."""
        dest = self.link_at(scene_pt)
        if dest is None:
            return False
        self._view.goto_page(dest)
        return True

    def invalidate(self) -> None:
        """Drop the cached per-page link boxes — after an edit remaps page indices / targets."""
        self._links.clear()
