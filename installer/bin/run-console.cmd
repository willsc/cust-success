@echo off
REM Customer Success Hub - run the server in this window.
REM Use this when the Windows service isn't installed, or to see the log live.
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "RUNTIME=%~dp0..\runtime\python.exe"
set "CONFIG=%LOCALAPPDATA%\CustomerSuccessHub\server.env"
if exist "%~dp0..\service\install.conf" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0..\service\install.conf") do (
        if /i "%%A"=="DATA" set "CSHUB_DATA_DIR=%%B"
    )
)
if defined CSHUB_DATA_DIR set "CONFIG=%CSHUB_DATA_DIR%\server.env"

REM server.env is written by the installer: data folder, host and port.
if exist "%CONFIG%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%CONFIG%") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)
if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8300"
set "CSHUB_PRIVATE_RUNTIME=1"

if not exist "%RUNTIME%" (
    echo.
    echo ERROR: the bundled Python runtime is missing:
    echo   %RUNTIME%
    echo Reinstall Customer Success Hub.
    echo.
    pause
    exit /b 1
)

echo.
echo Customer Success Hub
echo   http://localhost:%PORT%
echo   data: %CSHUB_DATA_DIR%
echo   Press Ctrl+C to stop.
echo.

"%RUNTIME%" -m uvicorn app.main:app --host %HOST% --port %PORT%
echo.
echo The server stopped.
pause
