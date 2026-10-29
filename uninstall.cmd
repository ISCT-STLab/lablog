@echo off
rem LabLog uninstall: runs uninstall.ps1 in this folder. Everything runs on one line because the
rem folder that holds this file is deleted while it runs (cmd reads batch files line by line).
cd /d "%TEMP%" & powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %* & echo. & pause & exit /b
