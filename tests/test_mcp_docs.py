"""M105 — what an agent can actually read: the description budget, and the docs resource.

**The client truncates.** Claude Code cuts a deferred tool's description at 2,048 characters and
appends `… [truncated]`; the same constant caps an MCP server's `instructions` block. Nothing
errors, so three milestones' worth of agent-facing documentation was written into a channel that
silently discarded it — 69% of `redact_text`'s 6,573 characters never arrived, and it was the wrong
69%: the residual-field catalogue, `matches` vs `boxes_redacted`, and `residual_scope` all sat past
the cut while the opening third was narrative (ENV-001, PLAN.md §M105).

So the budget is a **contract with the transport**, not a style preference, and it has to hold for
tools that do not exist yet — which is why every test here enumerates the live server rather than
listing tool names. A tool added next year is covered the day it is registered.

The depth that no longer fits lives in `klarpdf://docs/{tool}`, read through a channel that caps at
100,000 characters rather than 2,048.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_bridge.config import Config
from mcp_bridge.server import create_server, server

CLIENT_CAP = 2048
"""Where the client actually cuts, verified in the Claude Code binary (`yfe = 2048`).

Applied to a deferred tool's rendered description and, by the same constant, to the server's
`instructions`. Recorded here so the margin below is a decision rather than an accident.
"""

BUDGET = 1900
"""What we hold ourselves to: `CLIENT_CAP` less a margin.

The margin exists because the cap was read out of a minified bundle and belongs to a client we do
not ship — a future version may trim slightly differently, and being 148 characters clear of it
costs nothing. It is deliberately *not* generous enough to sit on: a description that needs more
than this has reference material in it, and reference material belongs in the docs resource.
"""


def tools(target=None):
    return asyncio.run((target if target is not None else server).list_tools())


def test_the_budget_leaves_real_margin_under_the_client_cap():
    """Pins the relationship rather than the numbers, so raising one without the other fails."""
    assert BUDGET < CLIENT_CAP
    assert CLIENT_CAP - BUDGET >= 100


@pytest.mark.parametrize("tool", tools(), ids=lambda t: t.name)
def test_no_tool_description_exceeds_the_budget(tool):
    """Every registered tool, enumerated live — a tool added later is covered without an edit here.

    If this fails, the fix is almost never to raise `BUDGET`: it is to move the reference material
    into `klarpdf://docs/{tool}`, which is what that resource is for.
    """
    size = len(tool.description or "")
    assert size <= BUDGET, (
        f"{tool.name}'s description is {size} chars, {size - BUDGET} over the {BUDGET} budget "
        f"(the client cuts at {CLIENT_CAP} and says nothing). Move reference material into the "
        f"docs resource rather than raising the budget."
    )


def test_every_tool_still_has_a_description_after_the_trimming():
    """The budget must not be met by deleting the contract. Pairs with the ceiling above: one test
    stops descriptions growing past the transport, this one stops them shrinking into stubs."""
    for tool in tools():
        assert len(tool.description or "") > 200, f"{tool.name} has no usable description"


@pytest.mark.parametrize("read_only", [False, True], ids=["default", "read-only"])
def test_the_server_instructions_fit_the_same_budget(read_only):
    """`instructions` goes through the same 2,048 constant, and the `--read-only` build appends to
    it — so the configuration with the *longest* instructions is not the default one."""
    built = create_server(Config(read_only=read_only))
    size = len(built.instructions or "")
    assert size <= BUDGET, f"instructions are {size} chars, {size - BUDGET} over the {BUDGET} budget"


# ---- the docs resource -------------------------------------------------------

RESOURCE_CAP = 100_000
"""What `ReadMcpResourceTool` caps a resource read at (`maxResultSizeChars: 1e5`), against the
2,048 the description channel gets. The whole point of moving material here is this ratio."""


def read(uri: str, target=None) -> str:
    got = asyncio.run((target if target is not None else server).read_resource(uri))
    return got[0].content


def test_every_documented_tool_publishes_a_static_resource():
    """Static, not only templated: `resources/list` shows static entries only, and that listing is
    how a caller finds out the documentation exists (ENV-001 probed for it and found nothing)."""
    from mcp_bridge.docs import REFERENCE

    listed = {str(r.uri) for r in asyncio.run(server.list_resources())}
    assert listed == {f"klarpdf://docs/{name}" for name in REFERENCE}


@pytest.mark.parametrize("read_only", [False, True], ids=["default", "read-only"])
def test_every_listed_resource_can_actually_be_read(read_only):
    """A listed resource whose read fails is worse than one that was never listed. Under
    `--read-only` the redactors are not registered, so their docs must not be advertised."""
    built = create_server(Config(read_only=read_only))
    served = {t.name for t in tools(built)}
    for resource in asyncio.run(built.list_resources()):
        body = read(str(resource.uri), built)
        assert body, f"{resource.uri} read back empty"
        assert str(resource.uri).rsplit("/", 1)[-1] in served


@pytest.mark.parametrize(
    "tool", [t for t in tools() if t.description and "klarpdf://docs/" in t.description],
    ids=lambda t: t.name,
)
def test_a_description_that_points_at_a_resource_has_one(tool):
    """A pointer to documentation that does not exist is worse than no pointer."""
    assert read(f"klarpdf://docs/{tool.name}")


def test_the_resource_contains_the_live_description_verbatim():
    """The anti-drift guarantee, as a test rather than a promise.

    The resource is assembled from the *registered* description plus a disjoint appendix, so there
    is no second copy of the description anywhere. If someone rewrites a docstring, this keeps
    passing; if someone pastes the description into `docs.REFERENCE` instead, the two can diverge
    and there is no way to tell which is authoritative — so the shape is pinned here.
    """
    for tool in tools():
        if tool.name not in __import__("mcp_bridge.docs", fromlist=["REFERENCE"]).REFERENCE:
            continue
        assert tool.description in read(f"klarpdf://docs/{tool.name}")


def test_the_appendix_does_not_restate_the_description():
    """Disjoint by construction; a sampled check that it stayed that way.

    Long shared sentences are the tell that someone duplicated rather than moved.
    """
    from mcp_bridge.docs import REFERENCE

    for tool in tools():
        appendix = REFERENCE.get(tool.name)
        if not appendix:
            continue
        described = {
            line.strip() for line in (tool.description or "").splitlines() if len(line.strip()) > 60
        }
        repeated = described & {line.strip() for line in appendix.splitlines()}
        assert not repeated, f"{tool.name}: appendix repeats description lines {repeated}"


def test_the_assembled_contract_fits_the_resource_channel():
    from mcp_bridge.docs import REFERENCE

    for name in REFERENCE:
        size = len(read(f"klarpdf://docs/{name}"))
        assert size <= RESOURCE_CAP, f"{name} docs are {size} chars, over the {RESOURCE_CAP} cap"


def test_an_unknown_tool_name_says_what_is_served():
    with pytest.raises(Exception, match="no such tool"):
        read("klarpdf://docs/not_a_tool")
