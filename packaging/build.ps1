<#
.SYNOPSIS
  Offline, reproducible Windows build (PLAN.md, Packaging - pin -> freeze -> install).

.DESCRIPTION
  Fetches the pinned win_amd64 wheels, creates a clean build venv and installs runtime + build
  deps with --require-hashes --no-index, runs PyInstaller (onedir + onefile), compiles the Inno
  Setup installer, and writes dist/SHA256SUMS. Produces:
      dist/pdfproj-setup.exe      (installer)
      dist/pdfproj-portable.exe   (portable --onefile)
      dist/SHA256SUMS
  Self-locates the repo root; run from anywhere:  pwsh packaging/build.ps1

.PARAMETER Version
  Installer version; defaults to version.py's __version__.

.PARAMETER Offline
  Skip the wheel download and build strictly from the existing vendor/wheels (proves the
  fully-offline build). Without it, wheels are (re)fetched from the lock first.

.PARAMETER Python
  Python launcher/exe to bootstrap with (default: auto-detect `py -3.12`, else `python`).
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Offline,
    [string]$Python
)
$ErrorActionPreference = 'Stop'

function Invoke-Checked([string]$File, [string[]]$Arguments) {
    # Native tools (pip, PyInstaller, ISCC) write progress to stderr; under $ErrorActionPreference
    # = 'Stop' that would abort the script even on success. Drop to 'Continue' around the call and
    # decide success by the real exit code instead.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $File @Arguments } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "FAILED ($LASTEXITCODE): $File $($Arguments -join ' ')" }
}

function Get-IsccPath {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $found = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $found) { $found = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }
    if (-not $found) { throw "ISCC.exe (Inno Setup 6) not found. Install Inno Setup, then retry." }
    return $found
}

$Root = Split-Path -Parent $PSScriptRoot   # packaging/ -> repo root
Push-Location $Root
try {
    # Bootstrap interpreter (only used to fetch wheels + create the venv).
    if ($Python) {
        $py = $Python; $pyArgs = @()
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $py = 'py'; $pyArgs = @('-3.12')
    } else {
        $py = 'python'; $pyArgs = @()
    }

    if (-not $Version) {
        $Version = (& $py @pyArgs -c "import version; print(version.__version__)").Trim()
    }
    Write-Host "==> Building pdfproj $Version  (offline=$Offline)" -ForegroundColor Cyan

    $wheels = Join-Path $Root 'vendor\wheels'
    if (-not $Offline) {
        New-Item -ItemType Directory -Force $wheels | Out-Null
        Write-Host '==> Fetching pinned win_amd64 wheels (runtime + build)'
        Invoke-Checked $py ($pyArgs + @('-m','pip','download','-r','requirements.txt',
            '-r','requirements-build.txt','--only-binary=:all:','-d',$wheels))
    }
    if (-not (Test-Path (Join-Path $wheels '*.whl'))) {
        throw "vendor/wheels is empty - run once without -Offline to populate it."
    }

    $venv = Join-Path $Root 'build\venv'
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    Write-Host '==> Creating clean build venv'
    Invoke-Checked $py ($pyArgs + @('-m','venv',$venv))
    $vpy = Join-Path $venv 'Scripts\python.exe'

    Write-Host '==> Installing runtime + build deps (--require-hashes --no-index)'
    Invoke-Checked $vpy @('-m','pip','install','--require-hashes','--no-index','--find-links',$wheels,
        '-r','requirements.txt','-r','requirements-build.txt')

    Write-Host '==> PyInstaller freeze (onedir + onefile)'
    Invoke-Checked $vpy @('-m','PyInstaller','packaging\pdfproj.spec','--noconfirm','--clean',
        '--workpath','build\pyi','--distpath','dist')

    $iscc = Get-IsccPath
    Write-Host "==> Inno Setup installer ($iscc)"
    Invoke-Checked $iscc @("/DMyAppVersion=$Version", 'packaging\installer.iss')

    Write-Host '==> SHA256SUMS'
    $lines = foreach ($a in @('dist\pdfproj-setup.exe', 'dist\pdfproj-portable.exe')) {
        if (Test-Path $a) { "$((Get-FileHash $a -Algorithm SHA256).Hash.ToLower())  $(Split-Path $a -Leaf)" }
    }
    $lines | Set-Content -Path 'dist\SHA256SUMS' -Encoding ascii
    $lines | ForEach-Object { Write-Host "    $_" }

    Write-Host "==> Done. dist\: pdfproj-setup.exe, pdfproj-portable.exe, SHA256SUMS" -ForegroundColor Green
}
finally {
    Pop-Location
}
