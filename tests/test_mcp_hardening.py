"""M43 — the three server-wide policies: read-only, the path allowlist, and the return-size caps.

These are properties of the *server*, not of any tool, which is why they are tested by building
servers with different configs rather than by calling helpers. Together with the per-tool guarantees
in `test_mcp_transforms.py` and `test_mcp_redaction.py`, they are what "agent-driven means untrusted
caller" (PLAN.md §Safety model) actually amounts to in code.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_bridge.config import Config, Limits, PathNotAllowed, PathPolicy
from mcp_bridge.server import create_server, parse_args
from tests.conftest import A_TEXT

READ_TOOLS = {
    "get_info",
    "get_outline",
    "search",
    "extract_text",
    "render_page",
    "get_form_fields",
    "get_annotations",
}


def tools_of(server) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


def payload(server, name, **arguments):
    result = call(server, name, **arguments)
    return json.loads(result.content[0].text)


# ---- --read-only: withhold the tools, do not merely refuse them ---------------------


def test_read_only_registers_exactly_the_query_tools():
    """A tool the model can see is a tool it will try. Listing sixteen and erroring on ten is worse
    than listing six, so read-only mode does not register the write tools at all."""
    server = create_server(Config(read_only=True))
    assert tools_of(server) == READ_TOOLS


def test_the_default_server_has_the_write_tools():
    """Writes are ON by default (decided 2026-08-12) — no write tool can destroy data by
    construction, so the flag is the cautious opt-out, not the default."""
    assert tools_of(create_server()) > READ_TOOLS


def test_read_only_still_reads(a_pdf):
    server = create_server(Config(read_only=True))
    assert payload(server, "get_info", path=a_pdf)["pages"] == 3


def test_read_only_says_so_in_its_instructions():
    """The instructions are what the model is told about the server. A read-only server that
    describes write tools it does not have would send the model looking for them."""
    assert "READ-ONLY" in create_server(Config(read_only=True)).instructions
    assert "READ-ONLY" not in create_server().instructions


def test_the_flag_is_parsed_and_so_is_the_env_var(monkeypatch):
    assert parse_args(["--read-only"]).read_only is True
    assert parse_args([]).read_only is False
    monkeypatch.setenv("KLARPDF_MCP_READ_ONLY", "1")
    assert parse_args([]).read_only is True


# ---- the path allowlist -------------------------------------------------------------


def test_unrestricted_by_default(a_pdf):
    """The honest default: a stdio server is a subprocess running as you, with the access you
    already have. Defaulting to some arbitrary root would buy nothing and break every call."""
    policy = PathPolicy()
    assert policy.restricted is False
    assert policy.check(a_pdf) == os.path.abspath(a_pdf)


def test_a_configured_root_admits_paths_inside_it(tmp_path):
    policy = PathPolicy.from_args([str(tmp_path)])
    inside = tmp_path / "doc.pdf"
    inside.write_bytes(b"%PDF-")
    assert policy.check(str(inside)) == str(inside)


def test_a_configured_root_refuses_paths_outside_it(tmp_path, a_pdf):
    (tmp_path / "allowed").mkdir()
    policy = PathPolicy.from_args([str(tmp_path / "allowed")])
    with pytest.raises(PathNotAllowed, match="outside the allowed roots"):
        policy.check(a_pdf)


def test_a_new_file_inside_a_root_is_allowed(tmp_path):
    """Outputs do not exist yet, so containment is decided on the parent — otherwise no write tool
    could ever be used with an allowlist."""
    policy = PathPolicy.from_args([str(tmp_path)])
    assert policy.check(str(tmp_path / "not-yet.pdf"))


def test_a_new_file_outside_a_root_is_refused(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    policy = PathPolicy.from_args([str(allowed)])
    with pytest.raises(PathNotAllowed):
        policy.check(str(tmp_path / "elsewhere.pdf"))


def test_dot_dot_cannot_escape_a_root(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-")
    policy = PathPolicy.from_args([str(allowed)])
    with pytest.raises(PathNotAllowed):
        policy.check(str(allowed / ".." / "secret.pdf"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_a_symlink_cannot_escape_a_root(tmp_path):
    """The check resolves before comparing, so a link planted inside an allowed root does not
    become a tunnel out of it."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-")
    os.symlink(tmp_path / "secret.pdf", allowed / "innocent.pdf")
    policy = PathPolicy.from_args([str(allowed)])
    with pytest.raises(PathNotAllowed):
        policy.check(str(allowed / "innocent.pdf"))


def test_a_sibling_with_a_shared_prefix_is_not_inside(tmp_path):
    """`/data/docs-private` must not count as inside `/data/docs` — a plain `startswith` bug that
    would quietly widen the boundary."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs-private").mkdir()
    policy = PathPolicy.from_args([str(tmp_path / "docs")])
    with pytest.raises(PathNotAllowed):
        policy.check(str(tmp_path / "docs-private" / "x.pdf"))


def test_several_roots_are_all_honoured(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    policy = PathPolicy.from_args([str(first), str(second)])
    assert policy.check(str(first / "x.pdf"))
    assert policy.check(str(second / "y.pdf"))


def test_a_root_that_is_not_a_directory_is_rejected_at_startup(tmp_path):
    """Better to fail launching than to run with a boundary that silently matches nothing."""
    with pytest.raises(ValueError, match="not a directory"):
        PathPolicy.from_args([str(tmp_path / "nope")])


def test_roots_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KLARPDF_MCP_ALLOW_ROOTS", str(tmp_path))
    assert PathPolicy.from_args(None).restricted is True


def test_the_allowlist_is_enforced_by_the_tools_not_just_the_policy(tmp_path, a_pdf):
    """The policy object being correct is worth nothing if a tool forgets to call it."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    server = create_server(Config(policy=PathPolicy.from_args([str(allowed)])))
    with pytest.raises(ToolError, match="outside the allowed roots"):
        call(server, "get_info", path=a_pdf)


def test_the_allowlist_covers_outputs_as_well_as_inputs(tmp_path, a_pdf):
    """Reading is only half of it — an unrestricted *output* path would let an agent write anywhere
    while looking contained."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    server = create_server(Config(policy=PathPolicy.from_args([str(allowed)])))
    inside = allowed / "copy.pdf"
    import shutil

    shutil.copy(a_pdf, inside)
    with pytest.raises(ToolError, match="outside the allowed roots"):
        call(server, "delete_pages", path=str(inside), pages=[1], out=str(tmp_path / "out.pdf"))


def test_merge_checks_every_input_path(tmp_path, a_pdf, b_pdf):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    import shutil

    inside = allowed / "a.pdf"
    shutil.copy(a_pdf, inside)
    server = create_server(Config(policy=PathPolicy.from_args([str(allowed)])))
    with pytest.raises(ToolError, match="outside the allowed roots"):
        call(server, "merge", paths=[str(inside), b_pdf], out=str(allowed / "m.pdf"))


# ---- return-size caps ------------------------------------------------------------------


def test_extract_text_is_capped_and_says_so(a_pdf):
    """A mis-call should degrade legibly, not blow out the caller's context."""
    server = create_server(Config(limits=Limits(max_text_chars=10)))
    result = payload(server, "extract_text", path=a_pdf)
    assert result["truncated"] is True
    assert result["pages_omitted"]
    assert "budget" in result["note"]
    assert len(result["pages"]) < 3


def test_an_uncapped_extract_says_nothing_about_truncation(a_pdf):
    result = payload(create_server(), "extract_text", path=a_pdf)
    assert "truncated" not in result
    assert len(result["pages"]) == 3


def test_search_hits_are_capped_with_the_real_total(a_pdf):
    """The count that matters when a query is too broad is how many there *are*, not how many came
    back — that is what tells the caller to narrow it."""
    server = create_server(Config(limits=Limits(max_search_hits=1)))
    result = payload(server, "search", path=a_pdf, query="ALPHA")
    assert result["count"] == 1
    assert result["truncated"] is True
    assert result["total_matches"] == 3
    assert "Narrow the query" in result["note"]


def test_render_page_refuses_an_oversized_image_with_advice(a_pdf):
    server = create_server(Config(limits=Limits(max_image_bytes=1024)))
    with pytest.raises(ToolError, match="lower dpi"):
        call(server, "render_page", path=a_pdf, page=1, dpi=300)


def test_the_default_caps_do_not_interfere_with_ordinary_use(a_pdf):
    server = create_server()
    assert payload(server, "search", path=a_pdf, query="ALPHA")["count"] == 3
    assert call(server, "render_page", path=a_pdf, page=1).content[0].type == "image"


# ---- error handling ------------------------------------------------------------------


def test_an_encrypted_document_tells_the_caller_what_to_do(tmp_path):
    """`PasswordRequired('/path.pdf')` tells a model nothing. The message is the whole interface at
    that point, so it names the fix."""
    import pymupdf as fitz

    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "hidden")
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()

    with pytest.raises(ToolError, match="Call again with `password`"):
        call(create_server(), "extract_text", path=path)


def test_a_missing_file_is_reported_as_such(tmp_path):
    with pytest.raises(ToolError, match="no such file"):
        call(create_server(), "get_info", path=str(tmp_path / "absent.pdf"))


def test_a_directory_given_as_a_document_is_reported_as_such(tmp_path):
    with pytest.raises(ToolError, match="is a directory"):
        call(create_server(), "get_info", path=str(tmp_path))


def test_the_tool_schemas_survive_the_error_wrapper():
    """The wrapper is a decorator around every tool, and a decorator that loses `__signature__`
    turns every schema into `(args, kwargs)` — which only fails at call time, far from the cause."""
    server = create_server()
    schemas = {t.name: set(t.input_schema.get("properties", {})) for t in asyncio.run(server.list_tools())}
    assert schemas["search"] == {"path", "query", "match_case", "whole_words", "password"}
    assert "args" not in schemas["get_info"] and "kwargs" not in schemas["get_info"]


def test_every_tool_keeps_its_description_through_the_wrapper():
    for tool in asyncio.run(create_server().list_tools()):
        assert tool.description and len(tool.description) > 40, tool.name
