"""Project rules that Ruff cannot express, checked by reading the source (M168).

Ruff has no plugin system, so a rule that needs more than a banned name is a test, in the pattern
``tests/test_pypdf_is_dev_only.py`` set. Each rule here is written down in CLAUDE.md or PLAN.md, and
each held on the day it became a test, by habit alone:

* **Every suppression gives a reason.** Ruff refuses a bare ``# noqa`` (PGH004) and one that
  suppresses nothing (RUF100). The reason is this file's half. Of the 23 ``noqa`` comments in the
  code on 2026-11-08, 10 suppressed nothing and 16 gave no reason (PLAN.md §M168).
* **No ``get_textbox`` in shipped code.** It re-extracts the whole page on every call, and it reads
  a box by clipping, so it returns whatever shares the box's band (the module docstring of
  ``klarpdf/model/page_text.py``, and PLAN.md §M138).
* **No ``.connect`` to a signal of ``windowHandle()``** (CLAUDE.md, *Never connect a widget's slot
  to a signal on a QObject that outlives it*). PySide6 does not reliably drop the connection when
  the widget dies, so the slot later runs on freed memory: a crash, not an exception.
* **No BOM and no mis-decoded UTF-8 in tracked text.** Windows PowerShell 5.1 reads a UTF-8 file
  without a BOM as ANSI, and one ``Get-Content | Set-Content`` once turned every ``—``, ``×`` and
  ``…`` in a file into two or three wrong characters.

A scan that finds nothing looks exactly like a clean one, so each detector is also run on a sample
it must catch.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

from tests.test_pypdf_is_dev_only import _shipped_modules, needs_git

ROOT = Path(__file__).resolve().parent.parent


def _tracked(*patterns: str) -> list[str]:
    """Paths git tracks, matching ``patterns``. Git, not a walk: a walk also reads what builds leave
    in ignored folders (the docstring of ``_shipped_modules`` has the story)."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return sorted(name for name in set(filter(None, out.split("\0"))) if (ROOT / name).is_file())


# ---- every suppression gives a reason ------------------------------------------------------------

_NOQA = re.compile(r"#\s*noqa\b(?P<rest>.*)", re.IGNORECASE)
# The codes, then a dash, then the reason: `noqa: F401 — why`. A hyphen is accepted as well as the
# em dash: the rule is that a reason is there, not how it is punctuated.
_NOQA_WITH_REASON = re.compile(r":\s*[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*\s*(?:—|-{1,2})\s*\S")
_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore(?P<codes>\[[^\]]*\])?(?P<rest>.*)")
# mypy reads a `type: ignore` only if the comment ends there or a new `#` starts, so the reason
# goes after a second hash, as in the module docstring.
_TYPE_IGNORE_REASON = re.compile(r"\s*#\s*\S")


def _unexplained_suppressions(source: str) -> list[tuple[int, str]]:
    """The ``noqa`` and ``type: ignore`` comments in ``source`` that give no reason.

    It reads comment tokens only, so a string or docstring that mentions ``# noqa`` (this file is
    full of them) is not a suppression and is not checked.
    """
    found = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        noqa = _NOQA.search(token.string)
        if noqa and not _NOQA_WITH_REASON.match(noqa.group("rest")):
            found.append((token.start[0], token.string))
        ignore = _TYPE_IGNORE.search(token.string)
        if ignore and not (ignore.group("codes") and _TYPE_IGNORE_REASON.match(ignore.group("rest"))):
            found.append((token.start[0], token.string))
    return found


def test_the_reason_check_tells_a_reason_from_none():
    with_reason = (
        "import x  # noqa: F401 — imported only to learn whether it exists\n"
        "y = lambda: 1  # noqa: E731, E501 - one expression\n"
        "z.w = 9  # type: ignore[misc]  # frozen: assigning must raise\n"
        "s = '# noqa'  # a string that mentions it is not a suppression\n"
    )
    assert _unexplained_suppressions(with_reason) == []
    without = (
        "import x  # noqa: F401\n"
        "import y  # noqa\n"
        "z.w = 9  # type: ignore[misc]\n"
        "z.v = 9  # type: ignore  # a reason, but no code\n"
    )
    assert [line for line, _ in _unexplained_suppressions(without)] == [1, 2, 3, 4]


@needs_git
def test_every_suppression_gives_a_reason():
    offenders = [
        f"{name}:{line}: {comment}"
        for name in _tracked("*.py")
        for line, comment in _unexplained_suppressions((ROOT / name).read_text("utf-8"))
    ]
    assert offenders == [], (
        "a suppression is a decision, and a decision needs its reason (CLAUDE.md, Run Ruff before a "
        "push, and give every suppression a reason). Write `# noqa: CODE — why` or "
        "`# type: ignore[code]  # why`:\n  " + "\n  ".join(offenders)
    )


# ---- no get_textbox in shipped code --------------------------------------------------------------


def _get_textbox_uses(tree: ast.AST) -> list[int]:
    """Lines that name ``get_textbox`` as an attribute: a call, or a reference kept for later."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "get_textbox"
    )


def test_the_get_textbox_check_finds_a_call():
    tree = ast.parse('"""Never page.get_textbox(rect)."""\ntext = page.get_textbox(rect)\nf = p.get_textbox\n')
    assert _get_textbox_uses(tree) == [2, 3]


@needs_git
def test_no_shipped_code_uses_get_textbox():
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _shipped_modules()
        for line in _get_textbox_uses(ast.parse(path.read_text("utf-8")))
    ]
    assert offenders == [], (
        f"shipped code uses page.get_textbox: {offenders}. It re-extracts the page on every call and "
        "clips, so it returns neighbouring text too. Read the page once with "
        "klarpdf.model.page_text.PageText instead (its module docstring has the measurements)."
    )


# ---- no connection to a windowHandle() signal ----------------------------------------------------


def _is_window_handle_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "windowHandle"
    )


def _window_handle_connects(tree: ast.AST) -> list[int]:
    """Lines that ``.connect`` a signal of a ``windowHandle()``.

    It sees the direct chain, ``w.windowHandle().screenChanged.connect(...)``, and a handle kept in
    a name or attribute first, ``h = w.windowHandle()`` then ``h.screenChanged.connect(...)``. It
    matches the kept handle by how it is written, anywhere in the module, so a handle passed
    through a function argument is not seen.
    """
    handles = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and _is_window_handle_call(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            handles.update(ast.unparse(target) for target in targets)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        signal = node.func.value
        if node.func.attr == "connect" and isinstance(signal, ast.Attribute):
            owner = signal.value
            if _is_window_handle_call(owner) or ast.unparse(owner) in handles:
                found.append(node.lineno)
    return sorted(found)


def test_the_window_handle_check_finds_both_shapes():
    tree = ast.parse(
        "self.window().windowHandle().screenChanged.connect(self._on_screen)\n"
        "self._handle = self.windowHandle()\n"
        "self._handle.screenChanged.connect(self._on_screen)\n"
        "self.windowHandle().resize(10, 10)\n"           # a call, not a connection
        "self.button.clicked.connect(self._on_click)\n"  # a connection, not to a window handle
    )
    assert _window_handle_connects(tree) == [1, 3]


@needs_git
def test_no_shipped_code_connects_to_a_window_handle_signal():
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line}"
        for path in _shipped_modules()
        for line in _window_handle_connects(ast.parse(path.read_text("utf-8")))
    ]
    assert offenders == [], (
        f"a slot is connected to a windowHandle() signal: {offenders}. The window handle outlives "
        "the widget, and PySide6 does not reliably drop the connection, so the slot later runs on "
        "freed memory. Use the widget event Qt delivers to the widget itself, such as "
        "QEvent.Type.DevicePixelRatioChange (CLAUDE.md, Gotchas)."
    )


# ---- no BOM and no mis-decoded UTF-8 -------------------------------------------------------------

# The lead byte of a UTF-8 character read as cp1252 or Latin-1, followed by the characters its
# continuation bytes became. Only four leads: C2 and C3 start every Latin-1 letter and sign (e-acute,
# the multiplication sign, the degree sign), E2 starts the punctuation, arrows and maths (the dashes,
# the ellipsis, the arrows), and F0 starts an emoji. Any other lead is a character real text puts
# before a dash: the multiplication sign then an en dash is the bytes D7 96, which are valid UTF-8,
# and PLAN.md writes exactly that. On 2026-11-08 all but six of the repository's non-ASCII
# characters had one of these four leads. The pattern is written in escapes so that this file holds
# none of the characters it looks for. Matched with a lookahead so that runs can overlap.
_LEAD = re.compile("(?=([\u00c2\u00c3\u00e2\u00f0][^\x00-\x7f]{1,3}))")


def _as_misread_bytes(text: str) -> bytes | None:
    """The bytes ``text`` came from, if it is UTF-8 that was read as cp1252 or Latin-1."""
    out = bytearray()
    for char in text:
        if ord(char) < 0x100:
            out.append(ord(char))  # Latin-1, and the five bytes cp1252 leaves undefined
        else:
            try:
                out += char.encode("cp1252")
            except UnicodeEncodeError:
                return None
    return bytes(out)


def _misdecoded(text: str) -> list[str]:
    """Runs of ``text`` that are UTF-8 read with the wrong code page, such as an em dash that has
    become three characters.

    It turns each candidate run back into the bytes it came from and asks whether they are UTF-8.
    So a document that *names* the symptom passes: PLAN.md writes A-tilde between backticks, and
    A-tilde followed by a backtick is not a UTF-8 sequence.
    """
    found = []
    for match in _LEAD.finditer(text):
        run = match.group(1)
        raw = _as_misread_bytes(run)
        if raw is None:
            continue
        length = 2 if raw[0] < 0xE0 else 3 if raw[0] < 0xF0 else 4
        try:
            raw[:length].decode("utf-8")
        except UnicodeDecodeError:
            continue
        found.append(run[:length])
    return found


def test_the_misdecoding_check_finds_the_symptom_and_not_its_name():
    for good in ("—", "×", "…", "é", "→", "°", "\U0001f680"):
        misread = good.encode("utf-8").decode("cp1252")
        assert _misdecoded(misread) == [misread], good
    misread = "é".encode("utf-8").decode("latin-1")
    assert _misdecoded(misread) == [misread]
    # Real text: PLAN.md names the symptom, and writes the multiplication sign before dashes.
    assert _misdecoded("No mis-decoded text (`\u00c3`, `\u00e2\u20ac`) — café, naïve, 3 × 4…") == []
    assert _misdecoded("1.5×–2×, and 2×…") == []


def _tracked_text_files() -> list[str]:
    """Tracked files git classes as text. Git decides from content, so a source file saved in the
    wrong encoding is still text here and fails below, rather than being skipped as binary."""
    out = subprocess.run(
        ["git", "ls-files", "--eol", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    names = []
    for record in filter(None, out.split("\0")):
        info, _, name = record.partition("\t")
        if not info.startswith("i/-text") and (ROOT / name).is_file():
            names.append(name)
    return sorted(names)


@needs_git
def test_the_text_scan_sees_source_and_documents():
    found = set(_tracked_text_files())
    assert {"main_window.py", "PLAN.md", "pyproject.toml", ".github/workflows/test.yml"} <= found
    assert "packaging/app/klarpdf.ico" not in found  # binary, so not read as text


@needs_git
def test_tracked_text_has_no_bom_and_no_misdecoded_utf8():
    problems = []
    for name in _tracked_text_files():
        raw = (ROOT / name).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{name}: starts with a BOM")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{name}: not UTF-8 ({exc.reason} at byte {exc.start})")
            continue
        problems += [f"{name}: mis-decoded {run!r}" for run in _misdecoded(text)]
    assert problems == [], (
        "tracked text must be UTF-8 with no BOM. Windows PowerShell 5.1 reads UTF-8 without a BOM "
        "as ANSI, so a Get-Content | Set-Content round trip mangles every non-ASCII character. Edit "
        "with an editor or the Edit tool instead:\n  " + "\n  ".join(problems)
    )
