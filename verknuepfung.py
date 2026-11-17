"""
verknuepfung.py - Jarvis schlägt Links zwischen Obsidian-Notizen vor, die im Graph noch fehlen.

Ablauf: Die Notizen sind für die semantische Suche bereits eingebettet (semantik.py). Hier wird pro Notiz ein
Gesamtvektor gebildet und jedes Paar verglichen. Ähnliche Notizen ohne gegenseitigen [[Link]] werden vorgeschlagen.
Geschrieben wird nur nach einem Ja des Nutzers: ein Eintrag unter "## Siehe auch" in EINER der beiden Notizen.
Abgelehnte Vorschläge merkt sich Jarvis (verknuepfung_abgelehnt.json) und fragt nicht noch einmal.

Config (alles optional):
    VERKNUEPFUNG_AKTIV = True
    VERKNUEPFUNG_MIN   = 0.60    # Mindest-Ähnlichkeit (0..1). Zu viele Vorschläge -> höher, zu wenige -> niedriger
    VERKNUEPFUNG_PRO_NOTIZ = 2   # höchstens so viele Vorschläge je Notiz (verhindert, dass ähnliche Gruppen komplett verlinkt werden)
    VERKNUEPFUNG_AUSSCHLUSS = ("Gespräche", "JARVIS")   # Ordner, die nie vorgeschlagen werden
"""
import os
import re
import json
import math
import threading

import config
import semantik
import wissen

_lock = threading.RLock()
_LINK_RE = re.compile(r"\[\[([^\]\|#]+)")
KOPF = "## Siehe auch"


def aktiv():
    return bool(getattr(config, "VERKNUEPFUNG_AKTIV", True)) and semantik.aktiv()


def _abgelehnt_pfad():
    return os.path.join(getattr(config, "PROJEKT_DIR", "."), "verknuepfung_abgelehnt.json")


def _abgelehnt():
    try:
        with open(_abgelehnt_pfad(), "r", encoding="utf-8") as f:
            return set(tuple(x) for x in json.load(f))
    except (OSError, ValueError):
        return set()


def _abgelehnt_speichern(s):
    try:
        tmp = _abgelehnt_pfad() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(list(x) for x in s), f, ensure_ascii=False)
        os.replace(tmp, _abgelehnt_pfad())
    except OSError:
        pass


def _stem(rel):
    return os.path.splitext(os.path.basename(rel))[0]


def _schluessel(a, b):
    return tuple(sorted((a, b)))


def _links_von(rel):
    """Menge der Link-Ziele (kleingeschrieben, ohne .md/.pdf) einer Notiz."""
    pfad = os.path.join(config.OBSIDIAN_VAULT_PATH, rel)
    try:
        with open(pfad, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return set()
    ziele = set()
    for m in _LINK_RE.finditer(text):
        n = os.path.basename(m.group(1).strip()).lower()
        for ext in (".md", ".pdf"):
            if n.endswith(ext):
                n = n[:-len(ext)]
        ziele.add(n)
    return ziele


def _ausgeschlossen(rel):
    teile = rel.replace("\\", "/").split("/")
    sperr = {x.lower() for x in getattr(config, "VERKNUEPFUNG_AUSSCHLUSS", ("Gespräche", "JARVIS"))}
    sperr |= {x.lower() for x in getattr(config, "WISSEN_AUSSCHLUSS", ("Gespräche",))}
    sperr.add(str(getattr(config, "JARVIS_ORDNER", "JARVIS")).lower())
    if any(t.lower() in sperr for t in teile[:-1]):
        return True
    return _stem(rel).lower().startswith("index")


def _gesamtvektor(chunks):
    vs = [c["v"] for c in chunks if c.get("v")]
    if not vs:
        return None
    n = len(vs[0])
    s = [0.0] * n
    for v in vs:
        for i in range(n):
            s[i] += v[i]
    norm = math.sqrt(sum(x * x for x in s)) or 1.0
    return [x / norm for x in s]


def vorschlaege(max_n=10, minimum=None):
    """Liste von Dicts {a, b, titel_a, titel_b, wert} (a, b = relative Pfade), beste zuerst.
    Leer, wenn die Semantik noch nicht bereit ist."""
    if not aktiv() or not semantik.bereit():
        return []
    minimum = float(minimum if minimum is not None else getattr(config, "VERKNUEPFUNG_MIN", 0.60))
    with semantik._lock:
        dateien = {rel: d for rel, d in semantik._index["dateien"].items() if not _ausgeschlossen(rel)}
        vek = {}
        for rel, d in dateien.items():
            v = _gesamtvektor(d.get("chunks", []))
            if v:
                vek[rel] = v
    links = {rel: _links_von(rel) for rel in vek}
    stems = {rel: _stem(rel).lower() for rel in vek}
    ab = _abgelehnt()

    # Nachbarschaft im Graph (Links in beide Richtungen, über ALLE Notizen) - für die "über Dritte schon verbunden"-Regel.
    # Index-Notizen zählen nicht als Verbindung: dort stehen ohnehin alle Notizen eines Ordners.
    with semantik._lock:
        alle = list(semantik._index["dateien"].keys())
    nachbar = {}
    for rel in alle:
        st = _stem(rel).lower()
        for ziel in _links_von(rel):
            nachbar.setdefault(st, set()).add(ziel)
            nachbar.setdefault(ziel, set()).add(st)
    ohne_index = lambda mengen: {x for x in mengen if not x.startswith("index")}

    rels = sorted(vek)
    kandidaten = []
    for i, a in enumerate(rels):
        for b in rels[i + 1:]:
            if stems[a] == stems[b] or _schluessel(a, b) in ab:
                continue
            if stems[b] in links[a] or stems[a] in links[b]:
                continue                                         # schon verknüpft
            gemeinsam = ohne_index(nachbar.get(stems[a], set()) & nachbar.get(stems[b], set())) - {stems[a], stems[b]}
            if gemeinsam:
                continue                                         # beide hängen schon an einer gemeinsamen Notiz (nicht Index)
            wert = sum(x * y for x, y in zip(vek[a], vek[b]))
            if wert >= minimum:
                kandidaten.append({"a": a, "b": b, "titel_a": dateien[a].get("titel") or _stem(a),
                                   "titel_b": dateien[b].get("titel") or _stem(b), "wert": round(wert, 3)})
    kandidaten.sort(key=lambda x: -x["wert"])
    # Pro Notiz höchstens k Vorschläge: sonst werden ähnliche Gruppen (z.B. alle Planeten) komplett untereinander verlinkt
    k = int(getattr(config, "VERKNUEPFUNG_PRO_NOTIZ", 2))
    zaehler, ergebnis = {}, []
    for x in kandidaten:
        if zaehler.get(x["a"], 0) >= k or zaehler.get(x["b"], 0) >= k:
            continue
        zaehler[x["a"]] = zaehler.get(x["a"], 0) + 1
        zaehler[x["b"]] = zaehler.get(x["b"], 0) + 1
        ergebnis.append(x)
    return ergebnis[:max_n]


def ablehnen(a, b):
    with _lock:
        s = _abgelehnt()
        s.add(_schluessel(a, b))
        _abgelehnt_speichern(s)


def eintragen(a, b):
    """Trägt in Notiz a einen Link auf Notiz b ein (unter '## Siehe auch'). Gibt eine Kurzmeldung zurück.
    Schreibt atomar und behält Zeilenenden (Windows: CRLF) bei."""
    vault = config.OBSIDIAN_VAULT_PATH
    pfad = os.path.join(vault, a)
    ziel = _stem(b)
    try:
        with open(pfad, "rb") as f:
            roh = f.read()
    except OSError:
        raise RuntimeError("Ich konnte die Notiz nicht lesen.")
    bom = roh.startswith(b"\xef\xbb\xbf")
    text = roh.decode("utf-8-sig", errors="replace")
    nl = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    if ziel.lower() in _links_von(a):
        return "bereits verknüpft"
    zeile = f"- [[{ziel}]]"
    zeilen = text.split("\n")
    idx = next((i for i, z in enumerate(zeilen) if z.strip().lower() == KOPF.lower()), None)
    if idx is None:
        text = text.rstrip("\n") + f"\n\n{KOPF}\n{zeile}\n"
    else:
        ende = idx + 1                                           # letzte nicht leere Zeile des Abschnitts
        for j in range(idx + 1, len(zeilen)):
            if re.match(r"^\s*#{1,6}\s", zeilen[j]):
                break
            if zeilen[j].strip():
                ende = j + 1
        zeilen.insert(ende, zeile)
        text = "\n".join(zeilen)
        if not text.endswith("\n"):
            text += "\n"
    daten = text.replace("\n", nl).encode("utf-8")
    if bom:
        daten = b"\xef\xbb\xbf" + daten
    tmp = pfad + ".jarvis.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(daten)
        os.replace(tmp, pfad)
    except OSError as e:
        raise RuntimeError(f"Ich konnte nicht schreiben ({e.__class__.__name__}).")
    return "eingetragen"


# ----------------------------- Sprache -----------------------------
_VORSCHLAG_RE = re.compile(
    r"\b(?:verkn\w*vorschl\w*|vorschl\w*\s+(?:f\w+\s+)?(?:verkn\w*|links?)|"
    r"(?:welche|fehlende|neue)\s+(?:verkn\w*|links?)|fehlen\w*\s+(?:verkn\w*|links?)|"
    r"(?:verkn\w*|verlink\w*)\s+(?:vorschl\w*|vorschlag)|"
    r"n(?:ae|a)chste[rn]?\s+(?:verkn\w*\s+)?vorschlag|notizen\s+verkn\w*|schlag\s+(?:mir\s+)?(?:verkn\w*|links?)\s+vor)")


def erkenne(cmd):
    if not aktiv():
        return None
    n = wissen.fold(cmd) if hasattr(wissen, "fold") else cmd.lower()
    n = " ".join(re.sub(r"^jarvis[ ,]*", "", n.lower()).strip(" .!?").split())
    return {"art": "verknuepfen_vorschlag"} if _VORSCHLAG_RE.search(n) else None


def frage_stellen(setze_rueckfrage):
    """Erster offener Vorschlag als Frage. setze_rueckfrage(intent, text) merkt die Rückfrage (Ja/Nein)."""
    if not semantik.bereit():
        return "Mein Notizindex ist noch nicht bereit. Versuch es gleich noch einmal."
    v = vorschlaege(1)
    if not v:
        return "Mir fallen gerade keine fehlenden Verknüpfungen zwischen deinen Notizen auf."
    x = v[0]
    text = (f"Ich würde {x['titel_a']} mit {x['titel_b']} verknüpfen, die Ähnlichkeit liegt bei "
            f"{int(round(x['wert'] * 100))} Prozent. Soll ich den Link in {x['titel_a']} eintragen?")
    setze_rueckfrage({"art": "verknuepfen", "a": x["a"], "b": x["b"], "ta": x["titel_a"], "tb": x["titel_b"]}, text)
    return text


def antwort_ausfuehren(intent, ja):
    if not ja:
        ablehnen(intent["a"], intent["b"])
        return "Okay, den Vorschlag merke ich mir als abgelehnt. Sag nächster Vorschlag, wenn du den nächsten hören willst."
    try:
        erg = eintragen(intent["a"], intent["b"])
    except RuntimeError as e:
        return str(e)
    if erg == "bereits verknüpft":
        return "Die beiden Notizen sind schon verknüpft."
    return (f"Erledigt. In {intent['ta']} steht jetzt ein Link auf {intent['tb']}. "
            "Der Graph aktualisiert sich beim nächsten Scan.")


def bekannt(rel):
    """Schutz für das HUD: nur Notizen aus dem Index dürfen verändert werden (kein beliebiger Pfad)."""
    with semantik._lock:
        return rel in semantik._index["dateien"] and not _ausgeschlossen(rel)


def hud_aktion(a, b, ja):
    """Ja/Nein aus dem HUD-Panel. Gibt (ok, Text) zurück."""
    if not (bekannt(a) and bekannt(b)) or a == b:
        return False, "Unbekannte Notiz."
    x = {"a": a, "b": b, "ta": _stem(a), "tb": _stem(b)}
    return True, antwort_ausfuehren(x, bool(ja))
