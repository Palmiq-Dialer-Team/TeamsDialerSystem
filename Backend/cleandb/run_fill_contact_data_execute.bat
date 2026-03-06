@echo off
REM Apply fill contact_data from CSV/XLSX in cleandb\disposition_files\ (backup created first).
cd /d "%~dp0.."
pip install -r requirements.txt
pip install pandas openpyxl
echo.
echo Running fill contact_data with --execute (will update calls.contact_data for rows missing it)...
python cleandb\fill_contact_data_from_files.py --execute
pause
