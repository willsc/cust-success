# Customer Success Hub - Windows launcher
# Usage:  right-click > "Run with PowerShell"   or   .\run.ps1
# If PowerShell blocks the script, run once:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Find a Python 3 launcher (the "py" launcher ships with python.org installers)
$python = $null
foreach ($candidate in @("py", "python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $python = $candidate; break }
}
if (-not $python) {
    Write-Error "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    if ($python -eq "py") { & py -3 -m venv .venv } else { & $python -m venv .venv }
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
}

$listenHost = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$port = if ($env:PORT) { $env:PORT } else { "8300" }

Write-Host ""
Write-Host "Customer Success Hub running at http://localhost:$port" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m uvicorn app.main:app --host $listenHost --port $port
