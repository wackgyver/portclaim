# Install the Receiver: client files, ViGEmBus, and a connect shortcut.
# After this, no extra Apple / Magic Utilities / usbip-win2 stack is required
# for the genesis trackpad. The stick still uses SidestickBridge + ViGEmBus.
#
#   .\deploy\Install-Receiver.ps1
#   .\deploy\Install-Receiver.ps1 -Hub http://HUB:27180 -DestHost DEST

param(
    [string] $Hub = $env:USB_LOOM_HUB,
    [string] $InstallDir = "",
    [string] $DestHost = $env:USB_LOOM_SELF
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "portclaim"
}

function Test-ViGEmBus {
    $svc = Get-Service -Name "ViGEmBus" -ErrorAction SilentlyContinue
    if ($svc) { return $true }
    return [bool](Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        $_.FriendlyName -match "ViGEm" -or $_.InstanceId -match "ViGEmBus"
    })
}

function Install-ViGEmBus {
    if (Test-ViGEmBus) {
        Write-Host "ViGEmBus already installed"
        return
    }
    $url = "https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe"
    $exe = Join-Path $env:TEMP "ViGEmBus_1.22.0.exe"
    Write-Host "Downloading ViGEmBus (needed by SidestickBridge for the T.A320)"
    Invoke-WebRequest -Uri $url -OutFile $exe
    $proc = Start-Process -FilePath $exe -ArgumentList "/qn", "/norestart" -Wait -PassThru
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        throw "ViGEmBus installer failed ($($proc.ExitCode)). Re-run this script from an elevated PowerShell."
    }
    Write-Host "ViGEmBus installed"
}

Write-Host "Installing PortClaim receiver -> $InstallDir"
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "client") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "proto") | Out-Null
Copy-Item (Join-Path $root "client\*.py") (Join-Path $InstallDir "client") -Force
Copy-Item (Join-Path $root "proto\*.py") (Join-Path $InstallDir "proto") -Force

Install-ViGEmBus

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command py -ErrorAction SilentlyContinue }
if (-not $pythonCmd) { throw "python not on PATH — install Python 3 before the receiver shortcut will run" }
$python = $pythonCmd.Source

$claim = Join-Path $InstallDir "client\claim.py"
$frozen = Join-Path $InstallDir "Receiver\PortClaim.exe"
$args = "`"$claim`" --hub $Hub connect --dest-host $DestHost"
$wsh = New-Object -ComObject WScript.Shell

function Write-ConnectShortcut([string] $path) {
    $lnk = $wsh.CreateShortcut($path)
    if (Test-Path $frozen) {
        $lnk.TargetPath = $frozen
        $lnk.Arguments = ""
        $lnk.WorkingDirectory = Split-Path $frozen
    } else {
        $lnk.TargetPath = $python
        $lnk.Arguments = $args
        $lnk.WorkingDirectory = Join-Path $InstallDir "client"
    }
    $lnk.Description = "PortClaim mapping surface"
    $lnk.Save()
    # Mark the shortcut "Run as administrator" so SendInput reaches games.
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ($bytes.Length -gt 0x15) {
        $bytes[0x15] = $bytes[0x15] -bor 0x20
        [System.IO.File]::WriteAllBytes($path, $bytes)
    }
}

$desktop = [Environment]::GetFolderPath("Desktop")
$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\PortClaim"
New-Item -ItemType Directory -Force -Path $startDir | Out-Null
Write-ConnectShortcut (Join-Path $desktop "PortClaim.lnk")
Write-ConnectShortcut (Join-Path $startDir "PortClaim.lnk")
$legacyConnect = Join-Path $desktop "usb-loom connect.lnk"
if (Test-Path $legacyConnect) {
    Write-ConnectShortcut $legacyConnect
}

Write-Host @"
Receiver ready.

  Shortcut: Desktop / Start Menu -> PortClaim  (opens the Receiver, elevated)
  Or:      python `"$claim`" --hub `$env:USB_LOOM_HUB connect

The Receiver window is the mapping surface. SidestickBridge stays the T.A320 / ViGEm map
(set USB_LOOM_SIDESTICK; site path lives in local ONBOARDING.md).
"@
