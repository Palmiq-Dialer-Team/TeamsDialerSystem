@echo off
REM Ensure dependencies then run fix_calls_db (merge No Disposition, dedupe, etc.).
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed. Fix errors and try again.
    pause
    exit /b 1
)
echo.
echo Running fix_calls_db...
python cleandb\fix_calls_db.py
pause
