"""
selbst.py - JARVIS weiß, was er kann, und kann es prüfen.

  KURZ        kurze Selbstbeschreibung für den System-Prompt (damit Qwen Fragen wie "Was kannst du?" sinnvoll beantwortet)
  auskunft()  echte Messwerte aus den Modulen (Suchindex, Gedächtnis, Jahrestage, Sprache) -> Sprechtext
"""

import time
from datetime import datetime

import config

KURZ = ("Was du selbst kannst (nur darüber sprichst du, wenn gefragt): Timer, Wecker und Erinnerungen; Notizen, Aufgaben- und "
        "Einkaufsliste in Obsidian; Wetter, Uhrzeit, Datum; Müllabfuhr; Google-Kalender; "
        "Suche in den Notizen des Nutzers (Stichwort UND Bedeutung über einen Suchindex, der beim Scannen und alle 30 Minuten "
        "aktualisiert wird) und im Web; Geburtstage und Jubiläen mit Erinnerung; ein Profil und ein Langzeitgedächtnis über "
        "Gespräche; Morgenbriefing. Eine Haussteuerung hast du NICHT. "
        "Fragt der Nutzer, ob du etwas erstellt, aktualisiert oder erledigt hast, oder wie es um dich steht, nutze das Werkzeug "
        "systemstatus und sage nur, was dort steht - rate nie.")


def _vor(ts):
    if not ts:
        return "noch nie in dieser Sitzung"
    m = int((time.time() - ts) // 60)
    return "gerade eben" if m < 1 else f"vor {m} Minute{'n' if m != 1 else ''}" if m < 90 else f"vor {m // 60} Stunden"


def _suche():
    import semantik
    a = semantik.auskunft()
    if not getattr(config, "SEMANTIK_AKTIV", True):
        return "Die Bedeutungssuche ist in der config ausgeschaltet, ich suche nur nach Stichwörtern."
    if a["grund"]:
        return f"Die Bedeutungssuche ist gerade nicht verfügbar ({a['grund']}). Ich suche nur nach Stichwörtern."
    if not a["notizen"]:
        return "Der Suchindex ist noch leer" + (", er wird gerade aufgebaut." if a["baut"] else ".")
    t = (f"Ja, der Suchindex steht: {a['notizen']} Notizen in {a['abschnitte']} Abschnitten, Modell {a['modell']}. "
         f"Zuletzt geprüft {_vor(a['lauf'])}")
    if a["aenderung"]:
        t += f", zuletzt geändert {_vor(a['aenderung'])}"
    return t + (". Er wird gerade aktualisiert." if a["baut"] else ".")


def _gedaechtnis():
    import gedaechtnis
    import agent
    teile = []
    try:
        letzte = gedaechtnis._lese_letzte(1)
    except Exception:
        letzte = []
    if letzte:
        d, uhr, _ = letzte[-1]
        teile.append(f"Die letzte Gesprächszusammenfassung in Obsidian ist vom {d[8:10]}.{d[5:7]}. um {uhr} Uhr.")
    else:
        teile.append("Es gibt noch keine Gesprächszusammenfassung in Obsidian.")
    teile.append(f"Noch nicht zusammengefasst: {len(gedaechtnis._offen)} Wortwechsel.")
    try:
        profil = [z for z in (agent.profil_lesen() or "").splitlines() if z.strip().startswith("-")]
        teile.append(f"Im Profil stehen {len(profil)} Einträge.")
    except Exception:
        pass
    return " ".join(teile)


def _jahrestage():
    import termine
    e = termine.laden()
    if not e:
        return "In der Jahrestage-Liste steht noch nichts."
    n = termine.hud_liste(1)
    nxt = ""
    if n:
        x = n[0]
        nxt = f" Als Nächstes: {x['wer']} in {x['tage']} Tagen." if x["tage"] else f" Heute: {x['wer']}."
    return f"In der Jahrestage-Liste stehen {len(e)} Einträge.{nxt}"


def _kalender():
    import kalender
    if not kalender.aktiv():
        return "Der Google-Kalender ist nicht eingerichtet."
    k = kalender.status()
    if not k["geladen"]:
        return "Der Google-Kalender wird gerade geladen."
    import time
    min_ = int((time.time() - k["stand"]) / 60) if k["stand"] else 0
    return f"{k['geladen']} Google-Kalender sind geladen, Stand vor {min_} Minuten." + (" Probleme bei: " + ", ".join(k["fehler"]) + "." if k["fehler"] else "")


def _sprache():
    import voice
    eng = str(getattr(config, "STT_ENGINE", "google")).lower()
    if voice.stt_bereit.is_set():
        return "Die Spracherkennung läuft mit Whisper, offline."
    return "Whisper lädt noch, bis dahin nutze ich Google." if eng == "whisper" else "Die Spracherkennung läuft über Google."


THEMEN = {"suche": _suche, "semantik": _suche, "index": _suche, "gedaechtnis": _gedaechtnis,
          "jahrestage": _jahrestage, "kalender": _kalender, "google": _kalender, "sprache": _sprache}


def auskunft(thema=""):
    t = (thema or "").lower().replace("ä", "ae").replace("ö", "oe")
    for k, f in THEMEN.items():
        if k in t:
            return f()
    teile = []
    for f in (_suche, _gedaechtnis, _jahrestage, _kalender, _sprache):
        try:
            teile.append(f())
        except Exception:
            pass
    return " ".join(teile)
