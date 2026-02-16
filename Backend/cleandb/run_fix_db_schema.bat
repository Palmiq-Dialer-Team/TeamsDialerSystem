@echo off
cd /d "%~dp0.."
echo Make sure the server is STOPPED before running this.
pause
venv\Scripts\python cleandb\fix_db_schema.py
pause
