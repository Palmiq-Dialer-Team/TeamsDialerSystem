@echo off
REM Delete duplicate call rows (same contact + same disposition), keep latest. Backup created first.
cd /d "%~dp0.."
echo Running dedupe with --execute...
python cleandb\dedupe_calls_db.py --execute
pause
