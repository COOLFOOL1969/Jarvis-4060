"""
zeit.py - Timer, Wecker und Erinnerungen für JARVIS

Sprachbeispiele:
    "Timer 20 Minuten"                         "Stelle einen Timer auf eine halbe Stunde für die Pizza"
    "Wecker auf 6 Uhr 30"                      "Weck mich morgen um halb acht"
    "Erinnere mich um 18 Uhr an die Wäsche"    "Erinnere mich in 10 Minuten an den Ofen"
    "Welche Timer laufen?"                     "Wie lange noch?"
    "Lösche alle Timer"                        "Wecker aus"

Alles wird in einer JSON-Datei gespeichert und überlebt einen Neustart von JARVIS.
"""

import os
import re
import json
import time
import uuid
import threading
from datetime import datetime, timedelta

import config

# ============================== Text-Helfer ==============================
# Längenerhaltende Vereinfachung: Positionen im vereinfachten Text passen zum Originaltext.
_TR = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s", "é": "e", "è": "e", "á": "a", "à": "a"})


def norm(s):
    return s.lower().translate(_TR)


_EINER = {"null": 0, "ein": 1, "eins": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "zwei": 2,
          "zwo": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
          "zehn": 10, "elf": 11, "zwolf": 12, "dreizehn": 13, "vierzehn": 14, "funfzehn": 15,
          "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_ZEHNER = {"zwanzig": 20, "dreisig": 30, "dreissig": 30, "vierzig": 40, "funfzig": 50,
           "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90}


def zahl(tok):
    """'20' -> 20, 'zwanzig' -> 20, 'einundzwanzig' -> 21, 'hundert' -> 100, sonst None."""
    if re.fullmatch(r"\d+", tok):
        return int(tok)
    if tok in _EINER:
        return _EINER[tok]
    if tok in _ZEHNER:
        return _ZEHNER[tok]
    if tok in ("hundert", "einhundert"):
        return 100
    m = re.fullmatch(r"([a-z]+?)und([a-z]+)", tok)
    if m and m.group(1) in _EINER and 1 <= _EINER[m.group(1)] <= 9 and m.group(2) in _ZEHNER:
        return _EINER[m.group(1)] + _ZEHNER[m.group(2)]
    return None


def _ueberlappt(sp, spans):
    return any(sp[0] < b and a < sp[1] for a, b in spans)


# ============================== Dauer ("in 20 Minuten") ==============================
def parse_dauer(text):
    """Liest eine Zeitdauer. Gibt (Sekunden, [Textbereiche]) oder None zurück."""
    n = norm(text)
    spans, total = [], 0.0

    sonder = (
        (r"\bdrei ?viertel ?stunden?\b", 2700),
        (r"\b(?:eine? )?halbe[nr]? stunden?\b", 1800),
        (r"\b(?:eine? )?viertel ?stunden?\b", 900),
        (r"\b(?:eine? )?halbe[nr]? minuten?\b", 30),
        (r"\banderthalb(?:e)? stunden?\b", 5400),
    )
    for pat, sek in sonder:
        for m in re.finditer(pat, n):
            if not _ueberlappt(m.span(), spans):
                spans.append(m.span())
                total += sek

    # "zweieinhalb Stunden", "eineinhalb Stunden", "2 einhalb Stunden"
    for m in re.finditer(r"\b([a-z]+|\d+) ?einhalb stunden?\b", n):
        z = zahl(m.group(1))
        if z is not None and not _ueberlappt(m.span(), spans):
            spans.append(m.span())
            total += (z + 0.5) * 3600

    einheiten = {"stunde": 3600, "stunden": 3600, "std": 3600, "minute": 60, "minuten": 60, "min": 60,
                 "sekunde": 1, "sekunden": 1, "sek": 1}
    hat_minuten = False
    for m in re.finditer(r"\b(\d+(?:[.,]\d+)?|[a-z]+)\s*(stunden?|std|minuten?|min|sekunden?|sek)\b", n):
        if _ueberlappt(m.span(), spans):
            continue
        roh = m.group(1).replace(",", ".")
        try:
            wert = float(roh) if re.fullmatch(r"\d+(?:\.\d+)?", roh) else zahl(roh)
        except ValueError:
            wert = None
        if wert is None:
            continue
        sek = einheiten[m.group(2)]
        spans.append(m.span())
        total += wert * sek
        if sek == 60:
            hat_minuten = True
        elif sek == 3600 and not hat_minuten:
            # "1 Stunde 30" -> 30 Minuten
            t = re.match(r"\s*(?:und\s+)?(\d{1,2}|[a-z]+)\b"
                         r"(?!\s*(?:stunden?|std|minuten?|min|sekunden?|sek|uhr))", n[m.end():])
            if t:
                z = zahl(t.group(1))
                if z is not None and 0 < z <= 59:
                    total += z * 60
                    spans.append((m.end() + t.start(1), m.end() + t.end(1)))
                    hat_minuten = True

    if not spans or total <= 0:
        return None
    return int(round(total)), spans


# ============================== Uhrzeit ("um halb acht") ==============================
_WOCHENTAGE = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]
_WOCHENTAGE_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_NUM = r"(\d{1,2}|[a-z]+)"


def _stunde(tok):
    z = zahl(tok)
    return z if z is not None and 0 <= z <= 24 else None


def parse_uhrzeit(text, jetzt=None, bare_ok=False, standard=None):
    """Liest eine Uhrzeit mit optionalem Tag. Gibt (datetime, [Textbereiche]) oder None zurück.

    bare_ok:  auch "Wecker auf 7" (ohne 'um'/'Uhr') akzeptieren
    standard: (Stunde, Minute), falls nur ein Tag genannt wird ("Erinnere mich morgen an ...")
    """
    jetzt = jetzt or datetime.now()
    n = norm(text)
    spans = []
    h = m = None
    explizit = False          # 24-Stunden-Angabe (13..24 oder 0) -> keine AM/PM-Unterscheidung nötig

    # --- Tag ---
    tag_off, wochentag = None, None
    dp = None                 # Tageszeit: am | pm | mittag | nacht
    mt = re.search(r"\bheute morgen\b", n)
    if mt:
        tag_off, dp = 0, "am"
        spans.append(mt.span())
    else:
        for pat, off in ((r"\bubermorgen\b", 2), (r"\bmorgen\b", 1), (r"\bheute\b", 0)):
            mt = re.search(pat, n)
            if mt:
                tag_off = off
                spans.append(mt.span())
                break
    mw = re.search(r"\b(?:am |nachsten |kommenden )?(" + "|".join(_WOCHENTAGE) + r")\b", n)
    if mw:
        wochentag = _WOCHENTAGE.index(mw.group(1))
        spans.append(mw.span())
    for pat, wert in ((r"\b(?:morgens|fruh|vormittags|fruhmorgens)\b", "am"),
                      (r"\b(?:nachmittags|abends|abend)\b", "pm"),
                      (r"\bmittags\b", "mittag"), (r"\bnachts\b", "nacht")):
        mt = re.search(pat, n)
        if mt and dp is None:
            dp = wert
            spans.append(mt.span())

    def frei(sp):
        return not _ueberlappt(sp, spans)

    # --- Uhrzeitform ---
    mm = re.search(r"\b(\d{1,2})[:.](\d{2})\b(?:\s*uhr\b)?", n)
    if mm and int(mm.group(1)) <= 24 and int(mm.group(2)) < 60 and frei(mm.span()):
        h, m = int(mm.group(1)), int(mm.group(2))
        explizit = h > 12 or h == 0
        spans.append(mm.span())
    if h is None:
        mm = re.search(r"\bhalb " + _NUM + r"\b", n)
        H = _stunde(mm.group(1)) if mm else None
        if mm and H is not None and 1 <= H <= 12 and frei(mm.span()):
            h, m = H - 1, 30
            spans.append(mm.span())
    if h is None:
        mm = re.search(r"\bdrei ?viertel " + _NUM + r"\b", n)
        H = _stunde(mm.group(1)) if mm else None
        if mm and H is not None and 1 <= H <= 12 and frei(mm.span()):
            h, m = H - 1, 45
            spans.append(mm.span())
    if h is None:
        mm = re.search(r"\bviertel (nach|vor) " + _NUM + r"\b", n)
        H = _stunde(mm.group(2)) if mm else None
        if mm and H is not None and 1 <= H <= 12 and frei(mm.span()):
            h, m = (H, 15) if mm.group(1) == "nach" else (H - 1, 45)
            spans.append(mm.span())
    if h is None:
        mm = re.search(r"\b" + _NUM + r" (nach|vor) " + _NUM + r"\b", n)
        if mm:
            mi, H = zahl(mm.group(1)), _stunde(mm.group(3))
            if mi is not None and 1 <= mi <= 29 and H is not None and 1 <= H <= 12 and frei(mm.span()):
                h, m = (H, mi) if mm.group(2) == "nach" else (H - 1, 60 - mi)
                spans.append(mm.span())
    if h is None:
        mm = re.search(r"\bviertel " + _NUM + r"\b", n)          # süddeutsch: "viertel acht" = 7:15
        H = _stunde(mm.group(1)) if mm else None
        if mm and H is not None and 1 <= H <= 12 and frei(mm.span()):
            h, m = H - 1, 15
            spans.append(mm.span())
    if h is None:
        mm = re.search(r"\b" + _NUM + r" uhr\b(?: " + _NUM + r"\b)?", n)
        H = _stunde(mm.group(1)) if mm else None
        # Prüfung nur für "8 Uhr" - ein folgendes Wort ("abends") darf andere Treffer nicht blockieren
        if mm and H is not None and frei((mm.start(), mm.end(1) + len(" uhr"))):
            h, m = H % 24 if H != 24 else 0, 0
            explizit = H > 12 or H in (0, 24)
            end = mm.end(1) + len(" uhr")
            if mm.group(2):
                z = zahl(mm.group(2))
                if z is not None and 0 <= z < 60:
                    m = z
                    end = mm.end(2)
            spans.append((mm.start(), end))
    if h is None:
        pat = r"\b(?:um|auf|fur)\b " + _NUM + r"\b(?!\s*(?:uhr|:|stunden?|std|minuten?|min|sekunden?|sek))" \
            if bare_ok else r"\bum " + _NUM + r"\b(?!\s*(?:uhr|:|stunden?|std|minuten?|min|sekunden?|sek))"
        mm = re.search(pat, n)
        H = _stunde(mm.group(1)) if mm else None
        if mm and H is not None and frei(mm.span()):
            h, m = H % 24 if H != 24 else 0, 0
            explizit = H > 12 or H in (0, 24)
            spans.append(mm.span())
    if h is None:
        mm = re.search(r"\bmitternacht\b", n)
        if mm:
            h, m, explizit = 0, 0, True
            spans.append(mm.span())
    if h is None and dp == "mittag" and re.search(r"\bmittags\b", n):
        h, m, explizit = 12, 0, True
    if h is None and (tag_off is not None or wochentag is not None) and standard:
        h, m, explizit = standard[0], standard[1], True
    if h is None:
        return None

    # --- AM/PM auflösen ---
    if explizit:
        kandidaten = [h % 24]
    else:
        b = h % 12
        if dp == "am":
            kandidaten = [b]
        elif dp == "pm":
            kandidaten = [b + 12]
        elif dp == "mittag":
            kandidaten = [12 if b in (11, 0) and h in (11, 12) else (b + 12 if b <= 4 else b)]
        elif dp == "nacht":
            kandidaten = [b + 12 if b >= 6 else b]
        else:
            kandidaten = [b, b + 12]

    heute = jetzt.date()
    if wochentag is not None:
        diff = (wochentag - heute.weekday()) % 7
        datum = heute + timedelta(days=diff)
    elif tag_off is not None:
        datum = heute + timedelta(days=tag_off)
    else:
        datum = None

    def bauen(d, std):
        return datetime(d.year, d.month, d.day, std, m)

    if datum is None:
        zeiten = sorted(bauen(heute, k) for k in kandidaten)
        ziel = next((z for z in zeiten if z > jetzt + timedelta(seconds=1)), None)
        if ziel is None:
            ziel = bauen(heute + timedelta(days=1), min(kandidaten))
    else:
        if datum == heute:
            zeiten = sorted(bauen(heute, k) for k in kandidaten)
            ziel = next((z for z in zeiten if z > jetzt + timedelta(seconds=1)), None)
            if ziel is None:       # heute schon vorbei -> nächstes Mal (morgen bzw. nächste Woche)
                ziel = bauen(heute + timedelta(days=7 if wochentag is not None else 1), min(kandidaten))
        else:
            if len(kandidaten) == 2:   # ohne Tageszeit: 1-4 Uhr eher nachmittags, 5-11 eher morgens
                b = h % 12
                std = 12 if b == 0 and h == 12 else (b + 12 if 1 <= b <= 4 else b)
                if h == 0:
                    std = 12
            else:
                std = kandidaten[0]
            ziel = bauen(datum, std)
    return ziel, spans


# ============================== Formatierung ==============================
def uhr_sprech(dt):
    return f"{dt.hour} Uhr" + (f" {dt.minute}" if dt.minute else "")


def uhr_text(dt):
    return f"{dt.hour:02d}:{dt.minute:02d}"


def tag_text(dt, jetzt=None):
    jetzt = jetzt or datetime.now()
    d = (dt.date() - jetzt.date()).days
    if d == 0:
        return "heute"
    if d == 1:
        return "morgen"
    if d == 2:
        return "übermorgen"
    if 2 < d < 7:
        return f"am {_WOCHENTAGE_DE[dt.weekday()]}"
    return f"am {dt.day}.{dt.month}."


def ziel_sprech(dt, jetzt=None):
    return f"{tag_text(dt, jetzt)} um {uhr_sprech(dt)}"


def dauer_sprech(sek):
    sek = int(round(sek))
    h, rest = divmod(sek, 3600)
    mi, s = divmod(rest, 60)
    teile = []
    if h:
        teile.append(f"{h} Stunde" + ("n" if h != 1 else ""))
    if mi:
        teile.append(f"{mi} Minute" + ("n" if mi != 1 else ""))
    if s and not h:
        teile.append(f"{s} Sekunde" + ("n" if s != 1 else ""))
    if not teile:
        return "0 Sekunden"
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


# ============================== Befehlserkennung ==============================
_LEAD_STOP = {"erinnere", "erinner", "erinnern", "mich", "mir", "bitte", "stelle", "stell", "stellen",
              "setze", "setz", "einen", "eine", "ein", "timer", "wecker", "erinnerung", "auf", "fur",
              "an", "daran", "dass", "jarvis", "neuen", "neue", "sag", "sage", "bescheid", "weck",
              "wecke", "ich", "und", "noch", "um", "in", "das", "mal", "doch"}
_TRAIL_STOP = {"bitte", "mir", "mich", "bescheid", "und", "um", "in", "auf", "an", "noch", "mal"}
_ARTIKEL = {"die", "der", "das", "den", "dem", "ein", "eine", "einen"}


def _label(text, spans, art):
    """Entfernt die Zeitangabe und die Befehlswörter, übrig bleibt z.B. 'die Wäsche aufhängen'."""
    stuecke, letzte = [], 0
    for a, b in sorted(spans):
        if a >= letzte:
            stuecke.append(text[letzte:a])
            letzte = b
    stuecke.append(text[letzte:])
    toks = re.findall(r"[\wäöüÄÖÜß\-]+", " ".join(stuecke))
    # "ich" nur am Anfang direkt hinter den Befehlswörtern stehen lassen ("daran, dass ich ...")
    while toks and norm(toks[0]) in (_LEAD_STOP - {"ich"}):
        toks.pop(0)
    while toks and norm(toks[-1]) in _TRAIL_STOP:
        toks.pop()
    if art == "timer":
        while toks and norm(toks[0]) in _ARTIKEL:
            toks.pop(0)
    label = " ".join(toks).strip()
    label = re.sub(r"^ich\b", "du", label, flags=re.IGNORECASE)
    label = re.sub(r"\b(soll|muss|will)$", lambda mo: {"soll": "sollst", "muss": "musst", "will": "willst"}[mo.group(1)],
                   label)
    return label


def erkenne(cmd, jetzt=None):
    """Erkennt Timer-/Wecker-/Erinnerungsbefehle. Gibt ein Dict mit 'art' zurück oder None."""
    jetzt = jetzt or datetime.now()
    n = norm(cmd)
    toks = set(re.findall(r"[a-z0-9]+", n))

    hat_timer = "timer" in toks
    hat_wecker = "wecker" in toks or bool(re.search(r"\bweck(?:e)? (?:mich|mir)\b", n))
    hat_erinn = any(t.startswith("erinner") for t in toks) or "bescheid" in toks or "melde" in toks
    nomen = hat_timer or hat_wecker or any(t.startswith("erinnerung") for t in toks)
    kind = ("wecker" if "wecker" in toks else "timer" if hat_timer
            else "erinnerung" if any(t.startswith("erinnerung") for t in toks) else None)

    if re.search(r"\bwie lange noch\b|\bwie viel zeit\b|\bwieviel zeit\b|\bwie lange dauert\b", n):
        return {"art": "rest", "kind": kind}
    if not (hat_timer or hat_wecker or hat_erinn or nomen):
        return None

    abbruch = re.search(r"\b(?:losch\w*|entfern\w*|storni\w*|abbrech\w*|abbruch|stopp\w*|beend\w*|"
                        r"ausschalt\w*|deaktivi\w*)\b|\b(?:wecker|timer) aus\b|\bbrech\w+ .{0,25}\bab\b", n)
    if nomen and abbruch:
        return {"art": "abbrechen", "kind": kind, "alle": "alle" in toks, "text": cmd}
    if nomen and re.search(r"\b(?:welche|wie viele|wieviele|zeig\w*|liste|gibt es|habe ich|laufen|lauft|"
                           r"stehen an|aktiv|offen)\b", n):
        return {"art": "liste", "kind": kind}

    art = "wecker" if hat_wecker else "timer" if hat_timer else "erinnerung"

    if art == "timer":
        d = parse_dauer(cmd)
        if d:
            sek, spans = d
        else:
            z = None
            for mo in re.finditer(r"\b(\d+|[a-z]+)\b", n):
                z = zahl(mo.group(1))
                if z:
                    spans = [mo.span()]
                    break
            if not z:
                return {"art": "fehler", "text": "Wie lange soll der Timer laufen?"}
            sek = z * 60                      # "Timer 20" -> 20 Minuten
        if sek > 7 * 86400:
            return {"art": "fehler", "text": "So lange Timer kann ich nicht stellen."}
        return {"art": "timer", "ziel": jetzt + timedelta(seconds=sek), "dauer": sek,
                "label": _label(cmd, spans, "timer")}

    # Wecker / Erinnerung: Uhrzeit oder Dauer
    standard = None
    if art == "erinnerung":
        try:
            sh, sm = str(getattr(config, "ERINNERUNG_STANDARD_UHRZEIT", "09:00")).split(":")
            standard = (int(sh), int(sm))
        except ValueError:
            standard = (9, 0)
    u = parse_uhrzeit(cmd, jetzt, bare_ok=(art == "wecker"), standard=standard)
    d = parse_dauer(cmd)
    if u and not (d and re.search(r"\bin\b", n) and not re.search(r"\bum\b", n)):
        ziel, spans = u
        dauer = None
    elif d:
        sek, spans = d
        ziel, dauer = jetzt + timedelta(seconds=sek), sek
    else:
        return {"art": "fehler",
                "text": "Wann soll ich dich wecken?" if art == "wecker" else "Wann soll ich dich erinnern?"}
    label = _label(cmd, spans, art)
    if art == "wecker" and not label:
        label = "Wecker"
    if art == "erinnerung" and not label:
        label = "Erinnerung"
    return {"art": art, "ziel": ziel, "dauer": dauer, "label": label}


# ============================== Planer (Speicher + Scheduler) ==============================
def _standard_pfad():
    pfad = getattr(config, "ERINNERUNGEN_DATEI", None)
    if pfad:
        return pfad
    basis = getattr(config, "PROJEKT_DIR", None)
    if basis and os.path.isdir(basis):
        return os.path.join(basis, "jarvis_erinnerungen.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_erinnerungen.json")


class Planer:
    """Verwaltet Timer, Wecker und Erinnerungen und ruft on_due(item) auf, wenn etwas fällig ist."""

    def __init__(self, on_due=None, log=print, pfad=None):
        self.lock = threading.RLock()
        self.items = []
        self.on_due = on_due
        self.log = log
        self.pfad = pfad or _standard_pfad()
        self._ende = threading.Event()
        self._laden()

    # ---------- Speicher ----------
    def _laden(self):
        try:
            with open(self.pfad, "r", encoding="utf-8") as f:
                roh = json.load(f)
        except (OSError, ValueError):
            return
        jetzt = time.time()
        for it in roh if isinstance(roh, list) else []:
            try:
                it = {"id": str(it["id"]), "art": it["art"], "label": it.get("label", ""),
                      "ziel_ts": float(it["ziel_ts"]), "status": "wartet", "ansagen": 0,
                      "next_ts": 0.0, "verpasst": False, "erstellt_ts": float(it.get("erstellt_ts", jetzt))}
            except (KeyError, TypeError, ValueError):
                continue
            if it["ziel_ts"] < jetzt:
                if jetzt - it["ziel_ts"] > 12 * 3600:
                    continue                     # zu alt -> verwerfen
                it["verpasst"] = True
            self.items.append(it)
        if self.items:
            self.log(f"[Zeit]: {len(self.items)} gespeicherte Timer/Erinnerungen geladen.")

    def _speichern(self):
        try:
            tmp = self.pfad + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([{k: it[k] for k in ("id", "art", "label", "ziel_ts", "erstellt_ts")}
                           for it in self.items], f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.pfad)
        except OSError as e:
            self.log(f"[Zeit Fehler]: Speichern nicht möglich ({e})")

    # ---------- Verwaltung ----------
    def hinzufuegen(self, art, ziel, label=""):
        ts = ziel.timestamp() if isinstance(ziel, datetime) else float(ziel)
        it = {"id": uuid.uuid4().hex[:8], "art": art, "label": label or "", "ziel_ts": ts,
              "status": "wartet", "ansagen": 0, "next_ts": 0.0, "verpasst": False,
              "erstellt_ts": time.time()}
        with self.lock:
            self.items.append(it)
            self._speichern()
        return it

    def entfernen(self, item_id):
        with self.lock:
            vorher = len(self.items)
            self.items = [it for it in self.items if it["id"] != item_id]
            if len(self.items) != vorher:
                self._speichern()
                return True
        return False

    def abbrechen(self, kind=None, alle=False, text=""):
        """Entfernt alle (oder den passendsten) Einträge einer Art. Gibt die entfernten Einträge zurück."""
        with self.lock:
            pool = [it for it in self.items if kind in (None, it["art"])]
            if not pool:
                return []
            if alle:
                ziel = pool
            else:
                ignorieren = {"timer", "wecker", "erinnerung", "erinnerungen", "loschen", "losche", "abbrechen",
                              "brich", "alle", "stoppen", "stoppe", "beenden", "beende", "bitte", "jarvis",
                              "ausschalten", "entfernen", "entferne", "storniere", "stornieren", "meinen",
                              "meine", "diesen", "diese", "den", "die", "das"}
                worte = [w for w in re.findall(r"[a-z]{4,}", norm(text)) if w not in ignorieren]
                treffer = [it for it in pool if any(w in norm(it["label"]) for w in worte)]
                ziel = (treffer or sorted(pool, key=lambda i: i["ziel_ts"]))[:1]
            ids = {it["id"] for it in ziel}
            self.items = [it for it in self.items if it["id"] not in ids]
            self._speichern()
            return ziel

    def quittieren(self):
        """Beendet klingelnde Einträge (nach 'Danke' / 'Okay' / Klick auf OK). Gibt die Anzahl zurück."""
        with self.lock:
            klingelnde = [it for it in self.items if it["status"] == "klingelt"]
            if klingelnde:
                ids = {it["id"] for it in klingelnde}
                self.items = [it for it in self.items if it["id"] not in ids]
                self._speichern()
            return len(klingelnde)

    def klingelt(self):
        with self.lock:
            return any(it["status"] == "klingelt" for it in self.items)

    def snapshot(self):
        jetzt = time.time()
        dt_jetzt = datetime.now()
        with self.lock:
            out = []
            for it in sorted(self.items, key=lambda i: i["ziel_ts"]):
                dt = datetime.fromtimestamp(it["ziel_ts"])
                out.append({"id": it["id"], "art": it["art"], "label": it["label"], "status": it["status"],
                            "ziel_ts": it["ziel_ts"], "rest_s": max(0, int(it["ziel_ts"] - jetzt)),
                            "uhrzeit": uhr_text(dt), "tag": tag_text(dt, dt_jetzt)})
            return out

    # ---------- Scheduler ----------
    def starten(self):
        threading.Thread(target=self._schleife, daemon=True).start()

    def beenden(self):
        self._ende.set()

    def _schleife(self):
        wdh = int(getattr(config, "ERINNERUNG_WIEDERHOLUNGEN", 2))
        pause = float(getattr(config, "ERINNERUNG_PAUSE_S", 45))
        while not self._ende.wait(0.5):
            jetzt = time.time()
            faellig = []
            with self.lock:
                for it in self.items:
                    if it["status"] == "wartet" and it["ziel_ts"] <= jetzt:
                        it["status"], it["next_ts"] = "klingelt", jetzt
                    if it["status"] == "klingelt" and it["next_ts"] <= jetzt:
                        it["ansagen"] += 1
                        faellig.append(dict(it))
                        it["next_ts"] = jetzt + pause
                abgelaufen = {it["id"] for it in self.items
                              if it["status"] == "klingelt" and it["ansagen"] > wdh}
                if abgelaufen:
                    self.items = [it for it in self.items if it["id"] not in abgelaufen]
                    self._speichern()
            for it in faellig:
                if self.on_due:
                    threading.Thread(target=self._melden, args=(it,), daemon=True).start()

    def _melden(self, it):
        try:
            self.on_due(it)
        except Exception as e:
            self.log(f"[Zeit Fehler]: {e}")


# ============================== Ansagen ==============================
def ansage_text(it):
    label = it.get("label") or ""
    if it["art"] == "timer":
        text = f"Dein Timer {label} ist abgelaufen." if label and label.lower() != "timer" else \
            "Dein Timer ist abgelaufen."
    elif it["art"] == "wecker":
        text = f"Wecker. Es ist {uhr_sprech(datetime.fromtimestamp(it['ziel_ts']))}."
        if label and label.lower() != "wecker":
            text += f" {label}."
    else:
        text = f"Erinnerung: {label}." if label else "Erinnerung."
    if it.get("verpasst") and it.get("ansagen", 1) <= 1:
        text = "Das ist während meiner Abwesenheit fällig geworden. " + text
    return text


def piepen():
    """Kurzer Signalton (nur Windows); sonst still."""
    try:
        import winsound
        for f in (880, 1175, 880):
            winsound.Beep(f, 150)
    except Exception:
        pass


def fuehre_aus(intent, planer, jetzt=None):
    """Führt einen erkannten Befehl aus und gibt den gesprochenen Text zurück."""
    jetzt = jetzt or datetime.now()
    art = intent["art"]

    if art == "fehler":
        return intent["text"]

    if art in ("timer", "wecker", "erinnerung"):
        label = intent.get("label") or ""
        ziel, dauer = intent["ziel"], intent.get("dauer")
        planer.hinzufuegen(art, ziel, label)
        if art == "timer":
            return f"Timer gestellt: {dauer_sprech(dauer)}" + (f" für {label}." if label else ".")
        if art == "wecker":
            return f"Wecker gestellt auf {ziel_sprech(ziel, jetzt)}" + \
                (f", {label}." if label and label != "Wecker" else ".")
        wann = f"in {dauer_sprech(dauer)}" if dauer else ziel_sprech(ziel, jetzt)
        if label and label != "Erinnerung":
            was = f" daran, dass {label}" if label.startswith("du ") else f" an {label}"
        else:
            was = ""
        return f"Okay, ich erinnere dich {wann}{was}."

    snap = planer.snapshot()

    if art == "liste":
        kind = intent.get("kind")
        sel = [s for s in snap if kind in (None, s["art"])]
        namen = {"timer": "Timer", "wecker": "Wecker", "erinnerung": "Erinnerungen"}
        if not sel:
            return f"Es läuft kein {namen[kind][:-1] if kind == 'erinnerung' else namen[kind]}." \
                if kind else "Es sind keine Timer, Wecker oder Erinnerungen gestellt."
        teile = []
        for s in sel[:5]:
            was = s["label"] or {"timer": "Timer", "wecker": "Wecker", "erinnerung": "Erinnerung"}[s["art"]]
            if s["art"] == "timer":
                teile.append(f"{was}: noch {dauer_sprech(s['rest_s'])}")
            else:
                dt = datetime.fromtimestamp(s["ziel_ts"])
                teile.append(f"{was}: {ziel_sprech(dt, jetzt)}")
        kopf = f"Es sind {len(sel)} Einträge gestellt. " if len(sel) > 1 else ""
        return kopf + ". ".join(teile) + ("." if teile else "")

    if art == "rest":
        kind = intent.get("kind")
        timer = [s for s in snap if s["art"] == "timer" and kind in (None, "timer")]
        if timer:
            s = timer[0]
            was = f" für {s['label']}" if s["label"] else ""
            return f"Der Timer{was} läuft noch {dauer_sprech(s['rest_s'])}."
        rest = [s for s in snap if kind in (None, s["art"])]
        if rest:
            s = rest[0]
            return f"Als Nächstes: {s['label'] or s['art']} {ziel_sprech(datetime.fromtimestamp(s['ziel_ts']), jetzt)}."
        return "Es läuft gerade kein Timer."

    if art == "abbrechen":
        weg = planer.abbrechen(intent.get("kind"), intent.get("alle", False), intent.get("text", ""))
        if not weg:
            return "Es gibt nichts zum Löschen."
        if len(weg) == 1:
            w = weg[0]
            name = {"timer": "Timer", "wecker": "Wecker", "erinnerung": "Erinnerung"}[w["art"]]
            return f"{name}{' ' + w['label'] if w['label'] and w['label'].lower() != name.lower() else ''} gelöscht."
        return f"{len(weg)} Einträge gelöscht."

    return ""
