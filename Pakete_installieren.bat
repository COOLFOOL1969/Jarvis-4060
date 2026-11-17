@echo off
rem file: Pakete_installieren.bat
chcp 65001 >nul
cd /d "%~dp0"
title Jarvis - Pakete installieren
echo ====================================================
echo  Installiere die Pakete, die Jarvis braucht ...
echo  Das kann beim ersten Mal ein paar Minuten dauern.
echo ====================================================
echo.
where python >nul 2>&1
if errorlevel 1 (
    echo [Fehler]: Python wurde nicht gefunden.
    pause
    exit /b 1
)
python --version
echo.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [Fehler]: Mindestens ein Paket konnte nicht installiert werden.
    echo           Bitte die Meldungen oben an Claude schicken.
    pause
    exit /b 1
)
echo.
echo ====================================================
echo  Fertig! Jetzt kannst du Jarvis wieder starten.
echo ====================================================
pause
