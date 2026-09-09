# Install VB-Audio Virtual Cable (donationware, www.vb-cable.com).
# Run elevated. A reboot may be required to finish.
$ErrorActionPreference = "Stop"
$zip = Join-Path $env:TEMP "VBCABLE_Driver_Pack.zip"
$out = Join-Path $env:TEMP "VBCABLE_Driver_Pack"
$setup = Join-Path $out "VBCABLE_Setup_x64.exe"
if (-not (Test-Path $setup)) {
    Invoke-WebRequest -Uri "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip" -OutFile $zip -UseBasicParsing
    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $out -Force
}
Start-Process -FilePath $setup -Verb RunAs -Wait
Write-Host "If Windows asked to reboot, do that, then pick CABLE Output in Handy."
