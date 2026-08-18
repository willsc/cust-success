<#
.SYNOPSIS
    Builds the Windows installer for Customer Success Hub.

.DESCRIPTION
    Stages a self-contained payload — a private CPython runtime with the app's
    packages already installed, the app itself, and the service wrapper — then
    compiles it into a single Setup.exe with Inno Setup.

    Nothing is required on the target machine: no Python, no admin rights
    (except for the optional service), no network.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -IncludeOptional -Version 1.1.0

.NOTES
    Needs Inno Setup 6 (https://jrsoftware.org/isdl.php) and an internet
    connection for the first build; downloads are cached in .build\cache.
#>
[CmdletBinding()]
param(
    [string]$Version = "1.0.0",
    [string]$PythonVersion = "3.12.10",
    # Ship the optional components (pandas, SQLAlchemy, drivers, python-pptx)
    # inside the installer so a machine with no internet still gets everything.
    [switch]$IncludeOptional,
    [string]$OutDir = "dist",
    [string]$ISCC
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$root    = Split-Path -Parent $here
$build   = Join-Path $here ".build"
$cache   = Join-Path $build "cache"
$payload = Join-Path $build "payload"
$runtime = Join-Path $payload "runtime"

$pyShort = "python" + $PythonVersion.Split(".")[0] + $PythonVersion.Split(".")[1]   # e.g. python312

function Say($message) { Write-Host "==> $message" -ForegroundColor Cyan }

function Get-Cached([string]$Url, [string]$Name) {
    $path = Join-Path $cache $Name
    if (Test-Path $path) { Write-Host "    cached: $Name"; return $path }
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Write-Host "    downloading: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $path -UseBasicParsing
    return $path
}

function Find-ISCC {
    if ($ISCC) { return $ISCC }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php or pass -ISCC <path to ISCC.exe>."
}

# ---------------------------------------------------------------- payload ----

Say "Cleaning $build"
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload, $runtime, $cache | Out-Null

# A mis-bound parameter used to land the app version here and 404 on a Python
# release that never existed. Say so plainly instead.
if ($PythonVersion -notmatch '^3\.\d+\.\d+$') {
    throw "-PythonVersion must look like 3.12.10, got '$PythonVersion'. (Splat named arguments with a hashtable: .\build.ps1 @{ Version = '1.0.0' })"
}

Say "Fetching the embeddable Python runtime ($PythonVersion)"
$embedZip = Get-Cached "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" `
                       "python-$PythonVersion-embed-amd64.zip"
Expand-Archive -Path $embedZip -DestinationPath $runtime -Force

# The embeddable distribution ships an isolated path file. Enable `site` so pip
# works, and add the install root so `import app` resolves. Paths in a ._pth are
# relative to the runtime folder, so ".." is the install directory.
$pth = Join-Path $runtime "$pyShort._pth"
if (-not (Test-Path $pth)) { $pth = (Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1).FullName }
Say "Rewriting $(Split-Path $pth -Leaf) so pip and the app are importable"
@(
    "$pyShort.zip"
    "."
    "Lib\site-packages"
    ".."
    "import site"
) | Set-Content -Path $pth -Encoding ASCII

# Marker the app looks for: "this interpreter belongs to us alone", so the
# Components panel doesn't warn about installing into a shared Python.
Set-Content -Path (Join-Path $runtime ".private-runtime") -Value "Customer Success Hub" -Encoding ASCII

Say "Installing pip into the private runtime"
$getPip = Get-Cached "https://bootstrap.pypa.io/get-pip.py" "get-pip.py"
& (Join-Path $runtime "python.exe") $getPip --no-warn-script-location --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }

Say "Installing the base requirements"
& (Join-Path $runtime "python.exe") -m pip install --no-warn-script-location --no-cache-dir `
    -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if ($IncludeOptional) {
    Say "Installing the optional components too (-IncludeOptional)"
    & (Join-Path $runtime "python.exe") -m pip install --no-warn-script-location --no-cache-dir `
        -r (Join-Path $root "requirements-optional.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install (optional) failed" }
}

Say "Fetching the service wrapper (WinSW)"
$winsw = Get-Cached "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe" "WinSW.NET461.exe"
New-Item -ItemType Directory -Force -Path (Join-Path $payload "service") | Out-Null
Copy-Item $winsw (Join-Path $payload "service\CustomerSuccessHubService.exe") -Force

Say "Staging the application"
Copy-Item (Join-Path $root "app") (Join-Path $payload "app") -Recurse -Force
Get-ChildItem (Join-Path $payload "app") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
foreach ($file in @("requirements.txt", "requirements-optional.txt", "README.md", ".env.example")) {
    Copy-Item (Join-Path $root $file) (Join-Path $payload $file) -Force
}
Copy-Item (Join-Path $here "bin") (Join-Path $payload "bin") -Recurse -Force
Copy-Item (Join-Path $here "service\CustomerSuccessHubService.xml.template") `
          (Join-Path $payload "service\CustomerSuccessHubService.xml.template") -Force

$size = [math]::Round(((Get-ChildItem $payload -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Say "Payload staged: $size MB"

# --------------------------------------------------------------- compile ----

$iscc = Find-ISCC
Say "Compiling with $iscc"
New-Item -ItemType Directory -Force -Path (Join-Path $here $OutDir) | Out-Null
& $iscc "/DAppVersion=$Version" "/DPayloadDir=$payload" "/DOutputDir=$(Join-Path $here $OutDir)" `
        (Join-Path $here "CustomerSuccessHub.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

$setup = Get-ChildItem (Join-Path $here $OutDir) -Filter "*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Say "Done: $($setup.FullName) ($([math]::Round($setup.Length / 1MB, 1)) MB)"
