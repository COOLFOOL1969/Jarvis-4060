"""
wissen.py - Wissensfunktionen für JARVIS

  * Obsidian-Volltextsuche (durchsucht den INHALT aller .md-Notizen, nicht nur die Dateinamen)
  * Websuche über DuckDuckGo (Paket "ddgs", kein API-Schlüssel nötig)
  * Antworten: Qwen (Ollama) fasst die Treffer zu einer kurzen, sprechbaren Antwort zusammen.
    Ist Ollama nicht erreichbar, liest JARVIS stattdessen die Treffer selbst vor.

Installation:   pip install ddgs ollama

Direkt testen (ohne Sprache):
    python wissen.py obsidian düsentemperatur
    python wissen.py web wie hoch ist der eiffelturm
"""

import os
import re
import sys
import difflib
import unicodedata

import config

try:
    import ollama
except ImportError:
    ollama = None


class WissenFehler(Exception):
    """Fehler mit einer verständlichen Meldung, die JARVIS auch aussprechen kann."""


# ============================== Text-Helfer ==============================
def fold(s):
    """Kleinschreibung, ä->ae, ö->oe, ü->ue, ß->ss und Akzente entfernen (für tolerantes Suchen)."""
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def clean_text(s):
    """Markdown, Links und Sonderzeichen entfernen, damit der Text gut sprechbar ist."""
    s = re.sub(r"!?\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", s)      # [[Notiz|Anzeige]] -> Anzeige
    s = re.sub(r"\[([^\]]*)\]\((?:[^)]*)\)", r"\1", s)           # [Text](url) -> Text
    s = re.sub(r"https?://\S+", "", s)                           # nackte Links
    s = re.sub(r"^\s*(?:#{1,6}\s+|[-*+>]\s+|\d+[.)]\s+)", "", s)  # Überschrift/Liste/Zitat
    s = re.sub(r"\[[ xX]\]", "", s)                              # Checkbox
    s = re.sub(r"[*_`~|]+", "", s)                               # Formatierung
    return " ".join(s.split())


def cut(s, limit):
    """Kürzt auf 'limit' Zeichen, bevorzugt am Satz- oder Wortende."""
    s = s.strip()
    if len(s) <= limit:
        return s
    part = s[:limit]
    for sep in (". ", "! ", "? "):
        i = part.rfind(sep)
        if i > limit * 0.5:
            return part[:i + 1]
    return part.rsplit(" ", 1)[0] + " ..."


# ============================== Befehlserkennung ==============================
WEB_WORDS = {"web", "internet", "google", "online", "netz"}
OBSIDIAN_WORDS = {"obsidian", "notiz", "notizen", "archiv", "vault", "aufzeichnungen", "quelle", "quellen",
                  "lexikon",      # "Suche im Zentralen Archiv / im Lexikon / in den Quellen"
                  "kochbuch", "rezept", "rezepte", "rezepten"}   # "Suche im Kochbuch nach Burger"
STRONG_VERBS = ("such", "find", "gefund", "recherch", "steht", "weiss", "schau", "zeig")
OPEN_WORDS = {"oeffne", "oeffnen", "starte", "starten"}
# Diese Wörter gehören zu anderen Befehlen (Kontaktsuche, PDF öffnen) -> nicht abfangen
BLOCK_WORDS = {"nummer", "telefon", "telefonnummer", "anrufen", "mobil", "handy", "pdf"}

CMD_STOP = {
    "nach", "in", "im", "ins", "auf", "bitte", "mal", "jarvis", "mir", "mich", "uns", "kannst",
    "kann", "du", "ich", "meinen", "meine", "meinem", "meiner", "steht", "weiss", "ueber",
    "zu", "zum", "zur", "alle", "alles", "etwas", "mehr", "informationen", "infos", "hast", "gefunden", "habe",
    "noch", "nochmal", "einen", "einer",      # Whisper-Verhörer für "nach" / "in den" ("Suche einen Quellen")
} | WEB_WORDS | OBSIDIAN_WORDS
# Füllwörter: nur bei der Obsidian-Stichwortsuche entfernt, bei Webfragen bleiben sie
# ("was ein Stirlingmotor ist" soll als ganze Frage gesucht werden)
FUNC_STOP = {
    "was", "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und",
    "oder", "von", "vom", "mit", "fuer", "wie", "wo", "wer", "wann", "warum", "ist", "sind", "hat",
    "gibt", "es", "man", "sich", "dazu", "dafuer", "dass",
}


def _is_cmdword(t):
    f = fold(t)
    return f in CMD_STOP or f.startswith(("such", "recherch", "find", "zeig", "schau"))


def detect_intent(cmd):
    """Erkennt Such-Befehle. Gibt ('obsidian'|'web', suchbegriff) zurück oder None.

    Beispiele:  'suche in obsidian nach düsentemperatur'   -> ('obsidian', 'düsentemperatur')
                'recherchiere im internet was ein stirlingmotor ist' -> ('web', 'was ein stirlingmotor ist')
                'öffne obsidian'                           -> None (bleibt das Programm-Starten)
    """
    cmd = re.sub(r"\bzentral(?:es|en|er|em)?\s+archiv", "archiv", cmd, flags=re.IGNORECASE)    # "Zentrales Archiv" = "Archiv"
    tokens = re.findall(r"[\wäöüß\-]+", cmd.lower())
    folded = [fold(t) for t in tokens]
    fset = set(folded)
    if fset & BLOCK_WORDS:
        return None

    strong = any(f.startswith(STRONG_VERBS) for f in folded)
    google_verb = "google" in fset and not (fset & OPEN_WORDS)
    if not (strong or google_verb):
        return None

    web_pos = [i for i, f in enumerate(folded) if f in WEB_WORDS]
    obs_pos = [i for i, f in enumerate(folded) if f in OBSIDIAN_WORDS]
    if not web_pos and not obs_pos:
        return None
    art = "web" if (web_pos and (not obs_pos or web_pos[0] < obs_pos[0])) else "obsidian"

    if art == "web":
        i = 0
        while i < len(tokens) and _is_cmdword(tokens[i]):
            i += 1
        query = " ".join(t for t, f in zip(tokens[i:], folded[i:]) if f not in WEB_WORDS)
    else:
        query = " ".join(t for t, f in zip(tokens, folded)
                         if not _is_cmdword(t) and f not in FUNC_STOP)
    return art, query.strip()


# ============================== Obsidian-Volltextsuche ==============================
SKIP_DIRS = {".obsidian", ".git", ".trash", ".space", "node_modules"}
MAX_NOTE_BYTES = 1_500_000
_cache = {}   # Pfad -> (mtime, Größe, Zeilen, gefaltete Zeilen, gefalteter Volltext)


def _load_note(path):
    try:
        st = os.stat(path)
        if st.st_size > MAX_NOTE_BYTES:
            return None
        hit = _cache.get(path)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read().lstrip("﻿")
        lines = text.splitlines()
        flines = [fold(l) for l in lines]
        entry = (st.st_mtime, st.st_size, lines, flines, "\n".join(flines))
        _cache[path] = entry
        return entry
    except OSError:
        return None


def _frontmatter_end(lines):
    """Index der ersten Zeile NACH dem YAML-Kopf (--- ... ---), sonst 0."""
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 60)):
            if lines[i].strip() in ("---", "..."):
                return i + 1
    return 0


# Zweibuchstabige Suchwörter, die trotzdem gemeint sind (sonst würde "Suche nach KI" nichts suchen)
KURZWOERTER = {"ki", "ai", "pc", "tv", "vr", "ar", "os", "gb", "tb", "wc", "ev"} | {str(w).lower() for w in getattr(config, "WISSEN_KURZWOERTER", ())}


def _keywords(query):
    out = []
    for t in re.findall(r"[a-z0-9]+", fold(query)):
        if t in CMD_STOP or t in FUNC_STOP or t in out:
            continue
        if len(t) >= 3 or (len(t) == 2 and (any(c.isdigit() for c in t) or t in KURZWOERTER)):
            out.append(t)
    return out


def _stem(t):
    """Kürzt lange Wörter etwas, damit 'düsentemperaturen' auch 'düsentemperatur' findet."""
    return t if len(t) <= 5 else t[:max(4, len(t) - 2)]


def _patterns(tokens):
    pats = []
    for t in tokens:
        st = _stem(t)
        # lange Wörter dürfen auch in Wortmitte stehen (deutsche Zusammensetzungen)
        pats.append(re.compile((r"\b" if len(st) < 6 else "") + re.escape(st)))
    return pats


def search_obsidian(query, max_results=5):
    """Durchsucht den Inhalt aller .md-Notizen. Liefert die besten Treffer (Titel, Pfad, Auszug)."""
    vault = config.OBSIDIAN_VAULT_PATH
    if not os.path.isdir(vault):
        raise WissenFehler("Das Obsidian-Archiv wurde nicht gefunden.")
    tokens = _keywords(query)
    if not tokens:
        raise WissenFehler("Ich habe keinen Suchbegriff verstanden.")
    pats = _patterns(tokens)
    mindest = max(1, (len(tokens) + 1) // 2)       # mindestens die Hälfte der Wörter

    treffer = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(root, name)
            note = _load_note(path)
            if not note:
                continue
            _, _, lines, flines, ftext = note
            ftitle = fold(os.path.splitext(name)[0])

            matched, score = 0, 0
            for p in pats:
                n = len(p.findall(ftext))
                in_title = bool(p.search(ftitle))
                if n or in_title:
                    matched += 1
                    score += min(n, 5) + (8 if in_title else 0)
            if matched < mindest:
                continue
            if matched == len(tokens):
                score += 3
            treffer.append((matched, score, path, name, lines, flines))

    treffer.sort(key=lambda x: (x[0], x[1]), reverse=True)

    ergebnis = []
    for matched, score, path, name, lines, flines in treffer[:max_results]:
        start = _frontmatter_end(lines)
        bi, best = -1, 0
        for i in range(start, len(flines)):
            n = sum(1 for p in pats if p.search(flines[i]))
            if n > best:
                best, bi = n, i
        if bi < 0:
            bi = start
        titel_f = fold(os.path.splitext(name)[0])
        # Zeilen, die nur den Notiztitel wiederholen (Überschrift "# Titel"), werden weggelassen
        gereinigt = [c for c in (clean_text(l) for l in lines[max(start, bi - 1):bi + 12])
                     if c and fold(c) != titel_f]
        excerpt = cut(" ".join(gereinigt), 1400)
        best_line = clean_text(lines[bi]) if bi < len(lines) else ""
        if fold(best_line) == titel_f or (len(best_line) < 60 and len(gereinigt) > 1):
            best_line = " ".join(gereinigt[:2])              # Überschrift o.ä. -> Folgezeile dazu
        ergebnis.append({
            "title": os.path.splitext(name)[0],
            "path": path,
            "rel": os.path.relpath(path, vault),
            "snippet": cut(best_line, 320),
            "excerpt": excerpt,
            "matched": matched,
            "score": score,
        })
    return ergebnis


# ============================== Websuche ==============================
def search_web(query, max_results=5):
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS      # alter Paketname
        except ImportError:
            raise WissenFehler("Für die Websuche fehlt das Paket ddgs. Bitte pip install ddgs ausführen.")
    try:
        roh = DDGS(timeout=10).text(query, region="de-de", safesearch="moderate",
                                    max_results=max_results)
    except Exception as e:
        raise WissenFehler(f"Die Websuche ist gerade nicht erreichbar ({type(e).__name__}).")
    ergebnis = []
    for r in roh or []:
        ergebnis.append({
            "title": clean_text(r.get("title", "")),
            "url": r.get("href", "") or r.get("url", ""),
            "snippet": cut(clean_text(r.get("body", "")), 400),
        })
    return ergebnis


# ============================== Qwen (Ollama) ==============================
KEINE_ANTWORT = "Dazu steht nichts Verwertbares in den Quellen."


def _system_prompt():
    name = getattr(config, "USER_NAME", "")
    return (
        f"Du bist JARVIS, der Sprachassistent von {name}. Beantworte die Frage NUR anhand der "
        "Quellen, die der Nutzer mitschickt. Antworte auf Deutsch in höchstens drei kurzen, gut "
        "sprechbaren Sätzen, ohne Aufzählungen, ohne Markdown, ohne Links und ohne Quellennummern. "
        "Gib nur Angaben wieder, die ausdrücklich zum gefragten Thema in den Quellen stehen, und vermische nichts aus "
        "anderen Abschnitten (z.B. nicht die Entstehung des Sonnensystems mit der Beschaffenheit der Sonne). "
        "Ist die Frage nur ein Thema oder ein Suchauftrag (z.B. \"Suche nach ...\"), fasse zusammen, was die Quellen "
        "dazu enthalten, und nenne die wichtigsten Angaben. "
        f"Nur wenn die Quellen wirklich nichts zum Thema enthalten, antworte genau mit: {KEINE_ANTWORT}"
    )


def _als_frage(frage, query):
    """Aus einem Suchauftrag ("Suche in Obsidian nach X") wird eine Frage, die Qwen beantworten kann."""
    f = (frage or "").strip()
    if re.match(r"^(?:jarvis[ ,]*)?(?:such|find|schau|schlag)", f, re.IGNORECASE) or not f.endswith("?") and len(f.split()) <= 3:
        return f"Was steht in den Quellen zu {query}? Fasse die wichtigsten Angaben kurz zusammen."
    return f


def ask_qwen(frage, quellen, max_kontext=3200):
    """quellen: Liste von (Bezeichnung, Text). Gibt eine kurze deutsche Antwort zurück."""
    if ollama is None:
        raise WissenFehler("Das Paket ollama fehlt.")
    kontext = ""
    for i, (label, text) in enumerate(quellen, 1):
        block = f"[{i}] {label}: {text}\n"
        if len(kontext) + len(block) > max_kontext:
            block = block[:max(0, max_kontext - len(kontext))]
        kontext += block
        if len(kontext) >= max_kontext:
            break

    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"Frage: {frage}\n\nQuellen:\n{kontext}"},
    ]
    # Gleiches Modell UND gleiche Kontextgröße wie der Agent: Ollama lädt das Modell sonst bei jedem
    # Wechsel neu (mehrere Sekunden Verzögerung, der nächste Befehl geht verloren).
    modell = getattr(config, "AGENT_MODELL", None) or getattr(config, "OLLAMA_MODEL", "qwen3.5:4b")
    opts = {"num_ctx": int(getattr(config, "AGENT_NUM_CTX", 8192)), "temperature": 0.2}
    keep = getattr(config, "OLLAMA_KEEP_ALIVE", "30m")
    try:
        client = ollama.Client(timeout=getattr(config, "OLLAMA_TIMEOUT", 90))
    except TypeError:
        client = ollama.Client()

    resp, fehler = None, None
    for extra in (dict(think=False, keep_alive=keep), dict(keep_alive=keep), dict(think=False), dict()):
        try:                                    # ältere ollama-Bibliothek/Modelle kennen 'think' oder 'keep_alive' evtl. nicht
            resp = client.chat(model=modell, messages=messages, options=opts, **extra)
            break
        except TypeError as e:
            fehler = e
        except Exception as e:
            fehler = e
            if not ("think" in str(e).lower() and "think" in extra):
                break
    if resp is None:
        raise WissenFehler(f"Qwen antwortet nicht ({type(fehler).__name__}).")

    try:
        text = resp["message"]["content"] or ""
    except (KeyError, TypeError):
        text = getattr(getattr(resp, "message", None), "content", "") or ""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = clean_text(text.replace("\n", " "))
    if not text:
        raise WissenFehler("Qwen hat keine Antwort geliefert.")
    return cut(text, 700)


def _ganze_notiz(path, limit=4800):
    """Text einer ganzen Notiz (ohne Bilder und Frontmatter), für Fragen, die genau diese Notiz meinen."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read(200_000).splitlines()
    except OSError:
        return ""
    lines = lines[_frontmatter_end(lines):]
    teile = [c for c in (clean_text(l) for l in lines if not l.strip().startswith("![")) if c]
    return cut(" ".join(teile), limit)


def _titel_passt(titel, query):
    """True, wenn (fast) alle Suchwörter im Notiztitel stehen: dann ist genau diese Notiz gemeint.
    Kurzwörter wie "KI" müssen als ganzes Wort im Titel stehen."""
    t = fold(titel)
    tw = set(re.findall(r"[a-z0-9]+", t))
    toks = [w for w in re.findall(r"[a-z0-9]+", fold(query))
            if (len(w) > 3 or w in KURZWOERTER) and w not in ("obsidian", "notiz")]
    if not toks:
        return False
    treffer = sum(1 for w in toks if (w in tw if len(w) <= 3 else w[:6] in t))
    return treffer / len(toks) >= 0.6      # Whisper-Verhörer ("Sondes") dürfen fehlen


def _bereich(frage):
    """Ordner, auf den sich die Suche beschränken soll ("Suche im Lexikon ..." -> "lexikon"), sonst None."""
    return "lexikon" if re.search(r"\blexikon\b", fold(frage or "")) else None


def _obsidian_treffer(query, frage, max_t):
    """Wortsuche + (wenn verfügbar) Bedeutungssuche. Ohne Index/Embedding-Modell bleibt es bei der Wortsuche.
    Jarvis' eigene Gesprächszusammenfassungen (WISSEN_AUSSCHLUSS) zählen nicht als Wissensquelle.
    "Suche im Lexikon ..." sucht nur in Notizen, die in einem Ordner "Lexikon" liegen."""
    bereich = _bereich(frage)
    mehr = 30 if bereich else max_t + 3
    treffer = None
    try:
        import semantik
        if semantik.bereit():
            treffer = semantik.kombiniert(query, frage, mehr)
    except ImportError:
        pass
    if treffer is None:
        treffer = search_obsidian(query, mehr)
    aus = [fold(a) for a in getattr(config, "WISSEN_AUSSCHLUSS", ("Gespräche",))]
    treffer = [t for t in treffer if not any(a in fold(t["rel"]) for a in aus)]
    if bereich:
        treffer = [t for t in treffer if bereich in [fold(p) for p in re.split(r"[\\/]", t["rel"])[:-1]]]
    return sorted(treffer, key=lambda t: _titelrang(t["title"], query))[:max_t]       # stabil: sonst bleibt die Reihenfolge


def _titelrang(titel, query):
    """0 = ein Suchwort ist ein ganzes Wort im Titel ("Die Sonne"), 1 = Titel enthält es nur als Wortteil
    ("Unser Sonnensystems"), 2 = sonstige Notiz, 3 = Inhaltsverzeichnis ("Index ...")."""
    tw = set(re.findall(r"[a-z0-9]+", fold(titel)))
    qw = [w for w in re.findall(r"[a-z0-9]+", fold(query)) if len(w) > 2]
    if "index" in tw and "index" not in qw:
        return 3
    if any(w in tw for w in qw):
        return 0
    if _titel_passt(titel, query):
        return 1
    return 2


# ============================== Gesamtablauf ==============================
def beantworte(art, query, frage, log=print):
    """Sucht und formuliert die Antwort. Gibt (gesprochener_text, quellen_liste) zurück.
    Wirft keine Fehler nach außen: Probleme werden geloggt und als verständlicher Satz zurückgegeben."""
    max_t = int(getattr(config, "WISSEN_MAX_TREFFER", 5))
    ki = bool(getattr(config, "WISSEN_KI_ANTWORT", True))
    try:
        if art == "obsidian":
            treffer = _obsidian_treffer(query, frage, max_t)
            if not treffer:
                wo = "Im Lexikon" if _bereich(frage) else "In Obsidian"
                return f"{wo} habe ich nichts zu {query} gefunden.", []
            quellen = [f"{i}. {t['title']}  ({t['rel']})" for i, t in enumerate(treffer, 1)]
            antwort = None
            if ki:
                try:
                    quellen_txt, kontext = [(t["title"], t["excerpt"]) for t in treffer[:3]], 4200
                    if _titel_passt(treffer[0]["title"], query):            # Titel = Suchbegriff -> ganze Notiz lesen
                        voll = _ganze_notiz(treffer[0]["path"])
                        if len(voll) > len(treffer[0]["excerpt"]):
                            quellen_txt, kontext = [(treffer[0]["title"], voll)], 5200
                    antwort = ask_qwen(_als_frage(frage, query), quellen_txt, max_kontext=kontext)
                    if antwort.startswith("Dazu steht nichts"):
                        antwort += f" Die ähnlichste Notiz heißt {treffer[0]['title']}."
                except WissenFehler as e:
                    log(f"[Wissen]: Qwen nicht verfügbar ({e}) - lese die Treffer vor.")
            if not antwort:
                n = len(treffer)
                kopf = ("Ich habe eine passende Notiz gefunden" if n == 1
                        else f"Ich habe {n} passende Notizen gefunden. Am besten passt")
                antwort = f"{kopf}: {treffer[0]['title']}. {treffer[0]['snippet']}"
            return antwort, quellen

        # --- Web ---
        treffer = search_web(query, max_t)
        if not treffer:
            return f"Im Web habe ich nichts zu {query} gefunden.", []
        quellen = [f"{i}. {t['title']}  -  {t['url']}" for i, t in enumerate(treffer, 1)]
        antwort = None
        if ki:
            try:
                antwort = ask_qwen(frage, [(t["title"], t["snippet"]) for t in treffer[:5]])
            except WissenFehler as e:
                log(f"[Wissen]: Qwen nicht verfügbar ({e}) - lese die Treffer vor.")
        if not antwort:
            antwort = f"Der erste Treffer: {treffer[0]['title']}. {treffer[0]['snippet']}"
        return antwort, quellen

    except WissenFehler as e:
        log(f"[Wissen Fehler]: {e}")
        return str(e), []
    except Exception as e:
        log(f"[Wissen Fehler]: {type(e).__name__}: {e}")
        return "Bei der Suche ist ein Fehler aufgetreten.", []


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("obsidian", "web"):
        print("Aufruf: python wissen.py obsidian|web <Suchbegriff oder Frage>")
        sys.exit(1)
    art, text = sys.argv[1], " ".join(sys.argv[2:])
    query = (detect_intent(f"suche {art} {text}") or (art, text))[1] or text
    antwort, quellen = beantworte(art, query, text)
    print("\nQuellen:")
    for q in quellen:
        print("  ", q)
    print("\nJARVIS sagt:\n  " + antwort)
