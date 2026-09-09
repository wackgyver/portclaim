# Freeze the PortClaim Receiver to a windowed exe and pin it.
#   .\deploy\Build-Receiver.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$client = Join-Path $root "client"
$spec = Join-Path $client "PortClaim.spec"
$dist = Join-Path $root "dist\PortClaim"
$install = Join-Path $env:LOCALAPPDATA "portclaim\Receiver"

Set-Location $client
python -m PyInstaller --noconfirm --clean --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build") $spec
$built = Join-Path $dist "PortClaim.exe"
if (-not (Test-Path $built)) {
    throw "build failed: missing PortClaim.exe"
}

New-Item -ItemType Directory -Force -Path $install | Out-Null
cmd /c "robocopy `"$dist`" `"$install`" /MIR /NFL /NDL /NJH /NJS /nc /ns /np"
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

& (Join-Path $PSScriptRoot "Install-ReceiverShortcut.ps1")
Write-Host "Receiver exe: $(Join-Path $install 'PortClaim.exe')"
