@echo off
rem Windows equivalent of update.sh — pull the latest checkout and
rem reinstall dependencies using native cmd.exe commands.
rem Requires: git, gh (GitHub CLI), Python 3. Optionally uv (for browser-harness).
setlocal

cd /d "%~dp0"

rem ── gh (GitHub CLI) is required for authenticated pulls ─────────────
where gh >nul 2>nul
if errorlevel 1 (
  echo error: gh is required. Install it with: winget install GitHub.cli
  exit /b 1
)
gh auth status >nul 2>nul
if errorlevel 1 (
  echo error: not authenticated with gh. Run: gh auth login
  exit /b 1
)

rem ── fast-forward pull using gh's git credential helper ──────────────
gh auth setup-git
for /f "delims=" %%b in ('git branch --show-current') do set "BRANCH=%%b"
if "%BRANCH%"=="" (
  echo error: detached HEAD; check out a branch first
  exit /b 1
)
git pull --ff-only origin %BRANCH%
if errorlevel 1 exit /b 1

rem ── reinstall dependencies (Windows equivalents of install.sh) ─────
where python >nul 2>nul
if errorlevel 1 (
  echo error: python not found. Install it from https://python.org
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip openai
if errorlevel 1 exit /b 1

if not exist "skills\browser-harness.SKILL.md" (
  echo error: bundled browser skill is missing
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo warning: uv not found - skipping browser-harness install. Install with: winget install astral-sh.uv
) else (
  uv tool install --python 3.12 --upgrade --force browser-harness
  if errorlevel 1 exit /b 1
)

where browser-harness >nul 2>nul
if errorlevel 1 (
  echo warning: browser-harness not found on PATH - ensure uv tool bin is on PATH
)

mkdir sessions 2>nul
mkdir workspace 2>nul
mkdir toolbin 2>nul

echo Update complete.
endlocal