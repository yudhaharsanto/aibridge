# aibridge installer for Windows (PowerShell)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "==> checking python..."
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python not found. install python >= 3.10 first." }

$ver = (& python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
$parts = $ver -split '\.'
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 10)) {
  throw "need python >= 3.10, got $ver"
}

Write-Host "==> creating venv (.venv)..."
& python -m venv .venv

& .\.venv\Scripts\python.exe -m pip install --upgrade pip wheel | Out-Null

Write-Host "==> installing aibridge + deps..."
& .\.venv\Scripts\pip install -e .

Write-Host "==> fetching camoufox browser (~200MB, once)..."
& .\.venv\Scripts\python -m camoufox fetch

Write-Host "==> creating config dir..."
$cfg = Join-Path $env:USERPROFILE ".aibridge"
New-Item -ItemType Directory -Force -Path (Join-Path $cfg "sessions") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $cfg "logs") | Out-Null

Write-Host ""
Write-Host "done."
Write-Host "to run: .\.venv\Scripts\aibridge.exe login monica"
Write-Host ""
Write-Host "or add this to your profile:"
Write-Host "  Set-Alias aibridge '$here\.venv\Scripts\aibridge.exe'"
