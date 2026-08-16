@echo off
REM Customer Success Hub - Windows launcher (double-click this file)
REM First run creates .venv and installs the small base requirements; later runs
REM start straight away, reinstalling only when requirements.txt has changed.
REM Optional components (spreadsheets, SQL drivers, decks) install from the
REM app's Sources tab - nothing to do here.
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "STAMP=%~dp0.venv\.requirements-sha"

if not exist "%VENV_PY%" (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist "%VENV_PY%" (
        echo.
        echo ERROR: Could not create the virtual environment.
        echo Install Python 3.10+ from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during setup.
        echo If you copied this folder from a Mac or Linux machine, delete the
        echo .venv folder first and run this file again.
        echo.
        pause
        exit /b 1
    )
)

REM Reinstall only when requirements.txt changes.
for /f "delims=" %%H in ('"%VENV_PY%" -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())"') do set "WANT=%%H"
set "HAVE="
if exist "%STAMP%" set /p HAVE=<"%STAMP%"

if not "%WANT%"=="%HAVE%" (
    echo Installing dependencies...
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency install failed - see the messages above.
        echo.
        pause
        exit /b 1
    )
    > "%STAMP%" echo %WANT%
)

if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8300"

echo.
echo Customer Success Hub running at http://localhost:%PORT%
echo Spreadsheets, databases and decks install from the Sources tab when you need them.
echo Press Ctrl+C to stop.
echo.

"%VENV_PY%" -m uvicorn app.main:app --host %HOST% --port %PORT%
pause
