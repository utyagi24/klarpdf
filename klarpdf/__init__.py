"""KlarPDF's importable namespace — the one top-level name a `pip install` may claim (M134).

**Why this package exists.** Before M134 an install put four names at the top of ``site-packages``:
``mcp_bridge``, ``model``, ``util``, and a module called plain ``version``. The first is
distinctive; the rest are not. Two distributions owning a top-level ``util/`` means whichever
installs last wins and uninstalling either damages the other — a hazard we could not fix
retroactively once the name was on PyPI, which is why the move landed *before* the first publish
rather than after (``PLAN.md`` §M133–M136).

**Deliberately empty of imports.** Nothing is re-exported here. ``import klarpdf`` must stay free of
side effects and cost, so that ``klarpdf.util.paths`` does not drag in the PDF engine and
``klarpdf.mcp_bridge`` does not load on an unrelated import. Reach for the submodule you want.

**What is *not* in here.** The GUI packages — ``viewer/``, ``ui/``, ``store/``, ``organize/`` — and
the root modules ``app.py``, ``main_window.py``, ``launcher.py``, ``platform_integration.py``. They
are never installed (the Windows app is PyInstaller-frozen), so they cannot collide with anything
and were left where they are. If an app distribution is ever published they move here too; that is
the remaining ~304 import sites recorded in ``PLAN.md``.
"""
