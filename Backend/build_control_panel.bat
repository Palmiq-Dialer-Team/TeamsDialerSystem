@echo off
cd /d "%~dp0"

echo Installing PyInstaller if needed...
venv\Scripts\pip install pyinstaller --quiet

echo Building Server Control Panel executable...
venv\Scripts\pyinstaller --onefile --windowed ^
  --name "Server Control Panel" ^
  --icon=control_panel/controlpanelicon.ico ^
  --paths . ^
  run_control_panel.py

echo Done. Executable: dist\Server Control Panel.exe
pause
