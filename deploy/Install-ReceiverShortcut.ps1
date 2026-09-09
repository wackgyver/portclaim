# Pin-able PortClaim shortcut. Prefers the frozen exe, not python.exe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exeCandidates = @(
    (Join-Path $env:LOCALAPPDATA "portclaim\Receiver\PortClaim.exe"),
    (Join-Path $root "dist\PortClaim\PortClaim.exe")
)
$exe = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) {
    throw "PortClaim.exe not built. Run deploy\Build-Receiver.ps1 first."
}

$names = @(
    (Join-Path $env:USERPROFILE "Desktop\PortClaim.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\PortClaim\PortClaim.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\PortClaim.lnk")
)
$legacy = @(
    (Join-Path $env:USERPROFILE "Desktop\usb-loom Receiver.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\usb-loom Receiver.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\usb-loom Receiver.lnk"),
    (Join-Path $env:USERPROFILE "Desktop\usb-loom connect.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\usb-loom\usb-loom connect.lnk")
)

$shell = New-Object -ComObject WScript.Shell
function Write-ExeShortcut([string] $path) {
    $dir = Split-Path $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $exe
    $lnk.Arguments = ""
    $lnk.WorkingDirectory = Split-Path $exe
    $lnk.WindowStyle = 1
    $lnk.Description = "PortClaim mapping surface"
    $lnk.IconLocation = "$exe,0"
    $lnk.Save()
    Write-Host "wrote $path -> $exe"
}

foreach ($path in $names) {
    Write-ExeShortcut $path
}
foreach ($path in $legacy) {
    if (Test-Path $path) {
        Write-ExeShortcut $path
    }
}
