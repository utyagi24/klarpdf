# Quick setup — Claude Code, Codex CLI, Gemini CLI

Use this quick setup guide for configuring KlarPDF MCP in your Claude Code, Codex CLI and Gemini CLI
environments. For setting up Claude Desktop, and for a more detailed explanation of the interface
the MCP server offers, refer to the [full guide](README.md).

Needs [`uv`](https://docs.astral.sh/uv/), and Python 3.11–3.14 — which `uv` will fetch for you if
you have none that fits. [`pipx`](https://pipx.pypa.io) works just as well if you already have it
and a suitable Python; every command below names its `pipx` equivalent.

## 1. Install

```bash
uv tool install klarpdf        # pipx: pipx install klarpdf
```

Either way the bridge lands in an environment of its own, at the exact 29 dependency versions we
test and scan.

### Then put that install's `bin` directory on your PATH

**Installing does not do this for you.** Both tools warn you when the directory is missing from your
PATH, in the middle of the install output where it scrolls past:

> `/home/you/.local/bin` is not on your PATH. To use installed tools, run `uv tool update-shell` or
> add the directory to your PATH.

```bash
uv tool update-shell        # pipx: pipx ensurepath
```

**That will never affect the session you are in.** It edits your shell profile (Linux, macOS) or
your user `PATH` variable (Windows), and a process keeps whatever environment it was started with —
which `pipx` says in as many words: *"You will need to open a new terminal or re-login for the PATH
changes to take effect."* So:

- **Open a new terminal** before step 2. On Linux and macOS, `source ~/.bashrc` in the current one
  works too; on Windows there is no equivalent — open a new one.
- **Quit and reopen anything already running** that has to find the command — Claude Desktop, your
  editor, a `claude` session you started earlier. Opening a new terminal does not restart those.

**Or skip all of it.** PATH is a convenience for typing `klarpdf-mcp` yourself; step 3 configures
your client with an absolute path, and step 2 can read that path off without PATH at all.

## 2. Note the path

```bash
which klarpdf-mcp        # Windows: where klarpdf-mcp
# -> ~/.local/bin/klarpdf-mcp
```

**Printed nothing?** Then this terminal's PATH predates the install — but you do not need PATH to
finish. Ask the installer where it put the command instead:

```bash
uv tool dir --bin                            # -> /home/you/.local/bin
# pipx: pipx environment --value PIPX_BIN_DIR
```

`klarpdf-mcp` is inside whichever directory that prints (`klarpdf-mcp.exe` on Windows).

Use that full path in step 3. Once the PATH step above has taken effect the bare name often works
too — but *often* is the problem: a client launched from an icon inherits a different PATH than your
terminal, and when that bites, the only symptom is a server that fails to start. The absolute path
always works.

## 3. Add it to your client

Each example below uses the Linux and macOS path. **On Windows, substitute whatever `where
klarpdf-mcp` printed in step 2** — the client commands themselves are identical, and a path with
spaces in it needs quoting.

**Claude Code**

```bash
claude mcp add --scope user klarpdf -- ~/.local/bin/klarpdf-mcp
```

Read that as three parts, in this order: **flags for `claude`** (`--scope user`), then the **name**
the server appears under in `/mcp` (`klarpdf`), then `--` and the **command to run**. The `--`
separates the two, so a flag written after it goes to `klarpdf-mcp` rather than to `claude`.

`--scope user` registers it once for **every** directory. Leave it off and you get the default,
`--scope local` — the current directory only, which is rarely what you want from a server that works
on any PDF anywhere. `--scope project` writes a `.mcp.json` for everyone who clones that repo. Which
one wins when two of them name `klarpdf`, and how to move an entry between scopes, are in
[the full guide](README.md#claude-code).

**Codex CLI**

```bash
codex mcp add klarpdf -- ~/.local/bin/klarpdf-mcp
```

No scope to choose: `codex mcp add` writes `~/.codex/config.toml`, which applies everywhere.

**Gemini CLI**

```bash
gemini mcp add klarpdf ~/.local/bin/klarpdf-mcp --scope user
```

Gemini takes its flag **after** the command, where Claude Code takes it before the name. Its default
is `--scope project` — the `.gemini/settings.json` of the directory you run it in; `--scope user`
writes `~/.gemini/settings.json` instead.

Using Claude Desktop instead? It installs from a downloadable bundle rather than a command — see
[Claude Desktop](README.md#claude-desktop).

## 4. Check it worked

In Claude Code, `/mcp` should list **klarpdf — 20 tools**.

**Not listed at all?** That is a scope question, not a broken server: you are in a directory the
entry does not cover. `claude mcp list` shows what *this* directory sees, and `claude mcp get
klarpdf` names the scope it came from. Re-add with `--scope user` (step 3) to stop it mattering.

**Listed, but failed?** Run the path from step 2 by hand in a terminal:

- `command not found` — the path is wrong, or you moved the clone.
- `No module named mcp` — the install did not complete; re-run step 1 and read its output.

## What you can ask for

The tools are mechanical, not semantic: the agent decides what to do, this server does it exactly.

> Split contract.pdf into one file per section, and tell me which pages mention indemnity.

Every write tool takes an explicit output path and leaves your input file byte-identical. The one to
read about before using is redaction, which deletes content permanently — see
[what it guarantees](README.md#what-redaction-guarantees-and-where-it-stops).

Nothing here makes a network connection, and your PDFs are never uploaded anywhere.
