@echo off
rem QwenAgent installer for Windows
rem The harness uses only the Python standard library, so this just checks
rem Python and creates the runtime directories. The optional browser tool
rem uses browser-harness if it is already on PATH; nothing is installed.
setlocal
cd /d "%~dp0"

echo === QwenAgent installer (Windows) ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [error] python not found on PATH. Install Python 3.10+ from
    echo         https://www.python.org/downloads/ and make sure
    echo         "Add Python to PATH" is checked during installation.
    goto fail
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [error] Python 3.10 or newer is required.
    goto fail
)

echo [verify] importing qwen_agent ...
python -c "import qwen_agent.main"
if errorlevel 1 (
    echo [error] qwen_agent failed to import.
    goto fail
)

mkdir sessions >nul 2>nul
mkdir workspace >nul 2>nul

where browser-harness >nul 2>nul
if errorlevel 1 (
    echo [info] browser-harness not found - optional browser tool disabled.
) else (
    echo [info] browser-harness found - browser tool enabled.
)

echo.
echo Installation complete. Start the agent with:  start.bat
echo.
pause
exit /b 0

:fail
echo.
echo Installation FAILED - see the messages above.
pause
exit /b 1
