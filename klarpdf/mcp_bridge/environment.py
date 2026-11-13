"""What ``klarpdf://env`` reports: the bridge's version, what it runs on, and its switches (M176).

An agent has no other way to learn which bridge it is talking to. The server sends its version to
the client in ``serverInfo`` when they connect, and clients do not pass that on to the model. So the
bridge publishes it as a resource, beside the two switches it was started with.

Every value is read when the resource is read, from the code that is running. None is written down
here, so none can go stale.

``platform.platform()`` describes the system and does not branch on it. It is not the OS-specific
code that CLAUDE.md keeps in ``platform_integration.py``, and the bridge could not import that file
anyway, because it belongs to the app.
"""

from __future__ import annotations

import platform
from importlib.metadata import version
from typing import TYPE_CHECKING

import pymupdf as fitz

from klarpdf.version import __version__

if TYPE_CHECKING:  # named in annotations only
    from klarpdf.mcp_bridge.config import Config
    from klarpdf.mcp_bridge.replies import EnvironmentReply


def environment(config: Config) -> EnvironmentReply:
    """The versions the bridge runs on, and the switches ``config`` holds.

    The folders are listed in full, as a refusal already names them to the agent (owner,
    2026-11-12, `PLAN.md` §M176). Nothing else here names a path: not the Python executable, not
    the user's home folder, and not the machine's name.
    """
    return {
        "klarpdf": __version__,
        "pymupdf": fitz.VersionBind,
        "mupdf": fitz.VersionFitz,
        "mcp": version("mcp"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "read_only": config.read_only,
        "paths_restricted": config.policy.restricted,
        "allowed_roots": list(config.policy.roots),
    }
