# Compatibility entry point. Explicit elevated driver installation, never CI.
& (Join-Path $PSScriptRoot "windows\Install-VBCABLE.ps1") @args
