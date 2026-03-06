@echo off
REM Dry-run: delete call rows that don't have proper contact_data (missing or empty).
cd /d "%~dp0.."
echo Running delete calls without contact_data (dry-run; no deletes)...
python cleandb\delete_calls_without_contact_data.py
echo.
echo To apply deletions, run: run_delete_calls_without_contact_data_execute.bat
pause
