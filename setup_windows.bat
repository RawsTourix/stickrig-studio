@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Python Launcher 'py' not found. Install Python 3.11 or newer.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :fail
)
echo Installing StickRig Studio and dependencies...
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail
echo.
echo Setup complete. Double-click run_windows.bat to start.
pause
exit /b 0
:fail
echo Setup failed. Check Python installation and internet access.
pause
exit /b 1
