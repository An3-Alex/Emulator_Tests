@echo off
setlocal
cd /d "%~dp0" || exit /b 1
echo Starte QEMU, Original-Datenbank und Live-Ereignislog ...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0program-and-start-emulator.ps1"
set "EMU_EXIT=%ERRORLEVEL%"
echo.
echo Emulator beendet. Exit-Code: %EMU_EXIT%
echo Zum Schliessen eine Taste druecken.
pause >nul
exit /b %EMU_EXIT%
