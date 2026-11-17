"""
startcheck.py - Systemcheck beim Start von JARVIS

Wartet nach dem Start, bis Ollama, Whisper und der Suchindex bereit sind (höchstens STARTCHECK_TIMEOUT_S),
prüft alles kurz und sagt dann "Alle Systeme geprüft und online." - oder nennt, was nicht läuft.
"""

import os
import time
import threading

import config


def _c(name, default):
    return getattr(config, name, default)


def _modellnamen(antwort):
    """Namen aus ollama.list() - egal ob dict oder Objekt (je nach Version des Pakets)."""
    modelle = antwort.get("models", []) if isinstance(antwort, dict) else getattr(antwort, "models", [])
    out = set()
    for m in modelle:
        n = (m.get("model") or m.get("name")) if isinstance(m, dict) else (getattr(m, "model", None) or getattr(m, "name", None))
        if n:
            out.add(str(n))
    return out


def _hat_modell(vorhanden, name):
    return any(v == name or v == name + ":latest" or v.split(":")[0] == name for v in vorhanden)


def pruefe():
    """(probleme, wartet): probleme = läuft nicht, wartet = noch nicht fertig. Beides Listen von Kurztexten."""
    probleme, wartet = [], []

    # Ollama + Modelle
    try:
        import ollama
        vorhanden = _modellnamen(ollama.Client(timeout=5).list())
        chat = _c("AGENT_MODELL", None) or _c("OLLAMA_MODEL", "qwen3.5:4b")
        if not _hat_modell(vorhanden, chat):
            probleme.append(f"das Modell {chat} fehlt in Ollama")
        if _c("SEMANTIK_AKTIV", True) and not _hat_modell(vorhanden, _c("EMBED_MODELL", "bge-m3")):
            probleme.append(f"das Suchmodell {_c('EMBED_MODELL', 'bge-m3')} fehlt in Ollama")
    except ImportError:
        probleme.append("das Python-Paket ollama fehlt")
    except Exception:
        probleme.append("Ollama ist nicht erreichbar")

    # Obsidian
    if not os.path.isdir(_c("OBSIDIAN_VAULT_PATH", "")):
        probleme.append("das Obsidian-Archiv wurde nicht gefunden")

    # Whisper
    if str(_c("STT_ENGINE", "google")).lower() == "whisper":
        try:
            import voice
            if not voice.stt_bereit.is_set():
                wartet.append("die Spracherkennung Whisper")
        except Exception:
            pass

    # Google-Kalender
    try:
        import kalender
        if kalender.aktiv():
            k = kalender.status()
            if k["fehler"]:
                probleme.append("der Kalender " + ", ".join(k["fehler"][:2]) + " ist nicht erreichbar")
            elif k["geladen"] < k["anzahl"]:
                wartet.append("der Kalender")
        elif _c("KALENDER_AKTIV", True) and _c("KALENDER_QUELLEN", []):
            probleme.append("das Paket python-dateutil für den Kalender fehlt")
    except Exception:
        pass

    # Suchindex
    try:
        import semantik
        a = semantik.auskunft()
        if _c("SEMANTIK_AKTIV", True):
            if a["grund"]:
                probleme.append("die Bedeutungssuche ist aus (" + str(a["grund"])[:60] + ")")
            elif not a["bereit"]:
                wartet.append("der Suchindex")
    except Exception:
        pass
    return probleme, wartet


def text(probleme, wartet):
    if not probleme and not wartet:
        return _c("STARTCHECK_OK_TEXT", "Alle Systeme geprüft und online.")
    teile = list(probleme) + [f"{w} ist noch nicht fertig" for w in wartet]
    return "Systemcheck abgeschlossen, aber: " + "; ".join(teile) + "."


def starten(sprich, log=print, verzoegerung=3.0):
    if not _c("STARTCHECK_AKTIV", True):
        return

    def lauf():
        time.sleep(verzoegerung)
        ende = time.time() + float(_c("STARTCHECK_TIMEOUT_S", 120))
        while True:
            try:
                p, w = pruefe()
            except Exception as e:
                log(f"[Startcheck Fehler]: {e.__class__.__name__}: {e}")
                return
            if not w or time.time() >= ende:
                break
            time.sleep(2)
        msg = text(p, w)
        log(f"[Startcheck]: {msg}")
        if _c("STARTCHECK_SPRECHEN", True):
            sprich(msg)
    threading.Thread(target=lauf, daemon=True).start()
