@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 (
  python -B "%~dp0agent-detective-lab\dashboard.py"
) else (
  py -3 -B "%~dp0agent-detective-lab\dashboard.py"
)
if errorlevel 1 (
  echo.
  echo The dashboard could not start. Install Python 3.10 or newer, or check the error above.
  pause
)
endlocal
