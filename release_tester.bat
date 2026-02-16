@echo off
cd /d "%~dp0"
echo Running tester release script...
python release_tester.py
echo.
pause
