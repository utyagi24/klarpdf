"""In-viewer internal-link navigation (PLAN.md, M33).

Clicking an internal link (a GoTo or named-destination link) jumps to the page its target currently
sits on; hovering one shows a pointing-hand cursor. The target is resolved with the same
``(source_id, source_page) -> display index`` map the materialise remap uses (``links_remap``), so
navigation lands on the page exactly where Save would repoint the link — and it follows reorders /
deletes live, since the map is rebuilt from ``ordered`` (and invalidated on every edit).

Hit-testing reuses the view's rotation-aware box mapping (``page_and_local_at`` /
``scene_rect_for_box``), the same one the text-selection and annotation overlays use, so link rects
land correctly on rotated pages too.

**External (URI) links are clickable too, since M149** (#333) — the click hands the URL to the
system browser, exactly as Help ▸ View Source and Help ▸ Donate… already do. This reverses M33/M46's
copy-only rule, and it does not weaken the offline guarantee, which is about what *the app* does:
KlarPDF still opens no socket of its own, and a URL leaves it only because the reader clicked a link
in the document in front of them. The hand-off itself stays out of this module — see
:meth:`LinkNavigator.openable_uri_at` — and Copy Link Address stays on the context menu either way.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pymupdf as fitz

from klarpdf.model.links_remap import internal_link_target, link_target_map

#: Link schemes a click may hand to the system browser (M149, #333; owner's call).
#:
#: A PDF can carry **any** URI, and ``QDesktopServices.openUrl`` hands whatever it is to the shell —
#: which for a registered app handler such as ``ms-msdt:`` means a document could start something
#: from a single click. So the gate is an allowlist, not a denylist: web pages open, a ``mailto:``
#: contact link opens the mail client (that is what a support link in a manual usually is — one of
#: the two documents in #333 is exactly that), and everything else stays copy-only, reachable
#: through the context menu just as it was before.
#:
#: ``file:`` is not on the list, and could not reach it anyway: measured on PyMuPDF 1.27.2.3, MuPDF
#: reports a ``file:`` action as ``LINK_LAUNCH`` with ``uri: None`` — whether MuPDF wrote the link
#: itself or it was hand-crafted as a ``/URI`` action — so such a link never reaches
#: :meth:`LinkNavigator.uri_at` at all. The schemes that *do* arrive as an ordinary ``LINK_URI`` are
#: ``javascript:``, ``data:``, ``ftp:`` and ``ms-msdt:``, which is what this is here to stop.
OPENABLE_SCHEMES = frozenset({"http", "https", "mailto"})


def openable_uri(uri: str) -> str | None:
    """``uri`` if a click may hand it to the browser (see :data:`OPENABLE_SCHEMES`), else ``None``.

    A scheme-less URI (``www.example.com``, which some PDFs really do carry) is **not** openable:
    it has no scheme to check, and handing it over would let the shell decide what it meant. It
    stays copy-only, which is what every URI was before M149.
    """
    uri = (uri or "").strip()
    try:
        scheme = urlsplit(uri).scheme.lower()
    except ValueError:
        return None  # a malformed URI is not one we hand to anything
    return uri if scheme in OPENABLE_SCHEMES else None


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
        """The URI of the external link under ``scene_pt``, else ``None`` — whatever the document
        says, openable or not. What the context menu's Copy Link Address copies."""
        return self._hit(scene_pt, self._uris_for)

    def openable_uri_at(self, scene_pt) -> str | None:
        """The URI under ``scene_pt`` **if a click may open it** (:func:`openable_uri`), else ``None``.

        What a click and the hover cursor ask, so a ``javascript:`` link neither looks clickable nor
        becomes clickable. Returning the URI rather than opening it keeps the browser hand-off out of
        ``viewer/`` — the view emits ``externalLinkClicked`` and ``MainWindow`` calls
        ``ui.about._open_url``, so every URL this app hands to a browser still goes through the one
        function that has always done it.
        """
        uri = self.uri_at(scene_pt)
        return openable_uri(uri) if uri else None

    def navigate_at(self, scene_pt) -> bool:
        """If an internal link is under ``scene_pt``, jump to its target page. Returns True if it
        consumed the click. Internal links only — an external one is the view's to hand on."""
        dest = self.link_at(scene_pt)
        if dest is None:
            return False
        self._view.goto_page(dest)
        return True

    def invalidate(self) -> None:
        """Drop the cached per-page link boxes — after an edit remaps page indices / targets."""
        self._links.clear()
        self._uris.clear()
