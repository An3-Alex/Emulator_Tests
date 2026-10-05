@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.13 -m venv ".venv"
    if errorlevel 1 goto setup_error
)
".venv\Scripts\python.exe" -c "import esptool, serial" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r "requirements-flasher.txt"
    if errorlevel 1 goto setup_error
)
".venv\Scripts\python.exe" usb_flasher.py
if errorlevel 1 pause
exit /b 0
:setup_error
echo ESP32-Flasher konnte seine isolierte Python-Umgebung nicht einrichten.
echo Python 3.13 und eine Internetverbindung pruefen.
pause
exit /b 1
