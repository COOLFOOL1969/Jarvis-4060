"""
proaktiv.py - JARVIS meldet sich von allein (und das Morgenbriefing)

Ohne Haussteuerung: meldet sich bei Geburtstagen/Jubiläen, Kalenderterminen, Müllabfuhr und
mit dem automatischen Morgenbriefing. Fenster-, Drucker-, Batterie- und Strom-Warnungen gibt es hier nicht.
Nachts (Ruhezeit) und bei stummem Mikrofon spricht Jarvis nichts - die Meldung steht dann nur im Log.

Briefing:  "Guten Morgen Jarvis", "Morgenbriefing", "Was liegt heute an?" - Wetter, Timer, Aufgaben, Termine, Müll.
"""

import re
import time
import threading
from datetime import datetime

import config
import wetter
import notizen
import termine
import kalender
import muell
import zeit
from zeit import norm


def _c(name, default):
    return getattr(config, name, default)


_zustand = {"fenster": {}, "drucker": {}, "batt": {}, "offline": {}, "haus_hoch": None, "haus_gemeldet": 0.0,
            "briefing_tag": None}
_planer = None


def _hm(text, default):
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def ruhezeit(jetzt=None):
    j = jetzt or datetime.now()
    von, bis = _hm(_c("PROAKTIV_RUHE_VON", "22:00"), 22 * 60), _hm(_c("PROAKTIV_RUHE_BIS", "07:00"), 7 * 60)
    m = j.hour * 60 + j.minute
    return (m >= von or m < bis) if von > bis else (von <= m < bis)


# ============================== Briefing ==============================
BRIEFING_RE = [re.compile(r"^(?:guten morgen|moin|morgen)(?: jarvis)?[.!]*$"),
               re.compile(r"\b(?:morgenbriefing|tagesbriefing|briefing|tagesubersicht|tagesuebersicht|morgenubersicht)\b"),
               re.compile(r"^(?:jarvis )?(?:bitte )?(?:briefe|brief|briefing) (?:mich|uns)\b"),
               re.compile(r"^(?:was liegt (?:heute )?an|was steht (?:heute )?an|was gibt es neues|was gibt's neues|"
                          r"gib mir (?:einen )?(?:uberblick|ueberblick)|was ist heute los)")]


def ist_briefing(cmd, addressed=True):
    n = norm(cmd).strip(" .!?")
    if not n or re.search(r"\b(?:timer|wecker|erinner)", n):
        return False
    if BRIEFING_RE[0].match(n):
        return addressed or "jarvis" in n            # "Guten Morgen" allein nur, wenn Jarvis angesprochen wurde
    return any(p.search(n) for p in BRIEFING_RE[1:])


def briefing_text(jetzt=None):
    j = jetzt or datetime.now()
    gruss = "Guten Morgen" if j.hour < 11 else "Guten Tag" if j.hour < 18 else "Guten Abend"
    teile = [f"{gruss}, {getattr(config, 'USER_NAME', '')}. {wetter.datum_antwort(j)} {wetter.uhrzeit_antwort(j)}"]
    try:
        teile.append(wetter.antwort(0))
    except Exception:
        teile.append("Das Wetter konnte ich gerade nicht abrufen.")
    if _planer:
        items = [i for i in _planer.snapshot() if i["status"] == "wartet"][:3]
        for i in items:
            was = i["label"] or {"timer": "Timer", "wecker": "Wecker", "erinnerung": "Erinnerung"}[i["art"]]
            teile.append(f"{was.capitalize() if i['art'] != 'timer' else 'Timer ' + was}: {i['tag']} um {zeit.uhr_sprech(datetime.fromtimestamp(i['ziel_ts']))}.")
    try:
        offen, _ = notizen.liste_lesen("aufgaben")
        if offen:
            teile.append(f"Du hast {len(offen)} offene Aufgabe{'n' if len(offen) != 1 else ''}: "
                         + ", ".join(offen[:3]) + (" und weitere." if len(offen) > 3 else "."))
    except Exception:
        pass
    try:
        t = termine.briefing_teil(j.date())
        if t:
            teile.append(t)
    except Exception:
        pass
    try:
        t = kalender.briefing_teil(j.date())
        if t:
            teile.append(t)
    except Exception:
        pass
    try:
        for t in (muell.briefing_teil(j.date()), muell.morgen_teil(j.date())):
            if t:
                teile.append(t)
    except Exception:
        pass
    return " ".join(teile)


# ============================== Dauerbetrieb ==============================
def starten(sprich, log=print, darf_sprechen=lambda: True, planer=None):
    """sprich(text) spricht; darf_sprechen() sagt, ob gerade gesprochen werden darf (z.B. Mikrofon nicht stumm)."""
    global _planer
    _planer = planer

    def lauf():
        time.sleep(20)                                        # erst Kalender und Co. einlesen lassen
        while True:
            try:
                if _c("PROAKTIV_AKTIV", True):
                    _briefing_auto(sprich, log, darf_sprechen)
                    _termine_melden(sprich, log, darf_sprechen)
                    _kalender_melden(sprich, log, darf_sprechen)
                    _muell_melden(sprich, log, darf_sprechen)
            except Exception as e:
                log(f"[Proaktiv Fehler]: {e.__class__.__name__}: {e}")
            time.sleep(20)
    threading.Thread(target=lauf, daemon=True).start()


def _termine_melden(sprich, log, darf_sprechen):
    """Geburtstage/Jubiläen: Tag selbst + Vorwarnung. Nicht in der Ruhezeit und nicht vor dem Auto-Briefing (das nennt sie mit)."""
    if not _c("JAHRESTAGE_AKTIV", True) or ruhezeit() or not darf_sprechen():
        return
    j = datetime.now()
    ziel = _hm(str(_c("BRIEFING_AUTO_UHRZEIT", "")), None) if _c("BRIEFING_AUTO_UHRZEIT", "") else None
    if ziel is not None and _zustand["briefing_tag"] != j.date() and j.hour * 60 + j.minute < ziel + 120:
        return
    for m in termine.meldungen(j.date()):
        log(f"[Termine]: {m}")
        sprich(m)


def _kalender_melden(sprich, log, darf_sprechen):
    """Erinnerung kurz vor einem Kalendertermin (KALENDER_VORWARN_MIN). Nicht in der Ruhezeit."""
    if ruhezeit() or not darf_sprechen():
        return
    for m in kalender.meldungen():
        log(f"[Kalender]: {m}")
        sprich(m)


def _muell_melden(sprich, log, darf_sprechen):
    """Vorabend-Erinnerung an die Müllabfuhr (einmal pro Tag ab MUELL_ERINNERUNG_UHRZEIT, nicht in der Ruhezeit)."""
    if ruhezeit() or not darf_sprechen():
        return
    m = muell.erinnerung_faellig()
    if m:
        log(f"[Müll]: {m}")
        sprich(m)


def _briefing_auto(sprich, log, darf_sprechen):
    zeitpunkt = str(_c("BRIEFING_AUTO_UHRZEIT", ""))
    if not zeitpunkt:
        return
    j = datetime.now()
    ziel = _hm(zeitpunkt, None)
    m = j.hour * 60 + j.minute
    if ziel is not None and ziel <= m < ziel + 120 and _zustand["briefing_tag"] != j.date() and darf_sprechen():
        _zustand["briefing_tag"] = j.date()
        text = briefing_text(j)
        log(f"[Proaktiv]: Morgenbriefing: {text}")
        sprich(text)
