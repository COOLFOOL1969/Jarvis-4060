import os
import re
import html
import config
import matrix_core as hud_server

SKIP_DIRS = {".obsidian", ".space", ".git", ".trash", "node_modules"}
MAX_GRAPH_KNOTEN = 700          # mehr Knoten kann das HUD flüssig nicht darstellen
MAX_LESEN = 300_000             # Zeichen pro Notiz, die nach [[Links]] durchsucht werden

_WIKI = re.compile(r"\[\[([^\]\|#\^]+)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]")


def _stem(name):
    return os.path.splitext(name)[0].strip().lower()


def _baum_html(ordner, dateien):
    """Ausklappbarer Baum: jeder Ordner ist ein <details>-Element (Klick auf den Ordner klappt ihn auf/zu)."""
    kinder_o, kinder_d = {}, {}
    for oid, name, parent in ordner:
        kinder_o.setdefault(parent, []).append((oid, name))
    for fid, name, parent, _a, art in dateien:
        kinder_d.setdefault(parent, []).append((fid, name, art))

    def anzahl(pid):
        return len(kinder_d.get(pid, [])) + sum(anzahl(o) for o, _n in kinder_o.get(pid, []))

    def render(pid):
        teile = []
        for oid, name in sorted(kinder_o.get(pid, []), key=lambda x: x[1].lower()):
            teile.append(f"<details class='tree-folder' data-p=\"{html.escape(oid, quote=True)}\">"
                         f"<summary class='folder-item'>📁 {html.escape(name)} "
                         f"<span class='tree-cnt'>({anzahl(oid)})</span></summary>"
                         f"<div class='tree-kids'>{render(oid)}</div></details>")
        for fid, name, art in sorted(kinder_d.get(pid, []), key=lambda x: x[1].lower()):
            icon = "📄" if art == "note" else "📕"
            teile.append(f"<div class='file-item tree-file' data-id=\"{html.escape(fid, quote=True)}\">"
                         f"{icon} {html.escape(name)}</div>")
        return "".join(teile)

    return render("")


def scan_obsidian_vault():
    vault = config.OBSIDIAN_VAULT_PATH
    print(f"[Archiv]: Starte Scan auf Pfad: {vault}")
    if not os.path.exists(vault):
        print("[Archiv Fehler]: Pfad existiert nicht!")
        hud_server.STATIC_TREE = "Kein Archiv gefunden."
        return

    vault_name = os.path.basename(vault.rstrip("\\/")) or "Archiv"
    tree_parts = []
    ordner, dateien = [], []          # (id, name, parent_id) / (id, name, parent_id, abs_pfad, art)
    scan_count = 0
    try:
        for root, dirs, files in os.walk(vault):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            rel = os.path.relpath(root, vault)
            tiefe = 0 if rel == "." else rel.count(os.sep) + 1
            rid = "" if rel == "." else rel.replace(os.sep, "/")
            if rel != ".":
                ordner.append((rid, os.path.basename(root), os.path.dirname(rid)))
            for file in sorted(files):
                low = file.lower()
                if not low.endswith((".md", ".pdf")):
                    continue
                scan_count += 1
                fid = f"{rid}/{file}" if rid else file
                dateien.append((fid, file, rid, os.path.join(root, file), "note" if low.endswith(".md") else "pdf"))

        # --- Graph: Notizen, Ordner (Struktur) und [[Wiki-Links]] ---
        stem2id, name2id = {}, {}
        for fid, name, _p, _a, _art in dateien:
            stem2id.setdefault(_stem(name), fid)
            name2id.setdefault(name.lower(), fid)
        kanten_links = set()
        for fid, name, _p, pfad, art in dateien:
            if art != "note":
                continue
            try:
                with open(pfad, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(MAX_LESEN)
            except OSError:
                continue
            for m in _WIKI.finditer(text):
                roh = os.path.basename(m.group(1).strip()).lower()       # [[Name]], [[Name.md]] und [[Name.pdf]]
                ziel = name2id.get(roh) or stem2id.get(roh.removesuffix(".md").removesuffix(".pdf"))
                if ziel and ziel != fid:
                    kanten_links.add(tuple(sorted((fid, ziel))))

        grad = {}
        for a, b in kanten_links:
            grad[a] = grad.get(a, 0) + 1
            grad[b] = grad.get(b, 0) + 1

        knoten = [{"id": "", "name": vault_name, "type": "root", "deg": 0, "grp": ""}]
        for oid, name, _p in ordner:
            knoten.append({"id": oid, "name": name, "type": "folder", "deg": 0, "grp": oid.split("/")[0]})
        notizen = [{"id": fid, "name": _stem(name) and os.path.splitext(name)[0], "type": art,
                    "deg": grad.get(fid, 0), "grp": (p.split("/")[0] if p else "")}
                   for fid, name, p, _a, art in dateien]
        gekappt = 0
        if len(knoten) + len(notizen) > MAX_GRAPH_KNOTEN:
            notizen.sort(key=lambda n: -n["deg"])
            gekappt = len(knoten) + len(notizen) - MAX_GRAPH_KNOTEN
            notizen = notizen[:max(0, MAX_GRAPH_KNOTEN - len(knoten))]
        knoten += notizen
        ids = {k["id"] for k in knoten}

        links = []
        for oid, _n, parent in ordner:
            links.append({"s": parent, "t": oid, "k": "f"})
        for fid, _n, p, _a, _art in dateien:
            if fid in ids:
                links.append({"s": p, "t": fid, "k": "f"})
        for a, b in kanten_links:
            if a in ids and b in ids:
                links.append({"s": a, "t": b, "k": "l"})
        links = [l for l in links if l["s"] in ids and l["t"] in ids]

        hud_server.STATIC_TREE = _baum_html(ordner, dateien) or "Archiv leer."
        hud_server.STATIC_GRAPH = {"nodes": knoten, "links": links,
                                   "stats": {"notizen": len(dateien), "ordner": len(ordner),
                                             "verknuepfungen": len(kanten_links), "ausgeblendet": gekappt}}
        hud_server.graph_version += 1
        print(f"[Archiv]: Scan beendet. {scan_count} Dateien, {len(kanten_links)} Verknüpfungen in Matrix geladen.")
    except Exception as e:
        print(f"[Archiv Kritischer Fehler]: {e}")


def find_pdf_in_vault(search_term):
    if not os.path.exists(config.OBSIDIAN_VAULT_PATH): return None
    words = [w.strip().lower() for w in search_term.split() if len(w.strip()) > 1]
    for root, dirs, files in os.walk(os.path.abspath(config.OBSIDIAN_VAULT_PATH)):
        if ".obsidian" in root: continue
        for file in files:
            if file.lower().endswith(".pdf") and all(w in file.lower() for w in words):
                return os.path.normpath(os.path.join(root, file))
    return None
