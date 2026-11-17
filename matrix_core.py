import threading

# Globaler Befehlspuffer für den Hauptthread
web_command = None
command_lock = threading.Lock()

# UI-Zustände, die von archiv.py und voice.py befüllt werden
STATIC_TREE = "Koppele Matrix..."
STATIC_GRAPH = {"nodes": [], "links": [], "stats": {}}
graph_version = 0          # wird bei jedem Archiv-Scan erhöht -> das HUD lädt den Graphen nur bei Änderung neu
aktueller_status = "STANDBY"
aktueller_text = "Systeme online. Matrix geladen."
