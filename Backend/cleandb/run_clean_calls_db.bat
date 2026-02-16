@echo off
REM Ensure dependencies then run clean_calls_db in DRY-RUN mode (no deletes).
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed. Fix errors and try again.
    pause
    exit /b 1
)
echo.
echo Running clean_calls_db (dry-run; no changes will be made)...
python cleandb\clean_calls_db.py
pause
