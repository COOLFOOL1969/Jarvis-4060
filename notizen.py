"""
notizen.py - Obsidian-Notizen per Sprache

  "Notiz: Filament PETG nachbestellen"            -> JARVIS/Inbox.md
  "Daily Note: Drucker kalibriert"                -> Daily Note von heute
  "Merk dir, Paket abholen"                       -> JARVIS/Aufgaben.md   (- [ ] Paket abholen)
  "Setz Milch und Eier auf die Einkaufsliste"     -> JARVIS/Einkaufsliste.md
  "Was steht auf meiner Liste?"                   -> liest die offenen Aufgaben vor
  "Hake Paket abholen ab"                         -> macht daraus - [x] ... ✅ Datum
  "Öffne die Notiz Düsentemperatur"               -> öffnet sie in Obsidian
  "Lies mir die Notiz Düsentemperatur vor"        -> liest sie vor

Die Dateien liegen im Obsidian-Archiv und sind ganz normale Notizen (Obsidian Tasks kompatibel).
"""

import os
import re
import difflib
import urllib.parse
from datetime import datetime

import config
import wissen
from zeit import norm


class NotizFehler(Exception):
    """Fehler mit einer verständlichen Meldung, die JARVIS aussprechen kann."""


LISTEN = {
    "aufgaben": ("Aufgaben.md", "Aufgaben", "Aufgabenliste"),
    "einkauf": ("Einkaufsliste.md", "Einkaufsliste", "Einkaufsliste"),
}
_EINKAUF_WOERTER = ("einkaufsliste", "einkaufszettel", "einkaufen", "besorgen", "kaufen", "gekauft")


# ============================== Dateien ==============================
def _vault():
    v = config.OBSIDIAN_VAULT_PATH
    if not os.path.isdir(v):
        raise NotizFehler("Das Obsidian-Archiv wurde nicht gefunden.")
    return v


def _jarvis_ordner():
    return os.path.join(_vault(), getattr(config, "JARVIS_ORDNER", "JARVIS"))


def _lesen(pfad):
    try:
        with open(pfad, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().lstrip("﻿")
    except OSError:
        return ""


def _anhaengen(pfad, zeilen, kopf=None):
    """Hängt Zeilen an eine Datei an (legt Datei und Ordner bei Bedarf mit Überschrift an)."""
    if isinstance(zeilen, str):
        zeilen = [zeilen]
    try:
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        vorhanden = os.path.exists(pfad)
        text = _lesen(pfad) if vorhanden else ""
        with open(pfad, "a", encoding="utf-8", newline="\n") as f:
            if not vorhanden and kopf:
                f.write(kopf + "\n\n")
            elif vorhanden and text and not text.endswith("\n"):
                f.write("\n")
            for z in zeilen:
                f.write(z + "\n")
    except OSError as e:
        raise NotizFehler(f"Ich konnte die Notiz nicht schreiben ({e.__class__.__name__}).")


def _sauber(s):
    s = " ".join(s.split()).strip(" :,-–—")
    s = s.rstrip(".!?;, ")
    return s[:1].upper() + s[1:] if s else s


# ============================== Diktat ==============================
def diktat(text, ziel="inbox", jetzt=None):
    """Speichert eine Notiz. Gibt den Pfad der Datei zurück."""
    jetzt = jetzt or datetime.now()
    text = _sauber(text)
    if not text:
        raise NotizFehler("Was soll ich notieren?")
    if ziel == "daily":
        ordner = os.path.join(_vault(), getattr(config, "DAILY_NOTE_ORDNER", "Daily Notes"))
        name = jetzt.strftime(getattr(config, "DAILY_NOTE_FORMAT", "%Y-%m-%d")) + ".md"
        pfad = os.path.join(ordner, name)
        _anhaengen(pfad, f"- {jetzt:%H:%M} {text}", kopf=f"# {jetzt:%Y-%m-%d}")
    else:
        pfad = os.path.join(_jarvis_ordner(), "Inbox.md")
        _anhaengen(pfad, f"- {jetzt:%Y-%m-%d %H:%M} – {text}", kopf="# JARVIS Inbox")
    return pfad


# ============================== Listen (Aufgaben / Einkauf) ==============================
_TASK = re.compile(r"^(\s*[-*+]\s+)\[( |x|X)\](\s+)(.*)$")
_META = re.compile(r"\s*[✅➕📅⏳🛫🔼🔽⏫⏬].*$")


def _listen_pfad(liste):
    return os.path.join(_jarvis_ordner(), LISTEN[liste][0])


def liste_lesen(liste="aufgaben"):
    """Gibt (offene Einträge, Anzahl erledigter) zurück."""
    offen, erledigt = [], 0
    for zeile in _lesen(_listen_pfad(liste)).splitlines():
        m = _TASK.match(zeile)
        if not m:
            continue
        if m.group(2) == " ":
            offen.append(_META.sub("", m.group(4)).strip())
        else:
            erledigt += 1
    return offen, erledigt


def liste_add(items, liste="aufgaben"):
    """Setzt Einträge auf die Liste. Gibt (neu hinzugefügt, schon vorhanden) zurück."""
    offen, _ = liste_lesen(liste)
    bekannt = {wissen.fold(o) for o in offen}
    neu, doppelt = [], []
    for it in items:
        it = _sauber(it)
        if not it:
            continue
        if wissen.fold(it) in bekannt:
            doppelt.append(it)
        else:
            neu.append(it)
            bekannt.add(wissen.fold(it))
    if neu:
        _anhaengen(_listen_pfad(liste), [f"- [ ] {i}" for i in neu], kopf=f"# {LISTEN[liste][1]}")
    return neu, doppelt


def _passt(query, item):
    q, i = wissen.fold(query), wissen.fold(item)
    qt = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) >= 3]
    abdeckung = sum(1 for t in qt if t in i) / len(qt) if qt else 0.0
    return max(abdeckung, difflib.SequenceMatcher(None, q, i).ratio())


def liste_abhaken(query, liste=None, jetzt=None):
    """Hakt den am besten passenden offenen Eintrag ab. Gibt (Text, Liste) oder None zurück."""
    jetzt = jetzt or datetime.now()
    query = re.sub(r"^(?:die|der|das|den|dem|ein|eine)\s+", "", query.strip(), flags=re.IGNORECASE)
    beste = (0.0, None, None, None)           # Score, Liste, Zeilenindex, Zeilen
    for l in ([liste] if liste else list(LISTEN)):
        zeilen = _lesen(_listen_pfad(l)).splitlines()
        for idx, z in enumerate(zeilen):
            m = _TASK.match(z)
            if m and m.group(2) == " ":
                s = _passt(query, _META.sub("", m.group(4)))
                if s > beste[0]:
                    beste = (s, l, idx, zeilen)
    score, l, idx, zeilen = beste
    if l is None or score < 0.6:
        return None
    m = _TASK.match(zeilen[idx])
    text = _META.sub("", m.group(4)).strip()
    zeilen[idx] = f"{m.group(1)}[x]{m.group(3)}{m.group(4)} ✅ {jetzt:%Y-%m-%d}"
    try:
        with open(_listen_pfad(l), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(zeilen) + "\n")
    except OSError as e:
        raise NotizFehler(f"Ich konnte die Liste nicht ändern ({e.__class__.__name__}).")
    return text, l


# ============================== Notizen finden / öffnen / vorlesen ==============================
def finde_notiz(query):
    """Sucht eine Notiz anhand ihres NAMENS (tolerant). Gibt Dict oder None zurück."""
    vault = _vault()
    toks = [t for t in re.findall(r"[a-z0-9]+", wissen.fold(query)) if len(t) >= 2]
    if not toks:
        return None
    q = " ".join(toks)
    kand = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in wissen.SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            stem = os.path.splitext(f)[0]
            fs = wissen.fold(stem)
            fj = " ".join(re.findall(r"[a-z0-9]+", fs))
            abdeckung = sum(1 for t in toks if t in fs) / len(toks)
            ratio = difflib.SequenceMatcher(None, q, fj).ratio()
            score = 1.0 if fj == q else 0.75 * abdeckung + 0.25 * ratio
            if score >= 0.5 and (abdeckung >= 0.5 or ratio >= 0.8):
                kand.append((score, len(stem), stem, os.path.join(root, f)))
    if not kand:
        return None
    kand.sort(key=lambda k: (-k[0], k[1]))
    best = kand[0]
    alt = [k[2] for k in kand[1:3] if k[0] >= best[0] - 0.05]
    return {"title": best[2], "path": best[3], "score": best[0], "alternativen": alt}


def obsidian_uri(pfad):
    vault = _vault()
    name = getattr(config, "OBSIDIAN_VAULT_NAME", None) or os.path.basename(vault.rstrip("\\/"))
    rel = os.path.relpath(pfad, vault).replace(os.sep, "/")
    if rel.lower().endswith(".md"):
        rel = rel[:-3]
    return f"obsidian://open?vault={urllib.parse.quote(name, safe='')}&file={urllib.parse.quote(rel, safe='')}"


def oeffne(pfad):
    uri = obsidian_uri(pfad)
    try:
        os.startfile(uri)                     # Windows: öffnet den obsidian://-Link
    except AttributeError:
        import webbrowser
        webbrowser.open(uri)
    return uri


def notiz_text(pfad):
    """Notiztext ohne YAML-Kopf, Code und Markdown - gut zum Vorlesen."""
    text = _lesen(pfad)
    zeilen = text.splitlines()
    start = 0
    if zeilen and zeilen[0].strip() == "---":
        for i in range(1, min(len(zeilen), 60)):
            if zeilen[i].strip() in ("---", "..."):
                start = i + 1
                break
    text = "\n".join(zeilen[start:])
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"%%.*?%%", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[\[[^\]]*\]\]", " ", text)
    teile = []
    for z in text.splitlines():
        z = wissen.clean_text(z)
        if z:
            teile.append(z if z[-1] in ".!?:;" else z + ".")
    return " ".join(teile)


def lese_stuecke(text, stueck=350, maximal=None):
    """Teilt den Text in sprechbare Stücke. Gibt (Stücke, wurde_gekürzt) zurück."""
    maximal = maximal or int(getattr(config, "NOTIZ_VORLESEN_MAX", 1500))
    gekuerzt = len(text) > maximal
    if gekuerzt:
        text = wissen.cut(text, maximal)
    stuecke, aktuell = [], ""
    for satz in re.split(r"(?<=[.!?])\s+", text):
        if aktuell and len(aktuell) + len(satz) + 1 > stueck:
            stuecke.append(aktuell)
            aktuell = ""
        aktuell = (aktuell + " " + satz).strip()
    if aktuell:
        stuecke.append(aktuell)
    return stuecke, gekuerzt


# ============================== Befehlserkennung ==============================
_LISTEN_NAMEN = r"(einkaufsliste|einkaufszettel|aufgabenliste|aufgaben|to-?do-?liste|todos?|liste)"
_ART = r"(?:die |das |den |meine |meinen |mein |der )?"

P_LIES_NOTIZ = re.compile(r"^(?:lies|lese|les)\s+(?:mir\s+)?" + _ART + r"notiz\s+(.+?)(?:\s+vor)?[.!?]*$")
P_LIES_NOTIZ2 = re.compile(r"^notiz\s+(.+?)\s+vorlesen[.!?]*$")
P_OEFFNE = re.compile(r"^(?:offne|offnen|zeig(?:e)?|mach(?:e)?)\s+(?:mir\s+)?" + _ART +
                      r"notiz\s+(.+?)(?:\s+(?:auf|an))?[.!?]*$")
P_LISTE_LESEN = [
    re.compile(r"^was\s+(?:steht|ist)\s+(?:auf|in)\s+(?:meiner|meinen|meine|der|den|die)\s+" + _LISTEN_NAMEN + r"\b"),
    re.compile(r"^(?:lies|lese|les|zeig(?:e)?|sag)\s+(?:mir\s+)?(?:meine|meiner|die|den)?\s*" + _LISTEN_NAMEN +
               r"(?:\s+vor)?[.!?]*$"),
    re.compile(r"^(?:welche|was fur)\s+(aufgaben)\s+(?:habe ich|sind offen|stehen an)"),
    re.compile(r"^(?:meine|offene)\s+(aufgaben|todos?)[.!?]*$"),
    re.compile(r"^was\s+muss\s+ich\s+(?:noch\s+)?(?:alles\s+)?(einkaufen|besorgen|kaufen|erledigen|tun|machen)\b"),
]
P_ABHAKEN = [
    re.compile(r"^(?:hak(?:e)?|streich(?:e)?|erledige)\s+(.+?)\s+(?:ab|durch|als erledigt|auf erledigt)"
               r"(?:\s+(?:von|auf|in)\s+(?:der|meiner|die|den)?\s*(?:einkaufsliste|liste|aufgabenliste|aufgaben)?)?[.!?]*$"),
    re.compile(r"^(?:hak(?:e)?|streich(?:e)?)\s+(.+?)\s+von\s+(?:der|meiner|die)\s+"
               r"(?:einkaufsliste|liste|aufgabenliste|aufgaben)[.!?]*$"),
]
P_ERLEDIGT = re.compile(r"^(.+?)\s+(?:ist|sind)\s+(?:erledigt|fertig|gekauft|geschafft|abgehakt)[.!?]*$")
P_AUF_LISTE = re.compile(r"^(?:setz(?:e)?|schreib(?:e)?|pack(?:e)?|tu|tue|leg(?:e)?)\s+(.+?)\s+(?:auf|in|an)\s+"
                         r"(?:die|meine|meiner|der|den|mein|meinen)?\s*" + _LISTEN_NAMEN +
                         r"(?:\s+(?:hinzu|drauf|dazu|rauf))?[.!?]*$")
P_FUEGE = re.compile(r"^fug(?:e)?\s+(.+?)\s+(?:zur|zu der|zu meiner|zu meinen|zu den|auf die|auf meine)\s+" +
                     _LISTEN_NAMEN + r"\s*(?:hinzu)?[.!?]*$")
P_LISTE_DP = re.compile(r"^(?:auf|zur|zu)\s+(?:die|der|meine|meiner)?\s*" + _LISTEN_NAMEN + r"\s*[:,\-]\s*(.+)$")
P_MERK = re.compile(r"^merk(?:e)?\s+(?:dir|mir)\b\s*[:,\-]?\s*(?:dass\s+)?(.+)$")
P_AUFGABE = re.compile(r"^(?:neue\s+|eine\s+neue\s+)?(?:aufgabe|to-?do|todo)\b\s*[:,\-]?\s*(.+)$")
P_DAILY = re.compile(r"^(?:schreib(?:e)?\s+)?(?:(?:in|ins|an|auf|zur|zu)\s+)?(?:die\s+|das\s+|meine\s+|mein\s+|meinen\s+)?"
                     r"(?:daily\s*note|tagesnotiz|tagebuch|tageseintrag)\s*(?:eintrag\s*)?[:,\-]?\s*(.+)$")
P_DIKTAT = re.compile(r"^(?:(?:neue|eine neue|kurze)\s+)?(?:notiz|notiere|notier|halte fest|"
                      r"schreib(?:e)?\s+(?:mir\s+)?auf)\b\s*[:,\-]?\s*(?:dass\s+)?(.+)$")


P_NOTIZ_LESEN = [
    re.compile(r"^was\s+(?:steht|ist|war)\s+(?:in|auf)\s+(?:der|meiner|meinem|dem)\s+(inbox|daily\s*note|tagesnotiz|tagebuch)\b"),
    re.compile(r"^(?:lies|lese|les|zeig(?:e)?|sag)\s+(?:mir\s+)?(?:die|meine|meiner|den|das)?\s*(inbox|daily\s*note|tagesnotiz|tagebuch)(?:\s+vor)?[.!?]*$"),
    re.compile(r"^was\s+(?:habe|hab)\s+ich\s+(?:heute\s+)?(?:alles\s+|zuletzt\s+)?(notiert|aufgeschrieben|diktiert)\b"),
]


def _liste_von(wort):
    return "einkauf" if any(w in wort for w in _EINKAUF_WOERTER) else "aufgaben"


def _teile_items(text, liste):
    if liste == "einkauf":
        return [t for t in re.split(r"\s*,\s*|\s+und\s+", text) if t.strip()]
    return [text]


def erkenne(cmd):
    """Erkennt Notiz-/Listenbefehle. Gibt ein Dict mit 'art' zurück oder None."""
    cmd = cmd.strip()
    n = norm(cmd)
    if not n:
        return None
    zeitwort = bool(re.search(r"\b(?:timer|wecker|erinnerung\w*|erinnere|erinner)\b", n))

    def teil(m, g):                      # Text aus dem Original (Groß-/Kleinschreibung bleibt erhalten)
        return cmd[m.start(g):m.end(g)]

    if not zeitwort:
        for p in P_NOTIZ_LESEN:
            m = p.match(n)
            if m:
                return {"art": "diktat_lesen", "ziel": "daily" if re.match(r"daily|tages|tagebuch", m.group(1)) else "inbox"}

    m = P_LIES_NOTIZ.match(n) or P_LIES_NOTIZ2.match(n)
    if m:
        return {"art": "lies", "query": teil(m, 1)}
    m = P_OEFFNE.match(n)
    if m:
        return {"art": "oeffne", "query": teil(m, 1)}

    if not zeitwort:
        for p in P_LISTE_LESEN:
            m = p.match(n)
            if m:
                return {"art": "liste_lesen", "liste": _liste_von(m.group(1))}
        for p in P_ABHAKEN:
            m = p.match(n)
            if m:
                return {"art": "abhaken", "query": teil(m, 1), "still": False}

    for p, gi, gl in ((P_AUF_LISTE, 1, 2), (P_FUEGE, 1, 2)):
        m = p.match(n)
        if m:
            liste = _liste_von(m.group(gl))
            return {"art": "liste_add", "liste": liste, "items": _teile_items(teil(m, gi), liste)}
    m = P_LISTE_DP.match(n)
    if m:
        liste = _liste_von(m.group(1))
        return {"art": "liste_add", "liste": liste, "items": _teile_items(teil(m, 2), liste)}
    m = P_MERK.match(n)
    if m and re.match(r"^(?:ich|mein\w*|wir|unser\w*|mir)\b", m.group(1)):
        m = None            # persönlicher Fakt ("merk dir, dass ich Vegetarier bin") -> Agent/Profil, keine Aufgabe
    elif m is None:
        m = P_AUFGABE.match(n)
    if m:
        return {"art": "liste_add", "liste": "aufgaben", "items": [teil(m, 1)]}

    m = P_DAILY.match(n)
    if m:
        return {"art": "diktat", "ziel": "daily", "text": teil(m, 1)}
    m = P_DIKTAT.match(n)
    if m and not re.match(r"^(?:vorlesen|offnen|aufmachen|auf)\b", m.group(1)):
        return {"art": "diktat", "ziel": "inbox", "text": teil(m, 1)}

    if not zeitwort:
        m = P_ERLEDIGT.match(n)
        if m and len(m.group(1).split()) <= 6:
            return {"art": "abhaken", "query": teil(m, 1), "still": True}
    return None


# ============================== Ausführen ==============================
def _aufzaehlen(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " und " + items[-1]


def fuehre_aus(intent, jetzt=None):
    """Führt den Befehl aus. Gibt ein Dict zurück:
       {'sprich': Text, 'lesen': [Stücke]|None, 'oeffne': Pfad|None, 'still': bool}"""
    art = intent["art"]
    erg = {"sprich": "", "lesen": None, "oeffne": None, "still": False}

    if art == "diktat":
        diktat(intent["text"], intent["ziel"], jetzt)
        erg["sprich"] = "In die Daily Note geschrieben." if intent["ziel"] == "daily" else "Notiz gespeichert."

    elif art == "liste_add":
        neu, doppelt = liste_add(intent["items"], intent["liste"])
        name = "Einkaufsliste" if intent["liste"] == "einkauf" else "Aufgabenliste"
        teile = []
        if neu:
            teile.append(f"Auf die {name}: {_aufzaehlen(neu)}.")
        if doppelt:
            teile.append(f"{_aufzaehlen(doppelt)} stand schon drauf." if len(doppelt) == 1
                         else f"{_aufzaehlen(doppelt)} standen schon drauf.")
        erg["sprich"] = " ".join(teile) or "Was soll ich auf die Liste setzen?"

    elif art == "diktat_lesen":
        jetzt = jetzt or datetime.now()
        if intent["ziel"] == "daily":
            ordner = os.path.join(_vault(), getattr(config, "DAILY_NOTE_ORDNER", "Daily Notes"))
            pfad = os.path.join(ordner, jetzt.strftime(getattr(config, "DAILY_NOTE_FORMAT", "%Y-%m-%d")) + ".md")
            zeilen = [re.sub(r"^\s*[-*]\s*", "", z).strip() for z in _lesen(pfad).splitlines() if re.match(r"^\s*[-*]\s+\S", z)]
            zeilen = [re.sub(r"^(\d{1,2}):(\d{2})\s+", lambda m: f"um {int(m.group(1))} Uhr {m.group(2)}: ", z) for z in zeilen]
            erg["sprich"] = ("In der Daily Note von heute steht noch nichts." if not zeilen else
                             f"In der Daily Note von heute {'steht' if len(zeilen) == 1 else 'stehen'} {len(zeilen)} {'Eintrag' if len(zeilen) == 1 else 'Einträge'}: " + ". ".join(zeilen[-8:]) + ".")
        else:
            roh = [z for z in _lesen(os.path.join(_jarvis_ordner(), "Inbox.md")).splitlines() if re.match(r"^\s*[-*]\s+\S", z)]
            ausgabe = []
            for z in roh[-5:]:
                m = re.match(r"^\s*[-*]\s+(\d{4})-(\d{2})-(\d{2})\s+\d{1,2}:\d{2}\s+[–-]\s+(.*)$", z)
                ausgabe.append(f"Am {int(m.group(3))}.{int(m.group(2))}.: {m.group(4)}" if m else re.sub(r"^\s*[-*]\s*", "", z))
            erg["sprich"] = ("Die Inbox ist leer." if not ausgabe else
                             f"In der Inbox {'steht' if len(roh) == 1 else 'stehen'} {len(roh)} Notiz{'en' if len(roh) != 1 else ''}"
                             + (", die letzten fünf: " if len(roh) > 5 else ": ") + ". ".join(ausgabe) + ".")

    elif art == "liste_lesen":
        offen, _ = liste_lesen(intent["liste"])
        if intent["liste"] == "einkauf":
            if not offen:
                erg["sprich"] = "Deine Einkaufsliste ist leer."
            else:
                zeig = offen[:10]
                rest = f" Und {len(offen) - 10} weitere." if len(offen) > 10 else ""
                erg["sprich"] = (f"Auf deiner Einkaufsliste {'steht' if len(offen) == 1 else 'stehen'} "
                                 f"{len(offen)} Artikel: {_aufzaehlen(zeig)}.{rest}")
        else:
            if not offen:
                erg["sprich"] = "Du hast keine offenen Aufgaben."
            else:
                zeig = offen[:8]
                rest = f" Und {len(offen) - 8} weitere." if len(offen) > 8 else ""
                erg["sprich"] = (f"Du hast {len(offen)} offene Aufgabe{'n' if len(offen) != 1 else ''}: "
                                 + ". ".join(zeig) + "." + rest)

    elif art == "abhaken":
        r = liste_abhaken(intent["query"], None, jetzt)
        if r:
            erg["sprich"] = f"Erledigt: {r[0]}."
        elif intent.get("still"):
            erg["still"] = True
        else:
            erg["sprich"] = "Dazu habe ich nichts auf den Listen gefunden."

    elif art in ("oeffne", "lies"):
        n = finde_notiz(intent["query"])
        if not n:
            erg["sprich"] = f"Eine Notiz mit dem Namen {intent['query']} habe ich nicht gefunden."
        elif art == "oeffne":
            erg["oeffne"] = n["path"]
            erg["sprich"] = f"Öffne die Notiz {n['title']}."
            if n["alternativen"]:
                erg["sprich"] += f" Es gibt auch {_aufzaehlen(n['alternativen'])}."
        else:
            text = notiz_text(n["path"])
            if not text:
                erg["sprich"] = f"Die Notiz {n['title']} ist leer."
            else:
                stuecke, gekuerzt = lese_stuecke(text)
                if gekuerzt:
                    stuecke.append("Das war der Anfang der Notiz.")
                erg["sprich"] = f"Ich lese die Notiz {n['title']}."
                erg["lesen"] = stuecke
    return erg
