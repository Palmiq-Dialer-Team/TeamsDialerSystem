@echo off
REM Dry-run: show how many rows would be deleted for Christian 2026.
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt >nul 2>&1
echo.
echo Running delete_agent_year (dry-run)...
python cleandb\delete_agent_year.py --agent Christian --year 2026
echo.
echo To actually delete, run: run_delete_christian_2026_execute.bat
pause
