"""
speeddial.py - JARVIS öffnet Seiten aus deinem NAS Speed Dial

Sprachbeispiele:
    "Öffne MPG X570"                       -> öffnet die Kachel "MPG X570 Gaming Pro Carbon Wifi"
    "Öffne X570 Carbon"                    -> Teilwörter reichen
    "Öffne Mindfactory bei Hardware"       -> sucht nur in der Gruppe "Hardware"
    "Öffne Gruppe Hardware"                -> zeigt die Gruppe im Speed Dial
    "Zeige Speed Dial" / "Öffne Speed Dial"
    "Öffne alle Seiten von Hardware"       -> alle Links der Gruppe als Tabs

So funktioniert es:
  * Jarvis liest die Datei speeddial.json direkt von der Synology (WebDAV) - dieselbe Datei wie die Erweiterung.
    Sie wird ein paar Minuten zwischengespeichert, damit es schnell geht.
  * Der Name muss nicht vollständig sein. Kleine Hörfehler werden toleriert.
  * Zahlen (Modellnummern) müssen genau passen: "X570" öffnet nie das "X670"-Board.
  * Passen mehrere Kacheln gleich gut, fragt Jarvis nach: "Meinst du A oder B?"
  * Findet Jarvis nichts Passendes, geht der Befehl ganz normal weiter (z. B. an die PDF-Suche).
  * Gesprächskontext: Kacheln aus der Gruppe, die zuletzt benutzt wurde, werden leicht bevorzugt.

Einstellungen in config.py (siehe config_beispiel.py):
    SPEEDDIAL_NAS_URL, SPEEDDIAL_NAS_ORDNER, SPEEDDIAL_NAS_USER, SPEEDDIAL_NAS_PASS, SPEEDDIAL_ERWEITERUNG_ID
"""

import os
import re
import ssl
import json
import time
import base64
import difflib
import threading
import subprocess
import webbrowser
import urllib.parse
import urllib.request

import config

# ============================== Einstellungen ==============================
AKTIV = bool(getattr(config, "SPEEDDIAL_AKTIV", True))
NAS_URL = str(getattr(config, "SPEEDDIAL_NAS_URL", "") or "").strip().rstrip("/")
NAS_ORDNER = "/" + str(getattr(config, "SPEEDDIAL_NAS_ORDNER", "/SpeedDial") or "/SpeedDial").strip("/")
NAS_USER = str(getattr(config, "SPEEDDIAL_NAS_USER", "") or "")
NAS_PASS = str(getattr(config, "SPEEDDIAL_NAS_PASS", "") or "")
ERWEITERUNG_ID = str(getattr(config, "SPEEDDIAL_ERWEITERUNG_ID", "") or "").strip()
CHROME_PFAD = str(getattr(config, "SPEEDDIAL_CHROME", "") or "").strip()
CACHE_S = int(getattr(config, "SPEEDDIAL_CACHE_S", 120))          # so lange gilt die gelesene Liste als frisch
SCHWELLE = float(getattr(config, "SPEEDDIAL_SCHWELLE", 0.78))     # ab hier gilt eine Kachel als Treffer
# Wörter, die zu anderen Jarvis-Befehlen gehören - die soll der Speed Dial nicht "wegschnappen"
IGNORIEREN = set(getattr(config, "SPEEDDIAL_IGNORIEREN",
                         ("obsidian", "anycubic", "slicer", "pdf", "anleitung", "notiz",
                          "timer", "wecker", "erinnerung")))
# ("Öffne Haus" und "Öffne den Chat" prüft Jarvis schon vorher - die kommen hier gar nicht an.)

_cache = {"daten": None, "zeit": 0.0, "fehler": ""}
_cache_lock = threading.Lock()
_letzte_gruppe = {"id": None, "zeit": 0.0}     # Gesprächskontext
_rueckfrage = None                             # {"kandidaten": [...], "ablauf": ts}


class SpeedDialFehler(Exception):
    pass


# ============================== Text-Helfer ==============================
_UML = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})

# Füllwörter, die beim Suchen nicht zählen
_FUELL = {"bitte", "mal", "mir", "uns", "die", "der", "das", "den", "dem", "des", "ein", "eine", "einen",
          "seite", "seiten", "webseite", "website", "homepage", "link", "links", "kachel", "von", "vom",
          "im", "in", "auf", "zu", "zur", "zum", "fur", "jarvis", "doch", "jetzt", "gleich", "nochmal",
          "noch", "einmal", "speed", "dial", "speeddial"}


def _fold(s):
    """Klein, ohne Umlaute, nur Buchstaben/Ziffern mit Leerzeichen."""
    s = str(s).lower().translate(_UML)
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _woerter(s):
    """Wörter; einzelne Buchstaben vor einer Zahl werden angehängt: "x 570" -> "x570", "b 650" -> "b650"."""
    roh = _fold(s).split()
    aus = []
    for w in roh:
        if aus and len(aus[-1]) <= 2 and aus[-1].isalpha() and w.isdigit():
            aus[-1] += w
        else:
            aus.append(w)
    return aus


def _kompakt(s):
    return _fold(s).replace(" ", "")


def _hat_ziffer(w):
    return any(c.isdigit() for c in w)


# ============================== Daten vom NAS ==============================
def _datei_url():
    pfad = NAS_ORDNER + "/speeddial.json"
    return NAS_URL + "/".join(urllib.parse.quote(t) for t in pfad.split("/"))


def _laden():
    if not NAS_URL or not NAS_USER:
        raise SpeedDialFehler("Der Speed Dial ist in der config.py noch nicht eingerichtet.")
    req = urllib.request.Request(_datei_url())
    zugang = base64.b64encode(f"{NAS_USER}:{NAS_PASS}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", "Basic " + zugang)
    ctx = None
    if NAS_URL.startswith("https://") and not getattr(config, "SPEEDDIAL_SSL_PRUEFEN", True):
        ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            daten = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SpeedDialFehler("Die Anmeldung am NAS für den Speed Dial ist fehlgeschlagen.")
        if e.code == 404:
            raise SpeedDialFehler("Die Speed-Dial-Datei habe ich auf dem NAS nicht gefunden.")
        raise SpeedDialFehler(f"Das NAS meldet Fehler {e.code}.")
    except (urllib.error.URLError, OSError, TimeoutError):
        raise SpeedDialFehler("Ich erreiche das NAS gerade nicht.")
    except ValueError:
        raise SpeedDialFehler("Die Speed-Dial-Datei auf dem NAS ist beschädigt.")
    if not isinstance(daten, dict) or not isinstance(daten.get("groups"), list):
        raise SpeedDialFehler("Die Speed-Dial-Datei auf dem NAS ist beschädigt.")
    return daten


def _aktualisieren():
    try:
        d = _laden()
        with _cache_lock:
            _cache.update(daten=d, zeit=time.time(), fehler="")
    except SpeedDialFehler as e:
        with _cache_lock:
            _cache["fehler"] = str(e)
            _cache["zeit"] = time.time()      # nicht bei jedem Satz neu versuchen


def daten(warten=True):
    """Liefert die Gruppen. Ist die Liste veraltet, wird im Hintergrund neu geladen."""
    with _cache_lock:
        d, alter = _cache["daten"], time.time() - _cache["zeit"]
    if d is None:
        if warten and alter > 30:
            _aktualisieren()
    elif alter > CACHE_S:
        threading.Thread(target=_aktualisieren, daemon=True).start()
    return _cache["daten"]


def vorladen():
    """Beim Start von Jarvis aufrufen: lädt die Liste schon einmal im Hintergrund."""
    if AKTIV:
        threading.Thread(target=_aktualisieren, daemon=True).start()


# ============================== Passende Kachel finden ==============================
def _wort_passt(q, titel_woerter, titel_kompakt):
    """Wie gut passt ein gesprochenes Wort zum Kachelnamen? 0..1"""
    if _hat_ziffer(q):                                   # Zahlen/Modellnummern: nur genau
        if q in titel_woerter or q in titel_kompakt:
            return 1.0
        return 0.0
    best = 0.0
    for t in titel_woerter:
        if t == q:
            return 1.0
        if len(q) >= 3 and t.startswith(q):
            best = max(best, 0.95)
        best = max(best, difflib.SequenceMatcher(None, q, t).ratio())
    if len(q) >= 4 and q in titel_kompakt:
        best = max(best, 0.9)
    return best


def _folge_passt(qk, tw):
    """Passt die zusammengeschriebene Anfrage auf aufeinanderfolgende Wörter des Titels?
    2 = endet genau an einer Wortgrenze ("mpg x570"), 1 = endet mitten im Wort ("hause" in "hauser"), 0 = nein."""
    for i in range(len(tw)):
        kette = ""
        for t in tw[i:]:
            kette += t
            if kette == qk:
                return 2
            if kette.startswith(qk):
                return 1
            if not qk.startswith(kette):
                break
    return 0


def bewerte(anfrage, titel):
    """Wie gut passt die Anfrage (gesprochen) zum Kachelnamen? Etwa 0..1.15 (über 1 = ganz genau getroffen)"""
    alle = [w for w in _woerter(anfrage) if w != "jarvis"]
    q = [w for w in alle if w not in _FUELL]
    if not q:
        return 0.0
    tw = _woerter(titel)
    tk = "".join(tw)
    if not tw:
        return 0.0
    qk = "".join(q)
    qk_voll = "".join(alle)                  # mit Füllwörtern: "zu hause" -> "zuhause"
    if qk_voll == tk or qk == tk:
        punkte = 1.15                        # genau der Name der Kachel
    elif qk_voll in tw or qk in tw:
        punkte = 1.05                        # ein ganzes Wort des Namens ("Mindfactory", "Zuhause", "Tomahawk")
    elif len(qk) >= 3 and (_folge_passt(qk, tw) == 2 or _folge_passt(qk_voll, tw) == 2):
        punkte = 0.97                        # "MPG X570" passt genau auf aufeinanderfolgende Wörter
    else:
        einzeln = [_wort_passt(w, tw, tk) for w in q]
        if any(_hat_ziffer(w) and s == 0 for w, s in zip(q, einzeln)):
            return 0.0                       # Modellnummer falsch -> sicher nicht gemeint
        punkte = sum(einzeln) / len(einzeln)
    # kleiner Bonus, wenn ein großer Teil des Namens gesagt wurde (bevorzugt den genaueren Treffer)
    abgedeckt = sum(1 for t in tw if any(t == w or (len(w) >= 3 and t.startswith(w)) for w in q)) / len(tw)
    return punkte + 0.05 * abgedeckt


def _alle_kacheln(d):
    for g in d.get("groups", []):
        for k in g.get("dials", []):
            if k.get("url"):
                yield g, k


def finde_gruppe(name, d):
    if not name or not d:
        return None
    best, bp = None, 0.0
    for g in d.get("groups", []):
        p = bewerte(name, g.get("name", ""))
        if p > bp:
            best, bp = g, p
    return best if bp >= 0.8 else None


def finde_kacheln(anfrage, d, gruppe=None):
    """Sortierte Liste [(punkte, gruppe, kachel)] der guten Treffer."""
    kontext = _letzte_gruppe["id"] if time.time() - _letzte_gruppe["zeit"] < 15 * 60 else None
    treffer = []
    for g, k in _alle_kacheln(d):
        if gruppe and g is not gruppe:
            continue
        p = bewerte(anfrage, k.get("title", ""))
        if p <= 0:
            continue
        if kontext and g.get("id") == kontext:
            p += 0.04
        treffer.append((p, g, k))
    treffer.sort(key=lambda x: -x[0])
    return [t for t in treffer if t[0] >= SCHWELLE]


# ============================== Befehle erkennen ==============================
_VERB = r"(?:offne|offnen|offnet|oeffne|zeig|zeige|starte|start|geh|gehe|ruf|rufe|lade|bring|bringe)"
_P_SPEEDDIAL = re.compile(r"^(?:jarvis\s+)?(?:bitte\s+)?" + _VERB + r"\s+(?:mir\s+)?(?:den\s+|das\s+|die\s+)?"
                          r"(?:speed\s*dial|speeddial|schnellwahl)(?:\s+(?:auf|an))?(?:\s+bitte)?$")
_P_GRUPPE = re.compile(r"^(?:jarvis\s+)?(?:bitte\s+)?(?:" + _VERB + r"|wechsel\w*)\s+(?:mir\s+)?(?:zu\s+|zur\s+|auf\s+|in\s+)?"
                       r"(?:die\s+|der\s+)?(?:speed\s*dial\s+)?gruppe\s+(.+?)(?:\s+(?:auf|an))?(?:\s+bitte)?$")
_P_GRUPPE_HINTEN = re.compile(r"^(?:jarvis\s+)?(?:bitte\s+)?(?:die\s+)?(?:gruppe\s+)?(.+?)\s*gruppe\s+"
                              r"(?:offnen|oeffnen|zeigen|anzeigen|starten|aufmachen|auf)(?:\s+bitte)?$")
_P_ALLE = re.compile(r"^(?:jarvis\s+)?(?:bitte\s+)?" + _VERB + r"\s+(?:mir\s+)?alle\s+(?:seiten|links|tabs|kacheln|webseiten)\s+"
                     r"(?:von|aus|in|der|im|bei)\s+(?:der\s+)?(?:gruppe\s+)?(.+?)(?:\s+bitte)?$")
_P_KACHEL = re.compile(r"^(?:jarvis\s+)?(?:bitte\s+)?" + _VERB + r"\s+(.+?)(?:\s+(?:auf|an))?(?:\s+bitte)?$")
_P_ZUSATZ_GRUPPE = re.compile(r"^(.+?)\s+(?:bei|aus|unter|in)\s+(?:der\s+)?(?:gruppe\s+)?(.+)$")


def erkenne(cmd):
    """Gibt ein Dict mit 'art' zurück oder None (= kein Speed-Dial-Befehl, normal weitermachen)."""
    if not AKTIV:
        return None
    n = _fold(cmd)
    if not n:
        return None
    w = set(n.split())
    if w & IGNORIEREN:
        return None

    if _P_SPEEDDIAL.match(n):
        return {"art": "speeddial"}

    m = _P_GRUPPE_HINTEN.match(n)
    if m:
        return {"art": "gruppe", "gruppe": m.group(1)}

    m = _P_ALLE.match(n)
    if m:
        return {"art": "alle", "gruppe": m.group(1)}

    m = _P_GRUPPE.match(n)
    if m:
        return {"art": "gruppe", "gruppe": m.group(1)}

    m = _P_KACHEL.match(n)
    if not m:
        return None
    rest = m.group(1)
    d = daten()
    if not d:
        return None                                  # NAS nicht erreichbar -> anderen Befehlen den Vortritt lassen
    # "Öffne Mindfactory bei Hardware" -> nur in dieser Gruppe suchen
    gruppe = None
    mz = _P_ZUSATZ_GRUPPE.match(rest)
    if mz:
        gruppe = finde_gruppe(mz.group(2), d)
        if gruppe:
            rest = mz.group(1)
    treffer = finde_kacheln(rest, d, gruppe)
    if not treffer:
        return None
    return {"art": "kachel", "treffer": treffer}


# ============================== Ausführen ==============================
def _chrome():
    if CHROME_PFAD and os.path.exists(CHROME_PFAD):
        return CHROME_PFAD
    for basis in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                  os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                  os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(basis, "Google", "Chrome", "Application", "chrome.exe")
        if basis and os.path.exists(p):
            return p
    return None


def _oeffnen(url):
    chrome = _chrome()
    if chrome:
        subprocess.Popen([chrome, url])
    elif url.startswith("chrome-extension://"):
        raise SpeedDialFehler("Ich habe Chrome nicht gefunden.")
    else:
        webbrowser.open(url)


def _kurzname(titel, max_woerter=5):
    w = str(titel).split()
    return " ".join(w[:max_woerter])


def _merke_gruppe(g):
    if g:
        _letzte_gruppe.update(id=g.get("id"), zeit=time.time())


def _speeddial_url(gruppe=None):
    if not ERWEITERUNG_ID:
        raise SpeedDialFehler("Mir fehlt noch die ID der Speed-Dial-Erweiterung in der config.py.")
    url = f"chrome-extension://{ERWEITERUNG_ID}/newtab.html"
    if gruppe:
        url += "?gruppe=" + urllib.parse.quote(gruppe.get("id", ""))
    return url


def _kachel_oeffnen(g, k):
    _oeffnen(k["url"])
    _merke_gruppe(g)
    return f"Öffne {_kurzname(k.get('title', ''))}."


def fuehre_aus(intent):
    """Führt den Befehl aus und gibt den Sprechtext zurück."""
    global _rueckfrage
    art = intent.get("art")
    try:
        if art == "speeddial":
            _oeffnen(_speeddial_url())
            return "Öffne den Speed Dial."

        if art in ("gruppe", "alle"):
            d = daten()
            if not d:
                raise SpeedDialFehler(_cache["fehler"] or "Ich erreiche das NAS gerade nicht.")
            g = finde_gruppe(intent.get("gruppe", ""), d)
            if not g:
                namen = [x.get("name", "") for x in d.get("groups", [])]
                return f"Diese Gruppe kenne ich nicht. Es gibt {_aufz(namen)}." if namen \
                    else "Im Speed Dial gibt es noch keine Gruppen."
            _merke_gruppe(g)
            if art == "gruppe":
                _oeffnen(_speeddial_url(g))
                return f"Öffne die Gruppe {g.get('name', '')}."
            links = [k for k in g.get("dials", []) if k.get("url")]
            if not links:
                return f"In der Gruppe {g.get('name', '')} sind noch keine Seiten."
            for k in links[:15]:
                _oeffnen(k["url"])
                time.sleep(0.3)
            mehr = " Ich habe nur die ersten fünfzehn geöffnet." if len(links) > 15 else ""
            return f"Öffne {len(links[:15])} Seiten aus {g.get('name', '')}.{mehr}"

        if art == "kachel":
            treffer = intent["treffer"]
            bester = treffer[0][0]
            nah = [t for t in treffer if bester - t[0] < 0.06][:3]
            if len(nah) == 1:
                return _kachel_oeffnen(nah[0][1], nah[0][2])
            # mehrere passen gleich gut -> nachfragen
            _rueckfrage = {"kandidaten": [(g, k) for _, g, k in nah], "ablauf": time.time() + 40}
            namen = [_kurzname(k.get("title", ""), 7) for _, _, k in nah]
            return f"Meinst du {_aufz(namen, 'oder')}?"
    except SpeedDialFehler as e:
        return str(e)
    except OSError:
        return "Den Browser konnte ich nicht starten."
    return "Das habe ich nicht verstanden."


# ============================== Rückfrage "Meinst du A oder B?" ==============================
_ORDNUNG = {"erste": 0, "ersten": 0, "erstes": 0, "eins": 0, "1": 0, "zweite": 1, "zweiten": 1, "zweites": 1,
            "zwei": 1, "2": 1, "dritte": 2, "dritten": 2, "drittes": 2, "drei": 2, "3": 2,
            "letzte": -1, "letzten": -1, "letztes": -1}
_EINLEITUNG = re.compile(r"^(?:jarvis\s+)?(?:(?:ich\s+)?(?:meine|meinte|will|mochte|hatte\s+gern)\s+)?"
                         r"(?:nimm\s+|offne\s+|bitte\s+)?(?:die\s+|den\s+|das\s+|der\s+)?")
_NEIN = {"nein", "nee", "keins", "keine", "keinen", "abbrechen", "stopp", "stop", "egal", "vergiss"}


def hat_rueckfrage():
    return bool(_rueckfrage and time.time() <= _rueckfrage["ablauf"])


def pending_antwort(text):
    """Antwort auf "Meinst du A oder B?". Gibt den Sprechtext zurück oder None (= war keine Antwort darauf)."""
    global _rueckfrage
    r, _rueckfrage = _rueckfrage, None
    if not r or time.time() > r["ablauf"]:
        return None
    w = _woerter(text)
    kand = r["kandidaten"]
    if set(w) & _NEIN and len(w) <= 4:
        return "Okay."
    for x in w:
        if x in _ORDNUNG and len(w) <= 4:
            i = _ORDNUNG[x]
            if -len(kand) <= i < len(kand):
                return _kachel_oeffnen(*kand[i])
    rest = _EINLEITUNG.sub("", _fold(text))
    punkte = sorted(((bewerte(rest, k.get("title", "")), g, k) for g, k in kand), key=lambda t: -t[0])
    if punkte and punkte[0][0] >= 0.6 and (len(punkte) == 1 or punkte[0][0] - punkte[1][0] >= 0.05):
        return _kachel_oeffnen(punkte[0][1], punkte[0][2])
    _rueckfrage = None
    return None


def _aufz(liste, und="und"):
    liste = [x for x in liste if x]
    if not liste:
        return ""
    return liste[0] if len(liste) == 1 else ", ".join(liste[:-1]) + f" {und} " + liste[-1]
