@echo off
rem QwenAgent start script for Windows
rem Runs harness.py with the .venv Python and puts uv's tool bin dir on
rem PATH so the browser tool can find browser-harness.
setlocal
cd /d "%~dp0"
title QwenAgent Harness

if not exist ".venv\Scripts\python.exe" (
    echo [error] .venv is missing; run install.bat first.
    exit /b 1
)

rem uv installs tool executables to %USERPROFILE%\.local\bin, which is often not on PATH.
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

echo [boot] starting harness.py
".venv\Scripts\python.exe" "%~dp0harness.py" %*