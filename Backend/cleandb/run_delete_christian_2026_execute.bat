@echo off
REM Delete all dispositions for Christian in 2026.
cd /d "%~dp0.."
echo Installing/checking dependencies...
pip install -r requirements.txt >nul 2>&1
echo.
echo Running delete_agent_year --execute...
python cleandb\delete_agent_year.py --agent Christian --year 2026 --execute
pause
