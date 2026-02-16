@echo off
REM Ensure dependencies then run clean_calls_db with --execute (applies deletions).
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed. Fix errors and try again.
    pause
    exit /b 1
)
echo.
echo Running clean_calls_db with --execute (will delete manual entries and old no-contact rows)...
python cleandb\clean_calls_db.py --execute
pause
