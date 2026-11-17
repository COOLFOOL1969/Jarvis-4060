"""
startbriefing.py - "Willkommen zurück": Jarvis sagt beim Start, was seit dem letzten Mal wichtig geworden ist.

Der Rechner läuft nicht rund um die Uhr, deshalb kann Jarvis sich nicht zur passenden Zeit melden. Stattdessen merkt er
sich, wann er zuletzt da war (jarvis_zuletzt.json), und fasst beim nächsten Start zusammen:
  * erster Start des Tages: das normale Morgenbriefing (Wetter, Aufgaben, Termine ...) plus das Neue
  * späterer Start nach längerer Pause: "Willkommen zurück ..." nur mit dem Neuen
  * kurzer Neustart (weniger als STARTBRIEFING_MIN_WEG_MIN Minuten weg): nichts
"Neu" heißt: neue Kalendereinträge (nächste 14 Tage), was morgen ansteht (Termin, Feiertag, Geburtstag),
Notizen in der Inbox, offene Fenster und Steckdosen, die noch Strom ziehen.
Bei stummem Mikrofon wird nicht gesprochen (Ruhezeit gilt hier nicht: du hast den Rechner ja gerade gestartet).

Config (optional):
    STARTBRIEFING_AKTIV = True
    STARTBRIEFING_MIN_WEG_MIN = 45        # kürzer weg -> kein Briefing
    STARTBRIEFING_LAUF_W = 25             # Steckdose mit mehr Watt gilt als "läuft noch"
    STARTBRIEFING_IGNORIEREN = ()         # Namensteile von Geräten, die nie genannt werden (z.B. ("router", "tv"))
    STARTBRIEFING_VERZOEGERUNG_S = 45     # so lange nach dem Start warten (Geräte, Kalender, Whisper laden)
"""
import os
import re
import json
import time
import threading
from datetime import datetime, date, timedelta

import config

_WT = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_lock = threading.RLock()


def _c(name, default):
    return getattr(config, name, default)


def _pfad():
    return os.path.join(_c("PROJEKT_DIR", "."), "jarvis_zuletzt.json")


def _lade():
    try:
        with open(_pfad(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _schluessel(t):
    return f"{t['titel']}|{t['start'].isoformat()}"


def _kalender_keys(jetzt):
    try:
        import kalender
        if not kalender.aktiv() or not kalender.status()["geladen"]:
            return None
        return sorted({_schluessel(t) for t in kalender.termine(jetzt.date(), jetzt.date() + timedelta(days=14))
                       if t["art"] != "feiertag"})
    except Exception:
        return None


def _inbox_n():
    try:
        import notizen
        pfad = os.path.join(notizen._jarvis_ordner(), "Inbox.md")
        return sum(1 for z in notizen._lesen(pfad).splitlines() if re.match(r"^\s*[-*]\s+\S", z))
    except Exception:
        return 0


def merken(jetzt=None):
    """Hält fest, dass Jarvis jetzt da ist (und welche Kalendereinträge er kennt). Atomar, fehlertolerant."""
    jetzt = jetzt or datetime.now()
    alt = _lade()
    keys = _kalender_keys(jetzt)
    d = {"ts": jetzt.timestamp(), "kal": keys if keys is not None else alt.get("kal", []), "inbox": _inbox_n()}
    with _lock:
        try:
            tmp = _pfad() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, _pfad())
        except OSError:
            pass


def _dauer(sek):
    h = sek / 3600.0
    if h < 1.5:
        return f"{max(1, int(sek // 60))} Minuten"
    if h < 36:
        return f"{int(round(h))} Stunden"
    return f"{int(round(h / 24))} Tagen"


def _tag(d, heute):
    if d == heute:
        return "heute"
    if d == heute + timedelta(days=1):
        return "morgen"
    return f"am {_WT[d.weekday()]}, den {d.day}.{d.month}."


def _neue_termine(jetzt, saved):
    if "kal" not in saved:
        return None                                              # erster Start überhaupt: nichts "Neues" behaupten
    try:
        import kalender
        if not kalender.aktiv() or not kalender.status()["geladen"]:
            return None
        bekannt = set(saved.get("kal", []))
        neu = [t for t in kalender.termine(jetzt.date(), jetzt.date() + timedelta(days=14))
               if t["art"] != "feiertag" and _schluessel(t) not in bekannt and t["start"] >= jetzt - timedelta(hours=1)
               and t["start"].date() != jetzt.date() + timedelta(days=1)]      # was morgen ansteht, steht ohnehin im Morgen-Teil
    except Exception:
        return None
    if not neu:
        return ""
    neu.sort(key=lambda t: t["start"])
    teile = []
    for t in neu[:3]:
        uhr = "" if t.get("ganztag") else f" um {t['start'].hour}:{t['start']:%M} Uhr"
        teile.append(f"{t['titel']} {_tag(t['start'].date(), jetzt.date())}{uhr}")
    rest = len(neu) - 3
    return "Neu im Kalender: " + "; ".join(teile) + (f" und {rest} weitere." if rest > 0 else ".")


def _morgen(jetzt):
    heute = jetzt.date()
    morgen = heute + timedelta(days=1)
    teile = []
    try:
        import kalender
        if kalender.aktiv() and kalender.status()["geladen"]:
            alle = kalender.termine(morgen, morgen + timedelta(days=1))
            for t in alle:
                if t["art"] == "feiertag":
                    teile.append(f"Morgen ist Feiertag: {t['titel']}.")
            echte = [t for t in alle if t["art"] != "feiertag"]
            if echte:
                teile.append("Morgen: " + "; ".join(
                    t["titel"] + ("" if t.get("ganztag") else f" um {t['start'].hour}:{t['start']:%M} Uhr") for t in echte[:3])
                    + (" und weitere." if len(echte) > 3 else "."))
    except Exception:
        pass
    try:
        import termine
        for e in termine.laden():
            d = termine.naechster(e, heute)
            if (d - heute).days == 1:
                teile.append(termine.satz(e, 1, termine.jahre(e, d)))
    except Exception:
        pass
    return " ".join(teile)


def _geraete():
    """Ohne Haussteuerung - keine Geräte-Meldungen."""
    return []


def neues(jetzt=None, saved=None, inbox_immer=False):
    """Liste von Sprechteilen mit dem Neuen. Leer = nichts Besonderes."""
    jetzt = jetzt or datetime.now()
    saved = saved if saved is not None else _lade()
    teile = []
    nt = _neue_termine(jetzt, saved)
    if nt:
        teile.append(nt)
    m = _morgen(jetzt)
    if m:
        teile.append(m)
    try:
        import muell
        for t in (muell.briefing_teil(jetzt.date()), muell.morgen_teil(jetzt.date())):
            if t:
                teile.append(t)
        if inbox_immer:                                          # Hinweis auf fehlende neue Datei: beim ersten Start des Tages / auf Nachfrage
            w = muell.warnung(jetzt.date())
            if w:
                teile.append(w)
    except Exception:
        pass
    n = _inbox_n()
    if n and (inbox_immer or n != saved.get("inbox")):
        teile.append(f"In deiner Inbox {'liegt' if n == 1 else 'liegen'} {n} Notiz{'en' if n != 1 else ''}.")
    teile.extend(_geraete())
    return teile


def text(jetzt=None):
    """Der Sprechtext für den Start oder None (nichts sagen)."""
    jetzt = jetzt or datetime.now()
    saved = _lade()
    ts = saved.get("ts")
    erster_heute = not ts or datetime.fromtimestamp(ts).date() != jetzt.date()
    weg_s = (jetzt.timestamp() - ts) if ts else None
    if not erster_heute and weg_s is not None and weg_s < float(_c("STARTBRIEFING_MIN_WEG_MIN", 45)) * 60:
        return None
    extra = neues(jetzt, saved, inbox_immer=erster_heute)
    if erster_heute:
        import proaktiv
        basis = proaktiv.briefing_text(jetzt)
        # Doppelungen vermeiden: Fenster stehen im Morgenbriefing schon drin
        extra = [e for e in extra if not e.startswith(("Offen sind die Fenster", "Heute wird abgeholt", "Morgen wird abgeholt"))]
        return " ".join([basis] + extra)
    name = _c("USER_NAME", "")
    kopf = f"Willkommen zurück, {name}." + (f" Du warst {_dauer(weg_s)} weg." if weg_s else "")
    if not extra:
        return None
    return " ".join([kopf] + extra)


# ----------------------------- Sprache -----------------------------
_NEUES_RE = re.compile(r"^(?:was habe ich verpasst|was hab ich verpasst|was ist (?:denn )?(?:alles )?neu(?: bei mir)?|"
                       r"was gibt es (?:bei mir |zu hause |zuhause |im haus )neues|was gibts (?:bei mir |zu hause |zuhause |im haus )neues|"
                       r"gibt es (?:bei mir |zu hause |zuhause |im haus )etwas neues|neuigkeiten (?:bei mir|zu hause|zuhause|im haus))$")


def erkenne(cmd):
    import wissen
    n = " ".join(re.sub(r"^jarvis[ ,]*", "", wissen.fold(cmd)).strip(" .!?").split())
    return {"art": "neues"} if _NEUES_RE.match(n) else None


def fuehre_aus(intent=None, jetzt=None):
    jetzt = jetzt or datetime.now()
    saved = _lade()
    teile = neues(jetzt, saved, inbox_immer=True)
    if not teile:
        return "Seit du zuletzt hier warst, gibt es nichts Neues."
    return " ".join(teile)


# ----------------------------- Dauerbetrieb -----------------------------
def starten(sprich, log=print, darf_sprechen=lambda: True):
    """Startbriefing einmal nach dem Hochfahren; danach alle 5 Minuten 'zuletzt da' festhalten."""
    def lauf():
        time.sleep(float(_c("STARTBRIEFING_VERZOEGERUNG_S", 45)))
        try:
            if _c("STARTBRIEFING_AKTIV", True):
                t = text()
                if t:
                    log(f"[Startbriefing]: {t}")
                    ende = time.time() + 90                        # du sitzt am Rechner -> auch in der Ruhezeit; nur stumm/Sprechen wartet
                    while not darf_sprechen() and time.time() < ende:
                        time.sleep(2)
                    if darf_sprechen():
                        sprich(t)
        except Exception as e:
            log(f"[Startbriefing Fehler]: {e.__class__.__name__}: {e}")
        while True:
            try:
                merken()
            except Exception:
                pass
            time.sleep(300)
    threading.Thread(target=lauf, daemon=True).start()
