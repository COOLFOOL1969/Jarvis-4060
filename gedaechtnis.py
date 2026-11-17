"""Langzeitgedächtnis: Gespräche überstehen Neustarts und werden nach einer Pause von Qwen zusammengefasst
und als Notiz in Obsidian abgelegt (JARVIS/Gespräche/JJJJ-MM-TT.md). Die Bedeutungssuche findet sie später wieder."""
import os
import re
import json
import time
import threading
from datetime import datetime

import config

_lock = threading.Lock()
_offen = []                    # noch nicht zusammengefasste Züge: [[zeit, user, antwort], ...]
_zusammenfassungen = None      # Cache: [(datum, uhrzeit, text)] der letzten Einträge
_geladen = False


def _c(k, d):
    return getattr(config, k, d)


def _datei():
    return os.path.join(config.PROJEKT_DIR, "gespraech_verlauf.json")


def _ordner():
    return os.path.join(config.OBSIDIAN_VAULT_PATH, _c("JARVIS_ORDNER", "JARVIS"), "Gespräche")


# ---------------- Verlauf auf der Festplatte ----------------
def laden():
    """Gibt den gespeicherten Kurzzeit-Verlauf [(zeit, rolle, text)] zurück und füllt die Liste der offenen Züge."""
    global _geladen
    verlauf = []
    try:
        with open(_datei(), "r", encoding="utf-8") as f:
            d = json.load(f)
        verlauf = [(float(z), str(r), str(t)) for z, r, t in d.get("verlauf", [])]
        with _lock:
            _offen[:] = [[float(z), str(u), str(a)] for z, u, a in d.get("offen", [])]
    except (OSError, ValueError, TypeError):
        pass
    _geladen = True
    return verlauf


def speichern(verlauf):
    try:
        os.makedirs(config.PROJEKT_DIR, exist_ok=True)
        with _lock:
            daten = {"verlauf": [list(v) for v in verlauf], "offen": [list(o) for o in _offen]}
        tmp = _datei() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, _datei())
    except OSError:
        pass


def zug_merken(user, antwort):
    with _lock:
        _offen.append([time.time(), user, antwort])
        del _offen[:-60]                      # nie unbegrenzt anwachsen lassen


def vergessen():
    with _lock:
        _offen.clear()


# ---------------- Zusammenfassungen lesen ----------------
_BLOCK = re.compile(r"^## (\d{1,2}:\d{2})\s*\n(.*?)(?=^## |\Z)", re.M | re.S)


def _lese_letzte(n):
    try:
        dateien = sorted(f for f in os.listdir(_ordner()) if re.match(r"\d{4}-\d{2}-\d{2}\.md$", f))
    except OSError:
        return []
    out = []
    for f in reversed(dateien[-4:]):
        try:
            with open(os.path.join(_ordner(), f), "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for uhr, inhalt in reversed(_BLOCK.findall(text)):
            out.append((f[:-3], uhr, " ".join(inhalt.split())))
        if len(out) >= n:
            break
    return list(reversed(out[:n]))


def erinnerungen():
    """Text für den System-Prompt: die letzten Gespräche in Kurzform (leer, wenn es noch keine gibt)."""
    global _zusammenfassungen
    n = int(_c("GEDAECHTNIS_IM_PROMPT", 3))
    if n <= 0:
        return ""
    if _zusammenfassungen is None:
        _zusammenfassungen = _lese_letzte(n)
    heute = datetime.now().date().isoformat()
    teile = []
    for datum, uhr, text in _zusammenfassungen[-n:]:
        wann = f"heute {uhr}" if datum == heute else f"{datum[8:10]}.{datum[5:7]}. {uhr}"
        teile.append(f"[{wann}] {text[:400]}")
    return " ".join(teile)


# ---------------- Zusammenfassen ----------------
_PROMPT = ("Fasse das folgende Gespräch zwischen dem Nutzer und seinem Sprachassistenten JARVIS in höchstens drei kurzen "
           "deutschen Sätzen zusammen. Nenne Themen, getroffene Entscheidungen und dauerhaft wichtige Fakten über den "
           "Nutzer. Keine Einleitung, keine Aufzählung, nichts erfinden. Antworte nur mit der Zusammenfassung.")


def zusammenfassen(log=print):
    """Fasst alle offenen Züge zusammen und hängt sie an die Tagesnotiz an. True, wenn etwas geschrieben wurde."""
    global _zusammenfassungen
    with _lock:
        zuege = [list(o) for o in _offen]
    if not zuege:
        return False
    gespraech = "\n".join(f"Nutzer: {u}\nJARVIS: {a}" for _z, u, a in zuege)
    if len(gespraech) < int(_c("GEDAECHTNIS_MIN_ZEICHEN", 60)):
        with _lock:
            del _offen[:len(zuege)]            # zu kurz, um etwas Merkenswertes zu enthalten
        return False
    import agent
    try:
        resp = agent._chat([{"role": "system", "content": _PROMPT}, {"role": "user", "content": gespraech[-4000:]}], None)
        _calls, inhalt = agent._tool_calls(resp)
    except Exception as e:
        log(f"[Gedächtnis Fehler]: {e.__class__.__name__}: {e}")
        return False                           # die Züge bleiben offen, später erneuter Versuch
    text = " ".join(re.sub(r"<think>.*?</think>", " ", inhalt or "", flags=re.S).split())
    if not text:
        return False
    j = datetime.fromtimestamp(zuege[0][0])
    try:
        os.makedirs(_ordner(), exist_ok=True)
        pfad = os.path.join(_ordner(), f"{j:%Y-%m-%d}.md")
        neu = not os.path.exists(pfad)
        with open(pfad, "a", encoding="utf-8", newline="\n") as f:
            if neu:
                f.write(f"# Gespräche mit JARVIS am {j:%d.%m.%Y}\n")
            f.write(f"\n## {j:%H:%M}\n{text}\n")
    except OSError as e:
        log(f"[Gedächtnis Fehler]: {e}")
        return False
    with _lock:
        del _offen[:len(zuege)]
    _zusammenfassungen = None
    log(f"[Gedächtnis]: Gespräch zusammengefasst ({len(zuege)} Wortwechsel) -> Obsidian")
    return True


def starten(log=print, darf=lambda: True, danach=None):
    """Hintergrundthread: fasst Gespräche nach einer Pause zusammen. darf() = gerade nichts anderes los;
    danach() wird nach jeder neuen Zusammenfassung aufgerufen (z.B. Index aktualisieren)."""
    def lauf():
        time.sleep(30)
        while True:
            try:
                with _lock:
                    letzter = _offen[-1][0] if _offen else None
                pause = float(_c("GEDAECHTNIS_PAUSE_MIN", 10)) * 60
                if (_c("GEDAECHTNIS_AKTIV", True) and letzter and time.time() - letzter >= pause and darf()
                        and zusammenfassen(log) and danach):
                    danach()
            except Exception as e:
                log(f"[Gedächtnis Fehler]: {e.__class__.__name__}: {e}")
            time.sleep(30)
    threading.Thread(target=lauf, daemon=True).start()
