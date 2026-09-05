"""M106 — an unrecognised argument name is an error, not a silent no-op.

TC-009 found the worst defect this series has produced: `redact_text` was called with `querys`
instead of `queries`, dropped the key, redacted only the other query, and reported
`residual_matches: 0`, `residual_literal: 0`, `cross_engine_verified: true` — an unqualified
success on a file that still held the PII. Every safety signal M95–M103 added read clean, correctly,
because all of them are *downstream of parameter binding*: they describe what the server did, and
the record of what it was asked to do was deleted before any of them ran (PLAN.md §M106).

**These tests must go through the protocol, not `server.call_tool`.** The guard is an extension
interceptor around the `tools/call` **handler**; `MCPServer.call_tool` goes straight to the tool
manager and never reaches it (measured — the interceptor does not fire). `tests/test_mcp_server.py`
calls tools that way deliberately and correctly, since it is testing the tools; a test of the guard
that did the same would pass against a server with the guard deleted. So these drive a real
in-memory client session, which is also the only way to prove the seam the fix hangs off still
works after an SDK bump.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from klarpdf.mcp_bridge.server import create_server, server
from klarpdf.mcp_bridge.strict_args import rejection_message, suggestions, unknown_parameters

# Borrowed rather than restated: the tool roster has exactly one owner, and a second copy of it
# here would drift the moment a tool is added.
from tests.test_mcp_server import EXPECTED_TOOLS

REDACT_TEXT_PARAMS = [
    "path",
    "out",
    "query",
    "queries",
    "match_case",
    "whole_words",
    "pages",
    "password",
    "overwrite",
]


def call(name: str, arguments: dict, target=None):
    """Invoke a tool over a real session, the way a client does, and hand back the result."""

    async def run():
        from mcp.client import Client

        async with Client(target if target is not None else server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def error_text(result) -> str:
    assert result.is_error, "expected a rejection, got a success"
    return result.content[0].text


# ---- the pure logic ----------------------------------------------------------


def test_unknown_parameters_keeps_the_order_the_caller_sent():
    given = ["path", "zzz", "out", "aaa"]
    assert unknown_parameters(REDACT_TEXT_PARAMS, given) == ["zzz", "aaa"]


def test_a_fully_correct_argument_list_has_no_unknowns():
    assert unknown_parameters(REDACT_TEXT_PARAMS, REDACT_TEXT_PARAMS) == []


@pytest.mark.parametrize(
    "typo, expected",
    [
        ("querys", ["query", "queries"]),  # TC-009's original: both, because a plural means the list
        ("wholewords", ["whole_words"]),
        ("Query", ["query"]),
        # Shapes the TC-009 retest probed, chosen to differ in kind from the reported strings so
        # the fix could not be a lookup of them.
        ("whole_word", ["whole_words"]),  # trailing character missing
        ("match-case", ["match_case"]),  # hyphen for underscore
        ("overwite", ["overwrite"]),  # internal deletion
        ("pth", ["path"]),  # heavy abbreviation, distance 2
    ],
)
def test_a_near_miss_is_answered_with_a_did_you_mean(typo, expected):
    message = rejection_message("redact_text", REDACT_TEXT_PARAMS, [typo])
    assert "Did you mean" in message
    for name in expected:
        assert repr(name) in message


@pytest.mark.parametrize(
    "shouted, expected",
    [
        ("PAGES", "pages"),  # the TC-009 retest's finding: rejected, but with no hint at all
        ("OUT", "out"),
        ("MATCH_CASE", "match_case"),
        ("Path", "path"),
        ("Whole_Words", "whole_words"),
    ],
)
def test_a_case_variant_of_a_real_name_still_gets_a_hint(shouted, expected):
    """Case-sensitive distance is dominated by the case difference, so an all-caps but otherwise
    correct name fell outside the cutoff and was rejected with nothing to act on."""
    assert suggestions(shouted, REDACT_TEXT_PARAMS) == [expected]


def test_case_folding_does_not_make_the_matcher_guess_more():
    """The rows that should stay quiet must stay quiet. Folding can only add matches — every
    accepted name is already lowercase — but that is worth pinning rather than reasoning about."""
    for semantic_alias in ("case_sensitive", "out_path", "dry_run"):
        assert suggestions(semantic_alias, REDACT_TEXT_PARAMS) == []


def test_a_case_variant_is_rejected_and_not_quietly_accepted(a_pdf, tmp_path):
    """The hint is case-insensitive; the *check* is not, and must not become so. Accepting `PAGES`
    as `pages` would be the same species of leniency that made TC-009 possible."""
    out = str(tmp_path / "shouted.pdf")
    message = error_text(
        call("redact_text", {"path": a_pdf, "out": out, "query": "ALPHA-zero-A0", "PAGES": [1]})
    )
    assert "'PAGES'" in message and "'pages'" in message
    assert not os.path.exists(out)


def test_an_invented_name_gets_no_suggestion_rather_than_a_wrong_one():
    """`dry_run` is not a typo of anything, and `out_path` must not be answered with `path`.

    Nudging a caller who meant the **output** towards the **input** file is worse than staying
    quiet — the accepted list is printed either way, so silence still leaves a complete answer.
    """
    for invented in ("dry_run", "out_path"):
        message = rejection_message("redact_text", REDACT_TEXT_PARAMS, [invented])
        assert "Did you mean" not in message
        assert "accepts:" in message


def test_the_message_names_every_unknown_and_says_nothing_happened():
    message = rejection_message("redact_text", REDACT_TEXT_PARAMS, ["dry_run", "wholewords"])
    assert "parameters" in message  # plural
    assert "'dry_run'" in message and "'wholewords'" in message
    assert "nothing was written" in message


def test_the_accepted_list_stays_in_signature_order_not_alphabetical():
    message = rejection_message("redact_text", REDACT_TEXT_PARAMS, ["zzz"])
    assert "accepts: path, out, query, queries" in message


# ---- the seam ----------------------------------------------------------------


def test_the_original_tc009_call_is_rejected_and_writes_nothing(a_pdf, tmp_path):
    out = str(tmp_path / "redacted.pdf")
    result = call(
        "redact_text",
        {
            "path": a_pdf,
            "out": out,
            "query": "ALPHA-zero-A0",
            "querys": ["ALPHA-one-A1"],
            "whole_words": True,
        },
    )
    message = error_text(result)
    assert "'querys'" in message
    assert "'queries'" in message
    assert not os.path.exists(out), "the rejected call still wrote a file"


def test_an_invented_dry_run_does_not_perform_a_real_write(a_pdf, tmp_path):
    """The most alarming shape TC-009 found: the parameter's whole purpose is to prevent the thing
    it failed to prevent. It reported success and wrote the file."""
    out = str(tmp_path / "dry.pdf")
    message = error_text(
        call("redact_text", {"path": a_pdf, "out": out, "query": "ALPHA-zero-A0", "dry_run": True})
    )
    assert "'dry_run'" in message
    assert not os.path.exists(out)


def test_the_guard_covers_every_tool_not_just_the_redactors():
    """PLAN.md §M106: the fix is one check over all of them, not an argument list per tool.

    An unknown name is caught before any argument validation, so no tool needs valid arguments
    here — which is the property that makes one loop over the whole surface possible.
    """
    names = sorted(tool.name for tool in asyncio.run(server.list_tools()))
    assert set(names) == EXPECTED_TOOLS and len(names) == 19
    for name in names:
        message = error_text(call(name, {"definitely_not_a_parameter": 1}))
        assert "'definitely_not_a_parameter'" in message, name
        assert f"'{name}' accepts:" in message


def test_a_correctly_spelled_call_is_untouched(a_pdf):
    result = call("get_info", {"path": a_pdf})
    assert not result.is_error
    assert '"pages": 3' in result.content[0].text


def test_the_guard_runs_above_argument_validation():
    """The unknown name is reported even when a required argument is also missing.

    This is what pins the fix to the right layer: `path` and `out` are absent, so pydantic would
    have raised first if the check had ended up below it — and that error would not mention the
    typo at all.
    """
    message = error_text(call("redact_text", {"querys": ["x"]}))
    assert "'querys'" in message


def test_a_tool_the_config_withheld_still_reports_as_an_unknown_tool():
    """Under `--read-only` the write tools are never registered. A call to one must keep saying so
    rather than being answered with an argument list it does not have."""
    from klarpdf.mcp_bridge.config import Config

    read_only = create_server(Config(read_only=True))
    result = call("redact_text", {"nope": 1}, target=read_only)
    assert result.is_error
    assert "accepts:" not in result.content[0].text


def test_no_argument_at_all_is_not_treated_as_an_unknown_one():
    """An empty argument list has nothing to reject; the tool's own required-argument error is the
    right answer, and it must still be the one that arrives."""
    result = call("get_info", {})
    assert result.is_error
    assert "accepts:" not in result.content[0].text
