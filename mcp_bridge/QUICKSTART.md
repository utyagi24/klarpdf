# Quick setup — Claude Code, Codex CLI, Gemini CLI

Use this quick setup guide for configuring KlarPDF MCP in your Claude Code, Codex CLI and Gemini CLI
environments. For setting up Claude Desktop, and for a more detailed explanation of the interface
the MCP server offers, refer to the [full guide](README.md).

Needs Python 3.11–3.14.

## 1. Install

```bash
git clone https://github.com/utyagi24/klarpdf.git
cd klarpdf
git checkout v<version>               # a release tag; `git tag` lists them
python3 -m venv .venv                 # Windows: py -3 -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements-mcp.txt
pip install -e .
```

## 2. Note the path

```bash
which klarpdf-mcp        # Windows: where klarpdf-mcp
# -> /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

Use that full path in step 3. The bare `klarpdf-mcp` works only while the virtualenv is active, and
your client will not activate it.

## 3. Add it to your client

**Claude Code**

```bash
claude mcp add klarpdf -- /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

**Codex CLI**

```bash
codex mcp add klarpdf -- /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

**Gemini CLI**

```bash
gemini mcp add klarpdf /path/to/klarpdf/.venv/bin/klarpdf-mcp
```

Using Claude Desktop instead? It installs from a downloadable bundle rather than a command — see
[Claude Desktop](README.md#claude-desktop).

## 4. Check it worked

In Claude Code, `/mcp` should list **klarpdf — 19 tools**.

If the server shows as failed, run the path from step 2 by hand in a terminal:

- `command not found` — the path is wrong, or you moved the clone.
- `No module named mcp` — the first `pip` line in step 1 did not run.

## What you can ask for

The tools are mechanical, not semantic: the agent decides what to do, this server does it exactly.

> Split contract.pdf into one file per section, and tell me which pages mention indemnity.

Every write tool takes an explicit output path and leaves your input file byte-identical. The one to
read about before using is redaction, which deletes content permanently — see
[what it guarantees](README.md#what-redaction-guarantees-and-where-it-stops).

Nothing here makes a network connection, and your PDFs are never uploaded anywhere.
