# Push usb-loom hub over SSH and run the headless installer.
# Usage:  .\deploy\Deploy-Hub.ps1
#         .\deploy\Deploy-Hub.ps1 -Target root@HUB

param(
    [string] $Target = $env:USB_LOOM_SSH_TARGET,
    [string] $Token = $env:USB_LOOM_TOKEN,
    [string] $Identity = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Target) { throw "set -Target or USB_LOOM_SSH_TARGET (no LAN default)" }
if (-not $Token) { throw "set -Token or USB_LOOM_TOKEN (no baked default)" }
if (-not $Identity) {
    $guess = Join-Path $root ".ssh\usb-loom_ed25519"
    if (Test-Path $guess) { $Identity = $guess }
}

$sshArgs = @("-o", "StrictHostKeyChecking=accept-new", "-o", "IdentitiesOnly=yes")
if ($Identity) { $sshArgs = @("-i", $Identity) + $sshArgs }

function Invoke-Remote([string] $remote) {
    & ssh @sshArgs $Target $remote
    if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $remote" }
}

Write-Host "Pushing $root -> ${Target}:/tmp/usb-loom"
Invoke-Remote "mkdir -p /tmp/usb-loom"
# Windows OpenSSH scp rejects comma-separated local files. Push directories.
& scp @sshArgs -r `
    (Join-Path $root "hub") `
    (Join-Path $root "proto") `
    (Join-Path $root "deploy") `
    (Join-Path $root "client") `
    "${Target}:/tmp/usb-loom/"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

Invoke-Remote "rm -f /tmp/usb-loom/deploy/usb-loom.env"
Invoke-Remote "sed -i 's/\r$//' /tmp/usb-loom/deploy/*.sh; sh /tmp/usb-loom/deploy/install.sh --token $Token"
Write-Host "Hub is up. Claim from any client: python client/claim.py --hub http://HUB:27180 devices"
