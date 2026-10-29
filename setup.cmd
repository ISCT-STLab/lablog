@echo off
rem LabLog setup: runs setup.ps1 in this folder (no administrator rights needed).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
