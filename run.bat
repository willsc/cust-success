@echo off
REM Customer Success Hub - Windows launcher (double-click this file)
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist "%VENV_PY%" (
        echo.
        echo ERROR: Could not create the virtual environment.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during setup.
        echo.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r requirements.txt
)

if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8300"

echo.
echo Customer Success Hub running at http://localhost:%PORT%
echo Press Ctrl+C to stop.
echo.

"%VENV_PY%" -m uvicorn app.main:app --host %HOST% --port %PORT%
pause
