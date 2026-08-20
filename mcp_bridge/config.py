"""Server configuration: the path allowlist, the return-size caps, and the read-only switch.

All three exist for the same reason (PLAN.md §Safety model): **an MCP tool runs with the host's
file access, and the caller is an agent**. The tools themselves are careful — no write touches its
input, redaction verifies before reporting success — but "careful" is a property of each tool, and
these are the properties of the *server*.

Kept out of `server.py` so the policy is testable without building a server, and out of
`queries.py`/`transforms.py` so the PDF layer stays a library that does not know it is being served.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from util.paths import normalize_path

# Return-size caps. A mis-call should degrade legibly, not blow out the caller's context: an agent
# that runs `extract_text` on an 800-page report has made a routing mistake, and the useful response
# is the first slice plus a clear statement that it was cut — not 4 million characters, and not an
# error either, since the text it did get may well answer the question.
DEFAULT_MAX_TEXT_CHARS = 200_000     # ~50k tokens: large enough for a long chapter
DEFAULT_MAX_SEARCH_HITS = 500        # a one-letter query on a long file finds tens of thousands
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024   # a 600-dpi A4 page is ~25 MB of PNG

DEFAULT_MAX_LISTED_FILES = 25
"""How many written paths `export_images` spells out before it stops listing them.

The files are all written either way — this caps the *listing*, which is the one bulk result that
grew without bound: 320 pages returned 320 near-identical absolute paths and no `truncated` flag,
the only bulk tool not following the server's own capping convention (TC-011). A caller that wants
them all can list the directory; what they need from the reply is where the files are, how many
there are, and how they are named.
"""

ENV_ALLOW_ROOTS = "KLARPDF_MCP_ALLOW_ROOTS"
ENV_READ_ONLY = "KLARPDF_MCP_READ_ONLY"


class PathNotAllowed(PermissionError):
    """A path outside every configured root. Raised before the file is opened."""


@dataclass(frozen=True)
class PathPolicy:
    """Which paths the server may touch.

    **Unrestricted by default, and that is the honest default rather than a lax one.** A stdio MCP
    server is a subprocess the user launched, running as them, with exactly the file access they
    already have; a client that can start it can already read their disk. Pretending otherwise by
    defaulting to some arbitrary root would buy no security and would break every reasonable call.
    What the allowlist *is* for is the case where the user wants a smaller blast radius than their
    own account — a shared machine, an agent they are still learning to trust, a directory of
    client documents — and that has to be asked for, because only they know the boundary.

    Containment is checked on the **resolved** path, through ``util/paths.py:normalize_path`` — the
    project's single file-identity chokepoint — so a symlink out of an allowed root, a `..` escape,
    and a case-different spelling on Windows are all caught. The same reasoning as
    `transforms.py:_resolve_out`, and for a stronger reason: that one protects a file, this one
    protects everything outside a boundary.
    """

    roots: tuple[str, ...] = ()

    @classmethod
    def from_args(cls, roots: list[str] | None) -> "PathPolicy":
        raw = list(roots or [])
        if not raw and os.environ.get(ENV_ALLOW_ROOTS):
            raw = [part for part in os.environ[ENV_ALLOW_ROOTS].split(os.pathsep) if part]
        resolved = []
        for root in raw:
            expanded = os.path.abspath(os.path.expanduser(root))
            if not os.path.isdir(expanded):
                raise ValueError(f"--allow-root {root!r} is not a directory")
            resolved.append(normalize_path(expanded))
        return cls(tuple(resolved))

    @property
    def restricted(self) -> bool:
        return bool(self.roots)

    def check(self, path: str | os.PathLike[str]) -> str:
        """Return ``path`` as an absolute string, or raise :class:`PathNotAllowed`.

        Works for outputs that do not exist yet: containment is decided on the resolved *parent*
        when the path itself is absent, so a write to a new file inside an allowed root is fine and
        a write to a new file outside one is not.
        """
        absolute = os.path.abspath(os.path.expanduser(os.fspath(path)))
        if not self.restricted:
            return absolute
        probe = absolute if os.path.exists(absolute) else os.path.dirname(absolute) or absolute
        key = normalize_path(probe)
        for root in self.roots:
            if key == root or key.startswith(root + os.sep):
                return absolute
        raise PathNotAllowed(
            f"{absolute!r} is outside the allowed roots for this server "
            f"({', '.join(self.roots)}). Start the server with --allow-root to widen them."
        )


@dataclass(frozen=True)
class Limits:
    """Caps on what a single call may return."""

    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    max_search_hits: int = DEFAULT_MAX_SEARCH_HITS
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    max_listed_files: int = DEFAULT_MAX_LISTED_FILES


@dataclass(frozen=True)
class Config:
    """Everything the server needs to know that is not a tool argument."""

    policy: PathPolicy = field(default_factory=PathPolicy)
    limits: Limits = field(default_factory=Limits)
    read_only: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            policy=PathPolicy.from_args(None),
            read_only=os.environ.get(ENV_READ_ONLY, "").lower() in {"1", "true", "yes"},
        )
