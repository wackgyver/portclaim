# Windows build; -BuildOnly never installs, pins, or starts anything.
param([switch] $BuildOnly, [string] $Python = "python")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dist = Join-Path $root "dist\PortClaim"
$install = Join-Path $env:LOCALAPPDATA "portclaim\Receiver"

if (-not $BuildOnly -and (Get-Process -Name PortClaim -ErrorAction SilentlyContinue)) {
    throw "Stop your Receiver explicitly before building/installing, or use -BuildOnly."
}
Push-Location $root
try {
    & $Python (Join-Path $PSScriptRoot "prepare_vgamepad.py") --output (Join-Path $root "build\windows-vendor")
    if ($LASTEXITCODE -ne 0) { throw "Preparing the verified Windows binding failed" }
    & $Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build\windows") (Join-Path $PSScriptRoot "PortClaim.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
    $built = Join-Path $dist "PortClaim.exe"
    if (-not (Test-Path $built)) { throw "build failed: missing PortClaim.exe" }
    if ($BuildOnly) {
        Write-Host "Built $built; no installation, shortcut, or driver changes."
        return
    }
    New-Item -ItemType Directory -Force -Path $install | Out-Null
    & robocopy $dist $install /MIR /NFL /NDL /NJH /NJS /nc /ns /np
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
    & (Join-Path $PSScriptRoot "Install-ReceiverShortcut.ps1")
    Write-Host "Receiver exe: $(Join-Path $install 'PortClaim.exe')"
} finally {
    Pop-Location
}
