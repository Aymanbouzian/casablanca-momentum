@echo off
setlocal
cd /d "%~dp0"
python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or later is required'"
if errorlevel 1 goto fail
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Setup complete. Run RUN_WINDOWS.cmd next.
pause
exit /b 0
:fail
echo Setup did not complete. Read the error above.
pause
exit /b 1
