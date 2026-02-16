@echo off
cd /d "%~dp0"
echo Running client release script...
python release_client.py
echo.
pause
