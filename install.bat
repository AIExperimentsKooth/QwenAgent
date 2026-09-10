@echo off
rem QwenAgent installer for Windows
rem Creates a .venv, installs the Python dependencies, installs the
rem browser-harness CLI (via uv) and creates the runtime directories.
setlocal
cd /d "%~dp0"

echo === QwenAgent installer (Windows) ===
echo

where python >nul 2>nul
if errorlevel 1 (
    echo [error] python not found on PATH. Install Python 3.10+ from
    echo         https://www.python.org/downloads/ and make sure
    echo         "Add Python to PATH" is checked during installation.
    exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
    echo [error] uv is required to install browser-harness.
    echo         Install it from https://docs.astral.sh/uv/ (PowerShell):
    echo           powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install --upgrade openai
if errorlevel 1 exit /b 1

echo [setup] installing browser-harness CLI ...
uv tool install --python 3.12 --upgrade --force browser-harness
if errorlevel 1 exit /b 1

mkdir sessions >nul 2>nul
mkdir workspace >nul 2>nul
mkdir toolbin >nul 2>nul

if not exist "skills\browser-harness.SKILL.md" (
    echo [error] bundled skill file skills\browser-harness.SKILL.md is missing
    exit /b 1
)

echo [verify] openai in .venv ...
".venv\Scripts\python.exe" -c "import openai; print('openai', openai.__version__)"
if errorlevel 1 exit /b 1

set "UV_BIN=%USERPROFILE%\.local\bin"
if exist "%UV_BIN%\browser-harness.exe" (
    echo [verify] browser-harness found in %UV_BIN%
) else (
    echo [warning] browser-harness.exe not found in %UV_BIN%
    echo           start.bat adds %UV_BIN% to PATH automatically, so the
    echo           browser tool should still work if uv installed it there.
)

echo.
echo Installation complete. Start the agent with:  start.bat