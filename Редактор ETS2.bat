@echo off
rem ETS2 / ATS save editor - double-click launcher (GUI).
rem Text kept ASCII-only on purpose: cmd.exe reads .bat files as OEM, not UTF-8.
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe -m ets2se gui
    exit /b 0
)

where python.exe >nul 2>nul
if not %errorlevel%==0 (
    echo Python 3.10+ is required. Install it from https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)

python.exe -m ets2se gui
if not %errorlevel%==0 pause
