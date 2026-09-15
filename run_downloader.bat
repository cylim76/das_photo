@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
echo.
echo DAS Photo Downloader
echo Local URL: http://127.0.0.1:8787
echo LAN URL: http://YOUR-PC-IP:8787
echo Project: %CD%
echo.
echo Opening browser...
start "" "http://127.0.0.1:8787"
echo Starting downloader. Close this window to stop it.
echo.
"%PYTHON%" downloader.py --host 0.0.0.0 --port 8787
pause
