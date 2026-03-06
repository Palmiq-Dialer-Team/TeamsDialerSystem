@echo off
REM Dry-run: report duplicate call rows (same contact + same disposition), keep latest only.
cd /d "%~dp0.."
echo Running dedupe (dry-run; no deletes)...
python cleandb\dedupe_calls_db.py
echo.
echo To apply deletes, run: run_dedupe_calls_db_execute.bat
pause
