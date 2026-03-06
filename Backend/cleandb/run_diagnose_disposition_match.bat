@echo off
REM Diagnose why disposition file rows don't match DB rows. No DB changes.
cd /d "%~dp0.."
pip install pandas openpyxl 2>nul
echo.
python cleandb\diagnose_disposition_match.py %*
echo.
pause
