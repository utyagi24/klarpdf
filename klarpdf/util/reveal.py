"""Where a revealed thing should end up on screen — one policy, shared by every surface.

Qt's two "scroll it into view" calls, ``QGraphicsView.ensureVisible`` and
``QAbstractItemView.scrollTo`` with its default ``EnsureVisible`` hint, both scroll the **minimum**
distance. That sounds economical and reads badly: whatever is being revealed ends up hard against
whichever edge the view travelled towards, so moving forward parks it at the bottom and moving back
parks it at the top. The reader sees a page that scrolled but seemingly not far enough, and the
asymmetry makes one direction feel broken while the other feels fine — which is exactly how it was
reported, on the search page view (2026-08-13) and then on the Pages sidebar.

The rule both surfaces now follow:

* **Already comfortably on screen → do not move.** Revealing is not re-centring. Stepping between
  two search hits that share a screen, or between two thumbnails already in the strip, must leave
  the view still, or reading turns into a slideshow.
* **Anything else → centre it**, which is what Preview and the browsers do, and what makes the
  result independent of window height, zoom, and the direction of travel.

Kept here, GUI-free and headless-testable, because the two callers sit in different packages
(``viewer/`` and ``organize/``) and a copy each is how a routine that has already been subtly wrong
once ends up subtly wrong twice.
"""

from __future__ import annotations

# Clear space a revealed item must already have above and below it before the view is left as it
# is: a fraction of the window, floored in pixels so a short one still gets a usable band. 15% of a
# 900 px window is 135 px; of a 300 px sidebar it is the 60 px floor.
REVEAL_BAND = 0.15
REVEAL_BAND_MIN_PX = 60.0


def is_settled(top: float, bottom: float, view_top: float, view_bottom: float) -> bool:
    """Is the span ``top``..``bottom`` far enough inside ``view_top``..``view_bottom`` to leave be?

    Plain numbers rather than a ``QRect``: the page view asks in scene coordinates and the sidebar
    in viewport pixels, and the policy is the same question in both.

    An item taller than the band can never satisfy this and will always be centred, which is the
    right answer — there is no resting position that gives it clearance on both sides.
    """
    band = max(REVEAL_BAND_MIN_PX, (view_bottom - view_top) * REVEAL_BAND)
    return top >= view_top + band and bottom <= view_bottom - band
