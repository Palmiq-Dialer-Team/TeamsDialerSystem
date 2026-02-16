@echo off
cd /d "%~dp0"
echo Starting Server directly on Port 80 (No IIS)...
echo Access URL: http://192.168.1.46

:: host 0.0.0.0 allows connections from other computers
:: port 80 is the standard web port
venv\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 80 --ws websockets --timeout-keep-alive 75 --workers 1

pause
