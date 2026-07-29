"""The process-global, byte-bounded store of rendered page pixmaps (PLAN.md §M87.2).

The viewer used to keep its rendered pages in a plain ``OrderedDict`` on each :class:`PdfView`,
bounded at **48 entries**. Measured 2026-07-28 (PR #207): one Ctrl+wheel sweep to max zoom on an
ordinary 60-page Letter document took the process from 127 MB to **4431 MB** of working set, with
the cache sitting at exactly 48 entries the whole way. A count bounds *pages*, and a page is not a
unit of memory — a rendered page costs ``w x h x 4`` bytes and nothing else, so 48 entries is 89 MB
of Letter at 100% and 4.3 GB of the same document at 8x. That is a defect a reader can hit today,
with no DPI change involved, which is why this is the first thing M87 fixes.

Four properties, each answering one part of that defect:

* **Global, not per-window.** ``_cache`` was a ``PdfView`` instance attribute, and there is one
  ``PdfView`` per ``MainWindow`` per document — so N open documents meant N independent caches and
  every memory figure ever quoted for the viewer was silently per-window. One store, one budget.
* **A byte ceiling, not an entry count** — :data:`BYTE_CEILING`, the backstop.
* **Pinned entries are never evicted.** The view pins the band it is currently painting, so
  eviction can never take a pixmap that is about to be asked for again: thrash is impossible by
  construction rather than by choosing a large enough number. It also means a single page bigger
  than the whole budget (an A0 poster at 500% is ~600 MB) still displays, temporarily putting the
  store over its nominal ceiling. That is the graceful behaviour, not a leak.
* **An owner can give its pixels back** without being torn down — see :meth:`_Owner.clear`, which
  the window uses when it stops being the one the reader is looking at.

**Two numbers, not one** (owner, 2026-07-27): *"I am okay to go up to 1 GB (global) only if we are
dealing with exceptionally heavy documents … just because resources are available should not imply
that we stop being stingy."* So retention is driven by **what responsiveness needs**, expressed in
pages (:data:`RETAIN_PAGES` — the band plus a bounded scrollback), and the byte ceiling is a
**backstop that only binds when pages are genuinely enormous** — never a target to fill. Ordinary
documents settle in the tens of MB and never come near it; a 500%-zoomed large-format document
climbs toward it, because there it must.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict

_MB = 1024 * 1024

# Retention, in pages: the band being painted plus a scrollback deep enough that turning back to
# where you just were is instant. At a typical 3-page band that is ~3 screenfuls, and for ordinary
# Letter pages at 100% the whole store is 24 x 1.85 = ~44 MB — the "tens of MB" the sizing policy
# asks for. This is the number that binds in normal reading.
RETAIN_PAGES = 24

# The backstop, in bytes. Only binds on genuinely enormous pages — a page has to average >42 MB
# (a Letter sheet past 5x zoom, or an A0 poster at 200%) before RETAIN_PAGES stops being the
# tighter of the two. Owner policy: 1 GB is the ceiling, not the target.
BYTE_CEILING = 1024 * _MB


def pixmap_bytes(pixmap) -> int:
    """What a ``QPixmap`` actually costs: ``w x h x bytes-per-pixel``.

    ``depth()`` is asked rather than assumed — Qt stores a pixmap in the display format, which is
    **32 bpp**, not the 24 bpp (``w x h x 3``) this project's own estimates assumed until they were
    measured (PR #207 found every projected figure ~27-33% low).
    """
    return pixmap.width() * pixmap.height() * (pixmap.depth() // 8)


class PixmapCache:
    """An LRU over ``(owner, key) -> QPixmap`` bounded by both entry count and total bytes."""

    def __init__(self, retain_pages: int = RETAIN_PAGES, byte_ceiling: int = BYTE_CEILING) -> None:
        self._entries: "OrderedDict[tuple, object]" = OrderedDict()  # (owner_id, key) -> QPixmap
        self._bytes = 0
        self._pinned: dict[int, frozenset] = {}   # owner_id -> the keys it is painting right now
        self._tokens = itertools.count(1)
        self.retain_pages = retain_pages
        self.byte_ceiling = byte_ceiling

    # ---- owners -----------------------------------------------------------------

    def owner(self) -> "_Owner":
        """A handle a view holds instead of its own dict. Identity is a fresh integer token, not
        ``id(view)``, which CPython reuses after a view is collected — a reused id would let a new
        window inherit a dead one's entries."""
        return _Owner(self, next(self._tokens))

    def release(self, owner_id: int) -> None:
        """Drop everything an owner holds. Called when a view is destroyed: with one shared store,
        a window that closed without releasing would be a real leak, where the old per-view dict
        simply died with the view."""
        self._drop(owner_id, keep_pinned=False)

    def _drop(self, owner_id: int, *, keep_pinned: bool) -> None:
        pinned = self._pinned.get(owner_id, frozenset())
        for entry in [e for e in self._entries if e[0] == owner_id]:
            if keep_pinned and entry[1] in pinned:
                continue
            self._bytes -= pixmap_bytes(self._entries.pop(entry))
        if not keep_pinned:
            # Drop the pin set too, or a stale pin would make the *next* pixmap put under one of
            # those keys unevictable until the owner's next paint replaces the set.
            self._pinned.pop(owner_id, None)

    # ---- the store ---------------------------------------------------------------

    def get(self, owner_id: int, key):
        hit = self._entries.get((owner_id, key))
        if hit is not None:
            self._entries.move_to_end((owner_id, key))
        return hit

    def put(self, owner_id: int, key, pixmap) -> None:
        entry = (owner_id, key)
        existing = self._entries.pop(entry, None)
        if existing is not None:
            self._bytes -= pixmap_bytes(existing)
        self._entries[entry] = pixmap
        self._bytes += pixmap_bytes(pixmap)
        self._evict()

    def pin(self, owner_id: int, keys) -> None:
        """Declare the keys this owner is painting. They survive every eviction pass until the
        owner pins something else."""
        self._pinned[owner_id] = frozenset(keys)

    def _evict(self) -> None:
        """Evict least-recently-used entries until both budgets are met, skipping pinned ones.

        Stops when everything left is pinned rather than looping or evicting a pixmap that is on
        screen — the over-budget-single-page case, which must still display.
        """
        while self._entries and (len(self._entries) > self.retain_pages or self._bytes > self.byte_ceiling):
            for entry in self._entries:  # oldest first
                if entry[1] not in self._pinned.get(entry[0], frozenset()):
                    self._bytes -= pixmap_bytes(self._entries.pop(entry))
                    break
            else:
                return  # nothing evictable left

    def clear(self) -> None:
        self._entries.clear()
        self._pinned.clear()
        self._bytes = 0

    # ---- introspection (tests, and the measurement harness) ----------------------

    @property
    def total_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)


class _Owner:
    """One view's view of the shared store — it never sees another owner's keys.

    Deliberately dict-shaped (``get`` / ``clear`` / ``len``) so the call sites that used to poke a
    plain ``OrderedDict`` read the same.
    """

    __slots__ = ("_cache", "_id")

    def __init__(self, cache: PixmapCache, owner_id: int) -> None:
        self._cache = cache
        self._id = owner_id

    def get(self, key):
        return self._cache.get(self._id, key)

    def put(self, key, pixmap) -> None:
        self._cache.put(self._id, key, pixmap)

    def pin(self, keys) -> None:
        self._cache.pin(self._id, keys)

    def clear(self, *, keep_pinned: bool = False) -> None:
        """Give this owner's pixels back. ``keep_pinned`` keeps the band it is currently painting,
        which is what a window that is merely no longer *focused* wants: the scrollback goes, the
        pixels the reader can still see stay."""
        self._cache._drop(self._id, keep_pinned=keep_pinned)

    def release(self, *_args) -> None:
        """Drop everything, pin set included. Called explicitly when the window closes."""
        self._cache.release(self._id)

    def __del__(self) -> None:
        """The backstop for a view that goes away without its window calling :meth:`release`.

        Deliberately **not** the view's ``destroyed`` signal, which was tried first and crashed the
        suite with an access violation: that fires while Qt is tearing the C++ object down, and
        dispatching a Python slot from inside a garbage collection pass there is not safe. The
        handle is a plain Python object holding no reference back to the view, so its refcount
        reaches zero the moment the view does — no Qt involvement at all.
        """
        try:
            self._cache.release(self._id)
        except Exception:       # interpreter shutdown: the store may already be torn down
            pass

    def __len__(self) -> int:
        return sum(1 for entry in self._cache._entries if entry[0] == self._id)


#: The one store the whole application shares. Tests build their own :class:`PixmapCache` or adjust
#: this one's budgets; nothing else should hold pixmaps outside it.
pixmap_cache = PixmapCache()
