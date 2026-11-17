@echo off
rem Windows equivalent of update.sh - pull the latest checkout and verify
rem the harness still imports. Requires: git, Python 3.10+.
setlocal

cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo error: git is required. Install it from https://git-scm.com
  exit /b 1
)

for /f "delims=" %%b in ('git branch --show-current') do set "BRANCH=%%b"
if "%BRANCH%"=="" (
  echo error: detached HEAD; check out a branch first
  exit /b 1
)
git pull --ff-only origin %BRANCH%
if errorlevel 1 exit /b 1

where python >nul 2>nul
if errorlevel 1 (
  echo error: python not found. Install it from https://python.org
  exit /b 1
)
python -c "import qwen_agent.main"
if errorlevel 1 (
  echo error: qwen_agent failed to import
  exit /b 1
)

mkdir sessions 2>nul
mkdir workspace 2>nul

echo Update complete.
endlocal
