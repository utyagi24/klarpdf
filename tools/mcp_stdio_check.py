"""RELEASE.md §4 item 8 — drive `klarpdf-mcp` over real stdio, the way a host does.

Not a pytest test, and deliberately not one. Everything in `tests/test_mcp_*.py` calls the server
**in process**: `server.call_tool(...)` on an imported object. That covers the tools; it cannot
cover the layer a host actually meets — the console script being on PATH, a JSON-RPC handshake over
a pipe, a protocol version both ends agree on, an image arriving as base64 in a content block, and
an error coming back as a result rather than a traceback down stdout that would corrupt the stream.

This was a one-off script in a WSL session before M126, which is why the matrix row said "done in
WSL" and pointed at nothing. Now it is one command:

    python tools/mcp_stdio_check.py

Requires `klarpdf-mcp` on PATH — `pip install -e .` from the repo, or the bridge lock. Everything
it touches is a temp directory it makes and removes; it never writes into the repo.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pymupdf
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = 19          # pinned to the manifest by tests/test_mcp_packaging.py
DESCRIPTION_CAP = 2048       # the client truncates past this, silently (M105)

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    tail = f" — {detail}" if detail else ""
    if condition:
        _passed += 1
        print(f"  PASS  {label}{tail}")
    else:
        _failed += 1
        print(f"  FAIL  {label}{tail}")


def make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 100), f"ALPHA page {i + 1}", fontsize=18)
        page.insert_text((72, 140), "BRAVO CHARLIE", fontsize=12)
    doc.set_toc([[1, "First", 1], [1, "Third", 3]])
    doc.save(str(path))
    doc.close()


def text_of(result) -> str:
    """The text blocks of a CallToolResult, joined."""
    return "\n".join(
        block.text
        for block in (getattr(result, "content", None) or [])
        if getattr(block, "type", None) == "text"
    )


async def run(server_path: str) -> int:
    work = Path(tempfile.mkdtemp(prefix="klarpdf-stdio-"))
    outside = Path(tempfile.mkdtemp(prefix="klarpdf-outside-"))
    try:
        pdf = work / "sample.pdf"
        make_pdf(pdf)
        before = hashlib.sha256(pdf.read_bytes()).hexdigest()
        (outside / "other.pdf").write_bytes(pdf.read_bytes())

        params = StdioServerParameters(
            command=server_path,
            args=["--allow-root", str(work)],
            # A host launches its servers from ITS working directory, never the repo. Starting from
            # home is what proves the console script does not depend on `cwd` the way `-m` did.
            cwd=str(Path.home()),
        )
        print(f"server : {server_path}")
        print(f"sandbox: {work}\n")

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                check(
                    "initialize",
                    bool(getattr(init.server_info, "name", "")),
                    f"{init.server_info.name} {init.server_info.version}, "
                    f"protocol {init.protocol_version}",
                )
                check(
                    "instructions present",
                    bool(init.instructions),
                    f"{len(init.instructions or '')} chars",
                )

                listed = await session.list_tools()
                names = sorted(tool.name for tool in listed.tools)
                check("list_tools", len(names) == EXPECTED_TOOLS, f"{len(names)} tools")
                print(f"        {', '.join(names)}")
                over = [t.name for t in listed.tools if len(t.description or "") > DESCRIPTION_CAP]
                check(f"every description under the {DESCRIPTION_CAP}-char client cap", not over, str(over))

                info = await session.call_tool("get_info", {"path": str(pdf)})
                payload = json.loads(text_of(info))
                check("get_info", payload.get("pages") == 3, f"pages={payload.get('pages')}")

                img = await session.call_tool(
                    "render_page", {"path": str(pdf), "page": 1, "dpi": 36}
                )
                images = [b for b in img.content if getattr(b, "type", None) == "image"]
                check(
                    "render_page returns an image",
                    bool(images),
                    f"{len(base64.b64decode(images[0].data))} bytes, {images[0].mime_type}"
                    if images
                    else "no image block",
                )

                res = await session.read_resource("klarpdf://docs/redact_text")
                body = "".join(getattr(c, "text", "") for c in res.contents)
                check("klarpdf://docs/redact_text", len(body) > 500, f"{len(body)} chars")

                out = work / "rotated.pdf"
                await session.call_tool(
                    "rotate", {"path": str(pdf), "degrees": 90, "out": str(out)}
                )
                check("rotate wrote a new file", out.exists())
                check(
                    "source left byte-identical",
                    hashlib.sha256(pdf.read_bytes()).hexdigest() == before,
                )

                # The three refusals, over the wire. Each must be a clean error *result* — a
                # traceback on stdout would corrupt the JSON-RPC stream rather than fail a test.
                bad = await session.call_tool("get_info", {"path": str(pdf), "nonsense": 1})
                check(
                    "unknown parameter rejected (M106)",
                    bool(bad.is_error) and "nonsense" in text_of(bad),
                    text_of(bad)[:90].replace("\n", " "),
                )

                ref = await session.call_tool(
                    "get_info", {"path": str(outside / "other.pdf")}
                )
                check(
                    "path outside --allow-root refused (M124)",
                    bool(ref.is_error) and "allow-root" in text_of(ref),
                    text_of(ref)[:90].replace("\n", " "),
                )

                folder = await session.call_tool("get_info", {"path": str(work)})
                check(
                    "a directory says so (M119)",
                    bool(folder.is_error) and "director" in text_of(folder).lower(),
                    text_of(folder)[:90].replace("\n", " "),
                )

                # The stream survived all three: an error must not end the session.
                again = await session.call_tool("search", {"path": str(pdf), "query": "ALPHA"})
                hits = json.loads(text_of(again))
                check(
                    "session healthy after errors",
                    hits.get("count", 0) >= 3,
                    f"search -> count={hits.get('count')}",
                )
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


def main() -> int:
    server_path = shutil.which("klarpdf-mcp")
    if not server_path:
        print(
            "klarpdf-mcp is not on PATH.\n"
            "  pip install -e .                       (from this repo)\n"
            "  pip install -r requirements-mcp.txt    (the audited pins)",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(run(server_path))


if __name__ == "__main__":
    sys.exit(main())
