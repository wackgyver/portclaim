# Compatibility entry point. -BuildOnly opts out of installation/shortcuts.
param([switch] $BuildOnly, [string] $Python = "python")
& (Join-Path $PSScriptRoot "windows\Build-Receiver.ps1") @PSBoundParameters
