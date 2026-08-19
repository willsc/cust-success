<#
.SYNOPSIS
    Installs Customer Success Hub on Windows, end to end, without Inno Setup.

.DESCRIPTION
    Does everything Setup.exe does, from a source checkout and a single command:
    fetches a private Python runtime, installs the app and its requirements,
    writes the configuration, makes Start Menu shortcuts, optionally registers
    the Windows service, and optionally connects Claude Desktop to the MCP
    servers. Nothing needs to be installed on the machine first - not even
    Python - and nothing outside the install and data folders is touched.

    Administrator is needed for one optional step only: registering the service.
    That step elevates itself; the rest runs as you.

.EXAMPLE
    .\install.ps1
    Installs to %LOCALAPPDATA%, starts the app and opens it.

.EXAMPLE
    .\install.ps1 -Service -Network -Port 8300 -IncludeOptional
    Everything: service that starts with the machine, reachable from the
    network, with the optional components (spreadsheets, SQL, decks) preloaded.

.EXAMPLE
    .\install.ps1 -Uninstall
    Removes the service, the shortcuts and the program folder. Leaves your data.

.NOTES
    Run it from a checkout of this repository:

        powershell -ExecutionPolicy Bypass -File installer\install.ps1
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\Customer Success Hub"),
    [string]$DataDir    = (Join-Path $env:LOCALAPPDATA "CustomerSuccessHub"),
    [ValidateRange(1, 65535)]
    [int]$Port = 8300,
    [string]$PythonVersion = "3.12.10",

    # Reachable from other machines rather than this one only.
    [switch]$Network,
    # Preload pandas, SQLAlchemy, drivers and python-pptx instead of installing
    # them from the Sources tab when first needed.
    [switch]$IncludeOptional,
    # Register the Windows service so the app starts with the machine (needs admin).
    [switch]$Service,
    # Leave Claude Desktop's configuration alone.
    [switch]$SkipClaudeDesktop,
    # Let the assistant reply to and send mail, not only read it.
    [switch]$AllowMailSending,
    [switch]$NoShortcuts,
    [switch]$NoStart,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$here       = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Split-Path -Parent $here
$runtimeDir = Join-Path $InstallDir "runtime"
$serviceDir = Join-Path $InstallDir "service"
$binDir     = Join-Path $InstallDir "bin"
$cacheDir   = Join-Path $here ".build\cache"
$ListenHost = if ($Network) { "0.0.0.0" } else { "127.0.0.1" }
$AppName    = "Customer Success Hub"
$pyShort    = "python" + $PythonVersion.Split(".")[0] + $PythonVersion.Split(".")[1]

function Say  ($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Note ($m) { Write-Host "    $m" }
function Warn ($m) { Write-Host "    ! $m" -ForegroundColor Yellow }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Cached([string]$Url, [string]$Name) {
    $path = Join-Path $cacheDir $Name
    if (Test-Path $path) { Note "cached: $Name"; return $path }
    New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
    Note "downloading: $Name"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $path -UseBasicParsing
    } catch {
        throw "Could not download $Name from $Url - check the internet connection. ($($_.Exception.Message))"
    }
    return $path
}

function Get-ShortcutDirs {
    # Either of these can come back empty - a redirected profile, a service
    # account, a machine with no desktop - and Join-Path throws on an empty
    # path. Callers check before using them; shortcuts are a convenience, and
    # never worth failing an install or, worse, an uninstall over.
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $desktop -and $env:USERPROFILE) { $desktop = Join-Path $env:USERPROFILE "Desktop" }
    $appData = $env:APPDATA
    @{
        StartMenu = if ($appData) { Join-Path $appData "Microsoft\Windows\Start Menu\Programs\$AppName" } else { "" }
        Desktop   = $desktop
    }
}

function Remove-Shortcuts {
    $dirs = Get-ShortcutDirs
    if ($dirs.StartMenu -and (Test-Path $dirs.StartMenu)) {
        Remove-Item $dirs.StartMenu -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($dirs.Desktop) {
        $desktopLink = Join-Path $dirs.Desktop "$AppName.lnk"
        if (Test-Path $desktopLink) { Remove-Item $desktopLink -Force -ErrorAction SilentlyContinue }
    }
}

# ------------------------------------------------------------- uninstall ----

if ($Uninstall) {
    Say "Removing $AppName"

    $serviceScript = Join-Path $binDir "service.ps1"
    if (Get-Service -Name "CustomerSuccessHub" -ErrorAction SilentlyContinue) {
        if (Test-Path $serviceScript) {
            Note "Removing the Windows service (this needs administrator)"
            $serviceArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$serviceScript`" -Action uninstall -InstallDir `"$InstallDir`""
            if (Test-Admin) {
                Start-Process powershell -ArgumentList $serviceArgs -Wait -NoNewWindow
            } else {
                Start-Process powershell -ArgumentList $serviceArgs -Verb RunAs -Wait
            }
        } else {
            Warn "The service is registered but service.ps1 is gone - remove it with: sc.exe delete CustomerSuccessHub"
        }
    }

    $python = Join-Path $runtimeDir "python.exe"
    if ((Test-Path $python) -and -not $SkipClaudeDesktop) {
        Note "Disconnecting Claude Desktop"
        Push-Location $InstallDir
        try { & $python -m app.mcpsetup disconnect | ForEach-Object { Note $_ } } catch { Warn $_.Exception.Message }
        Pop-Location
    }

    try { Remove-Shortcuts } catch { Warn "Could not remove the shortcuts: $($_.Exception.Message)" }
    if (Test-Path $InstallDir) {
        try {
            Remove-Item $InstallDir -Recurse -Force
            Note "Removed $InstallDir"
        } catch {
            Warn "Could not remove $InstallDir - close anything using it and delete it by hand."
        }
    }
    Write-Host ""
    Say "Uninstalled. Your data folder was left alone: $DataDir"
    return
}

# --------------------------------------------------------------- install ----

Say "Installing $AppName"
Note "From    : $sourceRoot"
Note "To      : $InstallDir"
Note "Data    : $DataDir"
Note "Address : http://localhost:$Port  (listening on $ListenHost)"

foreach ($required in @("app", "mcp_servers", "requirements.txt")) {
    if (-not (Test-Path (Join-Path $sourceRoot $required))) {
        throw "This does not look like a Customer Success Hub checkout - $required is missing from $sourceRoot."
    }
}

if ([Environment]::Is64BitOperatingSystem -eq $false) {
    throw "The bundled runtime is 64-bit only. Install 64-bit Windows, or run from source with run.ps1."
}

New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir, $serviceDir | Out-Null

# ---- private Python runtime ----

$python = Join-Path $runtimeDir "python.exe"
if (Test-Path $python) {
    Say "Reusing the runtime already in $runtimeDir"
} else {
    Say "Fetching the embeddable Python runtime ($PythonVersion)"
    $zip = Get-Cached "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" `
                      "python-$PythonVersion-embed-amd64.zip"
    Expand-Archive -Path $zip -DestinationPath $runtimeDir -Force

    # The embeddable build ships an isolated path file. Enable `site` so pip
    # works, and add the install root so `import app` resolves - paths in a
    # ._pth are relative to the runtime folder, so ".." is the install directory.
    $pth = Join-Path $runtimeDir "$pyShort._pth"
    if (-not (Test-Path $pth)) {
        $found = Get-ChildItem $runtimeDir -Filter "python*._pth" | Select-Object -First 1
        if (-not $found) { throw "No python*._pth in the runtime - the download looks wrong." }
        $pth = $found.FullName
    }
    @("$pyShort.zip", ".", "Lib\site-packages", "..", "import site") |
        Set-Content -Path $pth -Encoding ASCII

    # Marker the app looks for: this interpreter is ours alone, so the
    # Components panel doesn't warn about installing into a shared Python.
    Set-Content -Path (Join-Path $runtimeDir ".private-runtime") -Value $AppName -Encoding ASCII

    Say "Installing pip"
    $getPip = Get-Cached "https://bootstrap.pypa.io/get-pip.py" "get-pip.py"
    & $python $getPip --no-warn-script-location --no-cache-dir
    if ($LASTEXITCODE -ne 0) { throw "get-pip failed (exit $LASTEXITCODE)" }
}

Say "Installing the application's requirements"
& $python -m pip install --no-warn-script-location --no-cache-dir -r (Join-Path $sourceRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit $LASTEXITCODE)" }

if ($IncludeOptional) {
    Say "Installing the optional components as well"
    & $python -m pip install --no-warn-script-location --no-cache-dir -r (Join-Path $sourceRoot "requirements-optional.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install (optional) failed (exit $LASTEXITCODE)" }
}

# ---- the app itself ----

Say "Copying the application"
foreach ($dir in @("app", "mcp_servers")) {
    $target = Join-Path $InstallDir $dir
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Copy-Item (Join-Path $sourceRoot $dir) $target -Recurse -Force
    Get-ChildItem $target -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
foreach ($file in @("requirements.txt", "requirements-optional.txt", "README.md", ".env.example")) {
    $from = Join-Path $sourceRoot $file
    if (Test-Path $from) { Copy-Item $from (Join-Path $InstallDir $file) -Force }
}
Copy-Item (Join-Path $here "bin") $binDir -Recurse -Force
Copy-Item (Join-Path $here "service\CustomerSuccessHubService.xml.template") `
          (Join-Path $serviceDir "CustomerSuccessHubService.xml.template") -Force

# ---- configuration the launchers and the service read ----

Say "Writing the configuration"
@(
    "# Written by install.ps1. Edit and restart to change."
    "CSHUB_DATA_DIR=$DataDir"
    "CSHUB_PRIVATE_RUNTIME=1"
    "HOST=$ListenHost"
    "PORT=$Port"
) | Set-Content -Path (Join-Path $DataDir "server.env") -Encoding ASCII
Set-Content -Path (Join-Path $serviceDir "install.conf") -Value "PORT=$Port`nDATA=$DataDir" -Encoding ASCII
Note "Settings: $(Join-Path $DataDir 'server.env')"

# ---- shortcuts ----

if (-not $NoShortcuts) {
    Say "Creating shortcuts"
    # Shortcuts are a convenience. Nothing here may fail the install: the app is
    # already usable by this point, and Windows Script Host is disabled outright
    # on plenty of managed machines.
    $dirs  = Get-ShortcutDirs
    $shell = $null
    if (-not $dirs.StartMenu) {
        Warn "This profile has no Start Menu folder - skipping shortcuts."
    } else {
        try {
            New-Item -ItemType Directory -Force -Path $dirs.StartMenu | Out-Null
            $shell = New-Object -ComObject WScript.Shell
        } catch {
            Warn "Shortcuts skipped - Windows Script Host is unavailable: $($_.Exception.Message)"
        }
    }

    if ($shell) {
        $shortcuts = @(
            @{ Path = Join-Path $dirs.StartMenu "$AppName.lnk";           Target = Join-Path $binDir "open.cmd";        Desc = "Open the console in your browser" }
            @{ Path = Join-Path $dirs.StartMenu "Run in this window.lnk"; Target = Join-Path $binDir "run-console.cmd"; Desc = "Run the server in a console window" }
            @{ Path = Join-Path $dirs.StartMenu "Data folder.lnk";        Target = $DataDir;                            Desc = "Databases, uploads and ticket exports" }
        )
        if ($dirs.Desktop) {
            $shortcuts += @{ Path = Join-Path $dirs.Desktop "$AppName.lnk"; Target = Join-Path $binDir "open.cmd"; Desc = "Open the console in your browser" }
        }
        foreach ($sc in $shortcuts) {
            try {
                $link = $shell.CreateShortcut($sc.Path)
                $link.TargetPath       = $sc.Target
                $link.WorkingDirectory = $InstallDir
                $link.Description      = $sc.Desc
                if ($sc.Target -like "*.cmd") { $link.IconLocation = $python }
                $link.Save()
            } catch {
                Warn "Could not create $(Split-Path $sc.Path -Leaf): $($_.Exception.Message)"
            }
        }
        Note "Start Menu: $($dirs.StartMenu)"
    }
}

# ---- optional Windows service ----

if ($Service) {
    Say "Registering the Windows service"
    $winsw = Get-Cached "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe" "WinSW.NET461.exe"
    Copy-Item $winsw (Join-Path $serviceDir "CustomerSuccessHubService.exe") -Force

    $serviceScript = Join-Path $binDir "service.ps1"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$serviceScript`" -Action install " +
                 "-InstallDir `"$InstallDir`" -DataDir `"$DataDir`" -Port $Port -ListenHost $ListenHost"
    try {
        if (Test-Admin) {
            Start-Process powershell -ArgumentList $arguments -Wait -NoNewWindow
        } else {
            Note "This step needs administrator - approve the prompt."
            Start-Process powershell -ArgumentList $arguments -Verb RunAs -Wait
        }
    } catch {
        Warn "The service was not registered: $($_.Exception.Message)"
        Warn "Everything else is installed; the shortcuts still start the app."
    }

    if ($Network) {
        $fwArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$serviceScript`" -Action firewall -InstallDir `"$InstallDir`" -Port $Port"
        try {
            if (Test-Admin) { Start-Process powershell -ArgumentList $fwArgs -Wait -NoNewWindow }
            else { Start-Process powershell -ArgumentList $fwArgs -Verb RunAs -Wait }
        } catch {
            Warn "Could not add the firewall rule - colleagues may not be able to reach port $Port."
        }
    }
}

# ---- connect Claude Desktop to the MCP servers ----

if (-not $SkipClaudeDesktop) {
    Say "Connecting Claude Desktop to the MCP servers"
    $connectArgs = @("-m", "app.mcpsetup", "connect")
    if ($AllowMailSending) { $connectArgs += "--allow-writes" }
    Push-Location $InstallDir
    try {
        $env:CSHUB_DATA_DIR = $DataDir
        $output = & $python @connectArgs 2>&1
        if ($LASTEXITCODE -eq 0) {
            $output | ForEach-Object { Note $_ }
        } else {
            # Not an install failure: Claude Desktop simply may not be here.
            Note "Skipped - $output"
            Note "The Sources tab has a Connect button for when it is installed."
        }
    } catch {
        Warn "Could not update Claude Desktop's configuration: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }
}

# ---- done ----

Write-Host ""
Say "$AppName is installed."
Note "Open it     : http://localhost:$Port"
Note "Program     : $InstallDir"
Note "Data        : $DataDir"
if ($Service) { Note "Service     : starts with Windows (services.msc -> $AppName)" }
Note "Uninstall   : powershell -ExecutionPolicy Bypass -File `"$here\install.ps1`" -Uninstall"
Write-Host ""

if (-not $NoStart) {
    Say "Starting it"
    Start-Process (Join-Path $binDir "open.cmd")
}
