"""
muell.py - Müllabfuhr-Termine (ICS-Datei der Abfallwirtschaft)

Jedes Jahr gibt es eine neue ICS-Datei (z.B. abfuhrtermine_2027.ics von deiner Stadt/Abfallwirtschaft). Einfach in den Ordner "muell" neben
hud_server.py legen - alle .ics-Dateien dort werden gelesen, alte und neue zusammen (Jahreswechsel funktioniert).
Wird eine Datei ausgetauscht oder ergänzt, liest Jarvis sie von selbst neu ein (kein Neustart nötig).
Hinweis: Jarvis sagt ab ca. 30 Tage vor dem letzten Termin Bescheid, dass eine neue Datei fehlt.

Sprachbefehle:  "Wann wird der Müll abgeholt?"  "Welche Tonne kommt morgen?"  "Wann kommt die gelbe Tonne?"
                "Wann ist die nächste Altpapier-Abholung?"  "Wird heute Müll abgeholt?"  "Wann kommen die Weihnachtsbäume?"
Von allein:     Morgenbriefing/Startbriefing nennen "Heute/Morgen wird ... abgeholt"; am Vorabend (MUELL_ERINNERUNG_UHRZEIT)
                erinnert Jarvis, wenn er läuft: "Morgen kommt die gelbe Tonne - bitte rausstellen."

Config (optional):
    MUELL_AKTIV = True
    MUELL_ORDNER = <PROJEKT_DIR>\\muell
    MUELL_ERINNERUNG_UHRZEIT = "18:00"      # "" = keine Vorabend-Erinnerung
    MUELL_WARNUNG_TAGE = 30                 # so früh vor dem Ende der Daten wird auf die fehlende neue Datei hingewiesen
    MUELL_URL = ""                          # optional: feste Download-Adresse der ICS-Datei; Jarvis holt sie dann selbst
    MUELL_URL_TAGE = 7                      # so alt darf die geholte Datei werden, bevor er sie neu lädt
    MUELL_ABHOLUNG_BIS = 12                 # ab dieser Stunde gilt die Tonne von heute als abgeholt
                                            # (verschwindet aus dem HUD, "Wann kommt ...?" nennt den nächsten Termin)
"""
import os
import re
import glob
import threading
from datetime import date, datetime, timedelta

import config

_WT = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_lock = threading.RLock()
_cache = {"sig": None, "ereignisse": []}
_FARBEN = {"grau": "graue", "blau": "blaue", "braun": "braune", "gelb": "gelbe", "gruen": "grüne", "grün": "grüne",
           "schwarz": "schwarze", "rot": "rote", "orange": "orange"}


def _c(name, default):
    return getattr(config, name, default)


def aktiv():
    return bool(_c("MUELL_AKTIV", True))


def ordner():
    return _c("MUELL_ORDNER", os.path.join(_c("PROJEKT_DIR", "."), "muell"))


def _dateien():
    return sorted(glob.glob(os.path.join(ordner(), "*.ics")) + glob.glob(os.path.join(ordner(), "*.ICS")))


# ----------------------------- ICS lesen -----------------------------
def _entfalten(text):
    """RFC 5545: Zeilen, die mit Leerzeichen/Tab beginnen, gehören zur vorigen Zeile."""
    out = []
    for z in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if z[:1] in (" ", "\t") and out:
            out[-1] += z[1:]
        else:
            out.append(z)
    return out


def _entschuetzen(s):
    return s.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\N", " ").replace("\\\\", "\\")


def parse(text):
    """ICS-Text -> Liste {datum, text, name, farbe, typ, info}. Nur ganztägige/datierte Ereignisse (so liefert es die Abfallwirtschaft)."""
    out, ev = [], None
    for z in _entfalten(text):
        if z.strip() == "BEGIN:VEVENT":
            ev = {}
        elif z.strip() == "END:VEVENT":
            if ev and ev.get("start") and ev.get("summary"):
                e = _ereignis(ev)
                if e:
                    out.append(e)
            ev = None
        elif ev is not None and ":" in z:
            key, _, val = z.partition(":")
            k = key.split(";")[0].upper()
            if k == "SUMMARY":
                ev["summary"] = _entschuetzen(val).strip()
            elif k == "DTSTART":
                m = re.match(r"(\d{4})(\d{2})(\d{2})", val.strip())
                if m:
                    try:
                        ev["start"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    except ValueError:
                        pass
            elif k == "DESCRIPTION":
                ev["info"] = _entschuetzen(val).strip()
    return out


def _ereignis(ev):
    roh = re.sub(r"\s*//.*$", "", ev["summary"]).strip()                  # "Restmüll (grau) // GEM" -> "Restmüll (grau)"
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", roh)
    name = (m.group(1) if m else roh).strip()
    farbe = (m.group(2).strip().lower() if m else "")
    adj = _FARBEN.get(farbe)
    sprech = f"{name} ({adj} Tonne)" if adj else name
    return {"datum": ev["start"], "text": roh, "name": name, "farbe": farbe if adj else "", "sprech": sprech,
            "typ": (farbe if adj else name.lower()), "info": ev.get("info", "")}


def ereignisse():
    """Alle Termine aus allen Dateien (dedupliziert, sortiert). Liest nur neu, wenn sich Dateien geändert haben."""
    dateien = _dateien()
    sig = []
    for p in dateien:
        try:
            st = os.stat(p)
            sig.append((p, st.st_mtime, st.st_size))
        except OSError:
            pass
    sig = tuple(sig)
    with _lock:
        if _cache["sig"] == sig:
            return _cache["ereignisse"]
    alle, gesehen = [], set()
    for p, _, _ in sig:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                roh = f.read()
        except OSError:
            continue
        for e in parse(roh):
            k = (e["datum"], e["text"])
            if k not in gesehen:
                gesehen.add(k)
                alle.append(e)
    alle.sort(key=lambda e: (e["datum"], e["text"]))
    with _lock:
        _cache["sig"], _cache["ereignisse"] = sig, alle
    return alle


# ----------------------------- Abfragen -----------------------------
def am_tag(d):
    return [e for e in ereignisse() if e["datum"] == d]


def naechste(heute=None, anzahl_tage=1, typ=None):
    """Nächste Abholtage ab heute (inkl.), jeweils als (Datum, [Ereignisse]); anzahl_tage Termine."""
    heute = heute or date.today()
    tage = {}
    for e in ereignisse():
        if e["datum"] >= heute and (typ is None or _passt_typ(e, typ)):
            tage.setdefault(e["datum"], []).append(e)
    return [(d, tage[d]) for d in sorted(tage)[:anzahl_tage]]


def _erster_tag(heute):
    """Ab MUELL_ABHOLUNG_BIS Uhr ist die heutige Abholung vorbei -> erst ab morgen suchen."""
    try:
        bis = int(_c("MUELL_ABHOLUNG_BIS", 12))
    except (TypeError, ValueError):
        bis = 12
    if heute == date.today() and datetime.now().hour >= bis:
        return heute + timedelta(days=1)
    return heute


def _fold(s):
    import wissen
    return wissen.fold(s)


def _passt_typ(e, typ):
    return e["typ"] == typ or typ in _fold(e["name"]) or bool(e["farbe"] and typ == e["farbe"])


def _tag(d, heute):
    n = (d - heute).days
    if n == 0:
        return "heute"
    if n == 1:
        return "morgen"
    if n == 2:
        return "übermorgen"
    return f"am {_WT[d.weekday()]}, den {d.day}.{d.month}."


def _liste(es):
    namen = []
    for e in es:
        if e["sprech"] not in namen:
            namen.append(e["sprech"])
    return " und ".join(namen) if len(namen) <= 2 else ", ".join(namen[:-1]) + " und " + namen[-1]


def _in_tagen(d, heute):
    n = (d - heute).days
    return "" if n <= 2 else f", in {n} Tagen"


def briefing_teil(heute=None):
    """'Heute wird ... abgeholt.' (leer, wenn nichts)."""
    if not aktiv():
        return ""
    heute = heute or date.today()
    es = am_tag(heute)
    return f"Heute wird abgeholt: {_liste(es)}." if es else ""


def morgen_teil(heute=None):
    if not aktiv():
        return ""
    heute = heute or date.today()
    es = am_tag(heute + timedelta(days=1))
    return f"Morgen wird abgeholt: {_liste(es)}." if es else ""


def warnung(heute=None):
    """Hinweis, wenn die Termine bald enden (oder gar keine da sind). Leer = alles in Ordnung / Funktion nicht benutzt."""
    if not aktiv() or not os.path.isdir(ordner()):
        return ""
    heute = heute or date.today()
    es = ereignisse()
    if not es:
        return ""
    ende = es[-1]["datum"]
    if (ende - heute).days <= int(_c("MUELL_WARNUNG_TAGE", 30)):
        return ("Die Müllabfuhr-Termine reichen nur noch bis zum " + f"{ende.day}.{ende.month}.{ende.year}"
                if ende >= heute else "Die Müllabfuhr-Termine sind abgelaufen") + \
               ". Bitte lege die neue ICS-Datei der Abfallwirtschaft in den Ordner muell."
    return ""


def hud_liste(heute=None, n=2):
    """Für das HUD: nächste Abholtage innerhalb von 7 Tagen."""
    if not aktiv():
        return []
    heute = heute or date.today()
    out = []
    for d, es in naechste(_erster_tag(heute), n):
        tage = (d - heute).days
        if tage > 7:
            break
        farben = []
        for e in es:
            if e["farbe"] and e["farbe"] not in farben:
                farben.append(e["farbe"])
        out.append({"tage": tage, "datum": f"{d.day:02d}.{d.month:02d}.", "namen": [e["name"] for e in es], "farben": farben})
    return out


def meldung_vorabend(heute=None, jetzt=None):
    """Text für die Vorabend-Erinnerung oder ''."""
    heute = heute or date.today()
    es = am_tag(heute + timedelta(days=1))
    if not es:
        return ""
    return f"Morgen wird abgeholt: {_liste(es)}. Bitte stelle sie heute noch raus."


# ----------------------------- Sprache -----------------------------
_WORT_RE = re.compile(r"muell|tonne|abfuhr|abholung|abgeholt|altpapier|papiertonne|bioabfall|biomuell|biotonne|restmuell|"
                      r"gelber sack|gelbe[rn]? (?:sack|saecke|tonne)|verpackung|gruenschnitt|weihnachtsbaum|weihnachtsbaeume|"
                      r"elektrokleinger|elektrosammlung")
_FRAGE_RE = re.compile(r"\b(?:wann|welche|welcher|welches|wird|werden|kommt|kommen|ist|sind|steht|stehen|gibt)\b")
_TYPEN = (("grau", r"\bgrau\w*|restmuell"), ("blau", r"\bblau\w*|altpapier|papier"),
          ("braun", r"\bbraun\w*|bio\w*"), ("gelb", r"\bgelb\w*|verpackung\w*|wertstoff\w*"),
          ("weihnachtsb", r"weihnachtsb\w*|tannenbaum|tannenbaeume"), ("elektro", r"elektro\w*"),
          ("gruenschnitt", r"gruenschnitt|gruenabfall\w*"))


def _norm(s):
    import wissen
    return " ".join(re.sub(r"^jarvis[ ,]*", "", wissen.fold(s)).strip(" .!?").split())


def erkenne(cmd, heute=None):
    """Intent-Dict oder None. Greift nur bei Müll-Wörtern in einer Frage."""
    if not aktiv() or not ereignisse():
        return None
    n = _norm(cmd)
    if not _WORT_RE.search(n) or not _FRAGE_RE.search(n):
        return None
    if re.search(r"\b(?:setz|stell|schalt|erinner|notier|schreib|schick|merk)\w*", n):
        return None                                   # "Erinnere mich an den Müll" gehört den Erinnerungen
    heute = heute or date.today()
    typ = next((t for t, rx in _TYPEN if re.search(rx, n)), None)
    tag = None
    if re.search(r"\bheute\b", n):
        tag = heute
    elif re.search(r"\bubermorgen|uebermorgen\b", n):
        tag = heute + timedelta(days=2)
    elif re.search(r"\bmorgen\b", n):
        tag = heute + timedelta(days=1)
    return {"art": "muell", "typ": typ, "tag": tag}


def fuehre_aus(intent, heute=None):
    return _fuehre_aus(intent, heute).replace("..", ".")


def _fuehre_aus(intent, heute=None):
    heute = heute or date.today()
    typ, tag = intent.get("typ"), intent.get("tag")
    if tag is not None:
        es = [e for e in am_tag(tag) if typ is None or _passt_typ(e, typ)]
        wann = _tag(tag, heute).capitalize()
        if es:
            return f"{wann} wird abgeholt: {_liste(es)}."
        sp = {"grau": "die graue Tonne", "blau": "die blaue Tonne", "braun": "die braune Tonne", "gelb": "die gelbe Tonne",
              "weihnachtsb": "die Weihnachtsbaum-Abholung", "elektro": "die Elektrokleingeräte-Sammlung",
              "gruenschnitt": "der Grünschnitt"}
        nx = naechste(heute, 1, typ)
        zusatz = (f" Als Nächstes kommt {_liste([nx[0][1][0]])} {_tag(nx[0][0], heute)}{_in_tagen(nx[0][0], heute)}."
                  if (nx and typ) else
                  f" Als Nächstes wird abgeholt: {_liste(nx[0][1])} {_tag(nx[0][0], heute)}{_in_tagen(nx[0][0], heute)}." if nx else "")
        return (f"{wann} kommt {sp.get(typ, 'das')} nicht." if typ else f"{wann} wird nichts abgeholt.") + zusatz
    if typ:
        nx = naechste(_erster_tag(heute), 2, typ)
        if not nx:
            return "Dazu finde ich keinen weiteren Termin in den Müllabfuhr-Daten." + _warn_satz(heute)
        text = f"{_liste([nx[0][1][0]])} kommt {_tag(nx[0][0], heute)}{_in_tagen(nx[0][0], heute)}."
        if len(nx) > 1:
            text += f" Danach {_tag(nx[1][0], heute)}."
        return text
    nx = naechste(_erster_tag(heute), 3)
    if not nx:
        return "Ich finde keine weiteren Müllabfuhr-Termine." + _warn_satz(heute)
    teile = [f"{_tag(d, heute)} {_liste(es)}" for d, es in nx]
    return "Als Nächstes wird abgeholt: " + "; ".join(teile) + "." + _warn_satz(heute)


def _warn_satz(heute):
    w = warnung(heute)
    return (" " + w) if w else ""


# ----------------------------- Dauerbetrieb -----------------------------
_erinnert = {"tag": None}


def erinnerung_faellig(jetzt=None):
    """Vorabend-Erinnerung: genau einmal pro Tag ab MUELL_ERINNERUNG_UHRZEIT. Gibt Text oder ''. (Ruhezeit prüft der Aufrufer.)"""
    if not aktiv():
        return ""
    z = str(_c("MUELL_ERINNERUNG_UHRZEIT", "18:00"))
    m = re.match(r"^(\d{1,2}):(\d{2})$", z)
    if not m:
        return ""
    jetzt = jetzt or datetime.now()
    if jetzt.hour * 60 + jetzt.minute < int(m.group(1)) * 60 + int(m.group(2)):
        return ""
    if _erinnert["tag"] == jetzt.date():
        return ""
    _erinnert["tag"] = jetzt.date()
    return meldung_vorabend(jetzt.date())


# ----------------------------- Automatisch holen (optional) -----------------------------
def holen(log=print, jetzt=None):
    """Lädt MUELL_URL (eine Adresse oder eine Liste) nach <ordner>/online_N.ics, wenn die Datei fehlt oder älter als
    MUELL_URL_TAGE ist. Nur gültige ICS-Daten ersetzen die alte Datei; bei Fehlern bleibt alles, wie es war."""
    import time
    import urllib.request
    urls = _c("MUELL_URL", "")
    urls = [urls] if isinstance(urls, str) else list(urls or [])
    urls = [u.strip() for u in urls if u and u.strip()]
    if not urls or not aktiv():
        return False
    try:
        os.makedirs(ordner(), exist_ok=True)
    except OSError:
        return False
    neu = False
    maxalter = float(_c("MUELL_URL_TAGE", 7)) * 86400
    for i, u in enumerate(urls, 1):
        ziel = os.path.join(ordner(), f"online_{i}.ics")
        try:
            if os.path.exists(ziel) and time.time() - os.path.getmtime(ziel) < maxalter:
                continue
        except OSError:
            pass
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (JARVIS)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                daten = r.read(5_000_000)
            text = daten.decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" not in text or not parse(text):
                log("[Müll]: Die Antwort der Adresse enthält keine Abfuhrtermine - alte Datei bleibt.")
                continue
            tmp = ziel + ".tmp"
            with open(tmp, "wb") as f:
                f.write(daten)
            os.replace(tmp, ziel)
            neu = True
            log(f"[Müll]: Abfuhrtermine aktualisiert ({len(parse(text))} Termine).")
        except Exception as e:
            log(f"[Müll]: Download nicht möglich ({e.__class__.__name__}) - ich nutze die vorhandenen Dateien.")
    return neu


def starten(log=print):
    """Hintergrund: beim Start und danach einmal pro Tag prüfen, ob die Datei neu geholt werden soll."""
    import time
    if not str(_c("MUELL_URL", "")).strip() and not _c("MUELL_URL", ""):
        return

    def lauf():
        time.sleep(30)
        while True:
            try:
                holen(log)
            except Exception:
                pass
            time.sleep(24 * 3600)
    threading.Thread(target=lauf, daemon=True).start()
