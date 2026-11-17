import os
"""
wetter.py - Wetter (Open-Meteo, kostenlos, ohne Schlüssel), Uhrzeit und Datum

  "Wie spät ist es?"            "Welcher Tag ist heute?"
  "Wie wird das Wetter?"        "Wetter morgen"        "Brauche ich morgen einen Regenschirm?"
  "Wetter in Berlin"            "Wie warm ist es?"
"""

import re
import json
import time
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import config
from zeit import norm, uhr_sprech, _WOCHENTAGE_DE

_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"]

# WMO-Wettercodes -> (Text, Symbol für das HUD)
WMO = {0: ("klar", "☀"), 1: ("überwiegend klar", "🌤"), 2: ("teils bewölkt", "⛅"), 3: ("bedeckt", "☁"),
       45: ("Nebel", "🌫"), 48: ("Reifnebel", "🌫"),
       51: ("leichter Nieselregen", "🌦"), 53: ("Nieselregen", "🌦"), 55: ("starker Nieselregen", "🌧"),
       56: ("gefrierender Nieselregen", "🌧"), 57: ("gefrierender Nieselregen", "🌧"),
       61: ("leichter Regen", "🌦"), 63: ("Regen", "🌧"), 65: ("starker Regen", "🌧"),
       66: ("gefrierender Regen", "🌧"), 67: ("gefrierender Regen", "🌧"),
       71: ("leichter Schneefall", "🌨"), 73: ("Schneefall", "🌨"), 75: ("starker Schneefall", "❄"),
       77: ("Schneegriesel", "🌨"), 80: ("leichte Regenschauer", "🌦"), 81: ("Regenschauer", "🌧"),
       82: ("heftige Regenschauer", "⛈"), 85: ("Schneeschauer", "🌨"), 86: ("starke Schneeschauer", "❄"),
       95: ("Gewitter", "⛈"), 96: ("Gewitter mit Hagel", "⛈"), 99: ("Gewitter mit starkem Hagel", "⛈")}


def wmo(code):
    return WMO.get(code, ("unbeständig", "🌡"))


# ============================== Uhrzeit / Datum ==============================
def datum_sprech(jetzt=None):
    j = jetzt or datetime.now()
    return f"{_WOCHENTAGE_DE[j.weekday()]}, der {j.day}. {_MONATE[j.month - 1]} {j.year}"


def uhrzeit_antwort(jetzt=None):
    j = jetzt or datetime.now()
    return f"Es ist {uhr_sprech(j)}." if j.minute else f"Es ist {j.hour} Uhr."


def datum_antwort(jetzt=None):
    return f"Heute ist {datum_sprech(jetzt)}."


# ============================== HTTP / Cache ==============================
_lock = threading.Lock()
_cache = {}                      # schluessel -> (zeit, daten)
_geo_cache = {}


def _get_json(url, timeout=None):
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-HUD/1.0"})
    with urllib.request.urlopen(req, timeout=timeout or getattr(config, "WETTER_TIMEOUT", 8)) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(ort):
    """Ortsname -> (Name, Breite, Länge). Eigener Standort aus config, wenn ort leer."""
    if not ort:
        return (config.WETTER_ORT, config.WETTER_LAT, config.WETTER_LON)
    key = ort.lower()
    if key in _geo_cache:
        return _geo_cache[key]
    url = ("https://geocoding-api.open-meteo.com/v1/search?" +
           urllib.parse.urlencode({"name": ort, "count": 1, "language": "de", "format": "json"}))
    res = (_get_json(url).get("results") or [])
    if not res:
        raise LookupError(ort)
    r = res[0]
    _geo_cache[key] = (r.get("name", ort), r["latitude"], r["longitude"])
    return _geo_cache[key]


def _platte():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "wetter_cache.json")


def _platte_schreiben(key, daten):
    try:
        with open(_platte(), "w", encoding="utf-8") as f:
            json.dump({"key": list(key), "daten": daten}, f, ensure_ascii=False)
    except OSError:
        pass


def _platte_lesen(key, max_alter_s=12 * 3600):
    """Letzter erfolgreicher Abruf (für denselben Ort), höchstens 12 Stunden alt."""
    try:
        with open(_platte(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if tuple(d["key"]) == tuple(key) and time.time() - d["daten"]["stand"] < max_alter_s:
            return d["daten"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def hole(ort=None, force=False):
    """Holt Wetterdaten (gecacht). Gibt ein Dict zurück oder wirft eine Exception."""
    name, lat, lon = geocode(ort)
    key = (round(lat, 2), round(lon, 2))
    ttl = int(getattr(config, "WETTER_CACHE_MIN", 15)) * 60
    with _lock:
        c = _cache.get(key)
        if c and not force and time.time() - c[0] < ttl:
            return c[1]
    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}))
    raw = None
    for versuch in range(2):                              # ein kurzer Netzwerkaussetzer soll nicht gleich "nicht verfügbar" heißen
        try:
            raw = _get_json(url)
            break
        except Exception:
            if versuch == 1:
                with _lock:
                    c = _cache.get(key)
                if c and time.time() - c[0] < 6 * 3600:      # lieber der letzte Stand (max. 6 Std.) als gar nichts
                    return c[1]
                alt = _platte_lesen(key)                     # nach einem Neustart: Stand von der Festplatte
                if alt:
                    return alt
                raise
            time.sleep(1.5)
    cur, d = raw["current"], raw["daily"]
    tage = []
    for i, tag in enumerate(d["time"]):
        tage.append({"datum": tag, "code": d["weather_code"][i], "max": d["temperature_2m_max"][i],
                     "min": d["temperature_2m_min"][i], "regen": d.get("precipitation_probability_max", [None] * 9)[i]})
    daten = {"ort": name, "jetzt": {"temp": cur["temperature_2m"], "gefuehlt": cur.get("apparent_temperature"),
                                    "feuchte": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
                                    "regen_mm": cur.get("precipitation"), "code": cur["weather_code"]},
             "tage": tage, "stand": time.time()}
    with _lock:
        _cache[key] = (time.time(), daten)
    _platte_schreiben(key, daten)
    return daten


def hud_snapshot():
    """Kompaktes Dict fürs HUD (ohne Exceptions)."""
    try:
        d = hole()
    except Exception as e:
        return {"ok": False, "fehler": e.__class__.__name__}
    j = d["jetzt"]
    heute = d["tage"][0]
    return {"ok": True, "ort": d["ort"], "temp": round(j["temp"]), "text": wmo(j["code"])[0],
            "symbol": wmo(j["code"])[1], "max": round(heute["max"]), "min": round(heute["min"]),
            "regen": heute["regen"], "wind": round(j["wind"] or 0),
            "tage": [{"datum": t["datum"], "symbol": wmo(t["code"])[1], "max": round(t["max"]),
                      "min": round(t["min"]), "regen": t["regen"]} for t in d["tage"]]}


# ============================== Sprachausgabe ==============================
def _grad(x):
    return f"{round(x)} Grad"


def antwort(tag=0, ort=None, nur_jetzt=False, regenschirm=False):
    d = hole(ort)
    o = d["ort"]
    if nur_jetzt:
        j = d["jetzt"]
        s = f"In {o} sind es aktuell {_grad(j['temp'])}, {wmo(j['code'])[0]}."
        if j.get("gefuehlt") is not None and abs(j["gefuehlt"] - j["temp"]) >= 3:
            s += f" Gefühlt {_grad(j['gefuehlt'])}."
        return s
    t = d["tage"][min(tag, len(d["tage"]) - 1)]
    wort = ["heute", "morgen", "übermorgen"][min(tag, 2)]
    regen = t["regen"] or 0
    if regenschirm:
        if regen >= 50:
            return f"Ja, {wort} in {o} nimm einen Schirm mit, Regenwahrscheinlichkeit {regen} Prozent."
        if regen >= 25:
            return f"Vielleicht. {wort.capitalize()} in {o} liegt die Regenwahrscheinlichkeit bei {regen} Prozent."
        return f"Nein, {wort} in {o} bleibt es wahrscheinlich trocken, nur {regen} Prozent Regenwahrscheinlichkeit."
    s = (f"{wort.capitalize()} in {o}: {wmo(t['code'])[0]}, "
         f"zwischen {_grad(t['min'])} und {_grad(t['max'])}.")
    if regen >= 25:
        s += f" Regenwahrscheinlichkeit {regen} Prozent."
    if tag == 0:
        j = d["jetzt"]
        s += f" Aktuell {_grad(j['temp'])}."
    return s


# ============================== Befehlserkennung ==============================
_ORT = re.compile(r"\b(?:in|fur|von)\s+([a-z][a-z\- ]{2,30}?)(?=\s*(?:heute|morgen|ubermorgen|$|\?|\.|!))")


def erkenne(cmd):
    """Gibt {'art': 'uhrzeit'|'datum'|'wetter', ...} oder None zurück."""
    n = norm(cmd).strip(" .!?")
    if not n:
        return None
    if re.search(r"\b(?:timer|wecker|erinnere|erinnerung)", n):
        return None
    if re.match(r"^(?:wie\s+(?:spat|viel uhr)|wieviel uhr|sag(?:e)? (?:mir )?die (?:uhrzeit|zeit)|"
                r"(?:welche|was fur eine) uhrzeit|uhrzeit|wie ist die uhrzeit|wie viel uhr)", n) \
            or re.match(r"^(?:wie spat ist es|was ist die uhrzeit)", n):
        return {"art": "uhrzeit"}
    if re.match(r"^(?:welcher|was fur ein|welchen)\s+(?:tag|wochentag)\b", n) or \
       re.match(r"^(?:welches|was ist (?:das )?heutige|was fur ein)\s+datum", n) or \
       re.match(r"^(?:den )?wievielten (?:haben|ist)", n) or re.match(r"^(?:sag(?:e)? (?:mir )?(?:das )?datum|datum)$", n) or \
       re.match(r"^(?:welcher tag ist (?:heute|es)|welches datum haben wir|der wievielte ist heute)", n):
        return {"art": "datum"}
    if re.search(r"\b(?:wetter|regenschirm|schirm|regnet es|wird es regnen|temperatur drau|wie warm|wie kalt|"
                 r"grad drau|schneit es|wird es warm|wird es kalt)", n):
        if re.search(r"\b(?:duse|druckbett|hotend|filament|nozzle|bett)\w*", n):
            return None
        tag = 2 if "ubermorgen" in n else 1 if "morgen" in n else 0
        m = _ORT.search(re.sub(r"\b(?:das|dem|dort|drauen|draussen)\b", " ", n))
        ort = None
        if m:
            kandidat = m.group(1).strip()
            if kandidat not in ("der", "die", "den", "dem", "zukunft", "ordnung", "ruhe"):
                ort = kandidat
        # Original-Schreibweise für die Geokodierung verwenden
        if ort:
            mo = re.search(r"(?i)\b(?:in|für|fuer|von)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\- ]{2,30}?)"
                           r"(?=\s*(?:heute|morgen|übermorgen|ubermorgen|$|\?|\.|!))", cmd.strip())
            if mo:
                ort = mo.group(1).strip()
        return {"art": "wetter", "tag": tag, "ort": ort,
                "regenschirm": bool(re.search(r"\b(?:regenschirm|schirm|regnet|regnen)", n)),
                "nur_jetzt": bool(re.search(r"\b(?:wie warm|wie kalt|grad|temperatur)", n)) and tag == 0}
    return None


def fuehre_aus(intent, jetzt=None):
    art = intent["art"]
    if art == "uhrzeit":
        return uhrzeit_antwort(jetzt)
    if art == "datum":
        return datum_antwort(jetzt)
    try:
        return antwort(intent["tag"], intent.get("ort"), intent.get("nur_jetzt", False), intent.get("regenschirm", False))
    except LookupError:
        return f"Den Ort {intent.get('ort')} habe ich nicht gefunden."
    except Exception:
        return "Ich konnte das Wetter gerade nicht abrufen. Prüfe die Internetverbindung."
