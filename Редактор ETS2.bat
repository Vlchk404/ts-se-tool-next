@echo off
rem ETS2 / ATS save editor - double-click launcher (GUI).
rem Text kept ASCII-only on purpose: cmd.exe reads .bat files as OEM, not UTF-8.
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"

rem The py.exe launcher is the reliable way to find a real Python on Windows.
rem Plain "python.exe" on Windows 10/11 is often the Microsoft Store stub,
rem which is on PATH, reports success, and only opens the Store when run.
where pyw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pyw.exe -3 -m ets2se gui
    exit /b 0
)

where py.exe >nul 2>nul
if %errorlevel%==0 (
    py.exe -3 -m ets2se gui
    if not %errorlevel%==0 pause
    exit /b 0
)

rem No py.exe: fall back to pythonw/python, skipping the Store stub.
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%p in ('where pythonw.exe') do (
        echo %%p | find /i "WindowsApps" >nul || (
            start "" "%%p" -m ets2se gui
            exit /b 0
        )
    )
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%p in ('where python.exe') do (
        echo %%p | find /i "WindowsApps" >nul || (
            "%%p" -m ets2se gui
            if not %errorlevel%==0 pause
            exit /b 0
        )
    )
)

echo.
echo Python 3.8 or newer is required, and it was not found.
echo Install it from https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" in the installer, then run this file again.
echo.
pause
exit /b 1
