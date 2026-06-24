<#
.SYNOPSIS
  Local dependency-vulnerability audit for pdfproj - the on-demand / offline twin of the
  .github/workflows/audit.yml CI job.

.DESCRIPTION
  Runs pip-audit (PyPA's scanner; OSV + PyPI advisory DB) over the three pinned locks in a THROWAWAY
  venv created in $env:TEMP, so neither pip-audit nor its transitive deps ever touch the project's
  pinned .venv. The venv is deleted on exit.

  Needs network (queries the advisory DB) and python.org 3.12 reachable via the 'py -3.12' launcher.

  Keep the -IgnoreVuln defaults in sync with .github/workflows/audit.yml and PROGRESS.md
  "Open follow-ups". Exit code is 0 when clean (excluding accepted ignores), 1 when a vuln is found.

  NOTE: ASCII-only on purpose - Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so non-ASCII
  characters (em dashes, smart quotes) corrupt the parse. Keep this file ASCII.

.EXAMPLE
  ./tools/audit-deps.ps1
      Audit all three locks, ignoring the accepted track-only advisories.

.EXAMPLE
  ./tools/audit-deps.ps1 -IgnoreVuln @()
      Audit with NO ignores - show every known advisory, including accepted ones.
#>
[CmdletBinding()]
param(
    # Advisory IDs accepted as track-only (see PROGRESS.md). Pass @() to see everything.
    #   GHSA-jm82-fx9c-mx94 - pypdf 6.13.2 memory-DoS, fix 6.13.3 (fallback engine only).
    [string[]]$IgnoreVuln = @("GHSA-jm82-fx9c-mx94")
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Locks = "requirements.txt", "requirements-dev.txt", "requirements-build.txt"
$Venv = Join-Path $env:TEMP ("pdfproj-audit-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

Push-Location $RepoRoot
try {
    Write-Host "==> Creating throwaway audit venv (your project .venv is untouched):"
    Write-Host "    $Venv"
    py -3.12 -m venv $Venv
    $Py = Join-Path $Venv "Scripts\python.exe"
    & $Py -m pip install --quiet --disable-pip-version-check pip-audit

    $ignoreArgs = @()
    foreach ($v in $IgnoreVuln) { $ignoreArgs += @("--ignore-vuln", $v) }

    $failed = $false
    foreach ($lock in $Locks) {
        Write-Host ""
        Write-Host "==> pip-audit -r $lock"
        & $Py -m pip_audit -r $lock --no-deps --desc @ignoreArgs
        if ($LASTEXITCODE -ne 0) { $failed = $true }
    }

    Write-Host ""
    Write-Host "==> Native-binary watchlist - NOT covered by pip-audit or Dependabot; check by hand:"
    Write-Host "      Qt          (bundled via PySide6-Essentials)  https://www.qt.io/security"
    Write-Host "      MuPDF       (bundled via PyMuPDF)             PyMuPDF changelog / release notes"
    Write-Host "      Inno Setup  (build tool, see DEPENDENCIES.md) https://jrsoftware.org/files/is/whatsnew.htm"

    if ($IgnoreVuln) {
        $ignored = $IgnoreVuln -join ", "
        Write-Host ""
        Write-Host "(Ignored - accepted track-only, see PROGRESS.md: $ignored)"
    }

    Write-Host ""
    if ($failed) {
        Write-Host "RESULT: vulnerabilities found (see above)." -ForegroundColor Red
        exit 1
    }
    Write-Host "RESULT: clean (excluding accepted ignores)." -ForegroundColor Green
}
finally {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    Pop-Location
}
