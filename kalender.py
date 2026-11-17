"""
kalender.py - Google-Kalender (nur lesen, über die geheime iCal-Adresse)

Einrichtung: in config.py unter KALENDER_QUELLEN die iCal-Adressen eintragen (Google Kalender -> Einstellungen -> dein Kalender
-> "Geheime Adresse im iCal-Format"). Benötigt:  pip install python-dateutil

Sprachbeispiele:
    "Welche Termine habe ich morgen?"      "Was habe ich am Donnerstag vor?"      "Habe ich heute Termine?"
    "Wann ist mein nächster Termin?"       "Was steht diese Woche im Kalender?"   "Ist morgen Feiertag?"
Von allein: Erinnerung KALENDER_VORWARN_MIN Minuten vor einem Termin; im Morgenbriefing die Termine des Tages.
"""

import os
import re
import json
import time
import threading
import urllib.request
from datetime import datetime, date, timedelta

import config
from wetter import _MONATE
from zeit import uhr_sprech, tag_text

try:
    from dateutil import tz as _tz
    from dateutil.rrule import rrulestr
except ImportError:                                  # pip install python-dateutil
    _tz = None
    rrulestr = None

_lock = threading.RLock()
_quellen = {}          # index -> {"name", "art", "events": [...], "ok": bool, "fehler": str|None, "stand": ts}
_meldungen = {}        # "titel|start" -> True  (schon erinnert)
_ZONE = None


def _c(name, default):
    return getattr(config, name, default)


def aktiv():
    return bool(_c("KALENDER_AKTIV", True)) and bool(_c("KALENDER_QUELLEN", [])) and _tz is not None


def zone():
    global _ZONE
    if _ZONE is None and _tz is not None:
        _ZONE = _tz.gettz(_c("KALENDER_ZEITZONE", "Europe/Berlin")) or _tz.tzlocal()
    return _ZONE


# ============================== iCal lesen ==============================
def _entfalten(text):
    zeilen = []
    for z in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if z[:1] in (" ", "\t") and zeilen:
            zeilen[-1] += z[1:]
        else:
            zeilen.append(z)
    return zeilen


def _unescape(s):
    return s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _vevents(text):
    """Liste von Events als {NAME: [(params, wert), ...]}."""
    out, cur = [], None
    for z in _entfalten(text):
        if z == "BEGIN:VEVENT":
            cur = {}
        elif z == "END:VEVENT":
            if cur is not None:
                out.append(cur)
            cur = None
        elif cur is not None and ":" in z:
            kopf, wert = z.split(":", 1)
            teile = kopf.split(";")
            params = {}
            for p in teile[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.upper()] = v.strip('"')
            cur.setdefault(teile[0].upper(), []).append((params, wert))
    return out


def _eins(ev, name):
    return ev.get(name, [(None, None)])[0]


def _zeit(wert, params):
    """-> (naive lokale Zeit, ganztag, Zeitzone des Events oder None)"""
    wert = wert.strip()
    lokal = zone()
    if (params or {}).get("VALUE") == "DATE" or (len(wert) == 8 and wert.isdigit()):
        return datetime.strptime(wert[:8], "%Y%m%d"), True, None
    if wert.endswith("Z"):
        dt = datetime.strptime(wert[:15], "%Y%m%dT%H%M%S").replace(tzinfo=_tz.tzutc())
        return dt.astimezone(lokal).replace(tzinfo=None), False, _tz.tzutc()
    dt = datetime.strptime(wert[:15], "%Y%m%dT%H%M%S")
    tzid = (params or {}).get("TZID")
    zz = _tz.gettz(tzid) if tzid else None
    if zz is None:
        return dt, False, None                       # schwebende Zeit / unbekannte Zone: als lokale Zeit nehmen
    return dt.replace(tzinfo=zz).astimezone(lokal).replace(tzinfo=None), False, zz


def _dauer(s):
    m = re.match(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", s.strip())
    if not m:
        return timedelta(0)
    w, d, h, mi, se = (int(x or 0) for x in m.groups()[1:])
    td = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=se)
    return -td if m.group(1) == "-" else td


def parse(text):
    """Roh-Events: [{'uid','titel','ort','start','ende','ganztag','zone','rrule','exdate':set,'rec':naive|None,'abgesagt'}]"""
    out = []
    for ev in _vevents(text):
        p, wert = _eins(ev, "DTSTART")
        if not wert:
            continue
        try:
            start, ganz, zz = _zeit(wert, p)
        except ValueError:
            continue
        p2, w2 = _eins(ev, "DTEND")
        ende = None
        if w2:
            try:
                ende = _zeit(w2, p2)[0]
            except ValueError:
                ende = None
        if ende is None:
            _, wd = _eins(ev, "DURATION")
            ende = start + (_dauer(wd) if wd else (timedelta(days=1) if ganz else timedelta(0)))
        exd = set()
        for pp, ww in ev.get("EXDATE", []):
            for teil in ww.split(","):
                try:
                    exd.add(_zeit(teil, pp)[0])
                except ValueError:
                    pass
        rec = None
        pr, wr = _eins(ev, "RECURRENCE-ID")
        if wr:
            try:
                rec = _zeit(wr, pr)[0]
            except ValueError:
                rec = None
        out.append({
            "uid": (_eins(ev, "UID")[1] or "").strip(),
            "titel": _unescape(_eins(ev, "SUMMARY")[1] or "Termin"),
            "ort": _unescape(_eins(ev, "LOCATION")[1] or ""),
            "start": start, "ende": ende, "ganztag": ganz, "zone": zz,
            "dtstart_roh": (p, wert),
            "rrule": (_eins(ev, "RRULE")[1] or "").strip(), "exdate": exd, "rec": rec,
            "abgesagt": (_eins(ev, "STATUS")[1] or "").strip().upper() == "CANCELLED",
        })
    return out


def _vorkommen(e, von, bis):
    """Alle Vorkommen eines Roh-Events, die [von, bis) berühren (naive lokale Zeiten): Liste (start, ende)."""
    dauer = e["ende"] - e["start"]
    if not e["rrule"]:
        return [(e["start"], e["ende"])] if e["start"] < bis and max(e["ende"], e["start"] + timedelta(seconds=1)) > von else []
    # Wiederholung in der Zeitzone des Events auflösen (Sommerzeit bleibt korrekt)
    lokal = zone()
    p, wert = e["dtstart_roh"]
    if e["ganztag"] or e["zone"] is None:
        ez = None
        ds = e["start"]
    else:
        ez = e["zone"]
        ds = e["start"].replace(tzinfo=lokal).astimezone(ez)          # Beginn in der Event-Zone (aware)
    regel = e["rrule"]
    if ez is None:                                                      # UNTIL darf bei naivem Beginn kein Z haben
        regel = re.sub(r"(UNTIL=\d{8}T\d{6})Z", r"\1", regel)
    elif ez is not None:                                                # UNTIL als Datum -> an aware Beginn anpassen
        regel = re.sub(r"UNTIL=(\d{8})(?![T\d])", r"UNTIL=\1T235959Z", regel)
    try:
        rr = rrulestr("RRULE:" + regel, dtstart=ds)
    except (ValueError, TypeError):
        return [(e["start"], e["ende"])] if e["start"] < bis and e["ende"] > von else []
    if ez is None:
        a, b = von - dauer, bis
    else:
        a, b = (von - dauer).replace(tzinfo=lokal).astimezone(ez), bis.replace(tzinfo=lokal).astimezone(ez)
    out = []
    try:
        for s in rr.between(a, b, inc=True):
            sl = s.astimezone(lokal).replace(tzinfo=None) if s.tzinfo else s
            if sl in e["exdate"]:
                continue
            out.append((sl, sl + dauer))
    except Exception:
        pass
    return [(s, en) for s, en in out if s < bis and max(en, s + timedelta(seconds=1)) > von]


# ============================== Abruf ==============================
def _cache_pfad(i):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"kalender_cache_{i}.ics")


def _holen(url):
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Kalender/1.0"})
    with urllib.request.urlopen(req, timeout=float(_c("KALENDER_TIMEOUT_S", 25))) as r:
        return r.read().decode("utf-8", errors="replace")


def aktualisieren(log=print):
    """Lädt alle Kalender neu. Bei Fehlern bleibt der letzte gute Stand erhalten (auch von der Festplatte)."""
    if not aktiv():
        return
    for i, q in enumerate(_c("KALENDER_QUELLEN", [])):
        name, url = q[0], q[1]
        art = q[2] if len(q) > 2 else "termin"
        eintrag = {"name": name, "art": art, "events": [], "ok": False, "fehler": None, "stand": None}
        try:
            text = _holen(url)
            if "BEGIN:VCALENDAR" not in text:
                raise ValueError("keine Kalenderdaten erhalten (Link prüfen)")
            try:
                with open(_cache_pfad(i), "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass
            eintrag.update(events=parse(text), ok=True, stand=time.time())
        except Exception as e:
            eintrag["fehler"] = f"{e.__class__.__name__}"
            alt = None
            with _lock:
                alt = _quellen.get(i)
            if alt and alt["events"]:
                eintrag.update(events=alt["events"], stand=alt["stand"])
            else:
                try:
                    with open(_cache_pfad(i), "r", encoding="utf-8") as f:
                        eintrag.update(events=parse(f.read()), stand=os.path.getmtime(_cache_pfad(i)))
                except (OSError, ValueError):
                    pass
            log(f"[Kalender]: {name} konnte nicht geladen werden ({eintrag['fehler']})" + (" - nutze den letzten Stand." if eintrag["events"] else "."))
        with _lock:
            _quellen[i] = eintrag


def starten(log=print):
    if not aktiv():
        if _c("KALENDER_AKTIV", True) and _c("KALENDER_QUELLEN", []) and _tz is None:
            log("[Kalender]: Das Paket python-dateutil fehlt (pip install python-dateutil) - Kalender aus.")
        return

    def lauf():
        while True:
            try:
                aktualisieren(log)
                with _lock:
                    n = sum(len(q["events"]) for q in _quellen.values())
                log(f"[Kalender]: {len(_quellen)} Kalender geladen ({n} Einträge).")
            except Exception as e:
                log(f"[Kalender Fehler]: {e.__class__.__name__}: {e}")
            time.sleep(max(5, float(_c("KALENDER_ABRUF_MIN", 15))) * 60)
    threading.Thread(target=lauf, daemon=True).start()


def status():
    with _lock:
        qs = dict(_quellen)
    return {"aktiv": aktiv(), "anzahl": len(_c("KALENDER_QUELLEN", [])), "geladen": len(qs),
            "ok": [q["name"] for q in qs.values() if q["ok"]], "fehler": [q["name"] for q in qs.values() if q["fehler"] and not q["ok"]],
            "stand": max([q["stand"] or 0 for q in qs.values()] or [0]), "paket": _tz is not None}


# ============================== Abfragen ==============================
def termine(von, bis):
    """Alle Termine mit Berührung des Zeitraums [von, bis) (date oder datetime). Sortiert; Absagen und überschriebene Serientermine entfernt."""
    v = von if isinstance(von, datetime) else datetime.combine(von, datetime.min.time())
    b = bis if isinstance(bis, datetime) else datetime.combine(bis, datetime.min.time())
    out = []
    with _lock:
        qs = list(_quellen.values())
    for q in qs:
        ueber = {}
        for e in q["events"]:
            if e["rec"] is not None:
                ueber[(e["uid"], e["rec"])] = e
        for e in q["events"]:
            if e["abgesagt"]:
                continue
            for s, en in _vorkommen(e, v, b):
                if e["rec"] is None and (e["uid"], s) in ueber:
                    continue                                            # dieses Vorkommen wurde verschoben/geändert
                out.append({"titel": e["titel"], "ort": e["ort"], "start": s, "ende": en, "ganztag": e["ganztag"],
                            "kalender": q["name"], "art": q["art"]})
    return sorted(out, key=lambda x: (x["start"], x["titel"]))


def _satz_termin(t, jetzt, mit_tag):
    if t["art"] == "feiertag":
        return f"Feiertag: {t['titel']}"
    ort = f" in {t['ort'].split(',')[0]}" if t["ort"] and len(t["ort"]) < 40 else ""
    wann = ""
    if mit_tag:
        wann = tag_text(t["start"], jetzt) + " "
    if t["ganztag"]:
        return f"{wann}ganztägig {t['titel']}{ort}".strip()
    return f"{wann}um {uhr_sprech(t['start'])} {t['titel']}{ort}".strip()


def text_zeitraum(von, bis, label, jetzt=None):
    """Sprechtext für [von, bis) (Datum). label z.B. 'Heute', 'Morgen', 'Am Donnerstag', 'In den nächsten 7 Tagen'."""
    jetzt = jetzt or datetime.now()
    mehrtaegig = (bis - von).days > 1
    alle = termine(von, bis)
    feier = [t for t in alle if t["art"] == "feiertag"]
    echte = [t for t in alle if t["art"] != "feiertag"]
    # Termine, die schon vorbei sind, nicht mehr nennen (ganztägige bleiben)
    vorher = len(echte)
    echte = [t for t in echte if t["ganztag"] or t["ende"] >= jetzt]
    noch = "noch " if len(echte) < vorher else ""
    teile = []
    if echte:
        zeig = [_satz_termin(t, jetzt, mehrtaegig) for t in echte[:6]]
        rest = f" Und {len(echte) - 6} weitere." if len(echte) > 6 else ""
        n = len(echte)
        teile.append(f"{label} hast du {noch}{n} Termin{'e' if n != 1 else ''}: " + "; ".join(zeig) + "." + rest)
    elif vorher:
        teile.append(f"{label} hast du keine Termine mehr im Kalender.")
    else:
        teile.append(f"{label} hast du keine Termine im Kalender.")
    for t in feier:
        teile.append(("Heute" if t["start"].date() == jetzt.date() else tag_text(t["start"], jetzt).capitalize()) + f" ist Feiertag: {t['titel']}.")
    return " ".join(teile)


def hud_liste(n=3, jetzt=None):
    jetzt = jetzt or datetime.now()
    if not aktiv():
        return []
    out = []
    for t in termine(jetzt - timedelta(hours=1), jetzt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=3)):
        if t["art"] == "feiertag" or (not t["ganztag"] and t["ende"] < jetzt):
            continue
        out.append({"titel": t["titel"], "zeit": "ganztägig" if t["ganztag"] else f"{t['start']:%H:%M}",
                    "tage": (t["start"].date() - jetzt.date()).days})
    return out[:n]


def briefing_teil(heute=None):
    heute = heute or date.today()
    if not aktiv() or not status()["geladen"]:
        return ""
    alle = termine(heute, heute + timedelta(days=1))
    jetzt = datetime.now()
    echte = [t for t in alle if t["art"] != "feiertag"]
    # Termine, die heute schon vorbei sind, nicht mehr vorlesen (ganztägige bleiben)
    if heute == jetzt.date():
        echte = [t for t in echte if t["ganztag"] or t["ende"] >= jetzt]
        noch = "noch "
    else:
        noch = ""
    teile = []
    for t in alle:
        if t["art"] == "feiertag":
            teile.append(f"Heute ist Feiertag: {t['titel']}.")
    if echte:
        n = len(echte)
        teile.append(f"Im Kalender {'steht' if n == 1 else 'stehen'} heute {noch}{n} Termin{'e' if n != 1 else ''}: "
                     + "; ".join(_satz_termin(t, jetzt, False) for t in echte[:5]) + ".")
    return " ".join(teile)


# ============================== Erinnerungen ==============================
def meldungen(jetzt=None):
    """Texte für Termine, die in KALENDER_VORWARN_MIN Minuten beginnen (jeder nur einmal)."""
    jetzt = jetzt or datetime.now()
    if not aktiv() or not status()["geladen"]:
        return []
    grenze = max(int(x) for x in (_c("KALENDER_VORWARN_MIN", [30]) or [30]))
    out = []
    for t in termine(jetzt, jetzt + timedelta(minutes=grenze + 1)):
        if t["art"] == "feiertag" or t["ganztag"] or t["start"] < jetzt - timedelta(minutes=1):
            continue
        key = f"{t['titel']}|{t['start'].isoformat()}"
        if key in _meldungen:
            continue
        _meldungen[key] = True
        minuten = int(round((t["start"] - jetzt).total_seconds() / 60))
        ort = f" in {t['ort'].split(',')[0]}" if t["ort"] and len(t["ort"]) < 40 else ""
        out.append(("Gleich beginnt: " if minuten <= 1 else f"In {minuten} Minuten: ") + f"{t['titel']}{ort}.")
    if len(_meldungen) > 500:
        _meldungen.clear()
    return out


# ============================== Sprachbefehle ==============================
_WT = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6}


def _norm(s):
    return s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")


def _zeitraum(n, heute):
    """(von, bis, Label) aus dem Satz oder None."""
    if re.search(r"\buebermorgen\b", n):
        d = heute + timedelta(days=2)
        return d, d + timedelta(days=1), "Übermorgen"
    if re.search(r"\bheute\b", n):
        return heute, heute + timedelta(days=1), "Heute"
    if re.search(r"(?<!guten )\bmorgen\b(?! frueh)", n):
        d = heute + timedelta(days=1)
        return d, d + timedelta(days=1), "Morgen"
    m = re.search(r"\b(?:am|diesen|naechsten|kommenden)\s+(" + "|".join(_WT) + r")\b", n)
    if m:
        d = heute + timedelta(days=(_WT[m.group(1)] - heute.weekday()) % 7 or (7 if "naechst" in n or "kommend" in n else 0))
        return d, d + timedelta(days=1), "Am " + m.group(1).capitalize()
    m = re.search(r"\bnaechsten (\d{1,2}) tage", n)
    if m:
        k = int(m.group(1))
        return heute, heute + timedelta(days=k), f"In den nächsten {k} Tagen"
    if re.search(r"\bnaechste woche\b|\bkommende woche\b", n):
        mo = heute + timedelta(days=7 - heute.weekday())
        return mo, mo + timedelta(days=7), "Nächste Woche"
    if re.search(r"\bdiese woche\b|\bin dieser woche\b", n):
        return heute, heute + timedelta(days=7 - heute.weekday()), "Diese Woche"
    m = re.search(r"\bam (\d{1,2})\.?\s*(" + "|".join(_norm(x) for x in _MONATE) + r"|\d{1,2}\.)", n)
    if m:
        mo = m.group(2)
        mo = int(mo.rstrip(".")) if mo[0].isdigit() else [_norm(x) for x in _MONATE].index(mo) + 1
        try:
            d = date(heute.year, mo, int(m.group(1)))
            d = d if d >= heute else date(heute.year + 1, mo, int(m.group(1)))
        except ValueError:
            return None
        return d, d + timedelta(days=1), f"Am {d.day}. {_MONATE[d.month - 1]}"
    return None


def erkenne(cmd, heute=None):
    """Intent-Dict oder None. Greift nur bei Termin-/Kalenderwörtern mit Zeitangabe (ohne Zeitangabe antwortet termine.py)."""
    if not aktiv():
        return None
    heute = heute or date.today()
    n = " ".join(_norm(cmd).strip(" .!?").split())
    n = re.sub(r"^jarvis[ ,]*", "", n)
    if re.search(r"\bfeiertag", n):
        if re.search(r"\bnaechste[nr]?\b.*feiertag|wann ist .*feiertag", n):
            return {"art": "naechster_feiertag"}
        z = _zeitraum(n, heute)
        if z and re.search(r"\b(?:ist|sind|gibt)\b", n):
            return {"art": "feiertag", "von": z[0], "bis": z[1], "label": z[2]}
        return None
    if re.search(r"\bnaechste[nrs]?\s+(?:termin|kalendereintrag)", n):
        return {"art": "naechster"}
    kalenderwort = re.search(r"\btermine?\b|\bkalender\b|\bvorhaben\b|\bvor\b$|\bhabe ich\b.*\bvor\b|\bsteht\b.*\ban\b", n)
    if not kalenderwort:
        return None
    if re.match(r"^was (?:liegt|steht) (?:heute|morgen)? ?an$", n):                  # das ist das Morgenbriefing
        return None
    z = _zeitraum(n, heute)
    if not z:
        return None
    if not re.search(r"\b(?:termin|termine|kalender|vor|vorhaben|steht|stehen|liegt|gibt|habe|hab)\b", n):
        return None
    return {"art": "zeitraum", "von": z[0], "bis": z[1], "label": z[2]}


def fuehre_aus(it, jetzt=None):
    jetzt = jetzt or datetime.now()
    heute = jetzt.date()
    st = status()
    if not st["geladen"]:
        return "Der Kalender ist noch nicht geladen. Versuch es in einer Minute noch einmal."
    if it["art"] == "naechster":
        for t in termine(jetzt, jetzt + timedelta(days=60)):
            if t["art"] != "feiertag" and (t["ganztag"] or t["ende"] >= jetzt):
                return "Dein nächster Termin: " + _satz_termin(t, jetzt, True) + "."
        return "In den nächsten 60 Tagen steht nichts im Kalender."
    if it["art"] == "naechster_feiertag":
        for t in termine(heute, heute + timedelta(days=400)):
            if t["art"] == "feiertag":
                d = t["start"].date()
                return f"Der nächste Feiertag ist {t['titel']} am {d.day}. {_MONATE[d.month - 1]}, das ist {tag_text(t['start'], jetzt) if (d - heute).days < 7 else 'in ' + str((d - heute).days) + ' Tagen'}."
        return "Einen Feiertag finde ich in den nächsten Monaten nicht. Ist der Feiertage-Kalender eingetragen?"
    if it["art"] == "feiertag":
        fe = [t for t in termine(it["von"], it["bis"]) if t["art"] == "feiertag"]
        if fe:
            return f"{it['label']} ist Feiertag: {fe[0]['titel']}."
        return f"{it['label']} ist kein Feiertag."
    text = text_zeitraum(it["von"], it["bis"], it["label"], jetzt)
    try:                                                         # Geburtstage/Jubiläen im selben Zeitraum dazu
        import termine as tm
        extra = []
        for e in tm.laden():
            d = tm.naechster(e, heute)
            if it["von"] <= d < it["bis"]:
                extra.append(tm.satz(e, (d - heute).days, tm.jahre(e, d)))
        if extra:
            text += " " + " ".join(extra)
    except Exception:
        pass
    return text
