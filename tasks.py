"""Task runner for KlarPDF - ``invoke <task>`` ties the build steps into one interface.

Thin orchestration only: each task shells out to the existing authoritative scripts
(``packaging/build.ps1``, ``pip-compile``, ``vendor/gen-sources.py``, ``tools/audit-deps.ps1``,
``gh``), so OS-specific logic stays quarantined there (the same discipline as
``platform_integration.py`` / ``packaging/``). The step *semantics* and the *why* live in
``RELEASE.md``; this file just makes those documented steps single, discoverable commands.

    invoke --list                      # every task + its one-line description
    invoke test                        # headless suite
    invoke audit                       # scan the locks for known advisories
    invoke lock --package pypdf==6.13.3 # recompile the locks (Windows)
    invoke vendor                      # re-fetch wheels + regenerate vendor/wheels-sources.md (Windows)
    invoke build                       # freeze + installer + portable (Windows)
    invoke tag --version 0.9.5         # pre-flight test+audit, then tag + push -> CI draft Release
    invoke publish --version 0.9.5     # flip the CI-built draft public

Every shelled command is echoed before it runs (``echo=True``) - so for any step you can see
exactly what executes under the hood (or add ``invoke --echo`` globally).

Windows-only tasks (lock / vendor / build) fail fast off Windows: PyInstaller, Inno Setup, and the
``win_amd64`` hashed locks can't be produced elsewhere.
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

from invoke import task

ROOT = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"
_BIN = ROOT / ".venv" / ("Scripts" if IS_WIN else "bin")
_VENV_PY = _BIN / ("python.exe" if IS_WIN else "python")
# Prefer the project .venv; otherwise fall back to the interpreter running invoke - so `invoke test`
# / `audit` work in WSL even if the dev venv lives elsewhere, as long as invoke is run from it (the
# intended dev-dependency model). On Windows invoke is run from .venv, so behaviour is unchanged.
PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
PIP_COMPILE = str(_BIN / ("pip-compile.exe" if IS_WIN else "pip-compile"))
# PowerShell 7 (pwsh, matches release.yml / CI) if present, else Windows PowerShell 5.1.
POWERSHELL = shutil.which("pwsh") or "powershell"


def _windows_only(name: str) -> None:
    if not IS_WIN:
        sys.exit(f"`invoke {name}` is Windows-only (PyInstaller / Inno Setup / win_amd64 hashes).")


def _ps(script: str, args: str = "") -> str:
    return f"{POWERSHELL} -NoProfile -ExecutionPolicy Bypass -File {script} {args}".strip()


@task
def test(c):
    """Run the headless test suite (pytest, offscreen)."""
    c.run(f'"{PY}" -m pytest', echo=True)


@task
def audit(c):
    """Scan the dependency locks for known advisories.

    pip-audit is installed into a THROWAWAY venv (never the project .venv) so the scanner's own
    dependency tree can't pollute the dev env or the audited locks - matching the CI job
    (`pipx install pip-audit`) and the Windows script (`tools/audit-deps.ps1`). Needs network.
    """
    if IS_WIN:
        c.run(_ps("tools/audit-deps.ps1"), echo=True)
        return
    # Linux/WSL twin of tools/audit-deps.ps1: self-provision pip-audit in a throwaway venv.
    import tempfile

    venv = Path(tempfile.mkdtemp(prefix="klarpdf-audit-"))
    try:
        c.run(f'"{sys.executable}" -m venv "{venv}"', echo=True)
        vpy = venv / "bin" / "python"
        c.run(f'"{vpy}" -m pip install --quiet --disable-pip-version-check pip-audit', echo=True)
        for lock in ("requirements-win.txt", "requirements-dev.txt", "requirements-build-win.txt"):
            c.run(f'"{vpy}" -m pip_audit -r {lock} --no-deps --desc', echo=True, warn=True)
    finally:
        shutil.rmtree(venv, ignore_errors=True)


@task(help={"package": "re-pin only this package, e.g. pypdf==6.13.3 (optional)"})
def lock(c, package=None):
    """Recompile the requirement locks from the *.in files (Windows - win_amd64 hashes)."""
    _windows_only("lock")
    up = f" --upgrade-package {package}" if package else ""
    c.run(f'"{PIP_COMPILE}" --generate-hashes{up} -o requirements-win.txt requirements.in', echo=True)
    c.run(f'"{PIP_COMPILE}"{up} -o requirements-dev.txt requirements-dev.in', echo=True)
    c.run(
        f'"{PIP_COMPILE}" --generate-hashes --allow-unsafe{up} '
        "-o requirements-build-win.txt requirements-build.in",
        echo=True,
    )
    print("Locks recompiled - next: `invoke vendor` to refresh wheels + the sources record.")


@task
def vendor(c):
    """Re-fetch win_amd64 wheels + regenerate vendor/wheels-sources.md (Windows)."""
    _windows_only("vendor")
    c.run(f'"{PY}" -m pip download -r requirements-win.txt --only-binary=:all: -d vendor/wheels', echo=True)
    c.run(
        f'"{PY}" -m pip install -r requirements-win.txt --require-hashes --ignore-installed '
        "--dry-run --report report.json",
        echo=True,
    )
    c.run(f'"{PY}" vendor/gen-sources.py', echo=True)


@task(help={"version": "installer version (defaults to version.py)"})
def build(c, version=None):
    """Freeze (onedir + onefile) + Inno Setup installer (Windows)."""
    _windows_only("build")
    c.run(_ps("packaging/build.ps1", f"-Version {version}" if version else ""), echo=True)


@task(pre=[test, audit], help={"version": "must equal version.py, e.g. 0.9.5"})
def tag(c, version):
    """Pre-flight (test + audit), then annotated tag + push - triggers the CI draft Release."""
    branch = c.run("git rev-parse --abbrev-ref HEAD", hide=True).stdout.strip()
    if branch != "main":
        sys.exit(f"on '{branch}', not main - tag releases from an up-to-date main (RELEASE.md sec 3).")
    import runpy

    actual = runpy.run_path(str(ROOT / "version.py"))["__version__"]
    if actual != version:
        sys.exit(f"version.py is {actual}, not {version} - bump it (or fix --version) first.")
    c.run(f"git tag -a v{version} -m v{version}", echo=True)
    c.run(f"git push origin v{version}", echo=True)
    print(f"Pushed v{version} - CI builds the draft; then `invoke publish --version {version}`.")


@task(help={"version": "the tag to publish, e.g. 0.9.5"})
def publish(c, version):
    """Flip the CI-built draft Release public (gh release edit --draft=false)."""
    c.run(f"gh release edit v{version} --draft=false", echo=True)


@task
def clean(c):
    """Remove local build artifacts (build/, dist/, report.json)."""
    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
        print(f"removed {d}/")
    (ROOT / "report.json").unlink(missing_ok=True)
    print("removed report.json")
