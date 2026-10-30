@echo off
REM Build script for Onsumm Tally Agent
REM Run this on a Windows machine with Python 3.11+ installed
REM Output: dist/OnsummTallyAgent.exe  (~15-20 MB, fully self-contained)

echo Installing build dependencies...
pip install -r requirements.txt

echo.
echo Building standalone .exe...
pyinstaller ^
  --onefile ^
  --windowed ^
  --name OnsummTallyAgent ^
  --hidden-import tkinter ^
  --hidden-import PIL._tkinter_finder ^
  --hidden-import dashboard ^
  --collect-all pystray ^
  --collect-all PIL ^
  agent.py

echo.
echo Build complete: dist\OnsummTallyAgent.exe
echo.
echo SHA256:
certutil -hashfile dist\OnsummTallyAgent.exe SHA256
