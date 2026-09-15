# Compatibility entry point; explicit Windows installation still installs ViGEmBus.
param([string] $Hub = $env:USB_LOOM_HUB, [string] $InstallDir = "", [string] $DestHost = $env:USB_LOOM_SELF)
& (Join-Path $PSScriptRoot "windows\Install-Receiver.ps1") @PSBoundParameters
