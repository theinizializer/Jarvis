"""
utils_module.py — Utility globali di JARVIS
============================================
Contiene tutto ciò che era in jarvis_v9.py ma NON appartiene a un modulo
già esistente:

  - _load_dotenv()              → carica .env
  - _ensure_pkg()               → auto-installazione pacchetti
  - detect_system_lang()        → rileva lingua OS
  - WakeWordSession             → gestione sessione wake word
  - CleanupManager              → chiusura pulita, segnali UNIX
  - _scan_pactl()               → scansione microfoni PulseAudio
  - ensure_ollama()             → avvio/verifica Ollama
  - _pin_ollama_cores()         → pinning CPU
  - run_cmd() / run_sudo_cmd()  → esecuzione comandi shell
  - _extract_errors()           → filtra output errori
  - _is_informative()           → decide se mandare tutto l'output al modello
  - _log_software_error()       → logger errori su file
  - Costanti / regex globali    → MAX_HISTORY, OLLAMA_URL, ecc.

IMPORTANTE: jarvis_v9.py importa tutto da qui con:
    from utils_module import (
        _load_dotenv, _ensure_pkg, detect_system_lang,
        WakeWordSession, CleanupManager, _scan_pactl,
        ensure_ollama, _pin_ollama_cores,
        run_cmd, run_sudo_cmd, _extract_errors, _is_informative,
        _log_software_error,
        _levenshtein, _contains_wake_word,
        MAX_HISTORY, OLLAMA_URL, OLLAMA_TAGS,
        DEFAULT_MODEL, VISION_MODEL,
        GROQ_URL, GROQ_MODEL, GROQ_FALLBACK, GROQ_API_KEY,
        CEREBRAS_URL, CEREBRAS_MODEL, CEREBRAS_API_KEY,
        NVIDIA_URL, NVIDIA_MODEL, NVIDIA_API_KEY,
        HEAVY_TASK_KEYWORDS, _XDG_NAMES, _SYSTEM_LANG,
        WAKE_SESSION_TIMEOUT, WAKE_WORD_CORE,
        CONFIRM_SILENCE_TIMEOUT, _RE_WAKE, _WAKE_VARIANTS,
        _RE_JSON_TOOL, _RE_ECHO_DBL, _RE_ECHO_SGL, _RE_ECHO_RAW,
        _RE_CD, _RE_RM, _RE_SUDO, _RE_INLINE_FN, _RE_JSON_FULL,
        _RE_JSON_CMD, _RE_BASH_BLK, _RE_HOME_ERR, _RE_JSON_SEARCH,
        _RE_SUDO_MSG, _MIC_BLACKLIST, _MIC_WHITELIST,
        _CPU_THREADS, DISCORD_TOKEN, DISCORD_OK,
        _cleanup,
    )
"""

import atexit
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

# ══════════════════════════════════════════════════════════════════════════════
# ─── .env loader ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _load_dotenv():
    """Carica .env automaticamente dalla cartella dello script o da percorsi noti."""
    candidates = [
        Path(__file__).parent / ".env",
        Path.home() / "Documenti" / "modelli" / ".env",
        Path.home() / "Documents" / "modelli" / ".env",
        Path.home() / "jarvis_memory" / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip(); val = val.strip()
                # Rimuove virgolette circostanti (CHIAVE="valore" o CHIAVE='valore')
                # — errore di formato molto comune che altrimenti rompe le API key.
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("\"", "'"):
                    val = val[1:-1].strip()
                if key and val and key not in os.environ:
                    os.environ[key] = val
            break


_load_dotenv()


# ══════════════════════════════════════════════════════════════════════════════
# ─── Pacchetti auto-installazione ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_pkg(pip_name: str, import_name: str | None = None) -> bool:
    """Installa pip_name se non disponibile; ritorna True se ok."""
    name = import_name or pip_name
    try:
        __import__(name)
        return True
    except ImportError:
        print(f"📦 Installo {pip_name}...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_name, "-q", "--break-system-packages"],
            capture_output=True
        )
        try:
            __import__(name)
            return True
        except ImportError:
            return False


# ══════════════════════════════════════════════════════════════════════════════
# ─── Rilevamento lingua sistema ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_XDG_NAMES = {
    "it": {"desktop": "Scrivania",   "downloads": "Scaricati",       "documents": "Documenti",
           "pictures": "Immagini",   "music":     "Musica",          "videos": "Video"},
    "fr": {"desktop": "Bureau",      "downloads": "Téléchargements", "documents": "Documents",
           "pictures": "Images",     "music":     "Musique",         "videos": "Vidéos"},
    "de": {"desktop": "Schreibtisch","downloads": "Downloads",       "documents": "Dokumente",
           "pictures": "Bilder",     "music":     "Musik",           "videos": "Videos"},
    "en": {"desktop": "Desktop",     "downloads": "Downloads",       "documents": "Documents",
           "pictures": "Pictures",   "music":     "Music",           "videos": "Videos"},
}

def detect_system_lang() -> str:
    """Rileva la lingua del sistema operativo dalla variabile LANG/LANGUAGE."""
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val:
            code_raw = val.split(".")[0].split("_")[0].lower()
            if code_raw in _XDG_NAMES:
                return code_raw
    for path in ("/etc/locale.conf", "/etc/default/locale"):
        try:
            content = Path(path).read_text()
            m = re.search(r'LANG=([a-zA-Z]+)', content)
            if m:
                code_raw = m.group(1).lower()
                if code_raw in _XDG_NAMES:
                    return code_raw
        except Exception:
            pass
    return "en"

_SYSTEM_LANG = detect_system_lang()


# ══════════════════════════════════════════════════════════════════════════════
# ─── Costanti globali ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

MAX_HISTORY   = 4   # ridotto da 8 → taglia ~50% dello storico inviato ad ogni chiamata

# ─── Sorgenti configurabili via .env (PC = localhost, Pi = IP del PC) ─────────
# Stesso codice su PC e Raspberry: cambia solo OLLAMA_HOST nel .env.
#   PC:   OLLAMA_HOST=localhost          (default)
#   Pi:   OLLAMA_HOST=192.168.1.50       (IP del PC sulla rete locale)
OLLAMA_HOST   = os.environ.get("OLLAMA_HOST", "localhost").strip()
OLLAMA_PORT   = os.environ.get("OLLAMA_PORT", "11434").strip()
OLLAMA_BASE   = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
OLLAMA_URL    = f"{OLLAMA_BASE}/api/chat"
OLLAMA_TAGS   = f"{OLLAMA_BASE}/api/tags"
DEFAULT_MODEL = "jarvisQwen"
VISION_MODEL  = "llama3.2-vision:11b-instruct-q4_K_M"

# ─── Groq ─────────────────────────────────────────────────────────────────────
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL    = "openai/gpt-oss-120b"
GROQ_FALLBACK = "llama-3.3-70b-versatile"
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")

# ─── Cerebras ─────────────────────────────────────────────────────────────────
CEREBRAS_URL     = "https://api.cerebras.ai/v1/chat/completions"
CEREBRAS_MODEL   = "gpt-oss-120b"   # llama-3.3-70b deprecato da Cerebras (404)
# Chiave letta SOLO dall'ambiente/.env — mai hardcoded (era una falla di sicurezza).
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")

# ─── NVIDIA NIM ───────────────────────────────────────────────────────────────
NVIDIA_URL     = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL   = "qwen/qwen3.5-397b-a17b"
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

# Task pesanti → NVIDIA
HEAVY_TASK_KEYWORDS = re.compile(
    r'\b(debug|debugga|refactor|refactoring|ottimizza|analizza\s+.{0,20}codice|'
    r'architettura|progetta|spiega\s+il\s+codice|review|revisiona|'
    r'screenshot|cattura\s+schermo|vedi\s+lo\s+schermo|cosa\s+vedi|'
    r'clicca|premi\s+il\s+pulsante|apri\s+il\s+sito|naviga|'
    r'coordinate|dove\s+si\s+trova\s+il\s+pulsante|'
    r'ragionamento|reasoning|pensa\s+a\s+fondo|analisi\s+approfondita)\b',
    re.IGNORECASE
)

# ─── Discord ──────────────────────────────────────────────────────────────────
_DISCORD_TOKEN_FALLBACK = ""
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", _DISCORD_TOKEN_FALLBACK)

try:
    import discord
    from discord.ext import commands as dc_commands
    DISCORD_OK = True
except Exception:
    DISCORD_OK = False

# ─── CPU ──────────────────────────────────────────────────────────────────────
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ':0'

_CPU_THREADS = os.cpu_count() or 4


# ══════════════════════════════════════════════════════════════════════════════
# ─── Wake Word ────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

WAKE_SESSION_TIMEOUT    = 120
WAKE_WORD_CORE          = "jarvis"
CONFIRM_SILENCE_TIMEOUT = 12

_RE_WAKE = re.compile(
    r'\b'
    r'(?:j|g|y|dj|zh)?'
    r'[aeiou]?'
    r'(?:ar|er|ir|a|e)?'
    r'(?:r)?'
    r'(?:v|b|w|f)?'
    r'[aeiou]?'
    r'(?:s|z|c|ss|x)'
    r'(?:es?|is?|ez?)?'
    r'\b',
    re.IGNORECASE
)

_WAKE_VARIANTS = {
    "jarvis", "jervis", "gervis", "garvis", "jarwis", "jarviz",
    "javis", "jarvin", "jarvix", "giarvis", "djarvis", "harvis",
    "yarvis", "zarvis", "sarvis", "parvis", "carvis",
    "arvis", "jarvi", "jerbi", "gerbi", "jarvid",
    "iarviss", "iarviz", "giarviz",
    "heyjarvis", "eijarvis",
}

def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    la, lb = len(a), len(b)
    if abs(la - lb) > 4:
        return abs(la - lb)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j]    + 1,
                prev[j]    + (0 if ca == cb else 1)
            ))
        prev = curr
    return prev[lb]

def _contains_wake_word(text: str) -> bool:
    if not text:
        return False
    clean = re.sub(r"[^\w\s]", "", text.lower())
    tokens = clean.split()
    for token in tokens:
        if token in _WAKE_VARIANTS:
            return True
        if len(token) >= 4 and _levenshtein(token, WAKE_WORD_CORE) <= 2:
            return True
    for m in _RE_WAKE.finditer(clean):
        candidate = m.group(0)
        if len(candidate) >= 4 and _levenshtein(candidate, WAKE_WORD_CORE) <= 3:
            return True
    return False


class WakeWordSession:
    """Gestisce la sessione attiva dopo il wake word, con timeout automatico."""

    def __init__(self, timeout: float = WAKE_SESSION_TIMEOUT):
        self.timeout    = timeout
        self._active    = False
        self._last_time = 0.0
        self._lock      = threading.Lock()

    def is_active(self) -> bool:
        with self._lock:
            if not self._active:
                return False
            if time.time() - self._last_time >= self.timeout:
                self._active = False
                return False
            return True

    def activate(self):
        with self._lock:
            self._active    = True
            self._last_time = time.time()

    def touch(self):
        with self._lock:
            if self._active:
                self._last_time = time.time()

    def deactivate(self):
        with self._lock:
            self._active = False

    def seconds_left(self) -> float:
        with self._lock:
            if not self._active:
                return 0.0
            return max(0.0, self.timeout - (time.time() - self._last_time))


# ══════════════════════════════════════════════════════════════════════════════
# ─── CleanupManager ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class CleanupManager:
    """
    Gestisce la chiusura pulita di JARVIS.
    Si registra su tutti i segnali UNIX e su atexit.
    Killa Ollama completamente e ferma tutti i thread.
    """

    def __init__(self):
        self._done      = False
        self._lock      = threading.Lock()
        self._callbacks: list = []

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(sig, self._signal_handler)
            except (OSError, ValueError):
                pass

        atexit.register(self.cleanup)

    def register(self, callback):
        self._callbacks.append(callback)

    def _signal_handler(self, signum, frame):
        sig_names = {
            signal.SIGTERM: "SIGTERM",
            signal.SIGINT:  "SIGINT (Ctrl+C)",
            signal.SIGHUP:  "SIGHUP",
        }
        print(f"\n\n🛑 Segnale ricevuto: {sig_names.get(signum, signum)}")
        self.cleanup()
        os._exit(0)

    def cleanup(self):
        with self._lock:
            if self._done:
                return
            self._done = True

        print("\n" + "=" * 52)
        print("🧹 JARVIS — Chiusura in corso...")
        print("=" * 52)

        for cb in self._callbacks:
            try:
                cb()
            except (KeyboardInterrupt, SystemExit):
                pass
            except Exception as e:
                print(f"   ⚠️ Callback cleanup: {e}")

        self._kill_ollama()
        print("ok Cleanup completato. Ciao!")
        print("=" * 52 + "\n")

    def _kill_ollama(self):
        """Killa il processo Ollama completamente, liberando tutta la RAM."""
        print("🔴 Chiusura Ollama...")

        try:
            r = subprocess.run(
                ["sudo", "systemctl", "stop", "ollama"],
                capture_output=True, timeout=8
            )
            if r.returncode == 0:
                print("   ok Ollama fermato via systemctl")
                time.sleep(1)
                return
        except Exception:
            pass

        try:
            subprocess.run(["ollama", "stop"], capture_output=True, timeout=5)
        except Exception:
            pass

        try:
            r = subprocess.run(
                ["pkill", "-TERM", "-f", "ollama"],
                capture_output=True, timeout=5
            )
            time.sleep(2)
            check = subprocess.run(["pgrep", "-f", "ollama"], capture_output=True)
            if check.returncode != 0:
                print("   ok Ollama terminato via pkill TERM")
                return
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["pgrep", "-f", "ollama"], capture_output=True, text=True
            )
            pids = result.stdout.strip().split()
            if pids:
                for pid in pids:
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True)
                    except Exception:
                        pass
                print(f"   ok Ollama killato (PIDs: {', '.join(pids)})")
            else:
                print("   ℹ️  Ollama non era in esecuzione")
        except Exception as e:
            print(f"   ⚠️  Impossibile killare Ollama: {e}")


# Istanza globale — importata e usata da jarvis_v9.py
_cleanup = CleanupManager()


# ══════════════════════════════════════════════════════════════════════════════
# ─── Microfono — scansione PulseAudio ─────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_MIC_BLACKLIST = re.compile(
    r'hdmi|displayport|\.monitor|monitor$|virtual|'
    r'loopback|null|dummy|spdif|iec958|'
    r'digital.output|output.only',
    re.IGNORECASE
)
_MIC_WHITELIST = re.compile(
    r'mic|microphone|input|capture|headset|cuffie|analog|'
    r'bluetooth|bluez|hsp|hfp|a2dp.*source|handsfree|usb.*audio',
    re.IGNORECASE
)

def _scan_pactl() -> list[dict]:
    """Scansiona le sorgenti audio PulseAudio e ritorna una lista di microfoni."""
    mics = []
    try:
        out = subprocess.check_output(
            ['pactl', 'list', 'sources'], text=True, stderr=subprocess.DEVNULL
        )
        blocks = re.split(r'\nSource #', out)
        for block in blocks:
            name_m  = re.search(r'Name:\s*(.+)',        block)
            desc_m  = re.search(r'Description:\s*(.+)', block)
            state_m = re.search(r'State:\s*(\w+)',      block)
            rate_m  = re.search(r'(\d+) Hz',            block)
            if not name_m:
                continue
            name  = name_m.group(1).strip()
            desc  = desc_m.group(1).strip() if desc_m else name
            state = state_m.group(1).strip() if state_m else "SUSPENDED"
            rate  = int(rate_m.group(1)) if rate_m else 48000
            if '.monitor' in name:
                continue
            if re.search(r'output.only|sink$', name, re.I):
                continue
            is_bt = 'bluez' in name.lower()
            bt_a2dp_warning = (
                is_bt and
                'input' not in name.lower() and
                'hsp' not in name.lower() and
                'hfp' not in name.lower()
            )
            mics.append({
                'index':      name,
                'name':       desc,
                'pa_name':    name,
                'channels':   2,
                'samplerate': rate,
                'state':      state,
                'bt':         is_bt,
                'bt_a2dp':    bt_a2dp_warning,
                'source':     'pactl',
            })
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ pactl: {e}")
    return mics


# ══════════════════════════════════════════════════════════════════════════════
# ─── Ollama — avvio e pinning CPU ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _set_ollama_env():
    os.environ.setdefault("OLLAMA_NUM_THREADS", "11")
    os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
    os.environ.setdefault("OLLAMA_NUM_PARALLEL", "1")


def ensure_ollama() -> bool:
    """Verifica che Ollama sia in esecuzione; lo avvia se necessario.

    Se OLLAMA_HOST non è locale (es. il Pi punta al PC), NON tenta di avviarlo
    localmente — verifica solo che sia raggiungibile in rete.
    """
    _is_local = OLLAMA_HOST in ("localhost", "127.0.0.1", "::1", "")

    # Verifica raggiungibilità (vale sia locale sia remoto)
    for attempt in range(2):
        try:
            requests.get(OLLAMA_BASE, timeout=2)
            print(f"ok Ollama attivo ({OLLAMA_BASE})")
            if _is_local:
                _pin_ollama_cores()
            return True
        except Exception:
            if attempt == 0:
                print("⚠️ Ollama non risponde, secondo tentativo...")

    # Se è remoto e non risponde, non possiamo avviarlo noi — avvisa e basta.
    if not _is_local:
        print(f"❌ Ollama remoto non raggiungibile su {OLLAMA_BASE}")
        print("   Verifica che Ollama sia attivo sul PC e che la rete sia ok.")
        return False

    _set_ollama_env()
    print("⚠️ Avvio Ollama...")
    try:
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       capture_output=True, timeout=10, check=False)
        time.sleep(3)
        requests.get(OLLAMA_BASE, timeout=2)
        print("ok Ollama avviato via systemctl")
        _pin_ollama_cores()
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["taskset", "-c", "2-11", "ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("⏳ Attendo Ollama", end="", flush=True)
        for i in range(20):
            time.sleep(1)
            print(".", end="", flush=True)
            try:
                requests.get(OLLAMA_BASE, timeout=1)
                print(f" ok ({i+1}s)")
                return True
            except Exception:
                pass
        print(" ❌")
    except FileNotFoundError:
        print("❌ Ollama non installato")
    return False


def _pin_ollama_cores():
    """Pinna Ollama ai core 1-5 e JARVIS al core 0."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ollama"],
            capture_output=True, text=True, timeout=3
        )
        pids = result.stdout.strip().splitlines()
        for pid in pids:
            pid = pid.strip()
            if pid:
                subprocess.run(
                    ["taskset", "-cp", "2-11", pid],
                    capture_output=True, timeout=3, check=False
                )
        if pids:
            print(f"  CPU: Ollama pinned → core 1-5 (thread 2-11) [{len(pids)} processi]")
    except (FileNotFoundError, Exception):
        pass

    try:
        my_pid = str(os.getpid())
        subprocess.run(
            ["taskset", "-cp", "0-1", my_pid],
            capture_output=True, timeout=3, check=False
        )
        print("  CPU: JARVIS pinned → core 0 (thread 0-1)")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# ─── Esecuzione comandi shell ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_RE_SUDO_MSG = re.compile(r'\[sudo\] password for [^:]+:\s*')

# Comandi che producono output lungo e inutile — tutto il resto è informativo
_NOISY_CMDS = re.compile(
    r'^\s*('
    r'apt(-get)?\s+(install|remove|purge|update|upgrade|dist-upgrade|autoremove)|'
    r'apt\s+update|apt\s+upgrade|'
    r'dpkg\s+-i|dpkg\s+--install|'
    r'pip\s+install|pip3\s+install|pip\s+uninstall|'
    r'npm\s+install|npm\s+ci|npm\s+update|'
    r'yarn\s+add|yarn\s+install|'
    r'cargo\s+install|cargo\s+build|'
    r'gem\s+install|'
    r'snap\s+install|snap\s+remove|'
    r'flatpak\s+install|flatpak\s+update|'
    r'make(\s+-|\s|$)|cmake(\s|$)|gcc\s+-|g\+\+\s+-|ninja(\s|$)|'
    r'wget\s|curl\s.*(http|ftp)|'
    r'cp\s|mv\s|mkdir|touch\s|chmod|chown|ln\s|rsync\s|'
    r'tar\s|zip\s|unzip\s|gzip|gunzip|'
    r'systemctl\s+(start|stop|restart|enable|disable|reload)|'
    r'service\s+\w+\s+(start|stop|restart)|'
    r'apt\s+autoremove|apt\s+autoclean|apt\s+clean|'
    r'rm\s+-\w*r\w*\s|rmdir\s|shred\s|'
    r'docker\s+(build|pull|push|run|stop|rm|rmi|create)|'
    r'docker-compose\s+(up|down|build|pull)|'
    r'git\s+(commit|push|pull|clone|fetch|merge|rebase|reset|checkout|add)|'
    r'sudo\s+reboot|sudo\s+shutdown|init\s+[0-6]'
    r')\b',
    re.IGNORECASE
)

def _is_informative(cmd: str) -> bool:
    """True se l'output del comando va mandato intero al modello."""
    c = re.sub(r'^\s*sudo\s+(-\w+\s+)?', '', cmd.strip())
    return not bool(_NOISY_CMDS.match(c))


def _extract_errors(output: str, context_lines: int = 2) -> str:
    """
    Analizza l'output di un comando e restituisce SOLO le righe di errore
    + N righe di contesto. Se nessun errore ritorna stringa vuota (= successo).
    """
    ERROR_PATTERNS = re.compile(
        r'\b(error|errore|err:|fatal|critical|exception|traceback|'
        r'failed|failure|not found|not installed|no such|'
        r'cannot|can\'t|unable|impossible|denied|permission|'
        r'command not found|no module|importerror|syntaxerror|'
        r'segfault|killed|abort|panic|undefined|unresolved|'
        r'già installato|already installed|'
        r'E: |W: |dpkg:|apt-get:|\[error\]|\[fatal\]|\[critical\])\b',
        re.IGNORECASE
    )
    FALSE_POSITIVE = re.compile(
        r'(Scaricamento|Download|Recuperati|Selezionato|Preparazione|'
        r'Spacchettamento|Configurazione|Elaborazione|Processing|'
        r'ok |✓|successfully|completato|done|finished)',
        re.IGNORECASE
    )

    lines = output.splitlines()
    error_indices = set()
    for i, line in enumerate(lines):
        if ERROR_PATTERNS.search(line) and not FALSE_POSITIVE.search(line):
            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                error_indices.add(j)

    if not error_indices:
        return ""

    result_lines = []
    prev = -1
    for i in sorted(error_indices):
        if prev != -1 and i > prev + 1:
            result_lines.append("  ...")
        result_lines.append(lines[i])
        prev = i
    return "\n".join(result_lines)


def run_cmd(command, cwd, timeout=60):
    """Esegue un comando shell, filtra l'output e lo restituisce."""
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        out = (r.stdout or r.stderr or "").strip()
        if out:
            print(out)

        if r.returncode != 0:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else out[:800]
        elif _is_informative(command):
            model_out = out if out else "ok Completato"
        else:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else "ok Completato"

        return r.returncode, model_out
    except subprocess.TimeoutExpired:
        return -1, f"⏱️ Timeout ({timeout}s)"
    except Exception as e:
        return -1, f"❌ {e}"


def run_sudo_cmd(command, password, cwd, timeout=120):
    """Esegue un comando con sudo, gestisce la password via stdin."""
    try:
        proc = subprocess.Popen(
            f"sudo -S sh -c {shlex.quote(command)}",
            shell=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd,
        )
        try:
            stdout, stderr = proc.communicate(input=f"{password}\n", timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            return -1, f"⏱️ Timeout ({timeout}s)"

        out = _RE_SUDO_MSG.sub('', (stdout or stderr or "")).strip()
        if out:
            print(out)

        if proc.returncode != 0:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else out[:800]
        elif _is_informative(command):
            model_out = out if out else "ok Completato"
        else:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else "ok Completato"

        return proc.returncode, model_out
    except Exception as e:
        return -1, f"❌ sudo: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# ─── Regex precompilate ───────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_RE_JSON_TOOL  = re.compile(
    r'execute_terminal_command|"name"\s*:\s*"execute_|"parameters"\s*:|"command"\s*:',
    re.IGNORECASE
)
_RE_ECHO_DBL   = re.compile(r'echo\s+"([^"]*)"')
_RE_ECHO_SGL   = re.compile(r"echo\s+'([^']*)'")
_RE_ECHO_RAW   = re.compile(r'echo\s+(.+)')
_RE_CD         = re.compile(r'\bcd\s+([^\s;&|]+)')
_RE_RM         = re.compile(r'\brm\b', re.IGNORECASE)
_RE_SUDO       = re.compile(r'^sudo\s+', re.IGNORECASE)
_RE_INLINE_FN  = re.compile(
    r'execute_terminal_command\s*\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
    re.IGNORECASE
)
_RE_JSON_FULL  = re.compile(
    r'\{[^{}]*"name"\s*:\s*"execute_terminal_command"[^{}]*"parameters"\s*:\s*(\{[^{}]*\})[^{}]*\}',
    re.DOTALL
)
_RE_JSON_CMD   = re.compile(
    r'"command"\s*:\s*"((?:[^"\\]|\\.)*)"[^}]*"explanation"\s*:\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL
)
_RE_BASH_BLK   = re.compile(r'```(?:bash|sh)\n(.*?)```', re.DOTALL)
_RE_HOME_ERR   = re.compile(r'/home/user/')
_RE_JSON_SEARCH = re.compile(
    r'\{[^{}]*"name"\s*:\s*"web_search"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}',
    re.DOTALL
)


# ══════════════════════════════════════════════════════════════════════════════
# ─── Error Logger ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _log_software_error(log_dir: Path, context: str, exc: Exception = None, msg: str = ""):
    """
    Scrive un errore software nella log separata jarvis_errors.log.
    Formato: [timestamp] [CONTEXT] messaggio\\n  traceback se disponibile
    """
    import traceback as _tb
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    err_log = log_dir / "jarvis_errors.log"
    lines = [f"[{ts}] [{context}] {msg or (str(exc) if exc else 'errore sconosciuto')}"]
    if exc:
        tb = _tb.format_exc().strip()
        if tb and tb != "NoneType: None":
            for l in tb.splitlines():
                lines.append(f"    {l}")
    lines.append("")
    try:
        with open(err_log, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
