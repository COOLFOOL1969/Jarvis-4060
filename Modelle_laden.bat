@echo off
rem file: Modelle_laden.bat
chcp 65001 >nul
cd /d "%~dp0"
title Jarvis - KI-Modelle laden
echo ====================================================
echo  Lade die KI-Modelle (passend fuer 8 GB Grafikspeicher) ...
echo  qwen3.5:4b = Sprachmodell (ca. 3-4 GB)
echo  bge-m3     = Suchmodell fuer Obsidian (ca. 1,2 GB)
echo ====================================================
echo.
where ollama >nul 2>&1
if errorlevel 1 (
    echo [Fehler]: Ollama wurde nicht gefunden. Bitte zuerst Ollama installieren: https://ollama.com
    pause
    exit /b 1
)
ollama pull qwen3.5:4b
ollama pull bge-m3
echo.
echo Vorhandene Modelle:
ollama list
echo.
echo ====================================================
echo  Fertig! Jetzt Jarvis_Starten.bat doppelklicken.
echo ====================================================
pause
