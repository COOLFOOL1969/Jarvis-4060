# BEISPIEL-Konfiguration für JARVIS (ausgelegt auf eine NVIDIA-Grafikkarte mit 8 GB, z.B. RTX 4060).
# So geht's:  diese Datei kopieren, die Kopie in config.py umbenennen und deine Werte eintragen.
# config.py wird NIE hochgeladen (steht in .gitignore) - dort dürfen auch Passwörter stehen.
import os

USER_NAME = "Max"             # dein Vorname - so spricht Jarvis dich an
OBSIDIAN_VAULT_PATH = r"C:\Obsidian\Mein Archiv"   # <- Pfad zu deinem Obsidian-Tresor (Vault) ändern

OBSIDIAN_EXE_PATH = os.path.join(os.path.expanduser("~"), r"AppData\Local\Programs\obsidian\Obsidian.exe")
ANYCUBIC_EXE_PATH = r"C:\Program Files\AnycubicSlicerNext\AnycubicSlicerNext.exe"   # optional ("Starte Slicer")

# --- Sprachsteuerung ---
WAKE_WORD_REQUIRED = False    # False: Befehle auch ohne "Jarvis" | True: nur mit "Jarvis"
WAKE_WORDS = ["jarvis", "jarwis", "dschavis", "charvis"]   # Schreibweisen, die Google für "Jarvis" liefern kann

# --- Voicemeeter (optional; ohne Voicemeeter steuert Jarvis die Windows-Lautstärke) ---
VOICEMEETER_KIND = "potato"   # "basic", "banana" oder "potato"
VOICEMEETER_BUS = 0           # Potato: 0-4 = A1-A5, 5-7 = B1-B3  (0 = A1)
VOICEMEETER_CURVE = "log"     # "log": 50% = -6 dB | "linear": 50% = -30 dB

# --- Wissen: Obsidian-Volltextsuche, Websuche und Antworten mit Qwen (siehe wissen.py) ---
OLLAMA_MODEL = "qwen3.5:4b"   # Name wie bei "ollama list". Passt mit bge-m3 zusammen in 8 GB Grafikspeicher.
                              # Das große qwen3.5:9b ist für 8 GB zu knapp (läuft dann teilweise auf der CPU = langsam).
OLLAMA_KEEP_ALIVE = "30m"     # so lange bleibt das Modell im Grafikspeicher
WISSEN_KI_ANTWORT = True      # True: Qwen formuliert aus den Treffern eine kurze Antwort
                              # False: JARVIS liest die Treffer selbst vor (schneller, ohne KI)
OLLAMA_TIMEOUT = 90           # Sekunden, bis eine Qwen-Antwort abgebrochen wird
WISSEN_MAX_TREFFER = 5        # so viele Notizen / Webtreffer werden berücksichtigt

# --- Wetter im HUD und per Sprache (Open-Meteo, kostenlos, ohne Schlüssel) ---
WETTER_ORT = "Berlin"   # <- bitte auf deinen Wohnort ändern (Name nur für die Anzeige)
WETTER_LAT = 52.5200             # Breitengrad
WETTER_LON = 13.4050              # Längengrad
WETTER_CACHE_MIN = 15            # Wetter höchstens alle 15 Minuten neu abrufen
WETTER_TIMEOUT = 8               # Sekunden

# --- Obsidian-Notizen per Sprache (siehe notizen.py) ---
JARVIS_ORDNER = "JARVIS"         # Unterordner im Archiv für Inbox, Aufgaben und Einkaufsliste
DAILY_NOTE_ORDNER = "Daily Notes"   # Ordner deiner Daily Notes (wie in Obsidian eingestellt)
DAILY_NOTE_FORMAT = "%Y-%m-%d"   # Dateiname der Daily Note (ohne .md)
NOTIZ_VORLESEN_MAX = 1500        # so viele Zeichen werden einer Notiz maximal vorgelesen

# --- Timer, Wecker, Erinnerungen (siehe zeit.py) ---
ERINNERUNG_WIEDERHOLUNGEN = 2    # so oft wird eine Ansage nach dem ersten Mal wiederholt, bis du "Danke"/"Stopp" sagst
ERINNERUNG_PAUSE_S = 45          # Sekunden zwischen den Wiederholungen
ERINNERUNG_STANDARD_UHRZEIT = "09:00"   # Uhrzeit für "erinnere mich morgen an ..." ohne Zeitangabe

# --- Spracherkennung ---
STT_ENGINE = "whisper"           # "whisper" = offline (pip install faster-whisper) | "google" = online wie bisher
STT_FALLBACK_GOOGLE = True       # True: solange Whisper lädt oder fehlt, Google benutzen
WHISPER_MODEL = "small"          # "base" (schnell) | "small" (gut) | "medium" (sehr gut, langsamer) | "large-v3-turbo"
                                 # Erkennt er schlecht? Dann "medium" probieren (braucht mehr Rechenzeit).
WHISPER_DEVICE = "cpu"           # "cpu" lässt den Grafikspeicher (8 GB) für Qwen frei | "cuda" = auf der Grafikkarte
WHISPER_COMPUTE = "int8"         # "int8" ist auf der CPU am schnellsten
WHISPER_BEAM = 3                 # 1 = am schnellsten, 5 = am genauesten
STT_EXTRA_WOERTER = []           # eigene Begriffe/Namen, die Whisper besser erkennen soll, z.B. ["Max Mustermann"]

# --- Agent: Qwen als Auffangnetz für freie Sätze (siehe agent.py) ---
AGENT_AKTIV = True               # False: unbekannte Befehle bleiben bei "Befehl registriert"
AGENT_OHNE_ANREDE = False        # False: Jarvis antwortet frei nur, wenn du "Jarvis" sagst (oder im HUD tippst)
AGENT_WEB_AUTO = True            # aktuelle Fragen (Ämter, Preise, Nachrichten ...) automatisch im Web nachschlagen
AGENT_NUM_CTX = 8192             # Kontextgröße für Qwen (8192 passt gut in 8 GB, bitte nicht höher als 16384)
AGENT_VERLAUF_MIN = 120          # so lange (Minuten) bleibt das laufende Gespräch im Kurzzeitgedächtnis
AGENT_VERLAUF_MAX = 16           # wie viele Nachrichten (Frage+Antwort = 2) er davon im Kopf behält
AGENT_FOLGE_S = 75               # so lange nach seiner Antwort nimmt er Folgefragen auch ohne "Jarvis" an
AGENT_TIMEOUT = 60               # Sekunden, bis eine Agent-Antwort abgebrochen wird
PROFIL_NOTIZ = "JARVIS/Profil.md"   # Obsidian-Notiz mit dem, was Jarvis über dich wissen soll
# AGENT_PERSOENLICHKEIT = "Du bist JARVIS ..."   # optional: eigener Charakter-Text

# --- Obsidian-Suche nach Bedeutung (siehe semantik.py); einmalig:  ollama pull bge-m3 ---
SEMANTIK_AKTIV = True
EMBED_MODELL = "bge-m3"
SEMANTIK_MIN_SCORE = 0.45        # niedriger = mehr (auch unsichere) Treffer
SEMANTIK_INTERVALL_MIN = 30      # so oft wird der Index auf geänderte Notizen geprüft

# --- Proaktive Meldungen (siehe proaktiv.py): Termine, Kalender, Müll, Morgenbriefing ---
PROAKTIV_AKTIV = True
PROAKTIV_RUHE_VON = "22:00"      # in dieser Zeit spricht Jarvis nicht von allein (nur Log)
PROAKTIV_RUHE_BIS = "07:00"
BRIEFING_AUTO_UHRZEIT = ""       # z.B. "07:30": Morgenbriefing automatisch (leer = nur auf Zuruf)

# --- Langzeitgedächtnis (siehe gedaechtnis.py) ---
GEDAECHTNIS_AKTIV = True         # Gespräche überstehen Neustarts + werden nach einer Pause in Obsidian zusammengefasst
GEDAECHTNIS_PAUSE_MIN = 10       # nach so vielen Minuten Ruhe wird das Gespräch zusammengefasst
GEDAECHTNIS_IM_PROMPT = 3        # so viele der letzten Zusammenfassungen kennt er immer (0 = nur über die Suche)

# ----------------------------- Geburtstage & Jubiläen (Obsidian: JARVIS/Jahrestage.md) -----------------------------
JAHRESTAGE_AKTIV = True
JAHRESTAGE_DATEI = "Jahrestage.md"
JAHRESTAGE_VORWARN_GEBURTSTAG = [7, 1]    # so viele Tage vorher meldet Jarvis sich (zusätzlich zum Tag selbst)
JAHRESTAGE_VORWARN_JUBILAEUM = [14, 3]

# ----------------------------- Systemcheck beim Start -----------------------------
STARTCHECK_AKTIV = True
STARTCHECK_SPRECHEN = True      # False = nur ins Log schreiben
STARTCHECK_TIMEOUT_S = 120      # so lange wartet Jarvis höchstens auf Whisper und Suchindex
STARTCHECK_OK_TEXT = "Alle Systeme geprüft und online."

# ----------------------------- Spracheingabe -----------------------------
VOICE_PAUSE_S = 1.1             # Sprechpause (Sekunden), nach der ein Satz als beendet gilt. Höher = Sätze werden seltener abgeschnitten, Antwort kommt etwas später

# ----------------------------- Google-Kalender (nur lesen, per geheimer iCal-Adresse) -----------------------------
# Google Kalender -> Einstellungen -> (dein Kalender) -> "Geheime Adresse im iCal-Format" kopieren und hier einsetzen.
# Diese Adresse ist wie ein Passwort: nie weitergeben, nie in einen Chat posten. Benötigt:  pip install python-dateutil
KALENDER_AKTIV = True
KALENDER_QUELLEN = [
    # ("Privat",    "HIER-DIE-GEHEIME-ICAL-ADRESSE", "termin"),
    ("Feiertage", "https://calendar.google.com/calendar/ical/de.german%23holiday%40group.v.calendar.google.com/public/basic.ics", "feiertag"),
]
KALENDER_ABRUF_MIN = 15           # so oft wird neu geladen
KALENDER_VORWARN_MIN = [30]       # so viele Minuten vor einem Termin erinnert Jarvis
KALENDER_ZEITZONE = "Europe/Berlin"


# --- Speed Dial: Seiten aus der Chrome-Erweiterung "NAS Speed Dial" per Sprache öffnen (siehe speeddial.py) ---
SPEEDDIAL_AKTIV = False          # True nur, wenn du die Erweiterung "NAS Speed Dial" und ein NAS mit WebDAV hast
SPEEDDIAL_NAS_URL = "http://192.168.X.X:5005"   # WebDAV-Adresse deiner Synology (wie in der Erweiterung)
SPEEDDIAL_NAS_ORDNER = "/SpeedDial"               # Ordner auf dem NAS (wie in der Erweiterung)
SPEEDDIAL_NAS_USER = "speeddial"                  # NAS-Benutzer (wie in der Erweiterung)
SPEEDDIAL_NAS_PASS = "..."                         # Passwort dieses Benutzers
SPEEDDIAL_ERWEITERUNG_ID = ""                      # ID aus chrome://extensions (nur für "Öffne Gruppe ...")
SPEEDDIAL_CHROME = ""                              # leer = Chrome wird automatisch gesucht


# --- Synology-Chat-Bot (optional): Jarvis per Handy über Synology Chat Befehle schicken (siehe synology_bot.py) ---
SYNOLOGY_BOT_AKTIV = False
SYNOLOGY_BOT_TOKEN = ""
SYNOLOGY_CHAT_URL = ""            # z.B. "http://192.168.X.X:5000"
SYNOLOGY_BOT_USER_IDS = []

# --- Müllabfuhr (optional): ICS-Datei deiner Stadt in den Ordner "muell" legen (siehe muell.py) ---
MUELL_AKTIV = True
