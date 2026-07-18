"""In-viewer internal-link navigation (PLAN.md, M33).

Clicking an internal link (a GoTo or named-destination link) jumps to the page its target currently
sits on; hovering one shows a pointing-hand cursor. The target is resolved with the same
``(source_id, source_page) -> display index`` map the materialise remap uses (``links_remap``), so
navigation lands on the page exactly where Save would repoint the link — and it follows reorders /
deletes live, since the map is rebuilt from ``ordered`` (and invalidated on every edit).

Hit-testing reuses the view's rotation-aware box mapping (``page_and_local_at`` /
``scene_rect_for_box``), the same one the text-selection and annotation overlays use, so link rects
land correctly on rotated pages too. URI / external links stay non-clickable (offline app; no
browser launch), but are surfaced read-only via :meth:`uri_at` so the context menu (M46) can offer
Copy Link Address — clipboard only, never a socket.
"""

from __future__ import annotations

import pymupdf as fitz

from model.links_remap import internal_link_target, link_target_map


class LinkNavigator:
    def __init__(self, view) -> None:
        self._view = view
        self._links: dict[int, list[tuple[tuple, int]]] = {}  # display page -> [(box, target display)]
        self._uris: dict[int, list[tuple[tuple, str]]] = {}   # display page -> [(box, URI)]

    def _links_for(self, page_index: int) -> list[tuple[tuple, int]]:
        if page_index not in self._links:
            self._build(page_index)
        return self._links[page_index]

    def _uris_for(self, page_index: int) -> list[tuple[tuple, str]]:
        if page_index not in self._uris:
            self._build(page_index)
        return self._uris[page_index]

    def _build(self, page_index: int) -> None:
        """One ``get_links`` scan fills both caches: internal (navigable) and URI (copy-only)."""
        vdoc = self._view._vdoc
        ref = vdoc.ordered[page_index]
        page = vdoc.sources[ref.source_id][ref.source_page_index]
        target_map = link_target_map(vdoc.ordered)
        boxes: list[tuple[tuple, int]] = []
        uris: list[tuple[tuple, str]] = []
        for link in page.get_links():
            r = link["from"]
            box = (r.x0, r.y0, r.x1, r.y1)
            if link.get("kind") == fitz.LINK_URI and link.get("uri"):
                uris.append((box, link["uri"]))
                continue
            target_src = internal_link_target(link)
            if target_src is None:
                continue
            dest = target_map.get((ref.source_id, target_src))
            if dest is None:
                continue  # target page isn't in the current document (deleted)
            boxes.append((box, dest))
        self._links[page_index] = boxes
        self._uris[page_index] = uris

    def _hit(self, scene_pt, entries_for):
        page_index, _ = self._view.page_and_local_at(scene_pt)
        if page_index is None:
            return None
        for box, payload in entries_for(page_index):
            if self._view.scene_rect_for_box(page_index, box).contains(scene_pt):
                return payload
        return None

    def link_at(self, scene_pt) -> int | None:
        """The target **display index** of the internal link under ``scene_pt``, else ``None``."""
        return self._hit(scene_pt, self._links_for)

    def uri_at(self, scene_pt) -> str | None:
        """The URI of the external link under ``scene_pt``, else ``None``. Read-only surface for
        the context menu's Copy Link Address — external links are never click-navigable here."""
        return self._hit(scene_pt, self._uris_for)

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
        self._uris.clear()
