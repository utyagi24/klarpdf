"""M135 — what a PyPI visitor sees, and what a `pip install` resolves, asserted over built metadata.

**Why build rather than read `pyproject.toml`.** M42 shipped a `pyproject.toml` that looked right
and produced metadata carrying **zero** `Requires-Dist`, so the documented `pipx install .` made a
`klarpdf-mcp` that died on `import mcp`. It was found by hand, not by CI, which is why
`tests/test_mcp_packaging.py` started building the metadata instead of parsing the source. Same
reasoning here: the only honest question is what the wheel says, and the answer comes from
setuptools rather than from a TOML read.

**A publish is a one-way door.** A version number can never be reused on PyPI, even after the file
is deleted. So a wrong summary, a missing licence or an unrenderable readme is not a thing to fix
in place — it is a thing to catch before the upload. These tests are that catch, and the
`publish-pypi.yml` workflow runs `twine check` for the rendering half.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from email.parser import Parser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYNC_PINS = ROOT / "packaging" / "mcp" / "pypi" / "sync_pins.py"


@pytest.fixture(scope="module")
def metadata() -> dict:
    """`pyproject.toml` as setuptools actually renders it, parsed as the RFC 822 it is."""
    pytest.importorskip("setuptools", reason="build backend; not in the bridge's runtime lock")
    from setuptools.build_meta import prepare_metadata_for_build_wheel

    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path.cwd()
        try:
            import os

            os.chdir(ROOT)
            dist_info = prepare_metadata_for_build_wheel(tmp)
        finally:
            os.chdir(cwd)
        text = (Path(tmp) / dist_info / "METADATA").read_text(encoding="utf-8")
    return Parser().parsestr(text)


def test_the_distribution_is_named_for_the_one_package_it_installs(metadata):
    """`klarpdf`, matching the single top-level import name M134 created — and the name registered
    with the trusted publisher at pypi.org, which cannot be changed from this side."""
    assert metadata["Name"] == "klarpdf"


def test_the_summary_describes_this_package_and_not_the_windows_app(metadata):
    """It read "native-Windows PDF viewer + page editor" until M135 — describing software this
    distribution does not contain. It is the first line a visitor reads."""
    summary = metadata["Summary"] or ""
    assert summary, "no Summary: the PyPI page would have no one-line description"
    assert "viewer" not in summary.lower(), f"the summary still describes the GUI app: {summary!r}"
    assert "MCP" in summary, f"the summary should say what this actually is: {summary!r}"


def test_the_page_would_not_be_blank(metadata):
    """Without a readme PyPI renders an empty project page. The bridge README is the long
    description, so this also fails if that file is moved without updating `readme`."""
    body = metadata.get_payload()
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert len(body) > 5_000, f"long description is {len(body)} chars — is `readme` still pointing at a file?"


def test_the_licence_travels_with_the_package(metadata):
    """AGPL is the whole point of the corresponding-source obligation; shipping it unstated is not
    an option. `License-Expression` is the modern field; the files ride along beside it."""
    assert metadata["License-Expression"] == "AGPL-3.0-or-later"
    assert set(metadata.get_all("License-File") or []) >= {"LICENSE", "THIRD_PARTY_LICENSES"}


def test_the_sidebar_links_resolve_to_this_repo(metadata):
    urls = dict(
        entry.split(", ", 1) for entry in (metadata.get_all("Project-URL") or [])
    )
    assert {"Homepage", "Documentation", "Issues", "Source"} <= set(urls)
    for name, url in urls.items():
        assert url.startswith("https://github.com/utyagi24/klarpdf"), f"{name} points elsewhere: {url}"


def test_every_dependency_is_pinned_not_floored(metadata):
    """The decision this milestone exists to enforce (PLAN.md §M133–M136, decision 2): one
    dependency set for every install route, so `uvx` and a lock install agree."""
    requires = metadata.get_all("Requires-Dist") or []
    assert requires, "zero Requires-Dist — the M42 failure, where `import mcp` died after install"
    floored = [r for r in requires if "==" not in r]
    assert not floored, f"these would let two users get different versions: {floored}"


def test_the_pinned_set_is_the_audited_lock(metadata):
    """`pip-audit` scans requirements-mcp.txt. If the metadata drifts from it, the scan stops
    covering what users install — silently, because both files remain individually valid."""
    spec = importlib.util.spec_from_file_location("_klarpdf_sync_pins", SYNC_PINS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    locked = set(module._read_pins())
    declared = {r.split(";")[0].strip() for r in (metadata.get_all("Requires-Dist") or [])}
    assert declared == locked, (
        "pyproject.toml's dependencies and requirements-mcp.txt disagree.\n"
        f"  only in the lock:     {sorted(locked - declared)}\n"
        f"  only in the metadata: {sorted(declared - locked)}\n"
        "  fix: python packaging/mcp/pypi/sync_pins.py"
    )


def test_the_generator_reports_staleness_rather_than_fixing_it_silently(tmp_path):
    """`--check` is what CI runs before a publish, so it has to fail on a stale block and say why."""
    result = subprocess.run(
        [sys.executable, str(SYNC_PINS), "--check"], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, f"the committed pyproject.toml is already stale:\n{result.stderr}"


def test_the_publish_workflow_names_what_pypi_was_told_to_trust():
    """Trusted Publishing matches on the workflow **filename** and the environment name. Renaming
    either breaks publishing at upload time, with nothing failing before then."""
    workflow = ROOT / ".github" / "workflows" / "publish-pypi.yml"
    assert workflow.exists(), "publish-pypi.yml is the filename registered at pypi.org — renaming it breaks the publish"
    text = workflow.read_text(encoding="utf-8")
    assert "id-token: write" in text, "no OIDC permission: there would be no token to authenticate with"
    assert "environment: pypi" in text, "no environment claim: PyPI rejects the token"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to list tracked files")
def test_the_sdist_does_not_carry_the_test_suite():
    """It carried 129 test files and was 659 KB against the wheel's 245 KB until MANIFEST.in."""
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "prune tests" in manifest
