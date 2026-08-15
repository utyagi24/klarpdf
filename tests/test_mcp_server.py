"""M39 — the MCP tool layer: registration, schemas, and calling tools through the server.

``tests/test_mcp_queries.py`` covers the PDF behaviour. What is left to get wrong here is the part
only a protocol can get wrong — a tool that never registered, an argument name that does not match
what the helper expects, a return value the SDK cannot serialise — so these tests go through
``server.call_tool`` rather than around it.

Async without a plugin: ``asyncio.run`` per test. The suite has no ``pytest-asyncio`` and this
milestone is not the place to add a dependency for six calls.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_bridge.server import server
from tests.conftest import A_TEXT

READ_TOOLS = {
    "get_info",
    "get_outline",
    "search",
    "extract_text",
    "render_page",
    "get_form_fields",
}
WRITE_TOOLS = {
    "delete_pages",
    "reorder",
    "rotate",
    "split",
    "merge",
    "fill_form",
    "flatten",
    "export_images",
}
EXPECTED_TOOLS = READ_TOOLS | WRITE_TOOLS


def call(name: str, **arguments):
    """Invoke a tool the way a client does and hand back the ``CallToolResult``."""
    return asyncio.run(server.call_tool(name, arguments))


def payload(name: str, **arguments):
    """The JSON a tool returned, parsed.

    Asserts the **single** content block on the way through, which is the property the dict-wrapping
    in ``server.py`` exists to hold: the SDK turns a bare ``list`` return into one block per element,
    so a 500-hit search would arrive as 500 blocks.
    """
    result = call(name, **arguments)
    assert len(result.content) == 1, f"{name} returned {len(result.content)} blocks, expected 1"
    return json.loads(result.content[0].text)


# ---- registration + schema ----------------------------------------------------


def test_every_planned_tool_is_registered():
    assert {tool.name for tool in asyncio.run(server.list_tools())} == EXPECTED_TOOLS


def test_the_server_identifies_itself_with_the_app_version():
    from version import __version__

    assert server.name == "klarpdf"
    assert server.version == __version__


def test_every_tool_has_a_description_for_the_model_to_choose_by():
    """The description is the only thing an agent sees when deciding which tool to call, so an
    undescribed tool is an unusable one."""
    for tool in asyncio.run(server.list_tools()):
        assert tool.description and len(tool.description) > 40, tool.name


def test_schemas_name_the_arguments_the_helpers_take():
    schemas = {t.name: set(t.input_schema.get("properties", {})) for t in asyncio.run(server.list_tools())}
    assert schemas["get_info"] == {"path", "password"}
    assert schemas["search"] == {"path", "query", "match_case", "whole_words", "password"}
    assert schemas["extract_text"] == {"path", "pages", "password"}
    assert schemas["render_page"] == {"path", "page", "dpi", "password"}


def test_only_the_document_is_required(a_pdf):
    """Every optional argument must actually be optional — a required `dpi` would make the simple
    call fail for no reason."""
    required = {
        t.name: set(t.input_schema.get("required", [])) for t in asyncio.run(server.list_tools())
    }
    assert required["get_info"] == {"path"}
    assert required["render_page"] == {"path", "page"}
    assert required["search"] == {"path", "query"}


# ---- calling the tools ---------------------------------------------------------


def test_get_info_through_the_server(a_pdf):
    info = payload("get_info", path=a_pdf)
    assert info["pages"] == 3
    assert info["has_outline"] is True


def test_get_outline_through_the_server(a_pdf):
    outline = payload("get_outline", path=a_pdf)
    assert outline["count"] == 3
    assert [entry["title"] for entry in outline["entries"]] == [
        "Chapter 1",
        "Section 1.1",
        "Chapter 2",
    ]


def test_search_through_the_server(a_pdf):
    result = payload("search", path=a_pdf, query=A_TEXT[2])
    assert result["count"] == 1
    assert [hit["page"] for hit in result["hits"]] == [3]


def test_search_flags_reach_the_helper(a_pdf):
    """A flag that is declared but never forwarded is the classic adapter bug."""
    assert payload("search", path=a_pdf, query=A_TEXT[0].lower())["count"] == 1
    assert payload("search", path=a_pdf, query=A_TEXT[0].lower(), match_case=True)["count"] == 0


@pytest.mark.parametrize("name, key", [("search", "hits"), ("get_outline", "entries"),
                                       ("get_form_fields", "fields")])
def test_a_list_result_is_one_block_carrying_its_own_count(a_pdf, name, key):
    """The wrapping that keeps a many-item result from exploding into many content blocks — and
    gives the caller the total before it reads the items."""
    result = payload(name, path=a_pdf, **({"query": "ALPHA"} if name == "search" else {}))
    assert result["count"] == len(result[key])


def test_an_empty_result_is_still_one_block_with_a_zero_count(b_pdf):
    assert payload("get_outline", path=b_pdf) == {"count": 0, "entries": []}


def test_extract_text_through_the_server(a_pdf):
    result = payload("extract_text", path=a_pdf, pages=[2])
    assert [p["page"] for p in result["pages"]] == [2]
    assert A_TEXT[1] in result["pages"][0]["text"]


def test_get_form_fields_through_the_server(a_pdf):
    assert [f["name"] for f in payload("get_form_fields", path=a_pdf)["fields"]] == ["name"]


def test_render_page_comes_back_as_an_image_block(a_pdf):
    """Not JSON — an MCP image block, which is what makes the page visible to the model."""
    result = call("render_page", path=a_pdf, page=1)
    assert len(result.content) == 1
    block = result.content[0]
    assert block.type == "image"
    assert block.mime_type == "image/png"
    assert base64.b64decode(block.data)[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_dpi_reaches_the_helper(a_pdf):
    small = base64.b64decode(call("render_page", path=a_pdf, page=1, dpi=72).content[0].data)
    large = base64.b64decode(call("render_page", path=a_pdf, page=1, dpi=200).content[0].data)
    assert len(large) > len(small)


def test_password_reaches_the_helper(tmp_path):
    import pymupdf as fitz

    path = str(tmp_path / "locked.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "SERVER-locked-text", fontsize=12)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret")
    doc.close()

    assert payload("get_info", path=path)["needs_password"] is True
    assert payload("get_info", path=path, password="secret")["pages"] == 1


# ---- failures surface as tool errors, not as a dead server ----------------------


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("extract_text", {"pages": [99]}),
        ("render_page", {"page": 99}),
        ("render_page", {"page": 1, "dpi": -5}),
    ],
)
def test_bad_arguments_raise_a_tool_error(a_pdf, name, arguments):
    with pytest.raises(ToolError):
        call(name, path=a_pdf, **arguments)


def test_a_missing_document_raises_a_tool_error(tmp_path):
    with pytest.raises(ToolError):
        call("get_info", path=str(tmp_path / "absent.pdf"))


def test_the_server_survives_a_failed_call(a_pdf, tmp_path):
    """A tool error must not poison the session — the next call has to work."""
    with pytest.raises(ToolError):
        call("get_info", path=str(tmp_path / "absent.pdf"))
    assert payload("get_info", path=a_pdf)["pages"] == 3


# ---- the transform tools (M40) through the protocol -------------------------------


def test_every_write_tool_requires_an_explicit_output(a_pdf):
    """The safety model at schema level: no write tool can be called without saying where the
    result goes, so there is no argument shape that means "in place"."""
    required = {
        t.name: set(t.input_schema.get("required", []))
        for t in asyncio.run(server.list_tools())
        if t.name in WRITE_TOOLS
    }
    for name, args in required.items():
        assert {"out", "out_dir"} & args, f"{name} does not require an output path"


def test_delete_pages_through_the_server(a_pdf, tmp_path):
    out = str(tmp_path / "d.pdf")
    result = payload("delete_pages", path=a_pdf, pages=[2], out=out)
    assert result["pages"] == 2
    assert result["source_unchanged"] is True
    assert os.path.exists(out)


def test_reorder_through_the_server(a_pdf, tmp_path):
    out = str(tmp_path / "r.pdf")
    assert payload("reorder", path=a_pdf, order=[3, 1, 2], out=out)["pages"] == 3


def test_rotate_through_the_server(a_pdf, tmp_path):
    out = str(tmp_path / "t.pdf")
    assert payload("rotate", path=a_pdf, degrees=90, out=out, pages=[1])["rotated"] == [1]


def test_split_through_the_server(a_pdf, tmp_path):
    result = payload("split", path=a_pdf, out_dir=str(tmp_path), ranges=["1-2", "3"])
    assert result["count"] == 2


def test_merge_through_the_server(a_pdf, b_pdf, tmp_path):
    out = str(tmp_path / "m.pdf")
    assert payload("merge", paths=[a_pdf, b_pdf], out=out)["pages"] == 5


def test_fill_form_through_the_server(a_pdf, tmp_path):
    out = str(tmp_path / "f.pdf")
    assert payload("fill_form", path=a_pdf, values={"name": "Ada"}, out=out)["filled"] == ["name"]


def test_flatten_through_the_server(a_pdf, tmp_path):
    out = str(tmp_path / "fl.pdf")
    assert payload("flatten", path=a_pdf, out=out)["flattened"] is True


def test_export_images_through_the_server(a_pdf, tmp_path):
    result = payload("export_images", path=a_pdf, out_dir=str(tmp_path), pages=[1], dpi=36)
    assert result["count"] == 1


def test_a_refusal_reaches_the_client_as_a_tool_error(a_pdf):
    """The source-protection refusal has to arrive as an error the agent can read, not a crash."""
    with pytest.raises(ToolError, match="refusing to write over the input"):
        call("delete_pages", path=a_pdf, pages=[1], out=a_pdf)
