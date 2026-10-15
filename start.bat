@echo off
rem One-click start for the panel: double-click this file, or the desktop shortcut that
rem "start.bat --shortcut" makes. The first run sets everything up (a few minutes) - see start.py.
setlocal
cd /d "%~dp0"
title Leads Pilot Engine

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" start.py %*
    exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 start.py %*
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    python start.py %*
    exit /b
)

echo.
echo  The panel needs Python 3.11 or newer: https://www.python.org/downloads/
echo  While installing it, tick "Add python.exe to PATH". Then double-click start.bat again.
echo.
pause
exit /b 1
