@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_WINDOWS.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --config config.json
if errorlevel 1 (
  echo Calculation failed. Read the error above; the existing report may be from an older run.
  pause
  exit /b 1
)
start "" "outputs\report.html"
pause
