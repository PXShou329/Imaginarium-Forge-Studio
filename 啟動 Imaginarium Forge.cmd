@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_local.ps1"
if errorlevel 1 (
  echo.
  echo Imaginarium Forge could not be started. See the message above.
  pause
)
endlocal
