# Customer Success Hub - Windows launcher
# Usage:  right-click > "Run with PowerShell"   or   .\run.ps1
# If PowerShell blocks the script, run once:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# First run creates .venv and installs the (small) base requirements; later runs
# start straight away, reinstalling only when requirements.txt has changed.
# Optional components - spreadsheets, SQL drivers, decks - are installed from
# the app's Sources tab, not here.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$stamp      = Join-Path $PSScriptRoot ".venv\.requirements-sha"
$reqs       = Join-Path $PSScriptRoot "requirements.txt"

if (-not (Test-Path $venvPython)) {
    # Find a Python 3.10+ launcher (the "py" launcher ships with python.org installers)
    $python = $null
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $verArgs = if ($candidate -eq "py") { @("-3", "-c") } else { @("-c") }
        & $candidate @verArgs "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
    if (-not $python) {
        Write-Error "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
    }

    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    if ($python -eq "py") { & py -3 -m venv .venv } else { & $python -m venv .venv }
    if (-not (Test-Path $venvPython)) {
        Write-Error "Could not create the virtual environment. If this folder was copied from a Mac or Linux machine, delete the .venv folder and try again."
    }
}

# Reinstall only when requirements.txt changes.
$want = (Get-FileHash -Path $reqs -Algorithm SHA256).Hash
$have = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if ($want -ne $have) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Error "Dependency install failed - see the messages above." }
    Set-Content -Path $stamp -Value $want
}

$listenHost = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$port = if ($env:PORT) { $env:PORT } else { "8300" }

Write-Host ""
Write-Host "Customer Success Hub running at http://localhost:$port" -ForegroundColor Green
Write-Host "Optional components (spreadsheets, databases, decks) install from the Sources tab." -ForegroundColor DarkGray
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m uvicorn app.main:app --host $listenHost --port $port
