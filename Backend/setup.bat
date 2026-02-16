@echo off
cd /d %~dp0

TITLE TeamsPowerDialer Server - Setup
echo ==========================================
echo   TeamsPowerDialer Backend - Setup
echo ==========================================

:: 1. Check Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed!
    echo Please install Python 3.11 from python.org first.
    echo IMPORTANT: Check the box "Add Python to PATH" during install.
    pause
    exit
)

:: 2. Create venv only if it doesn't exist
IF NOT EXIST "venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment...
    python -m venv venv
) ELSE (
    echo [1/2] Virtual environment already exists.
)

:: 3. Install/update dependencies (adds new packages, upgrades existing ones)
echo [2/2] Installing/updating dependencies from requirements.txt...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
venv\Scripts\pip install -r requirements.txt

echo.
echo ==========================================
echo   Setup complete. Use start_control_panel.bat or start_server.bat
echo ==========================================
pause
