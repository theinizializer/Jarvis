"""
utils_module.py — JARVIS v10
Helper centrali condivisi da tutti i moduli.
Regola: nessun altro modulo ridefinisce funzioni già presenti qui.
"""

from __future__ import annotations

import atexit
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Lazy import helper — evita di caricare moduli pesanti al boot
# ---------------------------------------------------------------------------

_lazy_cache: dict = {}

def lazy_import(module_name: str):
    """Importa un modulo solo quando viene effettivamente usato."""
    if module_name not in _lazy_cache:
        import importlib
        _lazy_cache[module_name] = importlib.import_module(module_name)
    return _lazy_cache[module_name]


# ---------------------------------------------------------------------------
# Logging errori software
# ---------------------------------------------------------------------------

def _log_software_error(context: str, exc: BaseException, *, fatal: bool = False) -> None:
    """Logga un errore su stderr con timestamp. Se fatal=True, termina il processo."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] ERRORE in {context}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if fatal:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Rilevamento lingua sistema
# ---------------------------------------------------------------------------

def detect_system_lang() -> str:
    """Rileva la lingua del sistema operativo (es. 'it', 'en', 'fr')."""
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val:
            code = val.split("_")[0].split(".")[0].lower()
            if len(code) == 2:
                return code
    return "en"


# ---------------------------------------------------------------------------
# Gestione pacchetti (installazione silenziosa)
# ---------------------------------------------------------------------------

def _ensure_pkg(pkg: str) -> bool:
    """Verifica che un pacchetto Python sia installato; lo installa se mancante."""
    import importlib
    try:
        importlib.import_module(pkg)
        return True
    except ImportError:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# Wake word
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Distanza di Levenshtein tra due stringhe."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j] + (ca != cb), curr[-1] + 1, prev[j + 1] + 1))
        prev = curr
    return prev[-1]


def _contains_wake_word(text: str, wake_words: list[str], threshold: int = 2) -> bool:
    """Controlla se il testo contiene una wake word (tolleranza errori OCR/STT)."""
    text_lower = text.lower()
    for ww in wake_words:
        ww_lower = ww.lower()
        if ww_lower in text_lower:
            return True
        for word in text_lower.split():
            if _levenshtein(word, ww_lower) <= threshold:
                return True
    return False


class WakeWordSession:
    """Gestisce una sessione di ascolto wake word con timeout."""

    def __init__(self, wake_words: list[str], timeout: float = 30.0):
        self.wake_words = wake_words
        self.timeout = timeout
        self._active = False
        self._timer: Optional[threading.Timer] = None

    def start(self) -> None:
        self._active = True
        self._timer = threading.Timer(self.timeout, self.stop)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        self._active = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    @property
    def active(self) -> bool:
        return self._active


# ---------------------------------------------------------------------------
# Scan microfoni (via pactl / arecord)
# ---------------------------------------------------------------------------

def _scan_pactl() -> list[dict]:
    """Scansiona i dispositivi audio via pactl."""
    devices = []
    try:
        out = subprocess.check_output(
            ["pactl", "list", "sources", "short"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                devices.append({"index": parts[0], "name": parts[1]})
    except Exception:
        pass
    return devices


# ---------------------------------------------------------------------------
# Ollama — unica definizione centralizzata (rimossa da jarvis_v9.py)
# ---------------------------------------------------------------------------

def _set_ollama_env() -> None:
    """Imposta variabili d'ambiente per Ollama se non già presenti."""
    os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
    os.environ.setdefault("OLLAMA_KEEP_ALIVE", "10m")


def _pin_ollama_cores() -> None:
    """Opzionale: limita Ollama a un subset di core per lasciare risorse a JARVIS."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ollama"], capture_output=True, text=True
        )
        pids = result.stdout.strip().splitlines()
        cpu_count = os.cpu_count() or 4
        cores = f"0-{max(0, cpu_count // 2 - 1)}"
        for pid in pids:
            subprocess.run(
                ["taskset", "-cp", cores, pid.strip()],
                capture_output=True,
            )
    except Exception:
        pass


def _restart_ollama() -> bool:
    """Tenta di riavviare il servizio Ollama."""
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(3)
        return True
    except FileNotFoundError:
        return False


def ensure_ollama(
    host: str = "http://127.0.0.1:11434",
    timeout: float = 15.0,
    *,
    pin_cores: bool = False,
) -> bool:
    """
    Verifica che Ollama sia in esecuzione; lo avvia se necessario.
    Unica definizione — NON ridefinire in jarvis_v9.py o altrove.

    Returns:
        True  se Ollama è raggiungibile al termine.
        False se non è stato possibile avviarlo.
    """
    import requests as _req

    _set_ollama_env()

    def _ping() -> bool:
        try:
            r = _req.get(f"{host}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    if _ping():
        if pin_cores:
            _pin_ollama_cores()
        return True

    print("⚙  Avvio Ollama...", flush=True)
    if not _restart_ollama():
        print("✗  Ollama non trovato nel PATH.", file=sys.stderr)
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ping():
            if pin_cores:
                _pin_ollama_cores()
            print("✓  Ollama pronto.", flush=True)
            return True
        time.sleep(0.8)

    print("✗  Ollama non risponde dopo il riavvio.", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Esecuzione comandi di sistema
# ---------------------------------------------------------------------------

def _is_informative(output: str) -> bool:
    """True se l'output contiene contenuto utile (non solo whitespace/errori banali)."""
    if not output or not output.strip():
        return False
    noise = {"", "none", "null", "undefined", "n/a"}
    return output.strip().lower() not in noise


def _extract_errors(stderr: str) -> list[str]:
    """Estrae righe di errore significative dallo stderr."""
    lines = stderr.splitlines()
    return [l for l in lines if re.search(r"error|warning|fatal|exception", l, re.I)]


def run_cmd(
    cmd: str | list[str],
    *,
    timeout: float = 30.0,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> tuple[int, str, str]:
    """
    Esegue un comando di sistema.
    Returns: (returncode, stdout, stderr)
    """
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env={**os.environ, **(env or {})},
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout dopo {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Comando non trovato: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def run_sudo_cmd(cmd: str, password: str, *, timeout: float = 30.0) -> tuple[int, str, str]:
    """Esegue un comando con sudo passando la password via stdin."""
    full = f"echo {shlex.quote(password)} | sudo -S {cmd}"
    try:
        result = subprocess.run(
            full,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# Percorsi XDG standard
# ---------------------------------------------------------------------------

def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def jarvis_config_dir() -> Path:
    d = xdg_config_home() / "jarvis"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jarvis_data_dir() -> Path:
    d = xdg_data_home() / "jarvis"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Atexit / signal handler generico
# ---------------------------------------------------------------------------

_shutdown_callbacks: list = []


def register_shutdown(fn) -> None:
    """Registra una funzione da chiamare allo shutdown (atexit + SIGTERM)."""
    _shutdown_callbacks.append(fn)


def _run_shutdown() -> None:
    for fn in reversed(_shutdown_callbacks):
        try:
            fn()
        except Exception:
            pass


atexit.register(_run_shutdown)

def _sigterm_handler(signum, frame):
    _run_shutdown()
    sys.exit(0)

if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM, _sigterm_handler)


# ---------------------------------------------------------------------------
# Costanti globali — usate da discord_module, email_module, api_module
# ---------------------------------------------------------------------------

MAX_HISTORY = 40
MAX_TOKENS  = 4096
JARVIS_NAME = "JARVIS"


# ---------------------------------------------------------------------------
# _load_dotenv — carica .env dalla cartella del progetto
# ---------------------------------------------------------------------------

def _load_dotenv(extra_paths=None):
    from pathlib import Path as _Path
    import re as _re
    candidates = [
        _Path(__file__).parent / ".env",
        _Path.home() / ".config" / "jarvis" / ".env",
    ]
    if extra_paths:
        candidates = [_Path(p) for p in extra_paths] + candidates
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                line = _re.sub(r"^export\s+", "", line)
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("''")
                if key and key not in __import__("os").environ:
                    __import__("os").environ[key] = val
        except OSError:
            pass
        break


# ---------------------------------------------------------------------------
# CleanupManager — gestione shutdown pulito
# ---------------------------------------------------------------------------

class CleanupManager:
    def __init__(self):
        self._callbacks = []
        self._armed = False
        self.arm()

    def arm(self):
        import atexit as _ae, signal as _sig
        if self._armed:
            return
        self._armed = True
        _ae.register(self.cleanup)
        for s in (_sig.SIGTERM,):
            try:
                _sig.signal(s, self._signal_handler)
            except (OSError, ValueError):
                pass

    def register(self, fn):
        if fn not in self._callbacks:
            self._callbacks.append(fn)

    def _signal_handler(self, signum, frame):
        self.cleanup()
        __import__("sys").exit(0)

    def cleanup(self):
        for fn in reversed(self._callbacks):
            try:
                fn()
            except Exception:
                pass
        self._kill_ollama()

    def _kill_ollama(self):
        import os as _os, subprocess as _sp
        if _os.environ.get("JARVIS_STARTED_OLLAMA") != "1":
            return
        try:
            _sp.run(["pkill", "-f", "ollama serve"], capture_output=True, timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Costanti API e configurazione — lette da .env a runtime
# Aggiunte in v10 per compatibilità con discord_module e api_module
# ---------------------------------------------------------------------------

import os as _os
from pathlib import Path as _Path

def _read_env(key: str, default: str = '') -> str:
    """Legge una chiave da os.environ (già caricato da _load_dotenv)."""
    return _os.environ.get(key, default)

# Chiamata automatica al caricamento del modulo
_load_dotenv()

# Discord
DISCORD_TOKEN      = _read_env('DISCORD_TOKEN', '')

# API esterne
GROQ_URL           = _read_env('GROQ_URL',      'https://api.groq.com/openai/v1/chat/completions')
GROQ_MODEL         = _read_env('GROQ_MODEL',    'llama-3.3-70b-versatile')
GROQ_API_KEY       = _read_env('GROQ_API_KEY',  '')

CEREBRAS_URL       = _read_env('CEREBRAS_URL',  'https://api.cerebras.ai/v1/chat/completions')
CEREBRAS_MODEL     = _read_env('CEREBRAS_MODEL','llama-4-scout-17b-16e-instruct')
CEREBRAS_API_KEY   = _read_env('CEREBRAS_API_KEY', '')

NVIDIA_URL         = _read_env('NVIDIA_URL',    'https://integrate.api.nvidia.com/v1/chat/completions')
NVIDIA_MODEL       = _read_env('NVIDIA_MODEL',  'meta/llama-4-maverick-17b-128e-instruct')
NVIDIA_API_KEY     = _read_env('NVIDIA_API_KEY', '')

OLLAMA_URL         = _read_env('OLLAMA_HOST',   'http://127.0.0.1:11434')

# CPU threads per executor
import os as _os2
_CPU_THREADS       = max(1, (_os2.cpu_count() or 4) - 1)

# Keyword per task pesanti (usate da api_module per routing modello)
HEAVY_TASK_KEYWORDS = [
    'analizza', 'scrivi', 'genera', 'crea', 'programma',
    'debugga', 'spiega', 'confronta', 'riassumi', 'translate',
    'codice', 'script', 'funzione', 'classe', 'modulo',
]

# Regex JSON tool calls inline (usata da api_module._parse_inline_tools)
import re as _re2
_RE_JSON_TOOL = _re2.compile(
    r'<tool_call>\s*({.*?})\s*</tool_call>|```json\s*({.*?})\s*```',
    _re2.DOTALL,
)
