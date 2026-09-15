@echo off
setlocal
cd /d "%~dp0"

echo.
echo DAS Photo Tools
echo Downloader: http://127.0.0.1:8787
echo Labeler:    http://127.0.0.1:8787/labeler
echo.

call "%~dp0run_downloader.bat"
