<#
.SYNOPSIS
    Install, remove and control the Customer Success Hub Windows service.

.DESCRIPTION
    Called by the installer with administrator rights, and usable by hand later:

        powershell -ExecutionPolicy Bypass -File service.ps1 -Action status
        powershell -ExecutionPolicy Bypass -File service.ps1 -Action restart

    The service itself is WinSW wrapping the bundled Python — it appears in
    services.msc as "Customer Success Hub", starts with the machine, restarts on
    failure, and writes rolling logs next to the executable.
#>
[CmdletBinding()]
param(
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status", "firewall")]
    [string]$Action = "status",
    [string]$InstallDir,
    [string]$DataDir,
    [int]$Port = 8300,
    [string]$ListenHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

# Captured here on purpose: inside a function, $PSBoundParameters describes that
# function's own parameters, not the script's.
$PortWasGiven = $PSBoundParameters.ContainsKey("Port")

if (-not $InstallDir) { $InstallDir = Split-Path -Parent $PSScriptRoot }
$serviceDir = Join-Path $InstallDir "service"
$winsw      = Join-Path $serviceDir "CustomerSuccessHubService.exe"
$xml        = Join-Path $serviceDir "CustomerSuccessHubService.xml"
$template   = Join-Path $serviceDir "CustomerSuccessHubService.xml.template"
$conf       = Join-Path $serviceDir "install.conf"
$ServiceId  = "CustomerSuccessHub"
$RuleName   = "Customer Success Hub"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-Conf {
    # Fall back to what the installer recorded when parameters aren't passed.
    if (-not (Test-Path $conf)) { return }
    foreach ($line in Get-Content $conf) {
        if (-not $line -or $line -notmatch "=") { continue }
        $key, $value = $line -split "=", 2
        $key = $key.Trim(); $value = $value.Trim()
        if ($key -eq "PORT" -and -not $PortWasGiven -and $value -match '^\d+$') { $script:Port = [int]$value }
        if ($key -eq "DATA" -and -not $DataDir) { $script:DataDir = $value }
    }
}

function Resolve-Python {
    # The installer lays down a private runtime; a from-source install on
    # Windows has run.ps1's virtual environment instead. Either can back the
    # service, so take whichever is actually there rather than assuming.
    $candidates = @(
        (Join-Path $InstallDir "runtime\python.exe"),
        (Join-Path $InstallDir ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw ("No Python found under $InstallDir - looked for runtime\python.exe and " +
           ".venv\Scripts\python.exe. Install with installer\install.ps1, or run " +
           "run.ps1 once from source to create the virtual environment.")
}

function Write-ServiceXml {
    if (-not (Test-Path $template)) { throw "Missing $template - reinstall the app." }
    $python = Resolve-Python
    Write-Host "Using interpreter: $python"
    $content = (Get-Content $template -Raw).
        Replace("@PYTHON@", $python).
        Replace("@INSTALLDIR@", $InstallDir).
        Replace("@DATADIR@", $DataDir).
        Replace("@HOST@", $ListenHost).
        Replace("@PORT@", "$Port")
    Set-Content -Path $xml -Value $content -Encoding UTF8
}

function Get-Service-Safe { Get-Service -Name $ServiceId -ErrorAction SilentlyContinue }

switch ($Action) {

    "install" {
        if (-not (Test-Admin)) { throw "Installing the service needs an elevated PowerShell." }
        Read-Conf
        if (-not $DataDir) { $DataDir = Join-Path $env:LOCALAPPDATA "CustomerSuccessHub" }
        New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

        if (Get-Service-Safe) {
            Write-Host "Service already registered — reinstalling it."
            & $winsw stop $xml 2>$null | Out-Null
            & $winsw uninstall $xml 2>$null | Out-Null
            Start-Sleep -Seconds 2
        }

        Write-ServiceXml
        Set-Content -Path $conf -Value "PORT=$Port`nDATA=$DataDir" -Encoding ASCII

        & $winsw install $xml
        if ($LASTEXITCODE -ne 0) { throw "WinSW install failed (exit $LASTEXITCODE)" }
        & $winsw start $xml
        if ($LASTEXITCODE -ne 0) { throw "The service was registered but would not start. See $serviceDir\*.log" }

        Write-Host "Customer Success Hub is running as a service on http://localhost:$Port"
    }

    "uninstall" {
        if (-not (Get-Service-Safe)) { Write-Host "No service registered — nothing to remove."; break }
        if (-not (Test-Admin)) { throw "Removing the service needs an elevated PowerShell." }
        if (Test-Path $xml) {
            & $winsw stop $xml 2>$null | Out-Null
            & $winsw uninstall $xml 2>$null | Out-Null
        } else {
            sc.exe stop $ServiceId | Out-Null
            sc.exe delete $ServiceId | Out-Null
        }
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        Write-Host "Service removed. Your data folder was left alone."
    }

    "firewall" {
        if (-not (Test-Admin)) { throw "Adding a firewall rule needs an elevated PowerShell." }
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -Profile Domain,Private | Out-Null
        Write-Host "Firewall rule added for TCP $Port on domain and private networks."
    }

    "start"   { & $winsw start $xml }
    "stop"    { & $winsw stop $xml }
    "restart" { & $winsw restart $xml }

    "status" {
        $service = Get-Service-Safe
        Read-Conf
        if ($service) {
            Write-Host "Service : $($service.Status)  (startup: $((Get-CimInstance Win32_Service -Filter "Name='$ServiceId'").StartMode))"
        } else {
            Write-Host "Service : not installed"
        }
        Write-Host "URL     : http://localhost:$Port"
        Write-Host "Data    : $DataDir"
        Write-Host "Logs    : $serviceDir"
    }
}
