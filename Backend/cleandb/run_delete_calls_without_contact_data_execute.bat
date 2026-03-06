@echo off
REM Delete call rows that don't have proper contact_data (backup created first).
cd /d "%~dp0.."
echo Running delete calls without contact_data with --execute...
python cleandb\delete_calls_without_contact_data.py --execute
pause
