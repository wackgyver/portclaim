# Compatibility entry point for Windows shortcut installation.
& (Join-Path $PSScriptRoot "windows\Install-ReceiverShortcut.ps1") @args
