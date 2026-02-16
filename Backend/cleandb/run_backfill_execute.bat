@echo off
REM Ensure dependencies then run backfill with --execute (writes to DB).
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo pip install failed. Fix errors and try again.
    pause
    exit /b 1
)
pip install openpyxl
if errorlevel 1 (
    echo openpyxl install failed. Needed for reading .xlsx files.
    pause
    exit /b 1
)
echo.
echo Running backfill with --execute (will update calls.contact_data)...
python cleandb\backfill_calls_from_excel.py --execute
pause
