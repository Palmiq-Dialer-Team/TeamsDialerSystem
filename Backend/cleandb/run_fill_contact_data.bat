@echo off
REM Dry-run: fill calls.contact_data from XLSX/CSV in cleandb\disposition_files\ for rows missing it.
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (echo pip install failed. & pause & exit /b 1)
pip install pandas openpyxl
if errorlevel 1 (echo pandas/openpyxl install failed. & pause & exit /b 1)
echo.
echo Running fill contact_data (dry-run; no DB changes)...
python cleandb\fill_contact_data_from_files.py
echo.
echo To apply updates, run: run_fill_contact_data_execute.bat
pause
