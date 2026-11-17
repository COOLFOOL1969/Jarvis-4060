import os
import re
import sys
import glob
import time
import json
import queue
import difflib
import threading
import subprocess
import webbrowser
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import speech_recognition as sr

# Erzwingt den lokalen Verzeichnispfad für Imports unter Windows
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

import config

# Alle Dateien, die Jarvis selbst anlegt (Cache, Index, Sprachdateien), landen in DIESEM Ordner.
config.PROJEKT_DIR = BASE_DIR
config.JS_DATA_PATH = os.path.join(BASE_DIR, "hud_data.js")

import matrix_core as core   # früher "as hud_server" -> verwirrend, da diese Datei selbst hud_server.py heißt
import archiv
import voice
import wissen
import zeit
import notizen
import wetter
import agent
import semantik
import gedaechtnis
import proaktiv
import termine
import verknuepfung
import startbriefing
import muell
import kalender
import startcheck
import synology_bot
import speeddial

def _mit_name(satz):
    """'Ich höre dich' -> 'Ich höre dich, Max.' (Name aus config.py, USER_NAME). Ohne Namen nur mit Punkt."""
    name = str(getattr(config, "USER_NAME", "") or "").strip()
    return f"{satz}, {name}." if name else f"{satz}."


# Lokale, thread-sichere Log-Struktur für die Konsole im Web-HUD
local_system_logs = []
local_logs_lock = threading.Lock()


def add_matrix_log(message):
    """Schreibt Logs in die Konsole und füttert thread-sicher die HUD-Liste."""
    print(message)
    with local_logs_lock:
        local_system_logs.append(message)
        if len(local_system_logs) > 40:
            local_system_logs.pop(0)


# ----------------------------- Hilfsfunktionen -----------------------------
def set_status(status):
    core.aktueller_status = status


speak_lock = threading.Lock()      # verhindert, dass zwei Sprachausgaben gleichzeitig laufen
speaking = threading.Event()       # True, solange JARVIS spricht
last_speak_end = 0.0
mic_muted = threading.Event()      # True = Mikrofon stumm (JARVIS hört nicht zu)


recent_spoken = []                 # (Zeitpunkt, Text) der letzten eigenen Sprachausgaben


def _normalize(s):
    return " ".join(re.findall(r"[a-z0-9]+", _fold(s)))


# JARVIS' Sprachausgabe wird HIER zentral abgesichert. Jede Ausgabe - auch die aus voice.py
# (z.B. "Lautstärke auf 20 Prozent angepasst") - setzt dadurch 'speaking' und merkt sich den Text.
# Vorher lief die Lautstärke-Antwort an dieser Absicherung vorbei, JARVIS hörte sich selbst,
# erkannte das Wort "Lautstärke" + "20" und führte den Befehl endlos erneut aus.
_original_jarvis_speak = voice.jarvis_speak


def _guarded_jarvis_speak(text):
    global last_speak_end
    with speak_lock:
        recent_spoken.append((time.time(), _normalize(text)))
        del recent_spoken[:-8]
        speaking.set()
        try:
            _original_jarvis_speak(text)
        finally:
            last_speak_end = time.time()
            speaking.clear()


voice.jarvis_speak = _guarded_jarvis_speak


def speak(text):
    """Spricht einen Text (immer nur einer gleichzeitig) und setzt den Status danach auf ONLINE."""
    set_status("AUDIO-AUSGABE")
    try:
        voice.jarvis_speak(text)
    finally:
        set_status("ONLINE")


def is_own_echo(text):
    """Zweite Sicherung neben der Zeit-Prüfung: erkennt, wenn der Text (fast) das ist, was JARVIS
    gerade selbst gesagt hat. Bewusst streng, damit ein ähnlich lautender eigener Befehl
    ('Lautstärke 20 Prozent') nicht fälschlich als Echo gilt."""
    t = _normalize(text)
    if not t:
        return False
    jetzt = time.time()
    for ts, spoken in recent_spoken:
        if jetzt - ts > 8 or not spoken:
            continue
        if difflib.SequenceMatcher(None, t, spoken).ratio() >= 0.85:
            return True
        woerter, ende = t.split(), " ".join(spoken.split()[-2:])
        # Echo ist oft abgeschnitten, aber das Satzende ("prozent angepasst") bleibt erhalten
        if len(woerter) >= 2 and len(spoken.split()) >= 3 and " ".join(woerter[-2:]) == ende:
            return True
    return False


# ----------------------------- Timer / Wecker / Erinnerungen -----------------------------
def bei_faelligkeit(it):
    """Wird vom Planer aufgerufen, wenn ein Timer/Wecker/eine Erinnerung fällig ist (eigener Thread)."""
    text = zeit.ansage_text(it)
    add_matrix_log(f"[Zeit]: ⏰ {text}")
    if it.get("ansagen", 1) <= 1:
        zeit.piepen()
    speak(text)


planer = zeit.Planer(on_due=bei_faelligkeit, log=lambda m: add_matrix_log(m))

ACK_WORDS = {"danke", "okay", "ok", "gut", "stopp", "stop", "halt", "ruhe", "verstanden", "gehoert", "gehört",
             "alles", "klar", "ist", "schon", "gut", "passt", "super", "dankeschön", "dankeschoen", "bitte",
             "jarvis", "ja", "erledigt", "fertig", "aus", "mach", "das", "jetzt", "gesehen", "ich", "weiss",
             "weiß", "es", "bescheid", "sei", "still"}

lesen_stop = threading.Event()     # "Stopp" beendet das Vorlesen einer Notiz
lesen_aktiv = threading.Event()
STOP_LESEN = {"stopp", "stop", "halt", "genug", "aufhoeren", "aufhören", "abbrechen", "ruhe", "still"}


def _woerter(cmd):
    return re.findall(r"[a-zäöüß]+", cmd.lower())


def is_ack(cmd):
    w = _woerter(cmd)
    return 0 < len(w) <= 5 and all(x in ACK_WORDS for x in w)


def is_stop_lesen(cmd):
    w = _woerter(cmd)
    return 0 < len(w) <= 3 and any(x in STOP_LESEN for x in w) and \
        all(x in STOP_LESEN or x in ("jarvis", "bitte", "das", "lesen", "vorlesen", "mit", "dem", "hör", "hoer", "auf")
            for x in w)


voice_queue = queue.Queue()   # Sprachbefehle als (text, mit_Ansprache)


def _fold(s):
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s


def has_word(text, *keys, cutoff=0.8):
    """Tolerante Stichwortsuche für Spracherkennung: ignoriert Umlaute, findet Wortteile,
    zusammengeschriebene Varianten ('any cubic' = 'anycubic') und kleine Erkennungsfehler."""
    tokens = re.findall(r"[a-z0-9]+", _fold(text))
    cands = tokens + [a + b for a, b in zip(tokens, tokens[1:])]
    for key in keys:
        k = _fold(key)
        if any(k in c for c in cands):
            return True
        if len(k) >= 6 and difflib.get_close_matches(k, cands, n=1, cutoff=cutoff):
            return True
    return False


SHUTDOWN_WORDS = ("beenden", "ausschalten", "shutdown", "schließen")
SHUTDOWN_PHRASES = ("beende dich", "beende jarvis", "schließe dich", "schließ dich",
                    "fahre herunter", "herunterfahren", "offline")


_shutdown_laeuft = threading.Event()


def shutdown_jetzt(sprechen=True):
    """Beendet Jarvis zuverlässig: sofort, aus jedem Thread, mit Notausstieg.
    Die Verabschiedung darf höchstens ein paar Sekunden dauern, danach wird hart beendet (auch wenn Sprachausgabe/Netz hängt)."""
    if _shutdown_laeuft.is_set():
        return
    _shutdown_laeuft.set()
    t = threading.Timer(float(getattr(config, "SHUTDOWN_MAX_S", 8)), lambda: os._exit(0))   # Notausstieg
    t.daemon = True
    t.start()

    def lauf():
        try:
            set_status("OFFLINE")
            try:
                startbriefing.merken()      # 'zuletzt da' festhalten, damit der nächste Start das Neue ansagen kann
            except Exception:
                pass
            if sprechen:
                sp = threading.Thread(target=voice.jarvis_speak, args=("System fährt herunter.",), daemon=True)
                sp.start()
                sp.join(float(getattr(config, "SHUTDOWN_SPRECH_S", 5)))
            time.sleep(1.0)            # das HUD soll OFFLINE noch sehen
        finally:
            os._exit(0)
    threading.Thread(target=lauf, daemon=True).start()


def is_shutdown_command(cmd_clean, addressed):
    """Ohne Ansprache ('Jarvis') nur eindeutige Sätze, damit normales Gerede nicht abschaltet."""
    if any(p in cmd_clean for p in SHUTDOWN_PHRASES):
        return True
    return addressed and any(w in cmd_clean for w in SHUTDOWN_WORDS)


STOPWORDS = {
    "such", "suche", "suchen", "die", "der", "das", "den", "dem", "nummer",
    "telefonnummer", "telefon", "anrufen", "ruf", "mobil", "handy", "von",
    "vom", "frau", "herr", "jarvis", "mir", "bitte", "kontakt", "wie",
    "lautet", "ist", "was", "wo", "öffne", "pdf", "anleitung",
    # "Suche in Bedienungsanleitungen nach Gymnastikball" -> nur "gymnastikball" bleibt übrig
    "anleitungen", "bedienungsanleitung", "bedienungsanleitungen",
    "in", "im", "nach", "für", "zum", "zur", "finde", "zeig", "zeige",
}


def extract_search_term(cmd_clean):
    """Entfernt Füllwörter WORTWEISE (nicht als Teilstring, sonst wird z.B. 'Herrmann' zu 'mann')."""
    words = re.findall(r"[\wäöüß]+", cmd_clean)
    return " ".join(w for w in words if w not in STOPWORDS)


PHONE_LABELED = re.compile(
    r"(?:telefon|tel|mobil|handy|nr\.?|phone)\s*:?\s*(\+?\d[\d\s/\-()]{5,25}\d)",
    re.IGNORECASE,
)
PHONE_GENERIC = re.compile(r"(?<![\d.\-/])(\+?\d[\d\s/\-()]{6,25}\d)(?![\d.])")


def find_phone_number(text):
    """Bevorzugt Zahlen mit Label (Tel:, Mobil: ...), sonst allgemeine Telefonmuster.
    Datumsangaben wie 2024-01-15 werden ignoriert."""
    m = PHONE_LABELED.search(text)
    if m:
        return m.group(1).strip()
    for m in PHONE_GENERIC.finditer(text):
        kandidat = m.group(1).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", kandidat):
            continue
        if sum(c.isdigit() for c in kandidat) >= 7:
            return kandidat
    return None


def find_contact_note(such_name):
    """Sucht in der Obsidian-Vault eine .md-Datei, deren Name alle Suchwörter enthält."""
    if not os.path.exists(config.OBSIDIAN_VAULT_PATH):
        return None
    woerter = such_name.split()
    for root, _dirs, files in os.walk(config.OBSIDIAN_VAULT_PATH):
        for file in files:
            name = file.lower()
            if name.endswith(".md") and all(w in name for w in woerter):
                return os.path.join(root, file)
    return None


# ----------------------------- Webserver -----------------------------
class JARVISHUDRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html_path = os.path.join(BASE_DIR, "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
                code = 200
            else:
                body = b"index.html fehlt."
                code = 404
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/verknuepfungen":
            try:
                liste = verknuepfung.vorschlaege(8) if verknuepfung.aktiv() else []
                self._send_json({"bereit": semantik.bereit(), "liste": liste})
            except Exception as e:
                self._send_json({"bereit": False, "liste": [], "fehler": e.__class__.__name__})
        elif self.path == "/api/data":
            self._send_json({
                "tree": getattr(core, "STATIC_TREE", "Koppele Dateisystem..."),
                "status": getattr(core, "aktueller_status", "ONLINE"),
                "text": getattr(core, "aktueller_text", "Bereit."),
                "mic_muted": mic_muted.is_set(),
                "graph_version": getattr(core, "graph_version", 0),
                "stt": ("Whisper (offline)" if voice.stt_bereit.is_set() else
                        "Google (online)" if str(getattr(config, "STT_ENGINE", "google")).lower() != "whisper"
                        else "Whisper lädt ..."),
                "lesen": lesen_aktiv.is_set(),
                "haus": None,                      # ohne Haussteuerung
                "termine": termine.hud_liste(3),
                "kalender": kalender.hud_liste(3),
                "muell": muell.hud_liste(),
            })
        elif self.path == "/api/zeit":
            self._send_json({"items": planer.snapshot(), "klingelt": planer.klingelt(), "jetzt": time.time()})
        elif self.path == "/api/wetter":
            self._send_json(wetter.hud_snapshot())
        elif self.path.startswith("/api/graph"):
            try:
                v = int(parse_qs(urlparse(self.path).query).get("v", ["-1"])[0])
            except ValueError:
                v = -1
            aktuell = getattr(core, "graph_version", 0)
            if v == aktuell:
                self._send_json({"version": aktuell, "unveraendert": True})
            else:
                g = dict(getattr(core, "STATIC_GRAPH", {}))
                g["version"] = aktuell
                self._send_json(g)
        elif self.path == "/api/logs":
            with local_logs_lock:
                logs = list(local_system_logs)
            self._send_json({"logs": logs})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/shutdown":
            self._send_json({"status": "shutting_down"})
            shutdown_jetzt()
        elif self.path == "/api/command":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                post_data = self.rfile.read(content_length)
                payload = json.loads(post_data.decode("utf-8"))
                cmd = str(payload.get("command", ""))
            except (ValueError, UnicodeDecodeError):
                self.send_error(400)
                return
            with core.command_lock:
                core.web_command = cmd
            self._send_json({"status": "success"})
        elif self.path in ("/api/zeit/cancel", "/api/zeit/ack", "/api/open"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self.send_error(400)
                return
            if self.path == "/api/zeit/ack":
                self._send_json({"ok": True, "beendet": planer.quittieren()})
            elif self.path == "/api/zeit/cancel":
                ok = planer.entfernen(str(payload.get("id", "")))
                if ok:
                    add_matrix_log("[Zeit]: Eintrag im HUD gelöscht.")
                self._send_json({"ok": ok})
            else:
                self._send_json(open_vault_node(str(payload.get("id", ""))))
        elif self.path == "/api/verknuepfen":
            try:
                length = int(self.headers.get("Content-Length", 0))
                p = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                a, b, ja = str(p.get("a", "")), str(p.get("b", "")), bool(p.get("ja"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                self.send_error(400)
                return
            ok, text = verknuepfung.hud_aktion(a, b, ja)
            add_matrix_log(f"[Verknüpfung]: {text}")
            if ok and ja:
                _nach_verknuepfen()
            self._send_json({"ok": ok, "text": text})
        elif self.path == "/api/mic":
            # Optional {"muted": true/false}; ohne Angabe wird umgeschaltet
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (ValueError, UnicodeDecodeError):
                self.send_error(400)
                return
            want = payload.get("muted") if isinstance(payload, dict) else None
            if want is None:
                want = not mic_muted.is_set()
            if want:
                mic_muted.set()
                add_matrix_log("[Voice]: Mikrofon STUMM - JARVIS hört nicht mehr zu.")
            else:
                mic_muted.clear()
                add_matrix_log("[Voice]: Mikrofon wieder AKTIV.")
            self._send_json({"muted": mic_muted.is_set()})
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        return


def open_vault_node(node_id):
    """Klick auf einen Knoten im Graphen: öffnet die Notiz in Obsidian bzw. die PDF-Datei.
    Nur Dateien INNERHALB des Archivs sind erlaubt."""
    vault = os.path.abspath(config.OBSIDIAN_VAULT_PATH)
    pfad = os.path.abspath(os.path.join(vault, node_id.replace("/", os.sep)))
    if not node_id or os.path.commonpath([vault, pfad]) != vault or not os.path.isfile(pfad):
        return {"ok": False}
    try:
        if pfad.lower().endswith(".md"):
            notizen.oeffne(pfad)
        else:
            os.startfile(pfad)
        add_matrix_log(f"[Archiv]: Öffne {os.path.basename(pfad)}")
        return {"ok": True}
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")
        return {"ok": False}


def run_server(port=8080):
    server_address = ("127.0.0.1", port)
    # ThreadingHTTPServer: Das HUD fragt /api/data und /api/logs parallel ab,
    # der einfache HTTPServer würde sich dabei gegenseitig blockieren.
    httpd = ThreadingHTTPServer(server_address, JARVISHUDRequestHandler)
    httpd.daemon_threads = True
    add_matrix_log(f"🚀 JARVIS HUD aktiv: http://127.0.0.1:{port}")
    httpd.serve_forever()


# ----------------------------- Befehlsverarbeitung -----------------------------
def start_program(exe_path, name):
    if os.path.exists(exe_path):
        speak(f"Öffne {name}.")
        subprocess.Popen([exe_path])
    else:
        add_matrix_log(f"[Kern Fehler]: {name} nicht gefunden: {exe_path}")
        speak(f"{name} wurde nicht gefunden.")


def handle_contact_search(cmd_clean):
    set_status("SEARCHING")
    such_name = extract_search_term(cmd_clean)

    if len(such_name) < 2:
        speak(_mit_name("Ich habe keinen gültigen Namen verstanden"))
        return

    add_matrix_log(f"[Archiv]: Universelle Kontaktsuche für -> '{such_name}'")
    notiz_pfad = find_contact_note(such_name)

    if not notiz_pfad:
        add_matrix_log(f"[Archiv]: Kein Kontakt für '{such_name}'.")
        speak(_mit_name(f"Ich konnte keinen Kontakt für {such_name} finden"))
        return

    try:
        with open(notiz_pfad, "r", encoding="utf-8") as f:
            inhalt = f.read()
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")
        set_status("ONLINE")
        return

    nummer = find_phone_number(inhalt)
    basename = os.path.basename(notiz_pfad)
    if nummer:
        add_matrix_log(f"[Archiv]: {basename} -> Nummer: {nummer}")
        speak(f"Ich habe den Kontakt gefunden. Die Nummer lautet: {nummer}")
    else:
        add_matrix_log(f"[Archiv Warnung]: Keine Nummer in {basename}.")
        zeilen = [z.strip() for z in inhalt.split("\n") if z.strip()][:2]
        speak("Ich habe die Notiz gefunden, aber keine Nummer. Dort steht: " + " ".join(zeilen))


wissen_lock = threading.Lock()     # es läuft immer nur eine Suche gleichzeitig


def handle_knowledge(art, query, frage):
    """Obsidian-/Websuche. Läuft im eigenen Thread, weil Suche + Qwen 10-60 s dauern können -
    JARVIS bleibt währenddessen bedienbar (Lautstärke, Mikro, Abschalten ...)."""
    label = "Obsidian" if art == "obsidian" else "Web"
    if not query:
        speak("Wonach soll ich suchen?")
        return
    if not wissen_lock.acquire(blocking=False):
        speak("Ich bin noch mit der letzten Suche beschäftigt.")
        return
    try:
        wo = ("im Lexikon" if wissen._bereich(frage) else "in Obsidian") if art == "obsidian" else "im Web"
        add_matrix_log(f"[{label}]: Suche nach '{query}' ...")
        speak(f"Ich suche {wo} nach {query[:80]}.")
        set_status("SEARCHING")
        antwort, quellen = wissen.beantworte(art, query, frage, log=add_matrix_log)
        for q in quellen:
            add_matrix_log(f"[{label}] {q}")
        add_matrix_log(f"[{label}]: {antwort}")
        speak(antwort)
    except Exception as e:
        add_matrix_log(f"[Wissen Fehler]: {e}")
        speak("Bei der Suche ist ein Fehler aufgetreten.")
    finally:
        wissen_lock.release()
        set_status("ONLINE")


def handle_notizen(intent):
    """Obsidian-Notizen/Listen. Eigener Thread, weil das Vorlesen langer Notizen dauert."""
    try:
        erg = notizen.fuehre_aus(intent)
    except notizen.NotizFehler as e:
        add_matrix_log(f"[Notizen Fehler]: {e}")
        speak(str(e))
        return
    except Exception as e:
        add_matrix_log(f"[Notizen Fehler]: {e}")
        speak("Das hat leider nicht geklappt.")
        return
    if erg["oeffne"]:
        try:
            notizen.oeffne(erg["oeffne"])
        except Exception as e:
            add_matrix_log(f"[Notizen Fehler]: {e}")
            erg["sprich"] = "Ich konnte Obsidian nicht öffnen."
    if erg["still"]:
        add_matrix_log("[Notizen]: Nichts Passendes zum Abhaken - ignoriert.")
        return
    if erg["sprich"]:
        add_matrix_log(f"[Notizen]: {erg['sprich']}")
        speak(erg["sprich"])
    if erg["lesen"]:
        lesen_stop.clear()
        lesen_aktiv.set()
        try:
            for stueck in erg["lesen"]:
                if lesen_stop.is_set():
                    add_matrix_log("[Notizen]: Vorlesen abgebrochen.")
                    break
                speak(stueck)
        finally:
            lesen_aktiv.clear()


agent_lock = threading.Lock()      # es läuft immer nur eine Agent-Anfrage gleichzeitig


def handle_agent(cmd):
    """Freie Sätze: Qwen wählt eine Funktion oder antwortet. Läuft im eigenen Thread (2-10 s)."""
    if not agent_lock.acquire(blocking=False):
        speak("Einen Moment, ich bin noch beim letzten Satz.")
        return
    try:
        set_status("PROCESSING")
        antwort = agent.antworte(cmd)
        add_matrix_log(f"[Agent]: {antwort}")
        if antwort:
            speak(antwort)
    except Exception as e:
        add_matrix_log(f"[Agent Fehler]: {e}")
        speak("Dabei ist etwas schiefgegangen.")
    finally:
        agent_lock.release()
        set_status("ONLINE")


def handle_rueckfrage(cmd):
    try:
        antwort = agent.pending_antwort(cmd)
    except Exception as e:
        add_matrix_log(f"[Agent Fehler]: {e}")
        antwort = "Das hat leider nicht geklappt."
    if antwort:
        add_matrix_log(f"[Agent]: {antwort}")
        speak(antwort)
        return True
    return False


def handle_speeddial(intent):
    """Speed Dial: Seite, Gruppe oder alle Seiten einer Gruppe im Browser öffnen."""
    try:
        antwort = speeddial.fuehre_aus(intent)
    except Exception as e:
        add_matrix_log(f"[Speed Dial Fehler]: {e}")
        antwort = "Das hat leider nicht geklappt."
    add_matrix_log(f"[Speed Dial]: {antwort}")
    speak(antwort)


def handle_briefing():
    try:
        text = proaktiv.briefing_text()
    except Exception as e:
        add_matrix_log(f"[Briefing Fehler]: {e}")
        text = "Das Briefing konnte ich gerade nicht zusammenstellen."
    add_matrix_log(f"[Briefing]: {text}")
    speak(text)


def sprich_proaktiv(text):
    """Von Jarvis selbst ausgelöste Meldung (nur, wenn gerade nicht gesprochen wird)."""
    if not speaking.is_set():
        speak(text)


def handle_wetter(intent):
    try:
        antwort = wetter.fuehre_aus(intent)
    except Exception as e:
        add_matrix_log(f"[Wetter Fehler]: {e}")
        antwort = "Das habe ich gerade nicht herausgefunden."
    add_matrix_log(f"[Info]: {antwort}")
    speak(antwort)


_STT_FIX = [(re.compile(r"^((?:jarvis[ ,]+)?)(?:merkt|merk|merke)\s+(?:ihr|dir|euch|mir|die)\b", re.I), r"\1merk dir"),
            (re.compile(r"^((?:jarvis[ ,]+)?)(?:l(?:ö|oe)cher|l(?:ö|oe)scher|lösch)\b", re.I), r"\1lösche")]


def stt_korrigieren(text):
    """Typische Whisper-Verhörer am Satzanfang ('Merkt ihr' -> 'Merk dir', 'Löcher' -> 'Lösche')."""
    for p, ersatz in _STT_FIX:
        text = p.sub(ersatz, text, count=1)
    return text


def process_core_command(cmd, addressed=True):
    """addressed=False: per Sprache OHNE 'Jarvis' gesagt -> bei unbekanntem Befehl still bleiben."""
    cmd = stt_korrigieren(cmd)
    cmd_clean = cmd.strip().lower()
    if not cmd_clean:
        return
    # Fragen direkt an Jarvis ("Was weißt du über mich?") gelten auch ohne "Jarvis" als angesprochen
    if not addressed and re.match(r"^(?:was|wie viel|wieviel)\s+(?:weisst|weißt|kennst|merkst)\s+du\b.*\b(?:mich|mir)\b", cmd_clean):
        addressed = True
    add_matrix_log(f"[Kern]: Befehl -> '{cmd}'")

    # 1) Klingelnder Timer/Wecker: "Danke", "Okay", "Stopp" beendet ihn (auch ohne 'Jarvis')
    if planer.klingelt() and is_ack(cmd):
        n = planer.quittieren()
        add_matrix_log(f"[Zeit]: {n} Erinnerung(en) quittiert.")
        return

    # 2) Vorlesen einer Notiz abbrechen
    if lesen_aktiv.is_set() and is_stop_lesen(cmd):
        lesen_stop.set()
        return

    # 2a) Rückfrage zu Geburtstagen/Jubiläen ("... wirklich löschen?")
    if termine.hat_rueckfrage():
        antwort = termine.pending_antwort(cmd)
        if antwort:
            add_matrix_log(f"[Termine]: {antwort}")
            speak(antwort)
            return

    # 2a00) "Was habe ich verpasst?" / "Was ist neu?" - das Neue seit dem letzten Mal
    if startbriefing.erkenne(cmd):
        antwort = startbriefing.fuehre_aus()
        add_matrix_log(f"[Startbriefing]: {antwort}")
        speak(antwort)
        return

    # 2a000) Müllabfuhr: "Welche Tonne kommt morgen?", "Wann kommt die gelbe Tonne?"
    mi = muell.erkenne(cmd)
    if mi:
        antwort = muell.fuehre_aus(mi)
        add_matrix_log(f"[Müll]: {antwort}")
        speak(antwort)
        return

    # 2a0) Link-Vorschläge zwischen Obsidian-Notizen ("Welche Verknüpfungen schlägst du vor?")
    if verknuepfung.erkenne(cmd):
        def _vk():
            try:
                antwort = verknuepfung.frage_stellen(agent.rueckfrage_setzen)
            except Exception as e:
                add_matrix_log(f"[Verknüpfung Fehler]: {e}")
                antwort = "Das hat leider nicht geklappt."
            add_matrix_log(f"[Verknüpfung]: {antwort}")
            speak(antwort)
        threading.Thread(target=_vk, daemon=True).start()
        return

    # 2a1) Google-Kalender: "Welche Termine habe ich morgen?", "Wann ist mein nächster Termin?", "Ist morgen Feiertag?"
    ki = kalender.erkenne(cmd)
    if ki:
        antwort = kalender.fuehre_aus(ki)
        if antwort:
            add_matrix_log(f"[Kalender]: {antwort}")
            speak(antwort)
            return

    # 2a2) Geburtstage und Jubiläen: eintragen, abfragen, löschen
    ti = termine.erkenne(cmd)
    if ti:
        antwort = termine.fuehre_aus(ti)
        if antwort:
            add_matrix_log(f"[Termine]: {antwort}")
            speak(antwort)
            return

    # 2a3) Rückfrage vom Speed Dial ("Meinst du A oder B?")
    if speeddial.hat_rueckfrage():
        antwort = speeddial.pending_antwort(cmd)
        if antwort:
            add_matrix_log(f"[Speed Dial]: {antwort}")
            speak(antwort)
            return

    # 2b) Offene Rückfrage von Jarvis ("Soll ich ... wirklich ausschalten?") -> Ja/Nein
    if agent.hat_rueckfrage():
        def _rf():
            if not handle_rueckfrage(cmd):
                process_core_command(cmd, addressed)          # war keine Antwort -> ganz normal weiter
        threading.Thread(target=_rf, daemon=True).start()
        return

    # 3) Timer, Wecker, Erinnerungen
    zi = zeit.erkenne(cmd)
    if zi:
        antwort = zeit.fuehre_aus(zi, planer)
        add_matrix_log(f"[Zeit]: {antwort}")
        speak(antwort)
        return

    # 4) Obsidian-Notizen, Aufgaben- und Einkaufsliste
    ni = notizen.erkenne(cmd)
    if ni:
        threading.Thread(target=handle_notizen, args=(ni,), daemon=True).start()
        return

    # 4b3) Speed Dial: "Öffne MPG X570", "Öffne Gruppe Hardware" (nur wenn eine Kachel wirklich passt)
    sd = speeddial.erkenne(cmd)
    if sd:
        threading.Thread(target=handle_speeddial, args=(sd,), daemon=True).start()
        return

    # 4c) Briefing ("Guten Morgen Jarvis", "Was liegt heute an?")
    if proaktiv.ist_briefing(cmd, addressed):
        threading.Thread(target=handle_briefing, daemon=True).start()
        return

    # 5) Uhrzeit, Datum, Wetter
    wi = wetter.erkenne(cmd)
    if wi:
        threading.Thread(target=handle_wetter, args=(wi,), daemon=True).start()
        return

    # 6) Such-Befehle ("suche in Obsidian nach ...", "suche im Web nach ...")
    intent = wissen.detect_intent(cmd_clean)
    if intent:
        if intent[0] == "obsidian" and _rezept_pdf_oeffnen(cmd_clean, intent[1]):
            return
        threading.Thread(target=handle_knowledge, args=(intent[0], intent[1], cmd_clean),
                         daemon=True).start()
        return

    if has_word(cmd_clean, "lautstärke", "volume"):
        set_status("AUDIO-AUSGABE")
        try:
            voice.extract_and_set_volume(cmd)
        finally:
            set_status("ONLINE")

    elif has_word(cmd_clean, "scan", "aktualisier"):
        set_status("SCANNING")
        try:
            archiv.scan_obsidian_vault()
        finally:
            set_status("ONLINE")
        threading.Thread(target=semantik.aktualisieren, args=(add_matrix_log,), daemon=True).start()
        speak("Archiv aktualisiert.")

    elif has_word(cmd_clean, "obsidian"):  # auch "öffne Obsidian", "starte Obsidian" ...
        start_program(config.OBSIDIAN_EXE_PATH, "Obsidian")

    elif has_word(cmd_clean, "anycubic", "slicer"):
        start_program(config.ANYCUBIC_EXE_PATH, "Anycubic")

    elif has_word(cmd_clean, "youtube"):
        speak("Öffne YouTube.")
        webbrowser.open("https://youtube.com")

    elif is_shutdown_command(cmd_clean, addressed):
        shutdown_jetzt()

    elif any(w in cmd_clean for w in ("nummer", "telefon", "anrufen", "mobil")):
        handle_contact_search(cmd_clean)

    elif "öffne" in cmd_clean or "pdf" in cmd_clean or "anleitung" in cmd_clean:
        such_begriff = extract_search_term(cmd_clean)
        pdf_pfad = archiv.find_pdf_in_vault(such_begriff) if such_begriff.strip() else None
        if not such_begriff.strip():
            if addressed:
                speak("Was soll ich öffnen?")
            else:
                add_matrix_log("[Kern]: Öffnen ohne Namen - ignoriert.")
        elif pdf_pfad and os.path.exists(pdf_pfad):
            speak(f"Öffne {such_begriff}.")
            os.startfile(pdf_pfad)
        elif "öffne" in cmd_clean and _notiz_oeffnen(such_begriff):
            pass                                  # kein PDF, aber eine Notiz mit dem Namen gefunden
        elif addressed:
            speak("Nicht gefunden.")
        else:
            add_matrix_log(f"[Kern]: '{such_begriff}' nicht gefunden - ignoriert.")

    elif getattr(config, "AGENT_AKTIV", True) and (addressed or getattr(config, "AGENT_OHNE_ANREDE", False) or agent.im_gespraech()):
        threading.Thread(target=handle_agent, args=(cmd,), daemon=True).start()
    elif addressed:
        speak("Befehl registriert.")
    else:
        add_matrix_log("[Kern]: Kein bekannter Befehl - ignoriert.")


REZEPT_WOERTER = ("kochbuch", "rezept")


def _rezept_pdf_oeffnen(cmd_clean, query):
    """'Suche im Kochbuch nach Burger Rind': passt GENAU EIN PDF (z.B. 'Burger mit Rind.pdf'), wird es geöffnet.
    Passen mehrere (nur 'Burger'), gibt es False zurück -> dann läuft die normale Notizsuche mit der Liste."""
    if not any(w in cmd_clean for w in REZEPT_WOERTER):
        return False
    woerter = [w for w in re.findall(r"[\wäöüß]+", query.lower()) if len(w) > 1 and w not in ("mit", "und")]
    vault = config.OBSIDIAN_VAULT_PATH
    if not woerter or not os.path.exists(vault):
        return False
    treffer = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            name = f.lower()
            if name.endswith(".pdf") and all(w in name for w in woerter):
                treffer.append(os.path.join(root, f))
    if len(treffer) != 1:
        return False
    titel = os.path.splitext(os.path.basename(treffer[0]))[0]
    add_matrix_log(f"[Archiv]: Öffne Rezept {os.path.basename(treffer[0])}")
    speak(f"Öffne das Rezept {titel}.")
    try:
        os.startfile(treffer[0])
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")
        speak("Ich konnte das Rezept nicht öffnen.")
    return True


def _notiz_oeffnen(such_begriff):
    """'Öffne Index Kochbuch': kein PDF gefunden -> Obsidian-Notiz mit passendem Namen öffnen.
    Nur bei guter Übereinstimmung, damit nicht irgendeine Notiz aufgeht."""
    try:
        n = notizen.finde_notiz(such_begriff)
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")
        return False
    if not n or n["score"] < 0.75:
        return False
    add_matrix_log(f"[Archiv]: Öffne {n['title']}.md")
    speak(f"Öffne die Notiz {n['title']}.")
    try:
        notizen.oeffne(n["path"])
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")
        speak("Ich konnte Obsidian nicht öffnen.")
    return True


# ----------------------------- Spracherkennung -----------------------------
def _erkennung_worker(audio_q):
    """Wandelt Aufnahmen in Text um, ohne dass das Mikrofon so lange taub ist:
    die Aufnahme-Schleife hört sofort weiter zu, während hier (langsames) Whisper läuft."""
    while True:
        audio = audio_q.get()
        try:
            if getattr(core, "aktueller_status", "ONLINE") == "ONLINE":
                set_status("ERKENNUNG")           # Ring zeigt: Aufnahme wird gerade in Text umgewandelt
            try:
                text = voice.transcribe(audio)
            finally:
                if getattr(core, "aktueller_status", "") == "ERKENNUNG":
                    set_status("ONLINE")
            if is_own_echo(text):
                add_matrix_log("[Voice]: Eigenes Echo ignoriert.")
                continue
            add_matrix_log(f"[Voice]: Erkannt -> '{text}'")

            text_lower = text.lower()
            wake_words = [w.lower() for w in getattr(config, "WAKE_WORDS", ["jarvis"])]
            angesprochen = any(w in text_lower for w in wake_words)
            # Groß-/Kleinschreibung bleibt erhalten (für Notizen, Orte, Erinnerungstexte)
            befehl = text
            for w in wake_words:
                befehl = re.sub(re.escape(w), " ", befehl, flags=re.IGNORECASE)
            befehl = " ".join(befehl.split()).strip(" ,.;:!?-")

            if angesprochen and not befehl:
                speak(_mit_name("Ich höre dich"))
            elif befehl and (angesprochen or not getattr(config, "WAKE_WORD_REQUIRED", False)):
                voice_queue.put((befehl, angesprochen))
            elif befehl:
                add_matrix_log("[Voice]: Ignoriert (Ansprache 'Jarvis' fehlt).")
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            add_matrix_log(f"[Voice Fehler]: Spracherkennung nicht erreichbar: {e}")
        except Exception as e:
            add_matrix_log(f"[Voice Fehler]: {e}")


def voice_recognition_loop():
    audio_q = queue.Queue(maxsize=4)
    threading.Thread(target=_erkennung_worker, args=(audio_q,), daemon=True).start()
    gewartet = 0.0
    while not voice.mikrofon_bereit:
        time.sleep(0.5)
        gewartet += 0.5
        if gewartet == 15.0:
            add_matrix_log("[Voice Warnung]: Mikrofon nach 15 s noch nicht bereit. "
                           "Steuerung nur über das HUD möglich.")
    add_matrix_log("[Voice]: Aktiv. Lausche...")

    with sr.Microphone() as source:
        while True:
            try:
                if mic_muted.is_set():
                    time.sleep(0.3)   # stumm: gar nicht erst zuhören
                    continue
                audio = voice.recognizer.listen(source, timeout=None, phrase_time_limit=None)
                if mic_muted.is_set():
                    continue          # während des Zuhörens stumm geschaltet -> verwerfen
                # JARVIS soll sich nicht selbst zuhören. listen() kehrt erst NACH dem Satzende
                # zurück - die Aufnahme kann also noch Sprachausgabe enthalten, obwohl JARVIS
                # schon fertig ist. Darum wird geprüft, ob die Aufnahme zeitlich überlappt.
                t_ende = time.time()
                try:
                    dauer = len(audio.frame_data) / float(audio.sample_rate * audio.sample_width)
                except Exception:
                    dauer = 5.0
                if speaking.is_set() or last_speak_end > t_ende - dauer - 1.0:
                    continue
                audio_q.put(audio)
            except sr.UnknownValueError:
                pass  # nichts verstanden, normal
            except sr.RequestError as e:
                add_matrix_log(f"[Voice Fehler]: Spracherkennung nicht erreichbar: {e}")
                time.sleep(2)
            except Exception as e:
                add_matrix_log(f"[Voice Fehler]: {e}")
                time.sleep(1)


# ----------------------------- Hauptschleife -----------------------------
def _nach_verknuepfen():
    """Nach einem neuen Link: Archiv-Graph und Notizindex neu einlesen (im Hintergrund)."""
    def lauf():
        try:
            archiv.scan_obsidian_vault()
        except Exception as e:
            add_matrix_log(f"[Archiv Fehler]: {e}")
        semantik.aktualisieren(add_matrix_log)
    threading.Thread(target=lauf, daemon=True).start()


agent._nach_verknuepfen = _nach_verknuepfen


def delayed_initial_scan():
    """Scannt das Obsidian-Archiv kurz nach dem Start, damit der Baum im HUD gefüllt wird."""
    time.sleep(1.0)
    try:
        archiv.scan_obsidian_vault()
    except Exception as e:
        add_matrix_log(f"[Archiv Fehler]: {e}")


def cleanup_old_audio():
    """Löscht alte temporäre Sprachdateien (wie früher in jarvis.py)."""
    try:
        for f in glob.glob(os.path.join(config.PROJEKT_DIR, "jarvis_*.mp3")):
            os.remove(f)
    except OSError:
        pass


def main_system_loop():
    cleanup_old_audio()
    set_status("ONLINE")
    core.aktueller_text = "System initialisiert."

    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=delayed_initial_scan, daemon=True).start()

    # WICHTIG: Ohne diesen Aufruf wird voice.mikrofon_bereit nie True,
    # und voice_recognition_loop wartet für immer.
    voice.init_microphone_async()
    voice.init_stt_async()        # lädt Whisper im Hintergrund (nur wenn STT_ENGINE = "whisper")
    planer.starten()
    agent.verbinde(planer, add_matrix_log)
    semantik.starten(add_matrix_log)
    muell.starten(add_matrix_log)
    gedaechtnis.starten(add_matrix_log,
                        darf=lambda: not speaking.is_set() and not agent_lock.locked(),
                        danach=lambda: threading.Thread(target=semantik.aktualisieren, args=(add_matrix_log,), daemon=True).start())
    startbriefing.starten(sprich_proaktiv, add_matrix_log,
                          darf_sprechen=lambda: not mic_muted.is_set() and not speaking.is_set())
    proaktiv.starten(sprich_proaktiv, add_matrix_log,
                     darf_sprechen=lambda: not mic_muted.is_set() and not speaking.is_set(), planer=planer)
    kalender.starten(add_matrix_log)
    startcheck.starten(speak, add_matrix_log)
    synology_bot.starten(voice, voice_queue, add_matrix_log)
    speeddial.vorladen()                     # Speed-Dial-Liste schon einmal vom NAS holen
    threading.Thread(target=voice_recognition_loop, daemon=True).start()
    add_matrix_log("[Kern]: Hauptschleife läuft.")

    try:
        while True:
            cmd, addressed = None, True
            with core.command_lock:
                if core.web_command:
                    cmd = core.web_command
                    core.web_command = None
            if cmd is None:
                try:
                    cmd, addressed = voice_queue.get_nowait()
                except queue.Empty:
                    pass
            if cmd:
                try:
                    process_core_command(cmd, addressed)
                except Exception as e:
                    add_matrix_log(f"[Kern Fehler]: {e}")
                    set_status("ONLINE")
            time.sleep(0.2)
    except KeyboardInterrupt:
        add_matrix_log("[Kern]: Beendet (Strg+C).")


if __name__ == "__main__":
    main_system_loop()
