@echo off
REM Customer Success Hub - open the console in the default browser.
REM If the Windows service is running, this just opens the page. If it isn't,
REM the server is started in a minimised window first.
setlocal enabledelayedexpansion

set "PORT=8300"
if exist "%~dp0..\service\install.conf" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0..\service\install.conf") do (
        if /i "%%A"=="PORT" set "PORT=%%B"
    )
)

REM Is something already listening on the port?
set "RUNNING="
for /f "tokens=*" %%L in ('netstat -ano -p tcp ^| findstr /r /c:":%PORT% .*LISTENING"') do set "RUNNING=1"

if not defined RUNNING (
    sc query CustomerSuccessHub >nul 2>&1
    if not errorlevel 1 (
        echo Starting the Customer Success Hub service...
        net start CustomerSuccessHub >nul 2>&1
    )
)

for /f "tokens=*" %%L in ('netstat -ano -p tcp ^| findstr /r /c:":%PORT% .*LISTENING"') do set "RUNNING=1"

if not defined RUNNING (
    echo Starting Customer Success Hub...
    start "Customer Success Hub" /min "%~dp0run-console.cmd"
    REM Give the server a moment to bind the port before opening the browser.
    for /l %%i in (1,1,30) do (
        timeout /t 1 /nobreak >nul
        netstat -ano -p tcp | findstr /r /c:":%PORT% .*LISTENING" >nul && goto :ready
    )
)

:ready
start "" "http://localhost:%PORT%/"
