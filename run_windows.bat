@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environment not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m stickrig %*
if errorlevel 1 (
  echo StickRig Studio exited with an error.
  pause
)
