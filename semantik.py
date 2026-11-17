"""
semantik.py - Obsidian-Suche nach BEDEUTUNG (statt nur nach Wörtern)

Jede Notiz wird in Abschnitte geteilt, von einem Einbettungsmodell in Ollama in Zahlenvektoren umgewandelt und
lokal gespeichert (obsidian_index.json). Eine Frage wird genauso umgewandelt; die Abschnitte mit dem ähnlichsten
Vektor sind die Treffer. So findet "Wie heize ich das Druckbett auf?" auch eine Notiz, in der nur
"Bett-Temperatur 60 Grad" steht.

Einmalig installieren:   ollama pull bge-m3        (mehrsprachig, gut für Deutsch, ca. 1,2 GB)
Hinweis: Damit Qwen und das Einbettungsmodell zusammen im Speicher bleiben:  OLLAMA_MAX_LOADED_MODELS=2

Der Index wird im Hintergrund aufgebaut und aktuell gehalten. Solange er nicht fertig ist (oder das Modell fehlt),
funktioniert die normale Wortsuche weiter.
"""

import os
import re
import json
import time
import threading

import config
import wissen

try:
    import ollama
except ImportError:
    ollama = None
try:
    import numpy as np
except ImportError:
    np = None

_lock = threading.RLock()
_index = {"modell": None, "dateien": {}}        # rel -> {"m": mtime, "s": size, "titel":..., "chunks": [{"t": text, "v": [..]}]}
_matrix = None                                  # (Liste von (rel, chunk_idx), numpy-Matrix | Liste von Vektoren)
_bereit = False
_deaktiviert = None                             # Text, warum die Semantik gerade nicht verfügbar ist
_baut = False
_fortschritt = ""
_stand = {"lauf": None, "aenderung": None}      # Zeitpunkte (time.time()): letzte Prüfung / letzte Änderung am Index


def aktiv():
    return bool(getattr(config, "SEMANTIK_AKTIV", True)) and ollama is not None and _deaktiviert is None


def bereit():
    return aktiv() and _bereit


def status():
    n = sum(len(d["chunks"]) for d in _index["dateien"].values())
    if _deaktiviert:
        return f"aus ({_deaktiviert})"
    return f"{len(_index['dateien'])} Notizen, {n} Abschnitte" + (f" - baut: {_fortschritt}" if _baut else "")


def auskunft():
    """Für die Selbstauskunft: Zustand des Suchindex als Dict."""
    return {"aktiv": aktiv(), "bereit": bereit(), "grund": _deaktiviert, "baut": _baut,
            "notizen": len(_index["dateien"]), "abschnitte": sum(len(d["chunks"]) for d in _index["dateien"].values()),
            "lauf": _stand["lauf"], "aenderung": _stand["aenderung"], "modell": getattr(config, "EMBED_MODELL", "bge-m3")}


def _modell():
    return getattr(config, "EMBED_MODELL", "bge-m3")


def _pfad():
    return os.path.join(getattr(config, "PROJEKT_DIR", "."), "obsidian_index.json")


def _client():
    try:
        return ollama.Client(timeout=getattr(config, "OLLAMA_TIMEOUT", 90))
    except TypeError:
        return ollama.Client()


def _norm(v):
    s = sum(x * x for x in v) ** 0.5 or 1.0
    return [round(x / s, 5) for x in v]


def einbetten(texte):
    """Liste von Texten -> Liste normierter Vektoren. Wirft RuntimeError mit verständlicher Meldung."""
    if ollama is None:
        raise RuntimeError("Das Paket ollama fehlt.")
    cl = _client()
    out = []
    try:
        for i in range(0, len(texte), 16):
            teil = texte[i:i + 16]
            try:
                r = cl.embed(model=_modell(), input=teil, keep_alive=getattr(config, "OLLAMA_KEEP_ALIVE", "30m"))
                vecs = r["embeddings"] if not hasattr(r, "embeddings") else r.embeddings
            except AttributeError:                      # ältere ollama-Bibliothek
                vecs = [cl.embeddings(model=_modell(), prompt=t)["embedding"] for t in teil]
            out.extend(_norm(list(v)) for v in vecs)
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "pull" in msg or "404" in msg:
            raise RuntimeError(f"Modell {_modell()} fehlt - bitte 'ollama pull {_modell()}' ausführen")
        raise RuntimeError(f"Einbetten fehlgeschlagen ({e.__class__.__name__})")
    return out


# ============================== Aufteilen ==============================
def abschnitte(text, titel, ziel=700, maximal=40):
    """Teilt eine Notiz in Abschnitte von etwa 'ziel' Zeichen (an Überschriften/Absätzen)."""
    zeilen = text.splitlines()
    start = 0
    if zeilen and zeilen[0].strip() == "---":
        for i in range(1, min(len(zeilen), 60)):
            if zeilen[i].strip() in ("---", "..."):
                start = i + 1
                break
    teile, akt, kopf = [], "", ""
    for z in zeilen[start:]:
        if re.match(r"^\s*#{1,6}\s", z):
            if akt.strip():
                teile.append(akt.strip())
            akt = ""
            kopf = wissen.clean_text(z)
        if len(akt) + len(z) > ziel and akt.strip():
            teile.append(akt.strip())
            akt = (kopf + "\n") if kopf else ""
        akt += z + "\n"
    if akt.strip():
        teile.append(akt.strip())
    out = []
    for t in teile:
        c = wissen.clean_text(t.replace("\n", " "))
        if len(c) >= 15 and wissen.fold(c) != wissen.fold(titel):
            out.append(c)
    return out[:maximal]


# ============================== Index ==============================
def _laden():
    global _index
    try:
        with open(_pfad(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("modell") == _modell():
            _index = d
    except (OSError, ValueError):
        pass


def _speichern():
    try:
        tmp = _pfad() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_index, f, ensure_ascii=False)
        os.replace(tmp, _pfad())
    except OSError:
        pass


def _matrix_bauen():
    global _matrix, _bereit
    with _lock:
        ids, vecs = [], []
        for rel, d in _index["dateien"].items():
            for i, c in enumerate(d["chunks"]):
                ids.append((rel, i))
                vecs.append(c["v"])
        if not vecs:
            _matrix, _bereit = None, False
            return
        _matrix = (ids, np.array(vecs, dtype="float32") if np is not None else vecs)
        _bereit = True


def aktualisieren(log=print):
    """Liest geänderte/neue Notizen ein, entfernt gelöschte. Läuft im Hintergrund; sicher mehrfach aufrufbar."""
    global _baut, _deaktiviert, _fortschritt, _index
    if not getattr(config, "SEMANTIK_AKTIV", True) or ollama is None:
        return
    with _lock:
        if _baut:
            return
        _baut = True
    try:
        if not _index["dateien"]:
            _laden()
        vault = config.OBSIDIAN_VAULT_PATH
        if not os.path.isdir(vault):
            return
        gefunden = {}
        for root, dirs, files in os.walk(vault):
            dirs[:] = [d for d in dirs if d not in wissen.SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".md"):
                    p = os.path.join(root, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_size <= wissen.MAX_NOTE_BYTES:
                        gefunden[os.path.relpath(p, vault)] = (p, st.st_mtime, st.st_size)
        geaendert = [r for r, (p, m, s) in gefunden.items()
                     if r not in _index["dateien"] or _index["dateien"][r]["m"] != m or _index["dateien"][r]["s"] != s]
        weg = [r for r in _index["dateien"] if r not in gefunden]
        if not geaendert and not weg:
            _matrix_bauen()
            return
        _index["modell"] = _modell()
        for r in weg:
            _index["dateien"].pop(r, None)
        if geaendert:
            log(f"[Semantik]: Index wird aktualisiert ({len(geaendert)} Notizen) ...")
        for n, rel in enumerate(geaendert, 1):
            p, m, s = gefunden[rel]
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read().lstrip("﻿")
            except OSError:
                continue
            titel = os.path.splitext(os.path.basename(rel))[0]
            teile = abschnitte(text, titel)
            vecs = einbetten([f"{titel}: {t}" for t in teile]) if teile else []
            with _lock:
                _index["dateien"][rel] = {"m": m, "s": s, "titel": titel,
                                          "chunks": [{"t": t, "v": v} for t, v in zip(teile, vecs)]}
            _fortschritt = f"{n}/{len(geaendert)}"
            if n % 25 == 0:
                _matrix_bauen()
                _speichern()
        _speichern()
        _matrix_bauen()
        _deaktiviert = None
        _stand["lauf"] = time.time()
        if geaendert:
            _stand["aenderung"] = time.time()
        if geaendert:
            log(f"[Semantik]: Index fertig - {status()}")
    except RuntimeError as e:
        if _deaktiviert != str(e):
            log(f"[Semantik]: {e} -> es wird nur die Wortsuche benutzt.")
        _deaktiviert = str(e)
    except Exception as e:
        log(f"[Semantik Fehler]: {e.__class__.__name__}: {e}")
    finally:
        _baut = False


def starten(log=print):
    """Index laden/aufbauen und danach alle 30 Minuten auf Änderungen prüfen (Hintergrund-Thread)."""
    def lauf():
        _laden()
        _matrix_bauen()
        time.sleep(3)
        while True:
            aktualisieren(log)
            time.sleep(int(getattr(config, "SEMANTIK_INTERVALL_MIN", 30)) * 60)
    threading.Thread(target=lauf, daemon=True).start()


# ============================== Suche ==============================
def suche(frage, k=5, min_score=None):
    """Gibt die besten Notizen (je Notiz der beste Abschnitt) zurück: Liste von Dicts im Format von wissen.search_obsidian."""
    global _deaktiviert
    if not bereit() or _matrix is None:
        return []
    min_score = float(getattr(config, "SEMANTIK_MIN_SCORE", 0.45)) if min_score is None else min_score
    try:
        q = einbetten([frage])[0]
    except RuntimeError as e:
        _deaktiviert = str(e)
        return []
    ids, mat = _matrix
    if np is not None:
        scores = (mat @ np.array(q, dtype="float32")).tolist()
    else:
        scores = [sum(a * b for a, b in zip(v, q)) for v in mat]
    beste = {}
    for (rel, i), sc in zip(ids, scores):
        if sc >= min_score and (rel not in beste or sc > beste[rel][0]):
            beste[rel] = (sc, i)
    vault = config.OBSIDIAN_VAULT_PATH
    out = []
    for rel, (sc, i) in sorted(beste.items(), key=lambda x: -x[1][0])[:k]:
        d = _index["dateien"].get(rel)
        if not d or i >= len(d["chunks"]):
            continue
        text = d["chunks"][i]["t"]
        out.append({"title": d["titel"], "path": os.path.join(vault, rel), "rel": rel,
                    "snippet": wissen.cut(text, 320), "excerpt": wissen.cut(text, 1400),
                    "matched": 0, "score": round(sc, 3), "semantik": True})
    return out


def kombiniert(query, frage, k=5):
    """Wortsuche und Bedeutungssuche gemischt (Reciprocal Rank Fusion). Fällt ohne Index auf die Wortsuche zurück."""
    try:
        wort = wissen.search_obsidian(query, k)
    except wissen.WissenFehler:
        wort = []
    sem = suche(frage or query, k)
    if not sem:
        return wort
    punkte, daten = {}, {}
    for liste in (wort, sem):
        for rang, t in enumerate(liste):
            punkte[t["rel"]] = punkte.get(t["rel"], 0) + 1.0 / (60 + rang)
            if t["rel"] not in daten or (t.get("semantik") and not daten[t["rel"]].get("matched")):
                daten[t["rel"]] = t
            elif t.get("matched"):
                daten[t["rel"]] = t           # Wortsuche liefert oft den besseren Auszug
    ordnung = sorted(punkte, key=lambda r: -punkte[r])[:k]
    return [daten[r] for r in ordnung]
