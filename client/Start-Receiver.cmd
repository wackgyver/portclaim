@echo off
set "EXE=%LOCALAPPDATA%\portclaim\Receiver\PortClaim.exe"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)
echo Frozen Receiver not installed. Run deploy\Build-Receiver.ps1
exit /b 1
