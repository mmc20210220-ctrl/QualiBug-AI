@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_LOCAL_QUALIBUG.ps1" -Mode Daemon
pause
