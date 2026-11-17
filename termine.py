"""
termine.py - Geburtstage und Jubiläen (Hochzeitstag, Firmeneintritt ...) mit Erinnerungen

Die Daten liegen als normale Notiz in Obsidian:  JARVIS/Jahrestage.md  (selbst bearbeitbar), eine Zeile je Eintrag:

    - Erika Mustermann (Ehefrau, Frau): 05.06.1975 (Geburtstag)
    - Otto Mustermann (Großvater): 01.02.1920 (Geburtstag, verstorben 1990)
    - Hochzeitstag: 20.08.2005 (Jubiläum)
    - Anna Beispiel: 12.03. (Geburtstag)                      <- Jahr ist optional

Sprachbefehle:
    "Merk dir, Anna hat am 12. März 1985 Geburtstag"     "Unser Hochzeitstag ist am 20. August 2005"
    "Wann hat Erika Geburtstag?"   "Wie alt wird meine Mutter?"   "Wann ist unser Hochzeitstag?"
    "Wer hat bald Geburtstag?"   "Welche Jubiläen stehen an?"   "Was steht in den nächsten 30 Tagen an?"
    "Lösch den Geburtstag von Anna"  (mit Rückfrage)

Erinnerungen: am Tag selbst und je Art ein paar Tage vorher (JAHRESTAGE_VORWARN_*), nie doppelt (auch nach Neustart).
"""

import os
import re
import json
import time
import threading
from datetime import date, datetime, timedelta

import config
import notizen


def norm(s):
    """Kleinschreibung mit ae/oe/ue/ss (Umlaute ausgeschrieben) - einheitlich für alle Vergleiche in dieser Datei."""
    return s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _gross(t):
    return t[:1].upper() + t[1:]

from wetter import _MONATE

_lock = threading.RLock()
_cache = {"pfad": None, "mtime": None, "eintraege": []}
_pending = None

_MON = {norm(m): i + 1 for i, m in enumerate(_MONATE)}
_MON["maerz"] = 3
_MON["marz"] = 3
_MONATS_RE = "|".join(["januar", "februar", "märz", "maerz", "marz", "april", "mai", "juni", "juli", "august",
                       "september", "oktober", "november", "dezember"])
DATUM = (r"(?P<t>\d{1,2})\s*\.?\s*(?:(?P<mn>" + _MONATS_RE + r")|(?P<mz>\d{1,2})\s*\.)\s*(?:(?P<j>(?:19|20)\d{2}))?")

REL = {"mutter": "deine Mutter", "mama": "deine Mutter", "vater": "dein Vater", "papa": "dein Vater",
       "ehefrau": "deine Frau", "frau": "deine Frau", "ehemann": "dein Mann", "mann": "dein Mann",
       "grossvater": "dein Großvater", "opa": "dein Großvater", "grossmutter": "deine Großmutter", "oma": "deine Großmutter",
       "sohn": "dein Sohn", "tochter": "deine Tochter", "bruder": "dein Bruder", "schwester": "deine Schwester"}
SYN = {"frau": "ehefrau", "mama": "mutter", "papa": "vater", "opa": "grossvater", "oma": "grossmutter", "mann": "ehemann",
       "ich": "ich", "selbst": "ich"}
STOP = {"meine", "mein", "meiner", "meinen", "unser", "unsere", "unseren", "der", "die", "das", "von", "vom", "dem", "den",
        "deine", "dein", "dieses", "jahr", "diesem", "in", "als", "naechstes", "wird", "ist", "hat", "geburtstag", "am"}
HOCHZEIT = {1: "Papierhochzeit", 5: "Holzhochzeit", 10: "Rosenhochzeit", 15: "Kristallhochzeit", 20: "Porzellanhochzeit",
            25: "Silberhochzeit", 30: "Perlenhochzeit", 35: "Leinenhochzeit", 40: "Rubinhochzeit", 45: "Saphirhochzeit",
            50: "Goldene Hochzeit", 55: "Smaragdhochzeit", 60: "Diamantene Hochzeit"}


def _c(name, default):
    return getattr(config, name, default)


# ============================== Datei ==============================
def pfad():
    return os.path.join(notizen._jarvis_ordner(), _c("JAHRESTAGE_DATEI", "Jahrestage.md"))


ZEILE = re.compile(r"^\s*[-*]\s*(?P<name>[^:(]+?)\s*(?:\((?P<alias>[^)]*)\))?\s*:\s*(?P<t>\d{1,2})\.(?P<m>\d{1,2})\.(?P<j>\d{4})?\s*(?P<rest>.*)$")


def _parse_zeile(z):
    m = ZEILE.match(z)
    if not m:
        return None
    t, mo = int(m["t"]), int(m["m"])
    if not (1 <= mo <= 12 and 1 <= t <= 31):
        return None
    rest = m["rest"] or ""
    rl = norm(rest)
    art = "jubilaeum" if re.search(r"jubil|hochzeit|jahrestag", rl) else "geburtstag"
    vm = re.search(r"verstorben\s*(\d{4})?", rl)
    return {"name": m["name"].strip(), "alias": [a.strip() for a in (m["alias"] or "").split(",") if a.strip()],
            "tag": t, "monat": mo, "jahr": int(m["j"]) if m["j"] else None, "art": art,
            "verstorben": (int(vm.group(1)) if vm and vm.group(1) else True) if vm else None}


def laden():
    """Liste der Einträge (wird nur neu gelesen, wenn sich die Datei geändert hat)."""
    try:
        p = pfad()
        mt = os.path.getmtime(p)
    except (OSError, notizen.NotizFehler):
        return []
    with _lock:
        if _cache["pfad"] == p and _cache["mtime"] == mt:
            return _cache["eintraege"]
        eintr = [e for e in (_parse_zeile(z) for z in notizen._lesen(p).splitlines()) if e]
        _cache.update(pfad=p, mtime=mt, eintraege=eintr)
        return eintr


def zeile_text(e):
    d = f"{e['tag']:02d}.{e['monat']:02d}." + (f"{e['jahr']}" if e.get("jahr") else "")
    zusatz = ["Jubiläum" if e["art"] == "jubilaeum" else "Geburtstag"]
    if e.get("verstorben"):
        zusatz.append("verstorben" + (f" {e['verstorben']}" if e["verstorben"] is not True else ""))
    ali = f" ({', '.join(e['alias'])})" if e.get("alias") else ""
    return f"- {e['name']}{ali}: {d} ({', '.join(zusatz)})"


KOPF = ("# Jahrestage\n\nGeburtstage und Jubiläen für JARVIS - eine Zeile je Eintrag, du kannst die Datei selbst bearbeiten.\n"
        "Format: `- Name (Beziehung, Spitzname): TT.MM.JJJJ (Geburtstag)` oder `(Jubiläum)`; das Jahr ist optional, "
        "`verstorben 1985` stellt auf stilles Gedenken um.")


def _schreiben(zeilen_neu=None, ersetze=None, entferne=None):
    """ersetze/entferne: (name, art) eines vorhandenen Eintrags."""
    p = pfad()
    if not os.path.exists(p):
        notizen._anhaengen(p, [], kopf=KOPF)
    text = notizen._lesen(p)
    zeilen = text.splitlines()
    ziel = None
    key = ersetze or entferne
    if key:
        for i, z in enumerate(zeilen):
            e = _parse_zeile(z)
            if e and norm(e["name"]) == norm(key[0]) and e["art"] == key[1]:
                ziel = i
                break
    if ziel is not None:
        if entferne:
            del zeilen[ziel]
        else:
            zeilen[ziel] = zeilen_neu[0]
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(zeilen) + "\n")
    elif zeilen_neu:
        notizen._anhaengen(p, zeilen_neu)
    with _lock:
        _cache["mtime"] = None


# ============================== Rechnen ==============================
def _datum(jahr, monat, tag):
    try:
        return date(jahr, monat, tag)
    except ValueError:                                  # 29.02. in einem Nicht-Schaltjahr
        return date(jahr, monat, 28)


def naechster(e, heute):
    d = _datum(heute.year, e["monat"], e["tag"])
    return d if d >= heute else _datum(heute.year + 1, e["monat"], e["tag"])


def jahre(e, d):
    return d.year - e["jahr"] if e.get("jahr") else None


def vorwarn(e):
    if e.get("verstorben"):
        return [0]
    liste = _c("JAHRESTAGE_VORWARN_JUBILAEUM", [14, 3]) if e["art"] == "jubilaeum" else _c("JAHRESTAGE_VORWARN_GEBURTSTAG", [7, 1])
    return sorted({0, *[int(x) for x in liste]}, reverse=True)


# ============================== Sprechtexte ==============================
def _ist_ich(e):
    return any(norm(a) == "ich" for a in e["alias"])


def _wer(e):
    """('du'|'deine Mutter Sonja'|'Anna', ist_du)"""
    if _ist_ich(e):
        return "du", True
    vor = e["name"].split()[0]
    for a in e["alias"]:
        if norm(a) in REL:
            return f"{REL[norm(a)]} {vor}", False
    return vor, False


def _wann(tage, e):
    d = f"{e['tag']}. {_MONATE[e['monat'] - 1]}"
    return {0: "Heute", 1: "Morgen", 2: "Übermorgen"}.get(tage, f"In {tage} Tagen, am {d},")


def _tage_text(tage):
    return "heute" if tage == 0 else "morgen" if tage == 1 else "übermorgen" if tage == 2 else f"in {tage} Tagen"


def _hz_von(e):
    """'Hochzeitstag Eltern' / 'Hochzeitstag von Klaus und Sonja' -> 'Eltern' / 'Klaus und Sonja'; der eigene Hochzeitstag -> ''."""
    rest = re.sub(r"(?i)^\s*(?:\w*hochzeit\w*)\s*(?:von|vom|der|des)?\s*", "", e["name"]).strip()
    rest = re.sub(r"(?i)^(?:meinen|meine|meiner)\s+", "", rest)
    if not rest or norm(rest) in ("wir", "uns", "unser", "ich"):
        return ""
    return f"deinen Eltern" if norm(rest) == "eltern" else (f"deinen {rest}" if norm(rest) in ("grosseltern", "schwiegereltern", "geschwistern") else rest)


def satz(e, tage, j):
    """Erinnerungssatz für einen Eintrag, der in `tage` Tagen fällig ist (j = Jahre, falls bekannt)."""
    w = _wann(tage, e)
    if e["art"] == "jubilaeum":
        hz = "hochzeit" in norm(e["name"])
        besonders = j is not None and j % 5 == 0
        if hz and j:
            extra = f" Das ist die {HOCHZEIT[j]}." if j in HOCHZEIT else ""
            wessen = _hz_von(e)
            if wessen:
                return f"{w} ist der {j}. Hochzeitstag von {wessen}.{extra}" + (" Vielleicht magst du gratulieren oder etwas vorbereiten." if tage > 0 else " Vielleicht magst du gratulieren.")
            return f"{w} ist euer {j}. Hochzeitstag.{extra}" + (" Vielleicht magst du etwas vorbereiten." if tage > 0 else "")
        if j:
            return f"{w} ist der {j}. Jahrestag: {e['name']}." + (" Ein besonderes Jubiläum." if besonders else "")
        return f"{w} ist {e['name']}."
    wer, du = _wer(e)
    if e.get("verstorben"):
        vor = e["name"].split()[0]
        return (f"{w} wäre {vor} {j} Jahre alt geworden." if j else f"{w} ist der Geburtstag von {vor}.")
    rund = " Das ist ein runder Geburtstag." if j and j % 10 == 0 else ""
    geschenk = " Vielleicht brauchst du noch ein Geschenk." if tage > 0 and not du else ""
    if du:
        return f"{w} hast du Geburtstag, {_c('USER_NAME', '')}." + (f" Du wirst {j}." if j else "") + rund
    alter = f" und wird {j}" if j else ""
    return f"{w} hat {wer} Geburtstag{alter}.{rund}{geschenk}"


def antwort_wann(e, heute):
    d = naechster(e, heute)
    tage = (d - heute).days
    j = jahre(e, d)
    datum = f"{e['tag']}. {_MONATE[e['monat'] - 1]}"
    if e["art"] == "jubilaeum":
        hz = "hochzeit" in norm(e["name"])
        wessen = _hz_von(e) if hz else ""
        was = (f"der Hochzeitstag von {wessen}" if wessen else "euer Hochzeitstag") if hz else e["name"]
        rest = (f" Das wird der {j}. Hochzeitstag." if hz else f" Das wird der {j}. Jahrestag.") if j else ""
        return f"{_gross(was)} ist am {datum}, das ist {_tage_text(tage)}.{rest}"
    wer, du = _wer(e)
    if e.get("verstorben"):
        return f"Der Geburtstag von {e['name'].split()[0]} ist am {datum}, das ist {_tage_text(tage)}."
    if du:
        return f"Du hast am {datum} Geburtstag, das ist {_tage_text(tage)}." + (f" Du wirst {j}." if j else "")
    return f"{_gross(wer)} hat am {datum} Geburtstag, das ist {_tage_text(tage)}." + (f" Das wird der {j}. Geburtstag." if j else "")


# ============================== Erinnerungen ==============================
def _status_pfad():
    return _c("JAHRESTAGE_STATUS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "jahrestage_status.json"))


def _status_laden():
    try:
        with open(_status_pfad(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _status_speichern(st):
    try:
        with open(_status_pfad(), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except OSError:
        pass


def _key(e, d):
    return f"{norm(e['name'])}|{e['art']}|{d.isoformat()}"


def meldungen(heute=None, markieren=True):
    """Fällige Erinnerungstexte (jede Stufe nur einmal)."""
    heute = heute or date.today()
    with _lock:
        st = _status_laden()
        out = []
        for e in laden():
            d = naechster(e, heute)
            tage = (d - heute).days
            offen = [t for t in vorwarn(e) if tage <= t and t not in st.get(_key(e, d), [])]
            if offen:
                out.append((tage, satz(e, tage, jahre(e, d))))
                if markieren:
                    st.setdefault(_key(e, d), []).extend(offen)
        if markieren and out:
            for k in [k for k in st if k.rsplit("|", 1)[1] < (heute.fromordinal(heute.toordinal() - 7)).isoformat()]:
                del st[k]
            _status_speichern(st)
        return [t for _, t in sorted(out)]


def als_erledigt_markieren(e, heute=None):
    """Nach dem Anlegen: nicht gleich vorwarnen (der Nutzer weiß es ja)."""
    heute = heute or date.today()
    with _lock:
        d = naechster(e, heute)
        st = _status_laden()
        tage = (d - heute).days
        st.setdefault(_key(e, d), []).extend([t for t in vorwarn(e) if tage <= t])
        _status_speichern(st)


def briefing_teil(heute=None):
    heute = heute or date.today()
    neu = meldungen(heute, True)
    for e in laden():
        d = naechster(e, heute)
        if d == heute:
            t = satz(e, 0, jahre(e, d))
            if t not in neu:
                neu.append(t)
    return " ".join(neu)


def hud_liste(n=4, heute=None):
    heute = heute or date.today()
    out = []
    for e in laden():
        d = naechster(e, heute)
        j = jahre(e, d)
        name = e["name"].split()[0] if e["art"] == "geburtstag" else e["name"]
        out.append({"wer": name, "art": e["art"], "tage": (d - heute).days, "jahre": j,
                    "datum": f"{d.day:02d}.{d.month:02d}.", "gedenken": bool(e.get("verstorben"))})
    return sorted(out, key=lambda x: x["tage"])[:n]


# ============================== Name finden ==============================
def _tokens(s):
    return [SYN.get(w, w) for w in re.findall(r"[a-z0-9]+", norm(s)) if w not in STOP]


def finde(phrase, art=None):
    """Passende Einträge (beste Treffer). Leere Liste = nichts gefunden."""
    q = set(_tokens(phrase))
    if not q:
        return []
    best, treffer = 0, []
    for e in laden():
        if art and e["art"] != art:
            continue
        et = set(_tokens(e["name"]))
        for a in e["alias"]:
            et |= set(_tokens(a)) | {SYN.get(norm(a), norm(a))}
        s = len(q & et) if q <= et else 0              # alle genannten Wörter müssen passen
        if s > best:
            best, treffer = s, [e]
        elif s == best and s > 0:
            treffer.append(e)
    return treffer


# ============================== Befehle ==============================
_PREFIX = re.compile(r"^(?:jarvis[ ,]*)?(?:merk(?:e)? dir|trag(?:e)?(?: bitte)?(?: ein)?|notiere(?: dir)?|speichere|vergiss nicht)[ ,:]*", re.I)


def _datum_aus(m):
    t = int(m.group("t"))
    if m.group("mn"):
        mo = _MON[norm(m.group("mn"))]
    else:
        mo = int(m.group("mz"))
    j = int(m.group("j")) if m.group("j") else None
    if not (1 <= mo <= 12 and 1 <= t <= 31):
        return None
    return t, mo, j


def _bereinigen(name):
    n = re.sub(r"^(?:meine|mein|meinen|meiner|unser|unsere|der|die|das)\s+", "", name.strip(" ,.!?"), flags=re.I)
    return n.strip()


def erkenne(cmd):
    """Gibt einen Intent (dict) oder None zurück. Läuft vor Timer/Notizen, deshalb nur bei eindeutigen Wörtern."""
    raw = re.sub(r"\s+", " ", cmd.strip(" .!?")).strip()
    s = re.sub(r"^jarvis[ ,]*", "", raw, flags=re.I)
    n = norm(s)

    # ---- Eintragen ----
    kern = _PREFIX.sub("", s).strip()
    kern = kern.replace("ä", "ae").replace("Ä", "Ae") if re.search(r"jubil[äa]e?um", kern, re.I) else kern
    if re.search(r"geburtstag|geboren|jubil|\w+tag\b.*\b(?:ist|war) am\b", n):
        pats = [
            (r"^(?P<name>.+?)\s+hat\s+(?:am\s+)?" + DATUM + r"\s+geburtstag$", "geburtstag"),
            (r"^(?P<name>.+?)\s+hat\s+geburtstag\s+am\s+" + DATUM + r"$", "geburtstag"),
            (r"^(?:der\s+)?geburtstag\s+von\s+(?P<name>.+?)\s+ist\s+am\s+" + DATUM + r"$", "geburtstag"),
            (r"^(?P<name>.+?)\s+(?:ist|wurde)\s+am\s+" + DATUM + r"\s+geboren$", "geburtstag"),
            (r"^(?:ein\s+)?jubilaeum\s+(?P<name>.+?)\s+am\s+" + DATUM + r"$", "jubilaeum"),
            (r"^(?:unser|unsere|mein|meine|der|die|das)\s+(?P<name>\w*(?:tag|jubilaeum)(?:\s+(?:von|vom)\s+.+?)?)\s+(?:ist|war)\s+am\s+" + DATUM + r"$", "jubilaeum"),
        ]
        for p, art in pats:
            m = re.match(p, kern, re.I)
            if m:
                d = _datum_aus(m)
                if d:
                    name = _bereinigen(m.group("name"))
                    if name:
                        if art == "jubilaeum":
                            name = name[:1].upper() + name[1:]
                        return {"art": "eintragen", "typ": art, "name": name, "tag": d[0], "monat": d[1], "jahr": d[2]}

    # ---- "Merk dir, Ralf hat am 14. Januar" (Satz abgeschnitten) -> nachfragen, ob es ein Geburtstag ist ----
    if kern != s:
        m = re.match(r"^(?P<name>.+?)\s+hat\s+(?:am\s+)?" + DATUM + r"[\s.…]*$", kern, re.I)
        if m:
            d = _datum_aus(m)
            name = _bereinigen(m.group("name"))
            if d and name and len(name.split()) <= 4:
                return {"art": "frage_geburtstag", "typ": "geburtstag", "name": name, "tag": d[0], "monat": d[1], "jahr": d[2]}

    # ---- Termine allgemein ("Habe ich irgendwelche Termine?") ----
    if re.match(r"^(?:habe ich|hab ich|gibt es|gibt's)\s+(?:\w+\s+){0,2}?termine\b|^welche termine\b.*\b(?:habe ich|stehen an|gibt es)\b|^was steht (?:diese woche|in den naechsten tagen|bald) an\b", n):
        return {"art": "termine"}

    # ---- Löschen ----
    m = re.match(r"^(?:loesch|loesche|loeschen|entfern|entferne|entfernen|streich|streiche)\s+(?:bitte\s+)?(?:den|die|das|dem)?\s*"
                 r"(?P<was>geburtstag|jubilaeum|eintrag|termin)\s+(?:von|vom|der|des|fuer)?\s*(?P<name>.+)$", n)
    if m:
        return {"art": "loeschen", "name": m["name"], "typ": "jubilaeum" if m["was"] == "jubilaeum" else None}

    # ---- Listen ----
    m = re.search(r"(?:naechsten|kommenden)\s+(\d{1,3})\s+tage\w*.*\b(?:an|ansteht|anstehen)\b|\b(?:an|ansteht|anstehen)\b.*(?:naechsten|kommenden)\s+(\d{1,3})\s+tage", n)
    if m and re.search(r"was|welche|gibt|steht|stehen", n):
        return {"art": "liste", "tage": int(m.group(1) or m.group(2)), "typ": None}
    if re.search(r"\bwer hat (?:als naechstes|bald|demnaechst|naechste\w*|in naechster zeit) geburtstag|"
                 r"\b(?:welche|wann sind die|was sind die)\s+(?:naechsten\s+)?geburtstage|naechste\w* geburtstage?\b|geburtstage?\s+(?:stehen|kommen)\b", n):
        return {"art": "liste", "tage": None, "typ": "geburtstag"}
    if re.search(r"\b(?:welche|was fuer|naechste\w*|kommende\w*)\s+(?:\w+\s+)?jubilaeen?\b|\bjubilaeen?\s+(?:stehen|kommen)|stehen.*jubilaeen", n):
        return {"art": "liste", "tage": None, "typ": "jubilaeum"}

    # ---- Fragen ----
    m = re.match(r"^wann\s+(?:hat|habe)\s+(?P<n>.+?)\s+geburtstag$", n)
    if m:
        return {"art": "wann", "name": m["n"], "typ": "geburtstag"}
    m = re.match(r"^wann\s+ist\s+(?:der\s+)?geburtstag\s+(?:von|vom)\s+(?P<n>.+)$", n)
    if m:
        return {"art": "wann", "name": m["n"], "typ": "geburtstag"}
    m = re.match(r"^wie\s+alt\s+(?:wird|werde|wirst)\s+(?P<n>.+?)(?:\s+(?:dieses|in diesem|naechstes)\s+jahr)?$", n)
    if m:
        return {"art": "wann", "name": m["n"], "typ": "geburtstag"}
    m = re.match(r"^wann\s+ist\s+(?P<n>(?:unser|unsere|mein|meine|der|die|das)\s+.+)$", n)
    if m:
        return {"art": "wann", "name": m["n"], "typ": "jubilaeum", "leise": True}
    return None


def hat_rueckfrage():
    return bool(_pending and time.time() <= _pending["ablauf"])


def pending_antwort(text):
    """Ja/Nein auf eine offene Rückfrage -> Sprechtext oder None (= war keine Antwort, Rückfrage verfällt)."""
    global _pending
    from agent import JA, NEIN
    with _lock:
        p, _pending = _pending, None
    if not p or time.time() > p["ablauf"]:
        return None
    w = set(re.findall(r"[a-z]+", norm(text)))
    if w & NEIN:
        return "Okay, abgebrochen."
    if w & JA and len(w) <= 6:
        try:
            return _ausfuehren_bestaetigt(p)
        except notizen.NotizFehler as e:
            return str(e)
    return None


def _ausfuehren_bestaetigt(p):
    if p["aktion"] == "geburtstag":
        it = dict(p["it"], art="eintragen")
        return _fuehre_aus(it, date.today())
    e = p["e"]
    if p["aktion"] == "loeschen":
        _schreiben(entferne=(e["name"], e["art"]))
        return f"Gelöscht: {e['name']}."
    _schreiben([zeile_text(p["neu"])], ersetze=(e["name"], e["art"]))
    als_erledigt_markieren(p["neu"])
    return f"Okay, {p['neu']['name']} ist aktualisiert."


def fuehre_aus(it, heute=None):
    global _pending
    heute = heute or date.today()
    try:
        return _fuehre_aus(it, heute)
    except notizen.NotizFehler as e:
        return str(e)


def _fuehre_aus(it, heute):
    global _pending
    art = it["art"]
    if art == "eintragen":
        neu = {"name": it["name"], "alias": [], "tag": it["tag"], "monat": it["monat"], "jahr": it["jahr"],
               "art": it["typ"], "verstorben": None}
        rel = norm(it["name"])
        if rel in REL:                                   # "meine Mutter" -> Beziehung merken
            neu["alias"] = [it["name"].capitalize()]
        if rel == "ich":
            neu["name"], neu["alias"] = _c("USER_NAME", ""), ["ich"]
        vorhanden = next((e for e in laden() if norm(e["name"]) == norm(neu["name"]) and e["art"] == neu["art"]), None)
        if vorhanden is None:                            # "Anna" steht evtl. schon als "Anna Beispiel", "meine Mutter" unter ihrem Namen
            q = set(_tokens(neu["name"]))
            pas = [e for e in laden() if e["art"] == neu["art"] and q and q <= (set(_tokens(e["name"])) | {SYN.get(norm(a), norm(a)) for a in e["alias"]})]
            vorhanden = pas[0] if len(pas) == 1 else None
        datum_s = f"{neu['tag']}. {_MONATE[neu['monat'] - 1]}" + (f" {neu['jahr']}" if neu["jahr"] else "")
        if vorhanden:
            if (vorhanden["tag"], vorhanden["monat"], vorhanden["jahr"]) == (neu["tag"], neu["monat"], neu["jahr"]):
                return f"Das steht schon so in der Liste: {vorhanden['name']}, {datum_s}."
            neu["name"], neu["alias"] = vorhanden["name"], vorhanden["alias"]
            neu["verstorben"] = vorhanden.get("verstorben")
            if neu["jahr"] is None:
                neu["jahr"] = vorhanden["jahr"]
            alt = f"{vorhanden['tag']}. {_MONATE[vorhanden['monat'] - 1]}" + (f" {vorhanden['jahr']}" if vorhanden["jahr"] else "")
            _pending = {"aktion": "ersetzen", "e": vorhanden, "neu": neu, "ablauf": time.time() + 60}
            return f"Für {vorhanden['name']} steht schon der {alt} in der Liste. Soll ich ihn durch den {datum_s} ersetzen?"
        _schreiben([zeile_text(neu)])
        als_erledigt_markieren(neu)
        was = "das Jubiläum" if neu["art"] == "jubilaeum" else "den Geburtstag von"
        return f"Okay, ich habe {was} {neu['name']} am {datum_s} gespeichert."

    if art == "frage_geburtstag":
        datum_s = f"{it['tag']}. {_MONATE[it['monat'] - 1]}" + (f" {it['jahr']}" if it["jahr"] else "")
        _pending = {"aktion": "geburtstag", "it": it, "ablauf": time.time() + 30}
        return f"Meinst du den Geburtstag von {it['name']} am {datum_s}?"

    if art == "termine":
        teile = []
        try:
            import proaktiv, zeit
            for i in (proaktiv._planer.snapshot() if proaktiv._planer else []):
                if i["status"] == "wartet" and i["rest_s"] <= 86400:
                    was = i["label"] or {"timer": "Timer", "wecker": "Wecker", "erinnerung": "Erinnerung"}[i["art"]]
                    teile.append(f"{_gross(was)}: {i['tag']} um {zeit.uhr_sprech(datetime.fromtimestamp(i['ziel_ts']))}.")
        except Exception:
            pass
        kal = False
        try:
            import kalender
            if kalender.aktiv() and kalender.status()["geladen"]:
                kal = True
                jetzt = datetime.now()
                for t in kalender.termine(jetzt, jetzt + timedelta(days=7)):
                    if t["art"] != "feiertag" and (t["ganztag"] or t["ende"] >= jetzt):
                        teile.append(kalender._satz_termin(t, jetzt, True).capitalize() + ".")
        except Exception:
            pass
        for e in sorted(laden(), key=lambda e: naechster(e, heute)):
            d = naechster(e, heute)
            if (d - heute).days <= 14:
                teile.append(satz(e, (d - heute).days, jahre(e, d)))
        if teile:
            return " ".join(teile[:6])
        return ("In den nächsten 14 Tagen steht nichts an." if kal else
                "In den nächsten 14 Tagen steht nichts an. Einen Kalender habe ich noch nicht angebunden, "
                "nur Geburtstage, Jubiläen, Timer und Erinnerungen.")

    if art == "loeschen":
        treffer = finde(it["name"], it.get("typ"))
        if not treffer:
            return f"Ich finde in der Liste niemanden zu „{it['name']}“."
        if len(treffer) > 1:
            return "Das passt auf mehrere Einträge: " + ", ".join(t["name"] for t in treffer[:4]) + ". Wen meinst du genau?"
        e = treffer[0]
        _pending = {"aktion": "loeschen", "e": e, "ablauf": time.time() + 60}
        return f"Soll ich den Eintrag {e['name']} wirklich löschen?"

    if art == "wann":
        if not laden():
            return "In der Liste stehen noch keine Geburtstage oder Jubiläen."
        treffer = finde(it["name"], it.get("typ"))
        if not treffer:
            if it.get("leise"):
                return None                               # "Wann ist unser Urlaub?" ist keine Jahrestag-Frage
            return f"Zu „{it['name']}“ habe ich keinen Geburtstag gespeichert."
        if len(treffer) > 1 and re.search(r"\bunser", it["name"]):
            eigene = [e for e in treffer if not _hz_von(e)]
            treffer = eigene or treffer
        return " ".join(antwort_wann(e, heute) for e in treffer[:3])

    if art == "liste":
        eintr = [e for e in laden() if not it.get("typ") or e["art"] == it["typ"]]
        if not eintr:
            return "In der Liste stehen noch keine Einträge dazu."
        zeilen = sorted(((naechster(e, heute) - heute).days, e) for e in eintr)
        if it.get("tage") is not None:
            zeilen = [z for z in zeilen if z[0] <= it["tage"]]
            if not zeilen:
                return f"In den nächsten {it['tage']} Tagen steht nichts an."
        else:
            zeilen = zeilen[:3]
        teile = []
        for tage, e in zeilen[:6]:
            j = jahre(e, naechster(e, heute))
            if e["art"] == "jubilaeum":
                teile.append(f"{e['name']} {_tage_text(tage)}" + (f", {j}. Mal" if j else ""))
            else:
                wer, _ = _wer(e)
                teile.append(f"{wer} {_tage_text(tage)}" + (f", {j} Jahre" if j else ""))
        kopf = "Als Nächstes: " if it.get("tage") is None else f"In den nächsten {it['tage']} Tagen: "
        return kopf + "; ".join(teile) + "."
    return None
