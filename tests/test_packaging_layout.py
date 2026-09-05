"""Every build file's idea of "the repo root" must actually be the repo root (M133).

**Why this file exists.** M133 moved six app files into ``packaging/app/`` and ``packaging/mcpb/``
into ``packaging/mcp/mcpb/``. Rewriting the 108 textual ``packaging/…`` references was mechanical
and safe. What was neither was the other seventeen sites, which contain **no** ``packaging/`` string
and so no grep could reach — a repo root computed by counting directories up
(``HERE.parent.parent``, ``Path(SPECPATH).resolve().parent``, ``Split-Path -Parent $PSScriptRoot``,
Inno's ``..\\dist``) and paths assembled from components (``ROOT / "packaging" / "mcpb" / …``).

A move does not break those loudly. It **repoints** them: the code still runs, still resolves a
path, and silently addresses the wrong directory. ``build_mcpb.py`` died on a missing
``packaging/version.py`` only because something downstream happened to read a file; the Inno and
PowerShell ones would have surfaced as a broken installer on a release day.

**What is and is not covered.** The Python builders are already guarded by their own tests — a wrong
``ROOT`` makes ``tests/test_mcp_packaging.py`` fail with ``FileNotFoundError``, which is how M133's
first defect was caught. What nothing covered is the two files that **only execute on Windows during
a real build**: ``installer.iss`` (Inno resolves its relative paths against the script's own
directory) and ``build.ps1``. Those are what this file pins, so a future move fails here on Linux
in a second rather than on Windows at release time.

``klarpdf.spec``'s ``SPECPATH`` is deliberately not pinned: PyInstaller defines that name only while
it is running the spec, so asserting on it means reimplementing PyInstaller's own resolution. Its
``ROOT`` is instead checked by ``tests/test_about_dialog.py`` reading the file at its real path, and
by the Windows build itself.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "app" / "installer.iss"
BUILD_PS1 = ROOT / "packaging" / "app" / "build.ps1"


def _iss_relative_paths() -> list[tuple[str, str]]:
    """`(directive, raw path)` for every `..`-relative path the Inno script names."""
    text = ISS.read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(";"):  # Inno comment
            continue
        if m := re.match(r"(OutputDir)\s*=\s*(\.\..*)$", stripped):
            found.append((m.group(1), m.group(2).strip()))
        elif m := re.match(r'(Source):\s*"(\.\.[^"]*)"', stripped):
            found.append((m.group(1), m.group(2)))
    return found


def test_the_inno_script_names_at_least_one_relative_path() -> None:
    """Guard the guard: if the parse silently stops matching, the rest of this file proves nothing."""
    found = _iss_relative_paths()
    assert found, (
        f"parsed no `..`-relative paths out of {ISS.relative_to(ROOT)} — either the script stopped "
        "using them, or this file's regexes have drifted from its syntax. Both need a human."
    )


def test_every_relative_path_in_the_inno_script_lands_inside_the_repo() -> None:
    """Inno resolves a relative path against the .iss's OWN directory, so a move repoints them all.

    Before M133 the script sat in ``packaging/`` and said ``..\\dist``. After the move to
    ``packaging/app/`` that same string means ``packaging/dist`` — a directory that does not exist,
    reached without an error until Inno tries to read it.
    """
    for directive, raw in _iss_relative_paths():
        # Inno is Windows-only, so its separator is `\`; resolve it the way Inno would.
        resolved = (ISS.parent / Path(raw.replace("\\", "/"))).resolve()
        assert resolved.is_relative_to(ROOT), (
            f"{directive} in {ISS.relative_to(ROOT)} is {raw!r}, which resolves to {resolved} — "
            f"outside the repo at {ROOT}. Inno resolves relative paths against the script's own "
            f"directory, so this breaks whenever the script changes depth."
        )


def test_the_inno_output_directory_is_the_repo_dist() -> None:
    """`OutputDir` is where `klarpdf-setup-x64.exe` lands, and RELEASE.md collects it from `dist/`."""
    output_dirs = [raw for directive, raw in _iss_relative_paths() if directive == "OutputDir"]
    assert len(output_dirs) == 1, f"expected exactly one OutputDir, found {output_dirs}"
    resolved = (ISS.parent / Path(output_dirs[0].replace("\\", "/"))).resolve()
    assert resolved == ROOT / "dist", (
        f"OutputDir resolves to {resolved}, not {ROOT / 'dist'} — the release workflow and "
        f"RELEASE.md both collect the installer from dist/, so a mismatch ships nothing."
    )


def test_the_powershell_build_walks_up_to_the_repo_root() -> None:
    """`build.ps1` derives $Root from $PSScriptRoot by walking up; the count must match its depth.

    PowerShell does not run in CI here, so the assertion is on the *arithmetic*: one
    ``Split-Path -Parent`` per directory between the script and the repo root.
    """
    line = next(
        (ln for ln in BUILD_PS1.read_text(encoding="utf-8").splitlines() if "$Root" in ln and "PSScriptRoot" in ln),
        None,
    )
    assert line is not None, f"no `$Root = …$PSScriptRoot…` line in {BUILD_PS1.relative_to(ROOT)}"

    walks_up = line.count("Split-Path")
    depth = len(BUILD_PS1.parent.relative_to(ROOT).parts)  # packaging/app -> 2
    assert walks_up == depth, (
        f"{BUILD_PS1.relative_to(ROOT)} sits {depth} directories below the repo root but its "
        f"$Root line walks up {walks_up}: {line.strip()!r}. $Root would be "
        f"{'too shallow' if walks_up < depth else 'above the repo'}, and every path built from it "
        f"would be wrong — silently, because Push-Location succeeds either way."
    )


# --- Links that travel inside a shipped artifact (M135) ------------------------------------------
#
# Three files embed a `https://github.com/.../blob/main/<path>` URL, and each one leaves the repo
# inside something we publish: the bridge README is the wheel's `Description`, `manifest.json` is
# read by Claude Desktop, and `pyproject.toml`'s Documentation URL becomes a `Project-URL` on the
# PyPI sidebar. A repo path named in those is the same hazard this file already guards for build
# inputs — nothing fails when the file moves; the link just quietly 404s for everyone who installed
# that version, and the metadata of a published version cannot be corrected.
#
# It is not hypothetical. M134 moved all three targets under `klarpdf/`, and the manifest's URL was
# found by reading rather than by any check.
#
# `main` rather than a tag is deliberate: someone installing an older version should still reach
# current setup instructions. That choice is what makes this test necessary, not optional.

BLOB_LINK = re.compile(r"https://github\.com/utyagi24/klarpdf/blob/main/([^)\"'\s]+)")

SHIPPED_SOURCES = (
    "klarpdf/mcp_bridge/README.md",          # the wheel's long description
    "packaging/mcp/mcpb/manifest.json",      # travels inside the .mcpb
    "pyproject.toml",                        # becomes Project-URL on PyPI
)


def test_every_repo_link_in_a_shipped_artifact_points_at_a_file_that_exists():
    checked = 0
    for source in SHIPPED_SOURCES:
        text = (ROOT / source).read_text(encoding="utf-8")
        for path in BLOB_LINK.findall(text):
            checked += 1
            assert (ROOT / path).exists(), (
                f"{source} links to `{path}`, which does not exist in the repo. That URL ships "
                f"inside a published artifact, so it 404s for everyone who installed that version "
                f"and the metadata cannot be corrected afterwards. Update the link, or restore the "
                f"file at that path."
            )
    assert checked >= len(SHIPPED_SOURCES), (
        f"only found {checked} blob links across {SHIPPED_SOURCES} — if the links were removed on "
        f"purpose, drop them from SHIPPED_SOURCES; otherwise this test has stopped checking."
    )
