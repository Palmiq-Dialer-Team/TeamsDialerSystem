@echo off
REM Apply disposition repairs from CSV/XLSX in cleandb\disposition_files\ (backup created first).
cd /d "%~dp0.."
pip install -r requirements.txt
pip install pandas openpyxl
echo.
echo Running repair dispositions with --execute (will update calls.disposition and notes)...
python cleandb\repair_dispositions_from_files.py --execute
pause
