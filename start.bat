@echo off
rem QwenAgent start script for Windows
rem Runs harness.py with the .venv Python when one exists, otherwise the
rem python on PATH. No third-party packages are required.
setlocal
cd /d "%~dp0"
title QwenAgent Harness

if exist ".venv\Scripts\python.exe" goto havevenv

where python >nul 2>nul
if errorlevel 1 goto nopython
set "PY=python"
goto run

:havevenv
set "PY=.venv\Scripts\python.exe"

:run
echo [boot] starting harness.py
"%PY%" "%~dp0harness.py" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [error] harness.py exited with code %RC%
)
echo --- QwenAgent has exited. ---
echo.
pause
exit /b %RC%

:nopython
echo [error] python not found on PATH. Install Python 3.10+ and retry.
echo.
pause
exit /b 1
