"""Synology Chat Bot: Jarvis per Handy Befehle schicken.

So funktioniert es:
- Du schreibst dem Bot "Jarvis" in der Synology-Chat-App.
- Das NAS schickt die Nachricht an diesen kleinen Empfänger (Port 8082, nur im Heimnetz).
- Jarvis führt den Befehl ganz normal aus. Alles, was Jarvis danach sagt, wird als Text
  zurück in den Chat geschickt.
- Nur Nachrichten mit dem richtigen Bot-Schlüssel (Token) UND von erlaubten Personen (user_id) werden angenommen.

Einstellungen in config.py (diese Datei wird NIE hochgeladen):
    SYNOLOGY_BOT_AKTIV    = True
    SYNOLOGY_BOT_TOKEN    = "..."                       # aus den Bot-Einstellungen im Synology Chat
    SYNOLOGY_CHAT_URL     = "http://192.168.X.X:5000"  # Adresse deines NAS im Heimnetz
    SYNOLOGY_BOT_USER_IDS = []                          # erst leer lassen, der Bot verrät dir deine Nummer
    SYNOLOGY_BOT_PORT     = 8082
    SYNOLOGY_BOT_SPRECHEN = False                       # True = Jarvis sagt die Antwort zu Hause zusätzlich laut
Optional:
    SYNOLOGY_NAS_IP       = "192.168.X.X"            # nur Anfragen von dieser Adresse annehmen
    SYNOLOGY_SSL_PRUEFEN  = False                       # nur nötig bei https:// mit eigenem NAS-Zertifikat
    SYNOLOGY_BOT_INCOMING_URL = "..."                   # komplette Antwort-Adresse, falls die automatische nicht passt
"""
import hmac
import json
import queue
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config

AKTIV = bool(getattr(config, "SYNOLOGY_BOT_AKTIV", False))
TOKEN = str(getattr(config, "SYNOLOGY_BOT_TOKEN", "") or "").strip()
CHAT_URL = str(getattr(config, "SYNOLOGY_CHAT_URL", "") or "").strip().rstrip("/")
INCOMING_URL = str(getattr(config, "SYNOLOGY_BOT_INCOMING_URL", "") or "").strip()
USER_IDS = {int(x) for x in (getattr(config, "SYNOLOGY_BOT_USER_IDS", []) or [])}
PORT = int(getattr(config, "SYNOLOGY_BOT_PORT", 8082))   
SPRECHEN = bool(getattr(config, "SYNOLOGY_BOT_SPRECHEN", False))
NAS_IP = str(getattr(config, "SYNOLOGY_NAS_IP", "") or "").strip()
SSL_PRUEFEN = bool(getattr(config, "SYNOLOGY_SSL_PRUEFEN", True))
PFAD = "/synology"

WARTE_ERSTE = 90      # so lange (Sekunden) wartet der Bot nach einem Befehl auf die erste Antwort von Jarvis
NACHLAUF = 8          # nach der letzten Antwort noch so lange weitere Sätze mitschicken (z.B. beim Vorlesen)

_log = print
_befehle = None                   # die Befehls-Warteschlange von Jarvis (voice_queue)
_vorher = None                    # die ursprüngliche Sprachausgabe
_ausgang = queue.Queue()          # Nachrichten an das NAS (der Reihe nach)
_lock = threading.Lock()
_ziel = {"user": None, "start": 0.0, "letzte": 0.0, "n": 0}


# ----------------------------- Antworten zurück ans Handy -----------------------------
def _antwort_url():
    if INCOMING_URL:
        return INCOMING_URL
    # Der Token muss in Anführungszeichen stehen: token="abc" -> %22abc%22
    return (f"{CHAT_URL}/webapi/entry.cgi?api=SYNO.Chat.External&method=chatbot&version=2"
            f"&token={urllib.parse.quote(json.dumps(TOKEN))}")


def _sender():
    """Schickt die Nachrichten nacheinander ans NAS, damit die Reihenfolge stimmt."""
    ctx = None if SSL_PRUEFEN else ssl._create_unverified_context()
    while True:
        user_id, text = _ausgang.get()
        payload = json.dumps({"text": text, "user_ids": [int(user_id)]}, ensure_ascii=False)
        daten = urllib.parse.urlencode({"payload": payload}).encode("utf-8")
        try:
            req = urllib.request.Request(_antwort_url(), data=daten, method="POST")
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                antwort = r.read().decode("utf-8", "replace")
            if '"success":true' not in antwort.replace(" ", ""):
                _log(f"[Synology Fehler]: Das NAS meldet: {antwort[:200]}")
        except Exception as e:
            _log(f"[Synology Fehler]: Antwort nicht gesendet: {e.__class__.__name__}: {e}")


def senden(user_id, text):
    if text:
        _ausgang.put((user_id, str(text)))


def aktiv():
    """True, solange Jarvis gerade einen Befehl vom Handy beantwortet."""
    with _lock:
        if _ziel["user"] is None:
            return False
        jetzt = time.time()
        if _ziel["n"] == 0:
            ok = jetzt - _ziel["start"] < WARTE_ERSTE
        else:
            ok = jetzt - _ziel["letzte"] < NACHLAUF
        if not ok:
            _ziel["user"] = None
        return ok


def _sprachausgabe(text):
    """Ersetzt die Sprachausgabe: Antworten auf Handy-Befehle gehen als Text in den Chat."""
    if aktiv():
        with _lock:
            uid = _ziel["user"]
            _ziel["letzte"] = time.time()
            _ziel["n"] += 1
        if uid is not None:
            senden(uid, text)
        if not SPRECHEN:
            return                # zu Hause bleibt es still
    _vorher(text)


# ----------------------------- Nachrichten vom Handy -----------------------------
def _ohne_weckwort(text):
    for w in getattr(config, "WAKE_WORDS", ["jarvis"]):
        text = re.sub(re.escape(w), " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" ,.;:!?-")


def _verarbeiten(uid, name, text):
    if not USER_IDS:
        _log(f"[Synology]: Nachricht von {name} (user_id {uid}). Es sind noch keine erlaubten Nummern eingetragen.")
        senden(uid, f"Hallo {name}! Deine Nummer ist {uid}. Trage in config.py ein: "
                    f"SYNOLOGY_BOT_USER_IDS = [{uid}] und starte Jarvis neu.")
        return
    if uid not in USER_IDS:
        _log(f"[Synology]: Nachricht von {name} (user_id {uid}) abgelehnt - nicht erlaubt.")
        return
    befehl = _ohne_weckwort(text)
    if not befehl:
        senden(uid, "Ich höre dich" + (", " + getattr(config, "USER_NAME", "") if getattr(config, "USER_NAME", "") else "") + ".")
        return
    _log(f"[Synology]: Befehl vom Handy -> '{befehl}'")
    with _lock:
        _ziel.update(user=uid, start=time.time(), letzte=0.0, n=0)
    _befehle.put((befehl, True))          # genau so, als hättest du "Jarvis, ..." gesagt


class _Empfaenger(BaseHTTPRequestHandler):
    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != PFAD:
            self.send_error(404)
            return
        if NAS_IP and self.client_address[0] != NAS_IP:
            _log(f"[Synology]: Anfrage von fremder Adresse {self.client_address[0]} abgelehnt.")
            self.send_error(403)
            return
        try:
            laenge = int(self.headers.get("Content-Length", 0))
            roh = self.rfile.read(min(laenge, 65536)).decode("utf-8", "replace")
        except ValueError:
            self.send_error(400)
            return
        if roh.lstrip().startswith("{"):
            try:
                felder = json.loads(roh)
            except ValueError:
                felder = {}
        else:
            felder = {k: v[0] for k, v in urllib.parse.parse_qs(roh).items()}
        token = str(felder.get("token", "")).strip().strip('"')
        if not hmac.compare_digest(token.encode("utf-8"), TOKEN.encode("utf-8")):
            _log("[Synology]: Nachricht mit falschem Schlüssel abgelehnt.")
            self.send_error(403)
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        try:
            uid = int(felder.get("user_id", ""))
        except (TypeError, ValueError):
            return
        _verarbeiten(uid, str(felder.get("username", "")), str(felder.get("text", "")).strip())

    def log_message(self, format, *args):
        return


class _Server(ThreadingHTTPServer):
    # Unter Windows darf sonst ein zweites Programm denselben Port belegen, ohne Fehlermeldung.
    # So meldet Jarvis sofort, wenn der Port schon besetzt ist.
    allow_reuse_address = False
    daemon_threads = True


def starten(voice_modul, befehl_queue, log=print):
    """Wird von hud_server.py beim Start aufgerufen."""
    global _log, _befehle, _vorher
    _log = log
    if not AKTIV:
        return
    if not TOKEN:
        log("[Synology]: Kein SYNOLOGY_BOT_TOKEN in config.py - Bot bleibt aus.")
        return
    if not (CHAT_URL or INCOMING_URL):
        log("[Synology]: Keine SYNOLOGY_CHAT_URL in config.py - Bot bleibt aus.")
        return
    _befehle = befehl_queue
    _vorher = voice_modul.jarvis_speak
    voice_modul.jarvis_speak = _sprachausgabe
    threading.Thread(target=_sender, daemon=True).start()

    def server():
        try:
            httpd = _Server(("0.0.0.0", PORT), _Empfaenger)
            log(f"[Synology]: Chat-Bot bereit (Port {PORT}).")
            httpd.serve_forever()
        except OSError as e:
            log(f"[Synology Fehler]: Port {PORT} ist belegt oder gesperrt: {e}")
    threading.Thread(target=server, daemon=True).start()
