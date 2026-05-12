<#
.SYNOPSIS
    aibridge installer for Windows (PowerShell).

.DESCRIPTION
    Installs `uv` (Astral) if missing, then installs aibridge as an isolated
    tool via `uv tool install`. You don't need Python pre-installed — uv can
    bootstrap its own Python runtime.

.EXAMPLE
    irm https://raw.githubusercontent.com/yudhaharsanto/aibridge/main/install.ps1 | iex

.PARAMETER Ref
    Git ref (branch/tag/sha) to install. Defaults to "main".

.PARAMETER Repo
    Override the repo URL. Defaults to the official GitHub repo.

.PARAMETER SkipSetup
    Skip the automatic `aibridge setup` step.
#>

[CmdletBinding()]
param(
    [string]$Ref = $(if ($env:AIBRIDGE_REF) { $env:AIBRIDGE_REF } else { 'main' }),
    [string]$Repo = $(if ($env:AIBRIDGE_REPO) { $env:AIBRIDGE_REPO } else { 'https://github.com/yudhaharsanto/aibridge.git' }),
    [switch]$SkipSetup = [bool]($env:AIBRIDGE_SKIP_SETUP -eq '1')
)

$ErrorActionPreference = 'Stop'

function Write-Info  ($msg) { Write-Host "==> $msg" -ForegroundColor Blue }
function Write-Ok    ($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2 ($msg) { Write-Host "!   $msg" -ForegroundColor Yellow }
function Write-Err   ($msg) { Write-Host "x   $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "aibridge installer" -ForegroundColor Blue
Write-Host ""

# ---- step 1: ensure uv ----------------------------------------------------

if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvVersion = (& uv --version) 2>$null
    Write-Ok "uv already installed ($uvVersion)"
} else {
    Write-Info "installing uv (Astral) — a fast, isolated Python tool manager..."
    # Official uv installer for Windows. Runs in current session.
    try {
        Invoke-RestMethod -UseBasicParsing 'https://astral.sh/uv/install.ps1' | Invoke-Expression
    } catch {
        Write-Err "failed to install uv: $($_.Exception.Message)"
        exit 1
    }

    # uv installs to %USERPROFILE%\.local\bin on Windows.
    $uvBin = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path $uvBin) {
        if ($env:Path -notlike "*$uvBin*") {
            $env:Path = "$uvBin;$env:Path"
        }
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Err "uv install finished but 'uv' is still not on PATH."
        Write-Err "open a new PowerShell window and re-run this installer."
        exit 1
    }
    Write-Ok "uv installed"
}

# ---- step 2: install aibridge --------------------------------------------

Write-Info "installing aibridge from ${Repo}@${Ref}..."
& uv tool install --force "git+${Repo}@${Ref}"
if ($LASTEXITCODE -ne 0) {
    Write-Err "uv tool install failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

& uv tool update-shell 2>$null | Out-Null

if (-not (Get-Command aibridge -ErrorAction SilentlyContinue)) {
    $toolBin = Join-Path $env:USERPROFILE '.local\bin'
    if (Test-Path $toolBin) {
        $env:Path = "$toolBin;$env:Path"
    }
}

if (Get-Command aibridge -ErrorAction SilentlyContinue) {
    Write-Ok "aibridge installed: $((Get-Command aibridge).Source)"
} else {
    Write-Warn2 "aibridge installed, but not yet on PATH in this shell."
    Write-Warn2 "open a new PowerShell window to pick up the updated PATH."
}

# ---- step 3: one-time setup ----------------------------------------------

if ($SkipSetup) {
    Write-Info "skipping 'aibridge setup' (AIBRIDGE_SKIP_SETUP=1)"
} elseif (Get-Command aibridge -ErrorAction SilentlyContinue) {
    Write-Info "running one-time setup (downloads Camoufox browser, ~200 MB)..."
    try {
        & aibridge setup
    } catch {
        Write-Warn2 "'aibridge setup' failed. You can re-run it manually later:"
        Write-Warn2 "    aibridge setup"
    }
}

# ---- done -----------------------------------------------------------------

Write-Host ""
Write-Host "done." -ForegroundColor Green
Write-Host ""
@"
Next steps:

  1. Log in to the provider(s) you want:
       aibridge login monica
       aibridge login perplexity

  2. Start the background daemon(s):
       aibridge start all

  3. (optional) Install autostart on login:
       aibridge install-service monica
       aibridge install-service perplexity

  4. (optional) Register with 9router:
       aibridge register-9router monica
       aibridge register-9router perplexity

See 'aibridge --help' or https://github.com/yudhaharsanto/aibridge for details.
"@
