@echo off
REM Dry-run: repair call dispositions from CSV/XLSX in cleandb\disposition_files\ (latest wins).
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt
if errorlevel 1 (echo pip install failed. & pause & exit /b 1)
pip install pandas openpyxl
if errorlevel 1 (echo pandas/openpyxl install failed. & pause & exit /b 1)
echo.
echo Running repair dispositions (dry-run; no DB changes)...
python cleandb\repair_dispositions_from_files.py
echo.
echo To apply updates, run: run_repair_dispositions_execute.bat
pause
