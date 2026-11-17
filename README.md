# JARVIS – deutscher Sprachassistent für Windows (lokal mit Ollama)

JARVIS ist ein Sprachassistent für Windows, der auf deutsche Sprache hört und antwortet.
Die KI läuft **lokal auf deinem PC** (mit [Ollama](https://ollama.com)). Deine Notizen verwaltest du mit [Obsidian](https://obsidian.md).
Im Browser siehst du dazu eine Übersicht (HUD) mit Uhr, Wetter, Terminen und Protokoll.

Diese Version ist auf eine **NVIDIA-Grafikkarte mit 8 GB** ausgelegt (z. B. RTX 4060).

---

## Was JARVIS kann

| Bereich | Beispiele zum Sagen |
|---|---|
| Timer, Wecker, Erinnerungen | „Timer 10 Minuten“, „Weck mich um 7 Uhr“, „Erinnere mich morgen an den Zahnarzt“ |
| Notizen in Obsidian | „Notiz: Reifen wechseln“, „Setz Milch auf die Einkaufsliste“, „Was steht auf meiner Aufgabenliste?“ |
| Suche | „Suche in Obsidian nach Router“, „Suche im Web nach …“, „Öffne Anleitung Drucker“ |
| Wetter, Uhrzeit, Datum | „Wie wird das Wetter morgen?“, „Wie spät ist es?“ |
| Geburtstage und Jubiläen | „Merk dir, Anna hat am 12. März Geburtstag“, „Wer hat bald Geburtstag?“ |
| Google-Kalender (nur lesen) | „Welche Termine habe ich morgen?“, „Ist morgen Feiertag?“ |
| Müllabfuhr | „Welche Tonne kommt morgen?“ |
| Freie Fragen und Gespräche | „Jarvis, was weißt du über mich?“, „Jarvis, erklär mir Photosynthese“ |
| Morgenbriefing | „Guten Morgen Jarvis“, „Was liegt heute an?“ |
| Gedächtnis | „Merk dir, dass ich Kaffee schwarz trinke“ |
| Lautstärke | „Lautstärke 30 Prozent“ |

Eine Haussteuerung (Smart Home) ist in dieser Version **nicht** enthalten.

---

## Was du brauchst

### Hardware
- PC mit **Windows 10 oder 11** (64 Bit)
- **NVIDIA-Grafikkarte mit mindestens 8 GB** Grafikspeicher (z. B. RTX 4060, RTX 3060 / 3070)
- mindestens **16 GB Arbeitsspeicher** (RAM)
- etwa **10 GB freier Speicherplatz**
- **Mikrofon** (Headset oder Tischmikrofon) und **Lautsprecher**
- **Internetverbindung** (für Sprachausgabe, Wetter und Websuche, siehe [Datenschutz](#datenschutz--was-geht-ins-internet))

### Programme (kostenlos)

| Programm | Wofür | Pflicht? |
|---|---|---|
| [Python 3.12](https://www.python.org/downloads/) | Damit läuft JARVIS | **ja** |
| [Ollama](https://ollama.com/download) | Die KI (Sprachmodell) auf deinem PC | **ja** |
| [NVIDIA-Grafiktreiber](https://www.nvidia.com/de-de/drivers/) (aktuell) | Damit Ollama die Grafikkarte nutzt | **ja** |
| [Obsidian](https://obsidian.md/download) | Notizen, Listen, Profil, Gedächtnis | **ja** |
| [Voicemeeter](https://vb-audio.com/Voicemeeter/) | Genauere Lautstärkeregelung | nein |
| Google-Kalender | Termine vorlesen | nein |
| Synology-NAS mit Synology Chat | Befehle vom Handy schicken | nein |

### KI-Modelle (werden mit `Modelle_laden.bat` geladen)

| Modell | Größe | Wofür |
|---|---|---|
| `qwen3.5:4b` | ca. 3–4 GB | Sprachmodell: versteht Sätze, antwortet, wählt Funktionen |
| `bge-m3` | ca. 1,2 GB | Suche in deinen Obsidian-Notizen nach Bedeutung |
| Whisper `small` | ca. 0,5 GB | Spracherkennung (wird beim ersten Start automatisch geladen) |

### Python-Pakete (werden mit `Pakete_installieren.bat` installiert)

| Paket | Wofür |
|---|---|
| `SpeechRecognition` | Mikrofon abhören, Sätze erkennen |
| `PyAudio` | Zugriff auf das Mikrofon |
| `faster-whisper` | Spracherkennung offline (Whisper) |
| `numpy` | Rechnen (für Whisper und die Notizsuche) |
| `edge-tts` | Sprachausgabe (Stimme von JARVIS) |
| `playsound==1.2.2` | Sprachausgabe abspielen |
| `voicemeeter-api` | Lautstärke über Voicemeeter (optional) |
| `ollama` | Verbindung zur KI |
| `ddgs` | Websuche (DuckDuckGo) |
| `python-dateutil` | Google-Kalender lesen |

---

## Installation Schritt für Schritt

### Schritt 1: Python installieren
1. Gehe auf <https://www.python.org/downloads/> und lade **Python 3.12** herunter (Windows installer, 64-bit).
2. Starte die Installation.
3. **Wichtig:** Setze ganz unten den Haken bei **„Add python.exe to PATH“**.
4. Klicke auf **„Install Now“**.
5. Test: Drücke `Windows-Taste + R`, tippe `cmd` und Enter. Tippe `python --version` und Enter.
   Es muss `Python 3.12.x` erscheinen.

### Schritt 2: NVIDIA-Treiber aktualisieren
Installiere den aktuellen Treiber für deine Grafikkarte (NVIDIA App oder <https://www.nvidia.com/de-de/drivers/>).

### Schritt 3: Ollama installieren
1. Lade Ollama von <https://ollama.com/download> herunter und installiere es.
2. Ollama läuft danach im Hintergrund (Lama-Symbol unten rechts in der Taskleiste).

### Schritt 4: Obsidian installieren und einen Tresor anlegen
1. Lade Obsidian von <https://obsidian.md/download> herunter und installiere es.
2. Starte Obsidian und lege einen **neuen Tresor (Vault)** an, z. B. `C:\Obsidian\Mein Archiv`.
   Wenn du schon einen Tresor hast, kannst du den nehmen.
3. Merke dir den Pfad. Du brauchst ihn in Schritt 8.

JARVIS legt im Tresor selbst einen Ordner **`JARVIS`** an. Dort stehen dann:
`Profil.md` (was Jarvis über dich weiß), `Inbox.md`, `Aufgaben.md`, `Einkaufsliste.md` und `Jahrestage.md`.

### Schritt 5: JARVIS herunterladen
1. Klicke oben auf dieser Seite auf den grünen Knopf **„Code“** und dann auf **„Download ZIP“**.
2. Entpacke die ZIP-Datei, z. B. nach `C:\Jarvis`.
   (Tipp: Ein Ordner ohne Leerzeichen und Umlaute macht am wenigsten Ärger.)

### Schritt 6: KI-Modelle laden
Doppelklick auf **`Modelle_laden.bat`**. Das dauert je nach Internet ein paar Minuten.
Am Ende siehst du eine Liste mit `qwen3.5:4b` und `bge-m3`.

### Schritt 7: Python-Pakete installieren
Doppelklick auf **`Pakete_installieren.bat`**. Am Ende steht „Fertig!“.

### Schritt 8: Einstellungen (config.py) anlegen
1. Kopiere die Datei **`config_beispiel.py`** und nenne die Kopie **`config.py`**.
2. Öffne `config.py` mit einem Texteditor (Rechtsklick → Öffnen mit → Editor).
3. Trage mindestens das hier ein:

```python
USER_NAME = "Max"                                    # dein Vorname
OBSIDIAN_VAULT_PATH = r"C:\Obsidian\Mein Archiv"     # Pfad zu deinem Obsidian-Tresor
WETTER_ORT = "Köln"                                  # dein Ort (nur für die Anzeige)
WETTER_LAT = 50.94                                   # Breitengrad deines Ortes
WETTER_LON = 6.96                                    # Längengrad deines Ortes
```

Breiten- und Längengrad findest du so: Ort in Google Maps suchen, Rechtsklick auf den Ort,
die erste Zahl ist der Breitengrad, die zweite der Längengrad.

4. Speichern.

> `config.py` wird nicht hochgeladen (siehe `.gitignore`). Dort dürfen also auch private Daten und Passwörter stehen.

### Schritt 9: Starten
Doppelklick auf **`Jarvis_Starten.bat`**.
- Ein kleines Fenster „JARVIS Kern“ startet (minimiert in der Taskleiste).
- Der Browser öffnet die Übersicht unter <http://127.0.0.1:8080>.
- Beim **ersten Start** lädt Whisper sein Modell herunter. Bis dahin nutzt JARVIS die Google-Spracherkennung.
- Nach etwa einer Minute sagt JARVIS: „Alle Systeme geprüft und online.“
- Fragt die Windows-Firewall nach Python: **„Privates Netzwerk“** erlauben.

Jetzt kannst du sprechen, z. B. **„Jarvis, wie spät ist es?“**.
Du kannst Befehle auch im Browser unten in das Eingabefeld tippen.

### Beenden
Sag **„Jarvis, fahre herunter“** oder schließe das Fenster „JARVIS Kern“.

---

## Optionale Funktionen

### Google-Kalender
1. Öffne den Google-Kalender im Browser → Zahnrad → **Einstellungen**.
2. Links deinen Kalender anklicken → ganz unten **„Privatadresse im iCal-Format“** kopieren.
3. In `config.py` bei `KALENDER_QUELLEN` eintragen:

```python
KALENDER_QUELLEN = [
    ("Privat", "HIER-DIE-KOPIERTE-ADRESSE", "termin"),
    ("Feiertage", "https://calendar.google.com/calendar/ical/de.german%23holiday%40group.v.calendar.google.com/public/basic.ics", "feiertag"),
]
```

Diese Adresse ist wie ein Passwort. Gib sie niemandem.

### Müllabfuhr
Viele Städte bieten die Abfuhrtermine als **ICS-Datei** zum Herunterladen an.
Lege die Datei in den Ordner **`muell`** im JARVIS-Ordner (der Ordner wird beim ersten Start angelegt).
Im neuen Jahr legst du die neue Datei einfach dazu.

### Voicemeeter
Hast du [Voicemeeter](https://vb-audio.com/Voicemeeter/), stellt JARVIS die Lautstärke dort ein.
In `config.py`: `VOICEMEETER_KIND` (`"basic"`, `"banana"` oder `"potato"`) und `VOICEMEETER_BUS`.
Ohne Voicemeeter nutzt JARVIS die normale Windows-Lautstärke.

### Synology Chat (Befehle vom Handy)
Beschreibung oben in der Datei `synology_bot.py`. Einschalten mit `SYNOLOGY_BOT_AKTIV = True` in `config.py`.

---

## Wichtige Einstellungen in config.py

| Einstellung | Bedeutung |
|---|---|
| `OLLAMA_MODEL` | Sprachmodell. Standard `qwen3.5:4b` (passt in 8 GB). |
| `AGENT_NUM_CTX` | Wie viel Text das Modell auf einmal „im Kopf“ hat. 8192 passt gut zu 8 GB. |
| `WHISPER_MODEL` | `small` (Standard) oder `medium` (genauer, aber langsamer). |
| `WHISPER_DEVICE` | `cpu` (Standard, lässt die Grafikkarte frei) oder `cuda`. |
| `WAKE_WORD_REQUIRED` | `True`: JARVIS reagiert nur, wenn du „Jarvis“ sagst. |
| `PROAKTIV_RUHE_VON` / `_BIS` | In dieser Zeit spricht JARVIS nicht von allein. |
| `BRIEFING_AUTO_UHRZEIT` | z. B. `"07:30"`: Morgenbriefing automatisch. |

---

## Hilfe bei Problemen

| Problem | Lösung |
|---|---|
| „Python wurde nicht gefunden“ | Python neu installieren und den Haken **„Add python.exe to PATH“** setzen. |
| „config.py fehlt“ | Schritt 8 machen: `config_beispiel.py` kopieren und in `config.py` umbenennen. |
| „Ollama ist nicht erreichbar“ | Ollama starten (Startmenü → Ollama). |
| „Das Modell … fehlt in Ollama“ | `Modelle_laden.bat` noch einmal starten. |
| JARVIS antwortet sehr langsam | In der Eingabeaufforderung `ollama ps` eingeben. Unter PROCESSOR muss **100% GPU** stehen. Sonst andere Programme schließen, die die Grafikkarte nutzen (z. B. Spiele). |
| JARVIS hört nichts | Windows-Einstellungen → Datenschutz → **Mikrofon**: Zugriff für Desktop-Apps erlauben. Richtiges Standard-Mikrofon wählen. |
| JARVIS versteht schlecht | In `config.py` `WHISPER_MODEL = "medium"` probieren. |
| „Port 8080 wird von einem anderen Programm benutzt“ | Das andere Programm schließen und JARVIS neu starten. |
| PyAudio lässt sich nicht installieren | Prüfen, ob wirklich Python **3.12** installiert ist (`python --version`). |
| Andere Fehlermeldung | Das Fenster „JARVIS Kern“ in der Taskleiste öffnen. Dort steht die genaue Meldung. |

### Tipp: Eine KI beim Fehler suchen helfen lassen
JARVIS schreibt alles, was er tut, in das Fenster **„JARVIS Kern“** (Terminal).
Diesen Text kannst du in einen KI-Chat kopieren, z. B. Claude oder ChatGPT.
Die KI kann dann prüfen, was nicht funktioniert.

1. Das Fenster **„JARVIS Kern“** in der Taskleiste anklicken.
2. Den Text **zuerst markieren**: mit der Maus darüberziehen oder `Strg + A` für alles.
3. `Strg + C` drücken, um zu kopieren.
4. Im KI-Chat `Strg + V` drücken, um einzufügen. Dazu kurz schreiben, was nicht klappt,
   z. B. „Jarvis antwortet nicht auf Fragen. Hier ist die Ausgabe:“.

> **Achtung:** Drückst du `Strg + C`, **ohne** vorher Text zu markieren, wird JARVIS beendet.
> Dann einfach `Jarvis_Starten.bat` noch einmal starten.

> Bevor du den Text in einen Chat einfügst: Kurz drüberlesen, ob private Dinge darin stehen
> (z. B. Termine oder Notizen), und diese Stellen löschen.

---

## Datenschutz – was geht ins Internet?

Die KI (Ollama), die Spracherkennung (Whisper) und deine Notizen bleiben **auf deinem PC**.
Diese Dienste werden über das Internet genutzt:

- **Sprachausgabe:** `edge-tts` schickt den Text, den JARVIS spricht, an den Sprachdienst von Microsoft.
- **Spracherkennung (nur als Ersatz):** Solange Whisper lädt oder fehlt, wird Google genutzt. Abschalten mit `STT_FALLBACK_GOOGLE = False`.
- **Websuche:** Suchanfragen gehen an DuckDuckGo.
- **Wetter:** Open-Meteo (nur die Koordinaten aus `config.py`).
- **Google-Kalender:** nur, wenn du ihn einträgst.

---

## Dateien im Überblick

| Datei | Aufgabe |
|---|---|
| `Jarvis_Starten.bat` | Startet JARVIS und öffnet die Übersicht im Browser |
| `Modelle_laden.bat` | Lädt die KI-Modelle in Ollama |
| `Pakete_installieren.bat` | Installiert die Python-Pakete |
| `config_beispiel.py` | Vorlage für deine Einstellungen (`config.py`) |
| `hud_server.py` | Zentrale: Befehle erkennen, Übersicht im Browser |
| `voice.py` | Mikrofon, Spracherkennung, Sprachausgabe |
| `agent.py` | KI für freie Sätze und Gespräche |
| `wissen.py`, `semantik.py`, `archiv.py` | Suche in Obsidian und im Web |
| `notizen.py`, `gedaechtnis.py`, `verknuepfung.py` | Notizen, Listen, Gedächtnis, Link-Vorschläge |
| `zeit.py`, `wetter.py`, `termine.py`, `kalender.py`, `muell.py` | Timer, Wetter, Jahrestage, Kalender, Müll |
| `proaktiv.py`, `startbriefing.py`, `startcheck.py` | Meldungen von allein, Briefing, Systemcheck |
| `speeddial.py`, `synology_bot.py` | Optionale Zusatzfunktionen |
| `index.html` | Die Übersicht im Browser |

---

## Lizenz

Dieses Projekt steht unter der **Apache-Lizenz 2.0** (siehe [LICENSE](LICENSE)).

- Du darfst den Code **benutzen, verändern und weitergeben**, auch in eigenen Projekten.
- Wenn du ihn weitergibst, gib die Datei [NOTICE](NOTICE) mit. Darin steht der Verweis auf das Original:
  <https://github.com/COOLFOOL1969/Jarvis-4060>
- Wenn du Dateien veränderst, schreib in diese Dateien einen kurzen Hinweis, dass du sie geändert hast.

Copyright 2026 COOLFOOL1969
