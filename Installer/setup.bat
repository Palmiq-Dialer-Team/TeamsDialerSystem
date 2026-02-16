@echo off
:: Change to the directory where this batch file is located
cd /d %~dp0

TITLE Installing Teams PowerDialer...
echo ==========================================
echo      Teams PowerDialer - Setup
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

echo [1/3] Creating isolated environment...
python -m venv venv

echo [2/3] Installing libraries...
:: Upgrade pip first
venv\Scripts\python.exe -m pip install --upgrade pip
:: Install requests first so launcher works
venv\Scripts\pip install requests
:: Install PyQt6-sip explicitly first (required for PyQt6)
venv\Scripts\pip install PyQt6-sip
:: Install the rest
venv\Scripts\pip install -r requirements.txt

echo [3/3] Creating Desktop Shortcut...
set SCRIPT="%TEMP%\CreateShortcut.vbs"
(
echo Set oWS = WScript.CreateObject("WScript.Shell"^)
echo sLinkFile = oWS.SpecialFolders("Desktop"^) ^& "\Teams Dialer.lnk"
echo Set oLink = oWS.CreateShortcut(sLinkFile^)
echo oLink.TargetPath = "%CD%\venv\Scripts\python.exe"
echo oLink.Arguments = "launcher.py"
echo oLink.WorkingDirectory = "%CD%"
echo oLink.WindowStyle = 7
echo oLink.Description = "Sales Dialer"
echo oLink.Save
) > %SCRIPT%
cscript /nologo %SCRIPT%
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Failed to create desktop shortcut. You can manually create one.
) else (
    echo [SUCCESS] Desktop shortcut created successfully.
)
del %SCRIPT%

:: Create initial version file
echo {"version": 1.0} > local_version.json

echo.
echo ==========================================
echo        INSTALLATION SUCCESSFUL
echo ==========================================
echo You can now use the "Teams Dialer" shortcut on your Desktop.
pause