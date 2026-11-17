import os
import time
import asyncio
import threading
import subprocess
import re
import math
import speech_recognition as sr
from playsound import playsound
import config
import matrix_core as hud_server

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import voicemeeterlib   # pip install voicemeeter-api
except ImportError:
    voicemeeterlib = None

recognizer = sr.Recognizer()
recognizer.pause_threshold = float(getattr(config, "VOICE_PAUSE_S", 1.1))   # Sprechpause, nach der ein Satz als beendet gilt
mikrofon_bereit = False

def safe_delete(filepath):
    time.sleep(2.0)
    try:
        if os.path.exists(filepath): os.remove(filepath)
    except:
        pass

async def async_tts_save(text, path):
    if edge_tts:
        await edge_tts.Communicate(text, "de-DE-ConradNeural").save(path)

def jarvis_speak(text):
    print(f"\nJarvis: {text}")
    hud_server.aktueller_status = "AUDIO-AUSGABE"
    hud_server.aktueller_text = text
    temp_audio_path = os.path.join(config.PROJEKT_DIR, f"jarvis_voice_{int(time.time() * 1000)}.mp3")
    try:
        # Absicherung für den Fall, dass bereits ein Event-Loop im Thread läuft
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Falls ein Loop läuft, fügen wir die Task dort ein und warten, bis sie fertig ist
            future = asyncio.run_coroutine_threadsafe(async_tts_save(text, temp_audio_path), loop)
            future.result()
        else:
            asyncio.run(async_tts_save(text, temp_audio_path))

        if os.path.exists(temp_audio_path):
            playsound(temp_audio_path)
            threading.Thread(target=safe_delete, args=(temp_audio_path,), daemon=True).start()
    except Exception as e:
        print(f"[Sprachausgabe Fehler]: {e}")
        
    hud_server.aktueller_status = "ONLINE" # Zurück auf ONLINE statt STANDBY, da die Verbindung steht

def _set_windows_volume_keys(percent):
    """Alte Methode (Fallback): Windows-Lautstärketasten. Wirkt nur auf die Windows-Master-Lautstärke."""
    try:
        klicks = min(max(int(percent / 2), 0), 50)
        powershell_cmd = (
            f"$wsh = New-Object -ComObject WScript.Shell; "
            f"for ($i=0; $i -lt 50; $i++) {{ $wsh.SendKeys([char]174) }}; " 
            f"for ($i=0; $i -lt {klicks}; $i++) {{ $wsh.SendKeys([char]175) }}" 
        )
        subprocess.Popen(["powershell", "-NoProfile", "-Command", powershell_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        pass

def percent_to_db(percent):
    """Rechnet Prozent (0-100) in dB für Voicemeeter um (Bereich -60 bis 0 dB)."""
    percent = max(0, min(100, percent))
    if percent == 0:
        return -60.0
    if str(getattr(config, "VOICEMEETER_CURVE", "log")).lower() == "linear":
        db = -60.0 + 0.6 * percent
    else:
        db = 20 * math.log10(percent / 100)
    return round(max(-60.0, min(0.0, db)), 1)


def set_voicemeeter_volume(percent):
    """Setzt die Lautstärke des in config.py gewählten Voicemeeter-Busses in dB."""
    if voicemeeterlib is None:
        raise RuntimeError("voicemeeter-api nicht installiert (pip install voicemeeter-api)")
    kind = getattr(config, "VOICEMEETER_KIND", "potato")
    bus = getattr(config, "VOICEMEETER_BUS", 0)
    db = percent_to_db(percent)
    with voicemeeterlib.api(kind) as vm:
        vm.bus[bus].gain = db
    print(f"[Voicemeeter]: Bus {bus} -> {db} dB ({percent} %)")
    return db


def set_system_volume(percent):
    """Voicemeeter zuerst; wenn das nicht klappt, Fallback auf Windows-Lautstärketasten."""
    try:
        set_voicemeeter_volume(percent)
    except Exception as e:
        print(f"[Voicemeeter Fehler]: {e} -> nutze Windows-Lautstärketasten")
        _set_windows_volume_keys(percent)


_ONES = {"null": 0, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
         "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
         "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16,
         "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50,
         "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90}


def parse_number(txt):
    """Erste Zahl im Text: Ziffern ('40') oder deutsche Zahlwörter ('vierzig', 'einundfünfzig', 'hundert').
    Gibt None zurück, wenn keine Zahl gefunden wurde."""
    digit = re.search(r"\d+", txt)
    for tok in re.finditer(r"[a-zäöüß]+", txt.lower()):
        w = tok.group()
        val = None
        if w in ("hundert", "einhundert"):
            val = 100
        elif w in _TENS:
            val = _TENS[w]
        elif w in _ONES:
            val = _ONES[w]
        else:
            m = re.fullmatch(r"([a-zäöüß]+?)und([a-zäöüß]+)", w)
            if m and (m.group(1) in _ONES or m.group(1) == "ein") and m.group(2) in _TENS:
                val = (1 if m.group(1) == "ein" else _ONES[m.group(1)]) + _TENS[m.group(2)]
        if val is not None:
            if digit and digit.start() < tok.start():
                break  # eine Ziffer steht vor dem Zahlwort -> die zählt
            return val
    return int(digit.group()) if digit else None


def extract_and_set_volume(txt):
    wert = parse_number(txt)
    if wert is None:
        jarvis_speak("Ich habe keinen Prozentwert verstanden.")
        return
    if 0 <= wert <= 100:
        set_system_volume(wert)
        jarvis_speak(f"Lautstärke auf {wert} Prozent angepasst.")
    else:
        jarvis_speak("Bitte nenne einen Wert zwischen 0 und 100.")


# ============================== Spracherkennung (offline mit Whisper oder online mit Google) ==============================
# STT_ENGINE = "whisper": faster-whisper läuft komplett lokal (pip install faster-whisper).
# Beim allerersten Start wird das Modell einmalig heruntergeladen. Bis es geladen ist - oder falls
# faster-whisper fehlt - nutzt JARVIS (wenn STT_FALLBACK_GOOGLE = True) die Google-Erkennung.
stt_bereit = threading.Event()        # True, sobald das Whisper-Modell geladen ist
_stt_modell = None
_stt_lock = threading.Lock()
_stt_hinweis = set()                  # jede Warnung nur einmal ausgeben
_stt_geraet = "cpu"

# Whisper "erfindet" bei Stille/Rauschen gern Sätze. Diese bekannten Phantomtexte werden verworfen.
_PHANTOM = ("untertitel", "amara.org", "vielen dank fürs zuschauen", "vielen dank für's zuschauen",
            "danke fürs zuschauen", "bis zum nächsten mal", "copyright", "abonniert", "swr",
            "zdf", "www.", "musik", "applaus")


def _einmal(schluessel, text):
    if schluessel not in _stt_hinweis:
        _stt_hinweis.add(schluessel)
        print(text)


STT_AUTO_WOERTER = []                 # vom Server gefüllt: Gerätenamen, Räume ... (siehe hud_server.py)


def _stt_woerter():
    basis = ["Jarvis", "Obsidian", "Voicemeeter", "Anycubic Slicer", "Lautstärke", "Timer", "Wecker",
               "Erinnerung", "Einkaufsliste", "Daily Note", "Notiz", "Qwen", "Düsentemperatur"]
    alle, gesehen = [], set()
    for w in list(STT_AUTO_WOERTER) + list(getattr(config, "STT_EXTRA_WOERTER", [])) + basis:
        if w and w.lower() not in gesehen:
            gesehen.add(w.lower())
            alle.append(w)
    return alle[:70]                      # Whisper kann nur einen kurzen Hinweistext verarbeiten


def _stt_prompt():
    return "Sprachbefehle an Jarvis: " + ", ".join(_stt_woerter()) + "."


def _lade_whisper():
    """Lädt das Modell (im Hintergrund). Fällt bei GPU-Problemen automatisch auf die CPU zurück."""
    global _stt_modell, _stt_geraet
    if str(getattr(config, "STT_ENGINE", "google")).lower() != "whisper":
        return
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except ImportError:
        _einmal("kein_fw", "[STT Warnung]: faster-whisper ist nicht installiert (pip install faster-whisper) "
                           "-> nutze Google-Erkennung.")
        return
    name = getattr(config, "WHISPER_MODEL", "small")
    geraet = getattr(config, "WHISPER_DEVICE", "auto")
    rechnen = getattr(config, "WHISPER_COMPUTE", "auto")
    versuche = [(geraet, rechnen)]
    if geraet != "cpu":
        versuche.append(("cpu", "int8"))
    for g, c in versuche:
        try:
            print(f"[STT]: Lade Whisper-Modell '{name}' ({g}, {c}) - beim ersten Start ist ein Download nötig ...")
            threads = int(getattr(config, "WHISPER_THREADS", 8))      # Standard von faster-whisper wären nur 4 Kerne
            try:
                modell = WhisperModel(name, device=g, compute_type=c, cpu_threads=threads)
            except TypeError:                                         # sehr alte Version ohne cpu_threads
                modell = WhisperModel(name, device=g, compute_type=c)
            # Probelauf: GPU-Fehler (z.B. fehlende cuBLAS-DLL) zeigen sich oft erst beim Erkennen
            segs, _ = modell.transcribe(np.zeros(16000, dtype=np.float32), language="de")
            list(segs)
            with _stt_lock:
                _stt_modell, _stt_geraet = modell, g
            stt_bereit.set()
            print(f"[STT]: Whisper bereit (offline, {g}).")
            return
        except Exception as e:
            print(f"[STT Warnung]: Whisper mit {g}/{c} nicht möglich: {e}")
    print("[STT Fehler]: Whisper konnte nicht geladen werden -> nutze Google-Erkennung.")


def init_stt_async():
    threading.Thread(target=_lade_whisper, daemon=True).start()


def _whisper_text(audio):
    import numpy as np
    roh = audio.get_raw_data(convert_rate=16000, convert_width=2)
    daten = np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0
    if len(daten) < 4000:                      # < 0,25 s
        return ""
    optionen = dict(language="de", beam_size=int(getattr(config, "WHISPER_BEAM", 3)),
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                    condition_on_previous_text=False, initial_prompt=_stt_prompt(), temperature=0.0)
    try:                                  # neuere faster-whisper-Versionen kennen "hotwords" (Namen bevorzugen)
        import inspect
        if "hotwords" in inspect.signature(_stt_modell.transcribe).parameters:
            optionen["hotwords"] = ", ".join(_stt_woerter()[:30])
    except (TypeError, ValueError):
        pass
    segs, _info = _stt_modell.transcribe(daten, **optionen)
    teile = []
    for sg in segs:
        if sg.no_speech_prob > 0.6 or sg.avg_logprob < -1.2:
            continue
        teile.append(sg.text.strip())
    text = " ".join(t for t in teile if t).strip()
    low = text.lower()
    if any(p in low for p in _PHANTOM) and len(text) < 80:
        return ""
    return text


def transcribe(audio):
    """AudioData -> Text. Wirft sr.UnknownValueError, wenn nichts verstanden wurde, und
    sr.RequestError, wenn die Google-Erkennung nicht erreichbar ist."""
    if str(getattr(config, "STT_ENGINE", "google")).lower() == "whisper":
        if stt_bereit.is_set():
            with _stt_lock:
                modell_da = _stt_modell is not None
            if modell_da:
                try:
                    text = _whisper_text(audio)
                except Exception as e:
                    _einmal("w_fehler", f"[STT Fehler]: Whisper-Erkennung fehlgeschlagen ({e}) -> Google.")
                    text = None
                if text is not None:
                    if not text:
                        raise sr.UnknownValueError()
                    return text
        elif not getattr(config, "STT_FALLBACK_GOOGLE", True):
            raise sr.UnknownValueError()      # Modell lädt noch -> nichts erkennen
        else:
            _einmal("w_laedt", "[STT]: Whisper lädt noch - bis dahin Google-Erkennung.")
        if not getattr(config, "STT_FALLBACK_GOOGLE", True):
            raise sr.UnknownValueError()
    return recognizer.recognize_google(audio, language="de-DE")


def _bg_mic_init():
    global mikrofon_bereit
    try:
        print("[Hardware]: Koppele Audio-Schnittstelle im Hintergrund...")
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.2)
        mikrofon_bereit = True
        print("[Hardware]: Mikrofon-Matrix erfolgreich synchronisiert.")
    except Exception as e:
        print(f"[Hardware-Warnung]: Audio-Schnittstelle blockiert ({e}). HUD-Steuerung aktiv.")
        mikrofon_bereit = False

def init_microphone_async():
    threading.Thread(target=_bg_mic_init, daemon=True).start()
