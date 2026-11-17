"""
agent.py - JARVIS' "Gehirn" für alles, was nicht in ein festes Befehlsmuster passt

  * Qwen versteht freie Sätze und wählt selbst die passende Funktion (Werkzeug): Timer, Notiz, Websuche, Obsidian-Suche ...
  * Ohne Haussteuerung - ausgelegt auf ein kleines Modell (qwen3.5:4b), das in 8 GB Grafikspeicher passt.
  * Gespräche mit Gedächtnis: die letzten Sätze bleiben 10 Minuten im Kontext ("Und morgen?")
  * Profil: die Notiz JARVIS/Profil.md in Obsidian - was dort steht, weiß Jarvis immer. "Merke dir, dass ..." ergänzt sie.
  * Aktuelle Fakten (Ämter, Preise, Nachrichten ...) werden automatisch im Web nachgeschlagen.
Die schnellen festen Befehle (Timer, Schalten, Wetter ...) bleiben die erste Stufe; der Agent springt nur ein,
wenn keiner davon passt.
"""

import os
import re
import json
import time
import threading
from datetime import datetime

import config
import wissen
import notizen
import wetter
import zeit
import gedaechtnis
import selbst
from zeit import norm

try:
    import ollama
except ImportError:
    ollama = None

_WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_planer = None
_log = print
_verlauf = []                  # [(zeit, rolle, text)]
_pending = None                # {"art":..., "text": Rückfrage, "ablauf": ts, ...}
_lock = threading.Lock()


def verbinde(planer, log=print):
    global _planer, _log
    _planer, _log = planer, log
    if getattr(config, "GEDAECHTNIS_AKTIV", True):
        with _lock:
            _verlauf[:] = gedaechtnis.laden()          # Gespräch nach einem Neustart fortsetzen


# ============================== Profil ==============================
PROFIL_VORLAGE = """# JARVIS Profil

Hier steht, was Jarvis über dich wissen soll. Jede Zeile ein Fakt - er liest diese Notiz bei jeder Anfrage mit.
Beispiele (bitte ersetzen): "Ich wohne in Musterstadt.", "Ich trinke Kaffee schwarz."

## Über mich

## Gedächtnis (von Jarvis ergänzt)
"""


def _profil_pfad():
    return os.path.join(config.OBSIDIAN_VAULT_PATH, getattr(config, "PROFIL_NOTIZ", "JARVIS/Profil.md").replace("/", os.sep))


def profil_lesen():
    p = _profil_pfad()
    try:
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(PROFIL_VORLAGE)
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return ""
    zeilen = [z.strip() for z in text.splitlines()[1:] if z.strip() and not z.startswith("#")
              and not z.startswith("Hier steht") and not z.startswith("Beispiele")]
    return wissen.cut(" ".join(zeilen).replace("- ", "; ").strip("; "), 1500)


def fakt_merken(fakt):
    fakt = " ".join(fakt.split()).strip(" -:,")
    if not fakt:
        raise notizen.NotizFehler("Was soll ich mir merken?")
    p = _profil_pfad()
    profil_lesen()                                       # legt die Datei bei Bedarf an
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            if f"- {fakt[:300]}".lower() in {z.strip().lower() for z in f}:
                return                                   # schon gemerkt
    except OSError:
        pass
    try:
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"- {fakt[:300]}\n")
    except OSError:
        raise notizen.NotizFehler("Ich konnte das Profil nicht schreiben.")


# ============================== Werkzeuge ==============================
def _t(name, beschreibung, props, pflicht):
    return {"type": "function", "function": {"name": name, "description": beschreibung,
            "parameters": {"type": "object", "properties": props, "required": pflicht}}}


S = {"type": "string"}
TOOLS = [
    _t("web_suche", "Im Internet nachschlagen. IMMER nutzen für alles, was sich ändern kann: Ämter und Amtsinhaber, "
       "Preise, Nachrichten, Sport, Termine, Produkte, aktuelle Ereignisse.", {"anfrage": S}, ["anfrage"]),
    _t("obsidian_suche", "In den eigenen Notizen des Nutzers (Obsidian) nachschlagen, z.B. Anleitungen, Einstellungen, Kontakte, "
       "Rezepte, eigene Aufzeichnungen.", {"anfrage": S}, ["anfrage"]),
    _t("timer_stellen", "Einen Timer stellen.", {"minuten": {"type": "number"}, "bezeichnung": S}, ["minuten"]),
    _t("wecker_stellen", "Einen Wecker auf eine Uhrzeit stellen (24-Stunden-Format HH:MM) oder eine Erinnerung setzen, "
       "wenn 'text' angegeben ist. 'wann' kann auch z.B. 'morgen um 9 Uhr' oder 'in 2 Stunden' sein.",
       {"wann": S, "text": S}, ["wann"]),
    _t("notiz_speichern", "Einen Gedanken als Notiz in Obsidian speichern (Inbox oder Daily Note).",
       {"text": S, "ziel": {"type": "string", "enum": ["inbox", "daily"]}}, ["text"]),
    _t("liste_hinzufuegen", "Einträge auf die Einkaufsliste oder die Aufgabenliste setzen.",
       {"eintraege": {"type": "array", "items": S}, "liste": {"type": "string", "enum": ["einkauf", "aufgaben"]}},
       ["eintraege", "liste"]),
    _t("merken", "Eine dauerhafte persönliche Information über den Nutzer speichern (Vorlieben, Gewohnheiten, Namen, Geräte). "
       "NICHT für Aufgaben oder Einkäufe.", {"fakt": S}, ["fakt"]),
    _t("systemstatus", "Echte Auskunft über Jarvis selbst: Suchindex/Semantik (erstellt? wann aktualisiert?), Gedächtnis, "
       "Jahrestage, Spracherkennung. Nutzen, wenn der Nutzer fragt, ob du etwas erstellt/aktualisiert hast oder wie es dir geht.",
       {"thema": {"type": "string", "enum": ["suche", "gedaechtnis", "jahrestage", "sprache", "alles"]}}, []),
    _t("wetter_abfragen", "Wetter für heute (0), morgen (1) oder übermorgen (2), optional an einem Ort.",
       {"tag": {"type": "integer"}, "ort": S}, []),
]


def _ausfuehren(name, a):
    """Führt ein Werkzeug aus. Gibt (Text, Rückfrage-Dict|None) zurück. 'WEB:'/'OBS:' = Suche (läuft im Aufrufer)."""
    if name in ("web_suche", "obsidian_suche"):
        q = str(a.get("anfrage", "")).strip()
        if not q:
            return "Wonach soll ich suchen?", None
        return ("WEB:" if name == "web_suche" else "OBS:") + q, None

    if name == "systemstatus":
        return selbst.auskunft(str(a.get("thema") or "alles")), None

    if name == "timer_stellen":
        try:
            m = float(a.get("minuten"))
        except (TypeError, ValueError):
            return "Wie lange soll der Timer laufen?", None
        sek = int(round(m * 60))
        phrase = f"Timer {sek} Sekunden" + (f" für {a['bezeichnung']}" if a.get("bezeichnung") else "")
        zi = zeit.erkenne(phrase)
        return (zeit.fuehre_aus(zi, _planer) if zi and _planer else "Das konnte ich nicht einstellen."), None

    if name == "wecker_stellen":
        wann, text = str(a.get("wann", "")).strip(), str(a.get("text", "") or "").strip()
        wann = re.sub(r"^(\d{1,2}):(\d{2})$", lambda m: f"um {int(m.group(1))} Uhr {int(m.group(2))}", wann)
        phrase = f"Erinnere mich {wann} an {text}" if text else f"Wecker {wann}"
        zi = zeit.erkenne(phrase)
        return (zeit.fuehre_aus(zi, _planer) if zi and _planer else "Das konnte ich nicht einstellen."), None

    if name == "notiz_speichern":
        r = notizen.fuehre_aus({"art": "diktat", "text": str(a.get("text", "")),
                                "ziel": "daily" if a.get("ziel") == "daily" else "inbox"})
        return r["sprich"], None

    if name == "liste_hinzufuegen":
        items = a.get("eintraege") or []
        if isinstance(items, str):
            items = [items]
        r = notizen.fuehre_aus({"art": "liste_add", "items": [str(i) for i in items],
                                "liste": "einkauf" if a.get("liste") == "einkauf" else "aufgaben"})
        return r["sprich"], None

    if name == "merken":
        fakt_merken(str(a.get("fakt", "")))
        return "Okay, das merke ich mir.", None

    if name == "wetter_abfragen":
        try:
            tag = max(0, min(2, int(a.get("tag", 0) or 0)))
        except (TypeError, ValueError):
            tag = 0
        return wetter.fuehre_aus({"art": "wetter", "tag": tag, "ort": a.get("ort") or None}), None

    return "Diese Funktion kenne ich nicht.", None


def _aufz(l):
    return l[0] if len(l) == 1 else ", ".join(l[:-1]) + " und " + l[-1]


# ============================== Rückfragen (Ja/Nein) ==============================
JA = {"ja", "jawohl", "klar", "genau", "mach", "mache", "bitte", "okay", "ok", "gerne", "sicher", "natuerlich", "yes", "los"}
NEIN = {"nein", "nee", "nicht", "stopp", "stop", "abbrechen", "lass", "doch", "no"}


def pending_antwort(text):
    """Falls eine Rückfrage offen ist: verarbeitet 'Ja'/'Nein'. Gibt den Sprechtext zurück oder None
    (None = es war keine Antwort auf die Rückfrage; die Rückfrage verfällt)."""
    global _pending
    with _lock:
        p, _pending = _pending, None
    if not p or time.time() > p["ablauf"]:
        return None
    w = set(re.findall(r"[a-z]+", norm(text)))
    verkn = p["intent"].get("art") == "verknuepfen"
    if w & NEIN:
        if verkn:
            import verknuepfung
            return verknuepfung.antwort_ausfuehren(p["intent"], False)
        return "Okay, abgebrochen."
    if w & JA and len(w) <= 6:
        if verkn:
            import verknuepfung
            antwort = verknuepfung.antwort_ausfuehren(p["intent"], True)
            if _nach_verknuepfen:
                try:
                    _nach_verknuepfen()
                except Exception:
                    pass
            return antwort
        return None                                     # keine Geräte-Rückfragen (ohne Haussteuerung)
    return None


_nach_verknuepfen = None        # wird von hud_server gesetzt (Archiv-Scan + Index nach einem neuen Link)


def rueckfrage_setzen(intent, text):
    """Merkt eine Ja/Nein-Rückfrage (40 s) für beliebige Intents (z.B. Link-Vorschlag)."""
    global _pending
    with _lock:
        _pending = {"intent": intent, "ablauf": time.time() + 40, "text": text}


def hat_rueckfrage():
    return bool(_pending and time.time() <= _pending["ablauf"])


# ============================== Gespräch ==============================
AKTUELL_RE = re.compile(
    r"\b(?:(?:wer|wie) ist (?:der|die|das) (?:aktuelle|neue|jetzige|derzeitige)|aktuelle[nr]? (?:nachrichten|lage|stand|preis\w*|kurs\w*)|neueste\w*|"
    r"nachrichten|news|schlagzeilen|ergebnis(?:se)?|spielstand|tabelle|wahl(?:en|ergebnis)?|"
    r"(?:wer|wie) (?:ist|heisst|heißt) (?:der|die) (?:bundeskanzler\w*|praesident\w*|präsident\w*|ministerpraesident\w*|"
    r"aussenminister\w*|außenminister\w*|papst|ceo|chef)|"
    r"wie (?:viel|hoch|teuer) (?:kostet|ist der preis)|preis(?:e)? (?:von|fuer|für)|kurs (?:von|des|der)|aktie\w*|"
    r"wann (?:kommt|erscheint|startet|findet)|release|veroeffentlicht|veröffentlicht)\b", re.IGNORECASE)


GRUNDSAETZE = ("Grundsätze: Die Entscheidung liegt immer beim Nutzer. Bei Unsicherheit oder fehlenden Angaben frage kurz nach, statt zu raten. "
               "Tu nichts Unumkehrbares ohne seine ausdrückliche Zustimmung. Erfinde nichts über ihn oder andere Personen und gib nichts "
               "Persönliches weiter. Du kannst keine Nachrichten, E-Mails oder Dateien verschicken; sag das ehrlich, statt so zu tun, "
               "als wäre es nur eine Frage der Erlaubnis. Schadet oder gefährdet eine Bitte Menschen, lehne sie knapp ab.")


def _gruss(j):
    """Gleiche Regel wie im Briefing: vor 11 Uhr Morgen, bis 18 Uhr Tag, danach Abend."""
    return "Guten Morgen" if j.hour < 11 else "Guten Tag" if j.hour < 18 else "Guten Abend"


def _system_prompt():
    j = datetime.now()
    persoenlichkeit = getattr(config, "AGENT_PERSOENLICHKEIT",
                              f"Du bist JARVIS, der Sprachassistent von {getattr(config, 'USER_NAME', '') or 'deinem Nutzer'}. Du bist höflich, "
                              "präzise, trocken-witzig und kurz. Du duzt ihn.")
    profil = profil_lesen()
    erinnerung = gedaechtnis.erinnerungen() if getattr(config, "GEDAECHTNIS_AKTIV", True) else ""
    return (f"{persoenlichkeit}\n"
            f"Heute ist {_WT[j.weekday()]}, der {j:%d.%m.%Y}, es ist {j:%H:%M} Uhr. "
            f"Wenn du grüßt, sag genau \"{_gruss(j)}\" (nicht 'Guten Morgen' oder 'Guten Abend' zu einer anderen Tageszeit).\n"
            "Regeln: Antworte auf Deutsch in höchstens drei kurzen Sätzen, ohne Markdown, Listen oder Links - es wird laut "
            "vorgelesen. Nutze für Aktionen und für alles, was sich ändern kann (Ämter, Preise, Nachrichten, Sport), "
            "IMMER ein Werkzeug statt aus dem Gedächtnis zu antworten. Erfinde nichts; wenn du etwas nicht weißt, sag es. "
            "Wenn eine Angabe fehlt (z.B. der Raum), frage kurz nach; antwortet der Nutzer danach nur mit der fehlenden Angabe "
            "(z.B. dem Raum), führe die ursprüngliche Bitte jetzt mit dem Werkzeug aus. Notizen und Profil sind oft aus Sicht "
            "des Nutzers geschrieben (\"Ich bin ...\", \"meine Frau\"): gib solche Angaben IMMER in der Du-Form wieder (\"Du bist ...\", \"deine Frau\"), "
            "nie als Ich.\n"
            f"{getattr(config, 'AGENT_GRUNDSAETZE', GRUNDSAETZE)}\n"
            f"{selbst.KURZ}\n"
            "Du hast KEINE Haussteuerung: Du kannst keine Geräte, Lampen oder Heizungen schalten und kennst "
            "keine Messwerte aus dem Haus (Strom, Raumtemperatur, Fenster). Sag das bei solchen Bitten ehrlich und erfinde keine Werte.\n"
            + (f"Was du über den Nutzer weißt: {profil}\n" if profil else "")
            + (f"Erinnerung an frühere Gespräche (nur nutzen, wenn es passt): {erinnerung}\n" if erinnerung else ""))


def _chat(messages, tools):
    if ollama is None:
        raise RuntimeError("Das Paket ollama fehlt.")
    modell = getattr(config, "AGENT_MODELL", None) or getattr(config, "OLLAMA_MODEL", "qwen3.5:4b")
    opts = {"num_ctx": int(getattr(config, "AGENT_NUM_CTX", 8192)), "temperature": 0.3}
    keep = getattr(config, "OLLAMA_KEEP_ALIVE", "30m")   # Modell im Speicher halten (kein Neuladen)
    try:
        cl = ollama.Client(timeout=getattr(config, "AGENT_TIMEOUT", 60))
    except TypeError:
        cl = ollama.Client()
    # Von neu nach alt: ältere ollama-Bibliotheken/Modelle kennen 'think' oder 'keep_alive' nicht.
    varianten = [dict(think=False, keep_alive=keep), dict(keep_alive=keep), dict(think=False), dict()]
    letzter = None
    for extra in varianten:
        try:
            return cl.chat(model=modell, messages=messages, tools=tools, options=opts, **extra)
        except TypeError as e:
            letzter = e
        except Exception as e:
            if "think" in str(e).lower() and "think" in extra:
                letzter = e
                continue
            raise
    raise letzter


def _g(o, k, default=None):
    if o is None:
        return default
    if isinstance(o, dict):
        return o.get(k, default)
    return getattr(o, k, default)


def _tool_calls(resp):
    msg = _g(resp, "message")
    calls = []
    for c in _g(msg, "tool_calls", None) or []:
        fn = _g(c, "function")
        args = _g(fn, "arguments", {}) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append((str(_g(fn, "name", "")), dict(args)))
    if not calls:                                       # manche Vorlagen geben den Aufruf als Text zurück
        for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", _g(msg, "content", "") or "", re.DOTALL):
            try:
                d = json.loads(m.group(1))
                calls.append((str(d.get("name", "")), dict(d.get("arguments", {}) or {})))
            except ValueError:
                pass
    return calls, (_g(msg, "content", "") or "")


def _max_nachrichten():
    return max(2, int(getattr(config, "AGENT_VERLAUF_MAX", 16)))


def _verlauf_holen():
    grenze = time.time() - float(getattr(config, "AGENT_VERLAUF_MIN", 10)) * 60
    with _lock:
        _verlauf[:] = [v for v in _verlauf if v[0] >= grenze][-_max_nachrichten():]
        return [{"role": r, "content": t} for _z, r, t in _verlauf]


def _verlauf_merken(user, antwort):
    with _lock:
        jetzt = time.time()
        _verlauf.append((jetzt, "user", user))
        _verlauf.append((jetzt, "assistant", antwort))
        del _verlauf[:-_max_nachrichten()]
        kopie = list(_verlauf)
    if getattr(config, "GEDAECHTNIS_AKTIV", True):
        gedaechtnis.zug_merken(user, antwort)
        gedaechtnis.speichern(kopie)


def im_gespraech():
    """True, wenn gerade ein Gespräch läuft: Folgefragen werden dann auch ohne 'Jarvis' angenommen."""
    sek = float(getattr(config, "AGENT_FOLGE_S", 75))
    with _lock:
        return bool(_verlauf) and time.time() - _verlauf[-1][0] <= sek


_MERK_RE = re.compile(r"^(?:jarvis[\s,]+)?merk(?:e)?\s+(?:dir|mir)\b[\s,:\-]*(?:dass\s+)?(.+?)[.!?\s]*$", re.I)
_BEHAUPTET = re.compile(r"\b(?:notiert|gemerkt|merke mir|gespeichert|eingeschaltet|ausgeschaltet|angeschaltet|"
                        r"angemacht|ausgemacht|gestellt|eingestellt|aktiviert|deaktiviert|erledigt)\b", re.I)


_VERBEN = {"bin": "ist", "habe": "hat", "hab": "hat", "mag": "mag", "will": "will", "möchte": "möchte", "wohne": "wohnt",
           "arbeite": "arbeitet", "brauche": "braucht", "trinke": "trinkt", "esse": "isst", "liebe": "liebt",
           "hasse": "hasst", "fahre": "fährt", "spiele": "spielt", "nutze": "nutzt", "besitze": "besitzt",
           "heiße": "heißt", "heisse": "heißt", "bevorzuge": "bevorzugt", "benutze": "benutzt", "kann": "kann",
           "darf": "darf", "muss": "muss", "lebe": "lebt", "studiere": "studiert", "schlafe": "schläft"}


def _behauptet_aktion(inhalt):
    t = " ".join(re.sub(r"<think>.*?</think>", " ", inhalt or "", flags=re.S).split())
    return bool(t) and bool(_BEHAUPTET.search(t)) and not t.endswith("?") and len(t) < 200


_LIVE = re.compile(r"\b(?:strom\w*|verbrauch\w*|watt|kilowatt\w*|energie|temperatur\w*|grad|warm|kalt|feucht\w*|fenster|"
                   r"heizung\w*|thermostat\w*|batterie\w*|akku\w*|steckdose\w*|drucker|gr(?:ö|oe)(?:ß|ss)ten verbraucher)\b", re.I)
_FRAGE = re.compile(r"^(?:jarvis[ ,]+)?(?:wie|was|wieviel|welche|welcher|welches|ist|sind|hat|zeig|zeige|sag|sage|lies|lese|gib|nenn|nenne|"
                    r"habe ich|verbraucht|laeuft|l(?:ä|ae)uft)\b", re.I)


def _live_frage(text):
    """Frage nach Messwerten aus dem Haus ('Wie hoch ist der Stromverbrauch?'): darf nie aus der Fantasie des Modells kommen."""
    t = " ".join((text or "").split())
    return bool(_LIVE.search(t)) and bool(_FRAGE.match(t) or t.endswith("?"))


def _ich_zu_name(fakt):
    """'ich bin Vegetarier' / 'ich Vegetarier bin' -> 'Max ist Vegetarier'. Im Zweifel: 'Max sagt: ...' (nie falsch)."""
    n = getattr(config, "USER_NAME", "") or "Der Nutzer"
    f = " ".join(fakt.split()).strip(" .,;!?")
    w = f.split()
    if len(w) >= 2 and w[0].lower() == "ich":
        rest = w[1:]
        if rest[0].lower() in _VERBEN:                                   # "ich bin Vegetarier"
            return " ".join([n, _VERBEN[rest[0].lower()]] + rest[1:])
        if rest[-1].lower() in _VERBEN:                                  # Nebensatz: "ich Vegetarier bin"
            return " ".join([n, _VERBEN[rest[-1].lower()]] + rest[:-1])
    return f"{n} sagt: {f}"


def vergessen():
    with _lock:
        _verlauf.clear()
    gedaechtnis.vergessen()


def _suche(art, anfrage, frage):
    antwort, quellen = wissen.beantworte("web" if art == "WEB" else "obsidian", anfrage, frage, log=_log)
    # Websuche leer (oft ein Aussetzer oder eine ungünstige Modell-Formulierung): einmal mit dem Originalsatz wiederholen
    if art == "WEB" and (not quellen or antwort.startswith(wissen.KEINE_ANTWORT[:20])):
        q2 = re.sub(r"^(?:jarvis[\s,]+)", "", frage, flags=re.I).rstrip("?!. ")
        if q2 and q2.lower() != anfrage.lower():
            _log("[Web]: leer - zweiter Versuch mit dem Originalsatz")
            a2, q2s = wissen.beantworte("web", q2, frage, log=_log)
            if q2s:
                antwort, quellen = a2, q2s
    for q in quellen:
        _log(f"[{'Web' if art == 'WEB' else 'Obsidian'}] {q}")
    return antwort


_VERSENDEN_RE = re.compile(
    r"^(?:jarvis[\s,]+)?(?:(?:bitte\s+)?(?:schick|schicke|sende|verschick|verschicke|maile|mail|simse|whatsapp\w*)\b"
    r"|(?:schreib|schreibe)\s+.{0,40}?\b(?:e-?mail|mail|nachricht|sms|whatsapp)\b)", re.I)
VERSENDEN_ANTWORT = ("Nachrichten, E-Mails oder Dateien kann ich nicht verschicken, das ist nicht eingebaut. "
                     "Ich kann dir aber eine Notiz in Obsidian anlegen, die du selbst weitergibst.")


_THEMA_RE = re.compile(
    r"^(?:jarvis[\s,]+)?(?:was (?:kannst|könntest|koenntest) du mir (?:alles )?(?:über|ueber|zu) (?P<a>.+?) (?:sagen|erzählen|erzaehlen)"
    r"|was (?:weißt|weisst) du (?:alles )?(?:über|ueber|zu) (?P<b>.+?)"
    r"|(?:erzähl|erzaehl|sag)(?:e)? mir (?:etwas |was |mehr )?(?:über|ueber|zu) (?P<c>.+?)"
    r"|was ist über (?P<d>.+?) bekannt)[\s.!?]*$", re.I)
_PERSOENLICH = {"mich", "mir", "dich", "dir", "uns", "euch", "ihn", "sie", "es", "dich selbst", "dein", "dein leben"}


def _notiz_zuerst(text):
    """Allgemeine Themenfragen ("Was kannst du mir über die Sonne sagen?") zuerst in den eigenen Notizen nachschlagen.
    Gibt die Antwort zurück oder None (dann arbeitet der normale Weg weiter, z.B. mit Websuche)."""
    m = _THEMA_RE.match(text.strip())
    if not m or not getattr(config, "AGENT_NOTIZEN_ZUERST", True):
        return None
    thema = next((g for g in m.groups() if g), "").strip()
    thema = re.sub(r"^(?:die|der|das|den|dem|unser\w*|mein\w*)\s+", "", thema, flags=re.I).strip()
    if not thema or thema.lower() in _PERSOENLICH or len(thema) > 80:
        return None
    try:
        if not wissen.search_obsidian(thema, 1):
            return None
        antwort, quellen = wissen.beantworte("obsidian", thema, text, log=_log)
    except Exception:
        return None
    if not antwort or antwort.startswith(("Dazu steht nichts", "In Obsidian habe ich nichts", "Bei der Suche")):
        return None
    for q in quellen:
        _log(f"[Obsidian] {q}")
    _log(f"[Agent]: Antwort aus deinen Notizen zu '{thema}'")
    return antwort


def antworte(text, rueckfrage_cb=None):
    """Haupteingang. Gibt den Sprechtext zurück. rueckfrage_cb(text) wird aufgerufen, wenn eine Ja/Nein-Rückfrage offen ist."""
    global _pending
    text = text.strip()
    if not text:
        return ""
    # 0) "Merk dir, dass ..." fest verdrahtet - Qwen soll das nicht dem Zufall überlassen
    m = _MERK_RE.match(text)
    if m:
        fakt = _ich_zu_name(m.group(1))
        try:
            fakt_merken(fakt)
            antwort = "Okay, das merke ich mir."
            _log(f"[Agent]: Profil ergänzt: {fakt}")
        except notizen.NotizFehler as e:
            antwort = str(e)
        _verlauf_merken(text, antwort)
        return antwort
    # 0a) Verschicken ist nicht eingebaut: ehrlich sagen, nicht vom Modell erklären lassen
    if _VERSENDEN_RE.match(text):
        _verlauf_merken(text, VERSENDEN_ANTWORT)
        return VERSENDEN_ANTWORT
    # 0b) Themenfrage ("Was kannst du mir über X sagen?"): erst in den eigenen Notizen nachsehen
    antwort = _notiz_zuerst(text)
    if antwort:
        _verlauf_merken(text, antwort)
        return antwort
    # 1) Aktuelle Fakten: direkt im Web nachschlagen (spart den Umweg über das Modell und vermeidet veraltete Antworten)
    if getattr(config, "AGENT_WEB_AUTO", True) and AKTUELL_RE.search(text):
        _log("[Agent]: Aktuelle Frage -> Websuche")
        antwort = _suche("WEB", text.rstrip("?!. "), text)
        _verlauf_merken(text, antwort)
        return antwort

    messages = [{"role": "system", "content": _system_prompt()}] + _verlauf_holen() + [{"role": "user", "content": text}]
    try:
        resp = _chat(messages, TOOLS)
        calls, inhalt = _tool_calls(resp)
        if not calls and _behauptet_aktion(inhalt):
            # Qwen sagt "erledigt", ohne etwas getan zu haben -> einmal mit Nachdruck erneut versuchen
            _log("[Agent]: Aktion nur behauptet - zweiter Versuch mit Werkzeugzwang")
            messages = messages + [{"role": "assistant", "content": inhalt}, {"role": "user", "content":
                "(Hinweis des Systems: Du hast nichts ausgeführt. Rufe jetzt das passende Werkzeug auf. Meine Antwort auf "
                "deine Rückfrage gehört zu meiner ursprünglichen Bitte.)"}]
            resp = _chat(messages, TOOLS)
            calls, inhalt = _tool_calls(resp)
    except Exception as e:
        _log(f"[Agent Fehler]: {e.__class__.__name__}: {e}")
        return "Mein Sprachmodell antwortet gerade nicht."

    if calls:
        teile = []
        for name, args in calls[:3]:
            _log(f"[Agent]: Werkzeug {name} {json.dumps(args, ensure_ascii=False)}")
            try:
                antwort, rueck = _ausfuehren(name, args)
            except notizen.NotizFehler as e:
                antwort, rueck = str(e), None
            except Exception as e:
                _log(f"[Agent Fehler]: {name}: {e.__class__.__name__}: {e}")
                antwort, rueck = "Das hat leider nicht geklappt.", None
            if rueck:
                with _lock:
                    _pending = rueck
                teile.append(rueck["text"])
                break
            if antwort.startswith(("WEB:", "OBS:")):
                antwort = _suche(antwort[:3], antwort[4:], text)
            if antwort:
                teile.append(antwort)
        sprech = " ".join(teile) or "Erledigt."
    else:
        sprech = wissen.cut(wissen.clean_text(re.sub(r"<think>.*?</think>", " ", inhalt, flags=re.DOTALL).replace("\n", " ")), 600)
        if _live_frage(text) and not re.search(r"\?\s*$", sprech):
            _log(f"[Agent]: Live-Antwort ohne Messung verworfen: {sprech}")
            sprech = "Eine Haussteuerung ist nicht eingebaut, darum habe ich keine Messwerte aus dem Haus."
        elif not sprech:
            sprech = "Da bin ich überfragt."
        elif _BEHAUPTET.search(sprech) and not re.search(r"\?\s*$", sprech) and len(sprech) < 200:
            # Qwen behauptet eine Aktion, hat aber kein Werkzeug benutzt -> nicht glauben, nicht vorlesen
            _log(f"[Agent]: Behauptung ohne Aktion verworfen: {sprech}")
            sprech = "Das habe ich nicht ausgeführt. Sag es bitte noch einmal anders."
    _verlauf_merken(text, sprech)
    return sprech
