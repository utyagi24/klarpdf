#!/usr/bin/env python3
"""Install the KlarPDF MCP bridge. Needs nothing but a supported Python (M136).

    python3 install.py                 # macOS / Linux
    py -3 install.py                   # Windows

No clone, no `uv`, no `pipx`, no global `pip`. This file creates a virtual environment of its own
with the standard library's `venv` — whose bundled `ensurepip` provides `pip` offline — installs
`klarpdf` from PyPI into it, proves the server actually starts, and prints the one line your MCP
client needs.

**Why this exists when `uv tool install klarpdf` is one line.** Because `uv` is itself a
prerequisite, and so is `pipx`. Those are the right tools if you have them; this is for the machine
that has a Python and nothing else. See `PLAN.md` §M133–M136.

**It never touches your PATH**, your shell profile, or anything outside the one directory it owns.
Your system Python and its site-packages are not modified — every package operation runs through
the new environment's own interpreter.

**It needs no stdin, on purpose.** Anything that would otherwise be a prompt is a flag instead
(`--reinstall`, `--install-dir`), which makes it scriptable, usable in a Dockerfile, and safe to
pipe. Piping is not the documented path — you skip verifying the download against `SHA256SUMS` —
but it is your call, and this says so rather than refusing.

**pip does the network, not this file.** There is no HTTP code here at all: we hand pip a package
name and let it own TLS, proxies, retries and corporate certificates, which is where installers of
this kind usually break.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# BEGIN GENERATED — packaging/mcp/installer/sync_installer.py, from klarpdf/version.py + pyproject.toml
KLARPDF_VERSION = "0.19.0"
PYTHON_MIN = (3, 11)
PYTHON_MAX = (3, 14)
# END GENERATED

PACKAGE = "klarpdf"
SCRIPT = "klarpdf-mcp"
DOCS = "https://github.com/utyagi24/klarpdf/blob/main/klarpdf/mcp_bridge/README.md"

# Passed through to pip: a corporate mirror or proxy must keep working. `--isolated` would suppress
# these *and* the user's pip.conf/pip.ini, which is why it is not used.
KEEP_ENV = (
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CERT",
    "PIP_RETRIES", "PIP_TIMEOUT", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
)
# Stripped: these redirect *where* packages land, and two of them do it silently. Measured (M136):
# `PIP_TARGET` installs outside the virtual environment and still exits 0, so pip reports success
# and the entry point simply is not there. `PIP_USER` at least fails loudly.
STRIP_ENV = ("PIP_USER", "PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PYTHONHOME", "PYTHONPATH")


class InstallError(Exception):
    """Anything the user can act on. Printed without a traceback; exit code 1."""


# --- where it goes -------------------------------------------------------------------------------

def default_install_dir() -> Path:
    """One directory, named the same on every platform, independent of the app.

    The bridge is a separate optional component — plenty of people install it and never install the
    KlarPDF app — so it does not nest under the app's data directory. macOS uses its native
    location; the space in "Application Support" is safe because `venv` writes a `/bin/sh` exec
    shebang rather than a plain one when the path contains a space (measured).
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "klarpdf-mcp"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "klarpdf-mcp"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "klarpdf-mcp"


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def venv_script(venv_dir: Path) -> Path:
    name = f"{SCRIPT}.exe" if sys.platform == "win32" else SCRIPT
    return venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / name


# --- prerequisites -------------------------------------------------------------------------------

def check_python() -> None:
    """Version and interpreter, before anything is written."""
    have = sys.version_info[:2]
    if not (PYTHON_MIN <= have <= PYTHON_MAX):
        lo, hi = ".".join(map(str, PYTHON_MIN)), ".".join(map(str, PYTHON_MAX))
        raise InstallError(
            f"KlarPDF needs Python {lo} through {hi}; this is {sys.version.split()[0]}.\n"
            f"  Run it with a supported interpreter, e.g. `python3.12 install.py`"
            f"{' or `py -3.12 install.py`' if sys.platform == 'win32' else ''}."
        )
    # The Microsoft Store stub: `python` with no Python installed opens the Store and nothing runs,
    # and the Store build writes to a redirected, sandboxed location. Name the remedy here rather
    # than let it surface later as an incomprehensible venv failure.
    if sys.platform == "win32" and "WindowsApps" in sys.executable:
        raise InstallError(
            "This is the Microsoft Store Python, which installs into a sandboxed location.\n"
            "  Install Python from https://python.org, then run:  py -3 install.py"
        )


def check_venv_module() -> None:
    """Debian and Ubuntu split `ensurepip` into a separate package, and the failure is opaque."""
    try:
        import ensurepip  # noqa: F401
    except ImportError:
        raise InstallError(
            "This Python has no `ensurepip`, so it cannot create a virtual environment with pip.\n"
            f"  On Debian/Ubuntu:  sudo apt install python{sys.version_info.major}."
            f"{sys.version_info.minor}-venv\n"
            "  Otherwise, use a complete Python distribution (python.org, pyenv, Homebrew)."
        )


# --- the environment -----------------------------------------------------------------------------

def pip_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    return env


def run(cmd: list, *, verbose: bool, timeout: int = 900, **kw) -> subprocess.CompletedProcess:
    """Always an argv list, never a shell — no quoting rules, no injection surface."""
    if verbose:
        print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(
        [str(c) for c in cmd], shell=False, env=pip_env(), timeout=timeout,
        capture_output=True, text=True, **kw
    )


def make_venv(venv_dir: Path, *, reinstall: bool, verbose: bool) -> None:
    if venv_dir.exists():
        if reinstall:
            print(f"  removing the existing environment at {venv_dir}")
            shutil.rmtree(venv_dir)
        else:
            python = venv_python(venv_dir)
            if not python.exists():
                raise InstallError(
                    f"{venv_dir} exists but has no interpreter — it looks half-created.\n"
                    f"  Replace it:  python install.py --reinstall"
                )
            result = run([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                         verbose=verbose, timeout=60)
            found = result.stdout.strip()
            want = "%d.%d" % sys.version_info[:2]
            if result.returncode != 0 or found != want:
                raise InstallError(
                    f"{venv_dir} was built with Python {found or 'unknown'}, but you are running "
                    f"{want}.\n  Replace it:  python install.py --reinstall"
                )
            print(f"  reusing the existing environment ({want})")
            return
    print(f"  creating a virtual environment at {venv_dir}")
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        venv.create(venv_dir, with_pip=True, clear=True)
    except Exception as exc:  # subprocess failure inside ensurepip, permissions, full disk
        raise InstallError(f"could not create the virtual environment at {venv_dir}: {exc}")


def install_package(venv_dir: Path, *, index_url: str | None, verbose: bool) -> None:
    python = venv_python(venv_dir)
    cmd = [python, "-m", "pip", "install", "--upgrade", f"{PACKAGE}=={KLARPDF_VERSION}"]
    if index_url:
        cmd += ["--index-url", index_url]
    if not verbose:
        cmd.append("--quiet")
    print(f"  installing {PACKAGE}=={KLARPDF_VERSION} (all 29 dependencies are pinned by it)")
    result = run(cmd, verbose=verbose)
    if result.returncode != 0:
        raise InstallError(
            f"pip could not install {PACKAGE}=={KLARPDF_VERSION}.\n"
            f"{_excerpt(result.stderr or result.stdout)}\n"
            "  If you are behind a proxy or an internal index, pip reads the usual environment\n"
            "  variables and your pip.conf/pip.ini; `--index-url` overrides the index directly."
        )


def _excerpt(text: str, lines: int = 12) -> str:
    """A bounded tail of a subprocess's output — enough to diagnose, not a wall of text."""
    kept = [ln for ln in (text or "").strip().splitlines() if ln.strip()][-lines:]
    return "\n".join(f"  | {ln}" for ln in kept) or "  | (no output)"


# --- proving it works ----------------------------------------------------------------------------

def validate(venv_dir: Path, *, verbose: bool) -> str:
    """Two checks, because a pip that reports success can still leave nothing runnable.

    The first catches the silent case measured in M136 — `PIP_TARGET` set in the environment sends
    the install elsewhere and still exits 0. The second speaks the actual protocol: it writes one
    JSON-RPC `initialize` to the server's stdin and requires a well-formed JSON-RPC reply on
    stdout. That also proves nothing else is writing to stdout, which for a stdio server is fatal
    and otherwise invisible until a client session dies mid-conversation.

    A protocol *error* reply counts as success. The point is that the process started, read stdin
    and answered in JSON-RPC — pinning a protocol version here would make the installer break the
    next time the MCP SDK moves.
    """
    script = venv_script(venv_dir)
    if not script.exists():
        raise InstallError(
            f"the install reported success but {script} does not exist.\n"
            "  This is what a redirected pip looks like: check for PIP_TARGET, PIP_PREFIX or\n"
            "  PIP_USER in your environment, then re-run."
        )

    result = run([script, "--help"], verbose=verbose, timeout=120)
    if result.returncode != 0:
        raise InstallError(f"{script} would not start.\n{_excerpt(result.stderr or result.stdout)}")

    request = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "klarpdf-install", "version": "1"}},
    }) + "\n"
    try:
        spoke = subprocess.run(
            [str(script)], shell=False, env=pip_env(), input=request,
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise InstallError(f"{script} started but never answered an MCP initialize within 120s.")

    for line in spoke.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            raise InstallError(
                f"{script} wrote something that is not JSON-RPC to stdout, which corrupts every\n"
                f"  client session:\n  | {line[:200]}"
            )
        if isinstance(message, dict) and message.get("jsonrpc") == "2.0":
            return (message.get("result", {}).get("serverInfo", {}) or {}).get("version") or KLARPDF_VERSION
    raise InstallError(
        f"{script} answered nothing on stdout.\n{_excerpt(spoke.stderr)}"
    )


# --- what the user is left with -------------------------------------------------------------------

UNINSTALL = '''#!/usr/bin/env python3
"""Remove the KlarPDF MCP install in this directory. Written by install.py; safe to delete."""
import os, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if not (HERE / ".venv" / "pyvenv.cfg").exists():
    sys.exit(f"{HERE} does not look like a KlarPDF MCP install; refusing.")

print(f"This will delete: {HERE}")
if input("Continue? [y/N] ").strip().lower() != "y":
    sys.exit("Cancelled.")

os.chdir(Path.home())          # Windows cannot remove a directory that is the process's CWD
shutil.rmtree(HERE)
print("""Removed. Also remove it from your client:
  claude mcp remove klarpdf
  codex mcp remove klarpdf
  gemini mcp remove klarpdf
For Claude Desktop, delete the "klarpdf" entry from claude_desktop_config.json.""")
'''


def write_uninstaller(install_dir: Path) -> Path:
    path = install_dir / "uninstall.py"
    path.write_text(UNINSTALL, encoding="utf-8")
    return path


CLIENTS = {
    "claude-code": ["claude", "mcp", "add", "klarpdf", "--"],
    "codex": ["codex", "mcp", "add", "klarpdf", "--"],
    "gemini": ["gemini", "mcp", "add", "klarpdf"],
}

# What a scope means to each client, read off each CLI rather than assumed (M143). Values a client
# does not list are refused rather than passed through — `gemini` has no `local`, and Codex CLI has
# no scopes at all, always writing `~/.codex/config.toml`.
CLIENT_SCOPES = {
    "claude-code": ("local", "user", "project"),
    "codex": (),
    "gemini": ("user", "project"),
}

# Where each client wants the flag. Not cosmetic: Claude Code parses flags before the name and hands
# everything after `--` to the server, so a `--scope` written late is passed to `klarpdf-mcp`, which
# has no such option and fails. Gemini CLI takes it after the command instead.
SCOPE_POSITION = {"claude-code": "before-name", "gemini": "after-command"}


def client_argv(client: str, script: Path, scope: str | None) -> list:
    """The client's own registration command, with the scope where that client expects it."""
    argv = list(CLIENTS[client])
    if scope is None:
        return argv + [str(script)]
    if SCOPE_POSITION[client] == "before-name":
        cut = argv.index("klarpdf")
        return argv[:cut] + ["--scope", scope] + argv[cut:] + [str(script)]
    return argv + [str(script), "--scope", scope]


def check_client_scope(parser, client: str | None, scope: str | None) -> None:
    """Refuse anything ambiguous *before* installing, so a mistake costs a retype, not an install.

    `--client-scope` is mandatory with `--client` rather than defaulted, because both defaults on
    offer were wrong. Deferring to the client's own default anchors the entry to whatever directory
    `install.py` was run from — normally the download directory, which is the one place the reader
    will never work — while defaulting to `user` writes to a config file they did not name. Neither
    is a decision this script gets to make quietly.
    """
    if client is None:
        if scope is not None:
            parser.error("--client-scope has no meaning without --client")
        return

    allowed = CLIENT_SCOPES[client]
    if not allowed:
        if scope is not None:
            parser.error(f"--client {client} takes no --client-scope: {client} has no scopes and "
                         f"always writes one global config file")
        return

    if scope is None:
        parser.error(f"--client {client} requires --client-scope ({' | '.join(allowed)}). "
                     f"There is no default: see the scope table in --help")
    if scope not in allowed:
        parser.error(f"--client {client} has no `{scope}` scope; it accepts {' | '.join(allowed)}")


def configure_client(client: str, script: Path, scope: str | None, *, verbose: bool) -> bool:
    """Call the client's *own* CLI, on request. Not silent editing — the user passed `--client`."""
    argv = client_argv(client, script, scope)
    if shutil.which(argv[0]) is None:
        print(f"  `{argv[0]}` is not on your PATH — skipping, the command is printed below.")
        return False
    result = run(argv, verbose=verbose, timeout=120)
    if result.returncode != 0:
        print(f"  `{' '.join(argv[:3])}` failed; the command is printed below.")
        print(_excerpt(result.stderr or result.stdout, 6))
        return False
    where = f" at --scope {scope}" if scope else " (Codex CLI has no scopes; this applies everywhere)"
    print(f"  registered with {client}{where}")
    return True


def report(install_dir: Path, script: Path, version: str, configured: bool) -> None:
    quoted = f'"{script}"' if " " in str(script) else str(script)
    print(f"""
KlarPDF MCP {version} is installed.

  location   {install_dir}
  command    {script}
  uninstall  python {install_dir / 'uninstall.py'}
""")
    if not configured:
        print(f"""Add it to your client — the command is the whole configuration; there is no URL,
port or token, because this server speaks stdio and makes no network connections.

  Claude Code   claude mcp add --scope user klarpdf -- {quoted}
  Codex CLI     codex mcp add klarpdf -- {quoted}
  Gemini CLI    gemini mcp add klarpdf {quoted} --scope user

  Claude Desktop and anything else using an `mcpServers` block:
    {{"mcpServers": {{"klarpdf": {{"command": {json.dumps(str(script))}}}}}}}

`--scope user` registers it for every directory; Claude Code and Gemini CLI both
default to the one you run them in. Codex CLI has no scope — it is always global.
""")
    print(f"""Two switches narrow what it may do — `--read-only`, and `--allow-root DIR` to confine
it to one directory tree. Pass them as `args` beside the command. Full reference:
  {DOCS}""")


EPILOG = """
examples:
  python3 install.py
      install only. The commands to register it by hand are printed at the end.

  python3 install.py --client claude-code --client-scope user
      install, and register for every directory you use Claude Code in.

  py -3 install.py --client gemini --client-scope project
      Windows. Registers in the .gemini/settings.json of the directory this is run from.

  python3 install.py --client codex
      Codex CLI has no scopes, so --client-scope is not accepted for it.

--client-scope is required with --client, and the values differ per client because the
clients differ. `local` and `project` mean *the directory install.py is running in* — which
is wherever you downloaded it to, so `user` is usually what you want:

  claude-code   local | user | project    local   = this directory only  (Claude Code's own default)
  gemini        user | project            project = this directory only  (Gemini CLI's own default)
  codex         --                        always ~/.codex/config.toml

Full setup guide: https://github.com/utyagi24/klarpdf/blob/main/klarpdf/mcp_bridge/QUICKSTART.md
"""


# --- entry point ----------------------------------------------------------------------------------

def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py", description="Install the KlarPDF MCP bridge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("--install-dir", type=Path, default=None,
                        help=f"where to install (default: {default_install_dir()})")
    parser.add_argument("--reinstall", action="store_true",
                        help="replace an existing environment instead of reusing it")
    parser.add_argument("--index-url", default=None,
                        help="package index to install from (default: PyPI, or your pip config)")
    parser.add_argument("--client", choices=sorted(CLIENTS),
                        help="also register the server with this client, using its own CLI")
    parser.add_argument("--client-scope", choices=sorted({s for v in CLIENT_SCOPES.values() for s in v}),
                        help="which scope --client registers in; required with --client "
                             "(except codex, which has none). See the examples below")
    parser.add_argument("--verbose", action="store_true", help="show every command that is run")
    args = parser.parse_args(argv)
    check_client_scope(parser, args.client, args.client_scope)

    # `python -` reads the program from stdin, so there is no file on disk to verify. It works;
    # it just skips a check the documented path gives you for free.
    if globals().get("__file__", "<stdin>") == "<stdin>":
        print("note: running from a pipe, so this file was not verified against SHA256SUMS.\n"
              "      To verify: download install.py and SHA256SUMS, `sha256sum --check`, then run it.\n")

    install_dir = (args.install_dir or default_install_dir()).expanduser().resolve()
    venv_dir = install_dir / ".venv"

    print(f"KlarPDF MCP {KLARPDF_VERSION}")
    if args.verbose:
        print(f"  interpreter {sys.executable} ({sys.version.split()[0]})")

    check_python()
    check_venv_module()
    make_venv(venv_dir, reinstall=args.reinstall, verbose=args.verbose)
    install_package(venv_dir, index_url=args.index_url, verbose=args.verbose)
    version = validate(venv_dir, verbose=args.verbose)
    write_uninstaller(install_dir)

    script = venv_script(venv_dir)
    configured = (configure_client(args.client, script, args.client_scope, verbose=args.verbose)
                  if args.client else False)
    report(install_dir, script, version, configured)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)
