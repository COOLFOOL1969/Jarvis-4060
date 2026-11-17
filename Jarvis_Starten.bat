@echo off
setlocal EnableDelayedExpansion
title JARVIS - BOOT SYSTEM
color 0B
cls

rem Projektordner = der Ordner, in dem diese Datei liegt
set "PROJEKT=%~dp0"
set "PROJEKT=%PROJEKT:~0,-1%"
set "PORT=8080"

echo ====================================================
echo [Boot]: Starte JARVIS...
echo ====================================================

:: 0. Voraussetzungen pruefen
where python >nul 2>&1
if errorlevel 1 (
    echo [Fehler]: Python wurde nicht gefunden. Bitte Python installieren / PATH pruefen.
    pause
    exit /b 1
)
if not exist "%PROJEKT%\config.py" (
    echo [Fehler]: config.py fehlt. Bitte config_beispiel.py kopieren, in config.py umbenennen und ausfuellen.
    pause
    exit /b 1
)
if not exist "%PROJEKT%\hud_server.py" (
    echo [Fehler]: %PROJEKT%\hud_server.py nicht gefunden.
    pause
    exit /b 1
)

:: 1. Alte JARVIS-Instanz auf dem Port beenden
::    Nur Prozesse, die auf dem Port LAUSCHEN (nicht der Browser, der mit dem Port verbunden ist),
::    und nur wenn es ein Python-Prozess ist.
echo [Boot]: Pruefe Port %PORT% auf Alt-Prozesse...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /R /C:"127.0.0.1:%PORT% .*LISTENING"') do (
    tasklist /FI "PID eq %%a" 2>nul | find /I "python" >nul
    if not errorlevel 1 (
        echo [Boot]: Beende alte JARVIS-Instanz ^(PID %%a^)...
        taskkill /F /PID %%a >nul 2>&1
        timeout /t 1 /nobreak >nul
    ) else (
        echo [Fehler]: Port %PORT% wird von einem anderen Programm benutzt ^(PID %%a^).
        echo           Bitte dieses Programm schliessen und JARVIS erneut starten.
        pause
        exit /b 1
    )
)

:: 2. Ins Projektverzeichnis wechseln
cd /d "%PROJEKT%"

:: 3. JARVIS-Kern in eigenem, minimiertem Fenster starten.
::    Bei normalem Beenden schliesst sich das Fenster, bei einem Absturz bleibt es
::    mit der Fehlermeldung offen.
echo [Boot]: Starte JARVIS Kern...
start "JARVIS Kern" /min cmd /c "python hud_server.py || pause"

:: 4. Warten, bis der Server antwortet (max. 20 Sekunden)
echo [Boot]: Warte auf Kern-Synchronisation...
set READY=0
for /l %%i in (1,1,20) do if !READY!==0 (
    curl -s -o NUL --max-time 1 http://127.0.0.1:%PORT%/api/data && set READY=1
    if !READY!==0 timeout /t 1 /nobreak >nul
)

if !READY!==0 (
    echo [Fehler]: Der Server antwortet nicht. Schau im Fenster "JARVIS Kern" nach der Fehlermeldung.
    pause
    exit /b 1
)

:: 5. Web-HUD im Standardbrowser oeffnen
echo [Boot]: Verbinde Browser mit Live-Konsole...
start "" "http://127.0.0.1:%PORT%"

echo ====================================================
echo [Boot]: Startvorgang abgeschlossen. Interface aktiv.
echo ====================================================
timeout /t 2 >nul
exit
