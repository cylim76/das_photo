@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
set LABELER_HOST=0.0.0.0
set LABELER_PORT=8765

echo.
echo Container Photo Labeler
echo Local URL: http://127.0.0.1:%LABELER_PORT%/labeler
echo LAN URL: http://YOUR-PC-IP:%LABELER_PORT%/labeler
echo Project: %CD%
echo.
echo Opening browser...
start "" "http://127.0.0.1:%LABELER_PORT%/labeler"
echo Starting server. Close this window to stop the labeler.
echo.

"%PYTHON%" "%~dp0labeler\server.py"
pause
