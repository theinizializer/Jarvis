#!/usr/bin/env python3
"""
JARVIS v6.0 — Core Stabile
===========================
Cambiamenti rispetto a v5.5:
- ❌ Rimosso: SearchEngine, detect_search_need, _fetch_web_context
- ❌ Rimosso: newspaper3k, googlesearch-python, auto-install dipendenze ricerca
- ❌ Rimosso: Wikipedia, DuckDuckGo, Open-Meteo, ANSA RSS, Google CSE, GNews
- ✅ Aggiunto: cleanup totale su SIGTERM, SIGINT, SIGHUP, Ctrl+C, 'esci'
- ✅ Aggiunto: Ollama viene killato completamente alla chiusura
- ✅ Aggiunto: tutti i thread vengono fermati ordinatamente
- ✅ Mantenuto: voce/Whisper/TTS, Discord, memoria permanente, terminale
"""

import asyncio
import atexit
import base64
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

try:
    from jarvis_banner import print_banner as _print_jarvis_banner
    BANNER_OK = True
except ImportError:
    BANNER_OK = False

try:
    from search_module import SearchModule
    SEARCH_OK = True
except ImportError:
    SEARCH_OK = False
    print("⚠️  search_module.py non trovato — ricerche disabilitate")


# ─── Voce / STT ──────────────────────────────────────────────────────────────
def _ensure_pkg(pip_name: str, import_name: str | None = None) -> bool:
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

try:
    from faster_whisper import WhisperModel
    WHISPER_OK = True
except ImportError:
    WHISPER_OK = _ensure_pkg("faster-whisper", "faster_whisper")
    if WHISPER_OK:
        from faster_whisper import WhisperModel

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    SD_OK = True
except ImportError:
    ok1 = _ensure_pkg("sounddevice")
    ok2 = _ensure_pkg("soundfile")
    ok3 = _ensure_pkg("numpy")
    SD_OK = ok1 and ok2 and ok3
    if SD_OK:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np

# ─── Env / Display ───────────────────────────────────────────────────────────
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ':0'

_CPU_THREADS = os.cpu_count() or 4

# ─── Dipendenze opzionali ─────────────────────────────────────────────────────
try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "gtts", "-q"], check=False)
    try:
        from gtts import gTTS
        GTTS_OK = True
    except Exception:
        GTTS_OK = False

try:
    from PIL import ImageGrab
    PIL_OK = True
except Exception:
    PIL_OK = False

try:
    import discord
    from discord.ext import commands as dc_commands
    DISCORD_OK = True
except Exception:
    DISCORD_OK = False

_DISCORD_TOKEN_FALLBACK = ""  # metti qui il token come fallback oppure usa env
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", _DISCORD_TOKEN_FALLBACK)

# ─── Costanti ─────────────────────────────────────────────────────────────────
MAX_HISTORY   = 8
OLLAMA_URL    = "http://localhost:11434/api/chat"
OLLAMA_TAGS   = "http://localhost:11434/api/tags"
DEFAULT_MODEL = "jarvisQwen"

# ── Rilevamento lingua sistema ────────────────────────────────────────────
# Mappa nomi cartelle per lingua sistema
_XDG_NAMES = {
    "it": {"desktop": "Scrivania",  "downloads": "Scaricati", "documents": "Documenti",
           "pictures": "Immagini",  "music": "Musica",        "videos": "Video"},
    "fr": {"desktop": "Bureau",     "downloads": "Téléchargements", "documents": "Documents",
           "pictures": "Images",    "music": "Musique",       "videos": "Vidéos"},
    "de": {"desktop": "Schreibtisch","downloads": "Downloads", "documents": "Dokumente",
           "pictures": "Bilder",    "music": "Musik",         "videos": "Videos"},
    "en": {"desktop": "Desktop",    "downloads": "Downloads", "documents": "Documents",
           "pictures": "Pictures",  "music": "Music",         "videos": "Videos"},
}

def detect_system_lang() -> str:
    """Rileva la lingua del sistema operativo dalla variabile LANG/LANGUAGE."""
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val:
            code_raw = val.split(".")[0].split("_")[0].lower()
            if code_raw in _XDG_NAMES:
                return code_raw
    # Fallback: leggi /etc/locale.conf o /etc/default/locale
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
    return "en"  # default inglese se non trovato

_SYSTEM_LANG = detect_system_lang()

VISION_MODEL  = "llama3.2-vision:11b-instruct-q4_K_M"

# ─── Wake word ────────────────────────────────────────────────────────────────
WAKE_SESSION_TIMEOUT    = 120
WAKE_WORD_CORE          = "jarvis"
CONFIRM_SILENCE_TIMEOUT = 12

_active_voice_input: 'VoiceInput | None' = None
_voice_mode_active: bool = False

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


# ─── Stato sessione vocale ────────────────────────────────────────────────────
class WakeWordSession:
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
# ─── CLEANUP MANAGER ─────────────────────────────────────────────────────────
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

        # Registra su tutti i segnali possibili
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(sig, self._signal_handler)
            except (OSError, ValueError):
                pass  # alcuni segnali non disponibili su certi OS

        # Registra su atexit (copre anche crash e sys.exit)
        atexit.register(self.cleanup)

    def register(self, callback):
        """Registra una funzione da chiamare durante il cleanup."""
        self._callbacks.append(callback)

    def _signal_handler(self, signum, frame):
        sig_names = {
            signal.SIGTERM: "SIGTERM",
            signal.SIGINT:  "SIGINT (Ctrl+C)",
            signal.SIGHUP:  "SIGHUP",
        }
        print(f"\n\n🛑 Segnale ricevuto: {sig_names.get(signum, signum)}")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        with self._lock:
            if self._done:
                return
            self._done = True

        print("\n" + "=" * 52)
        print("🧹 JARVIS — Chiusura in corso...")
        print("=" * 52)

        # Esegui tutti i callback registrati (TTS worker, Discord, ecc.)
        for cb in self._callbacks:
            try:
                cb()
            except Exception as e:
                print(f"   ⚠️ Callback cleanup: {e}")

        # Killa Ollama completamente
        self._kill_ollama()

        print("✅ Cleanup completato. Ciao!")
        print("=" * 52 + "\n")

    def _kill_ollama(self):
        """Killa il processo Ollama completamente, liberando tutta la RAM."""
        print("🔴 Chiusura Ollama...")

        # Metodo 1: systemctl stop (se installato come servizio)
        try:
            r = subprocess.run(
                ["sudo", "systemctl", "stop", "ollama"],
                capture_output=True, timeout=8
            )
            if r.returncode == 0:
                print("   ✅ Ollama fermato via systemctl")
                time.sleep(1)
                return
        except Exception:
            pass

        # Metodo 2: ollama stop (ferma il server)
        try:
            subprocess.run(
                ["ollama", "stop"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

        # Metodo 3: pkill diretto sul processo
        try:
            r = subprocess.run(
                ["pkill", "-TERM", "-f", "ollama"],
                capture_output=True, timeout=5
            )
            time.sleep(2)
            # Verifica se ancora in esecuzione
            check = subprocess.run(
                ["pgrep", "-f", "ollama"],
                capture_output=True
            )
            if check.returncode != 0:
                print("   ✅ Ollama terminato via pkill TERM")
                return
        except Exception:
            pass

        # Metodo 4: kill -9 (forza)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "ollama"],
                capture_output=True, text=True
            )
            pids = result.stdout.strip().split()
            if pids:
                for pid in pids:
                    try:
                        subprocess.run(["kill", "-9", pid], capture_output=True)
                    except Exception:
                        pass
                print(f"   ✅ Ollama killato (PIDs: {', '.join(pids)})")
            else:
                print("   ℹ️  Ollama non era in esecuzione")
        except Exception as e:
            print(f"   ⚠️  Impossibile killare Ollama: {e}")


# Istanza globale del cleanup manager
_cleanup = CleanupManager()


# ─── Microfono — scansione e selezione ───────────────────────────────────────
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



from language_module import LanguageManager, ALL_LANGUAGES
try:
    from agent_module import JarvisAgent, should_use_agent
    AGENT_OK = True
except ImportError:
    AGENT_OK = False
from voice_module import (
    scan_microphones, choose_microphone, find_tts_player,
    VoiceInput, TTSEngine, SpeakerVerifier, VoiceModule,
    is_wake_word, is_sleep_word, strip_wake_word,
    WHISPER_OK, SD_OK, GTTS_OK, RESEMBLYZER_OK
)

def ensure_ollama() -> bool:
    for attempt in range(2):
        try:
            requests.get("http://localhost:11434", timeout=2)
            print("✅ Ollama attivo")
            return True
        except Exception:
            if attempt == 0:
                print("⚠️ Ollama non risponde, secondo tentativo...")
    print("⚠️ Avvio Ollama...")
    try:
        subprocess.run(["sudo", "systemctl", "start", "ollama"],
                       capture_output=True, timeout=10, check=False)
        time.sleep(3)
        requests.get("http://localhost:11434", timeout=2)
        print("✅ Ollama avviato via systemctl")
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("⏳ Attendo Ollama", end="", flush=True)
        for i in range(20):
            time.sleep(1)
            print(".", end="", flush=True)
            try:
                requests.get("http://localhost:11434", timeout=1)
                print(f" ✅ ({i+1}s)")
                return True
            except Exception:
                pass
        print(" ❌")
    except FileNotFoundError:
        print("❌ Ollama non installato")
    return False




_RE_SUDO_MSG = re.compile(r'\[sudo\] password for [^:]+:\s*')

def _extract_errors(output: str, context_lines: int = 2) -> str:
    """
    Analizza l'output di un comando e restituisce SOLO le righe
    che contengono errori + N righe di contesto prima e dopo.
    Se non trova errori restituisce stringa vuota (= successo).
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
    # Righe da ignorare anche se contengono parole chiave (falsi positivi)
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
        return ""  # nessun errore = successo

    result_lines = []
    prev = -1
    for i in sorted(error_indices):
        if prev != -1 and i > prev + 1:
            result_lines.append("  ...")
        result_lines.append(lines[i])
        prev = i
    return "\n".join(result_lines)


# Comandi che generano log lunghe e inutili — tutto il resto è informativo
# Principio: se non è in questa lista, l'output VA mandato al modello intero
_NOISY_CMDS = re.compile(
    r'^\s*('
    # Package managers — log di download/install enormi
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
    # Compilazione — output lunghissimo e inutile
    r'make(\s+-|\s|$)|cmake(\s|$)|gcc\s+-|g\+\+\s+-|ninja(\s|$)|'
    # Download
    r'wget\s|curl\s.*(http|ftp)|'
    # Operazioni su file/directory — nessun output utile
    r'cp\s|mv\s|mkdir|touch\s|chmod|chown|ln\s|rsync\s|'
    r'tar\s|zip\s|unzip\s|gzip|gunzip|'
    # Servizi — start/stop/restart non danno info, status sì
    r'systemctl\s+(start|stop|restart|enable|disable|reload)|'
    r'service\s+\w+\s+(start|stop|restart)|'
    # Pulizia sistema
    r'apt\s+autoremove|apt\s+autoclean|apt\s+clean|'
    r'rm\s+-\w*r\w*\s|rmdir\s|shred\s|'
    # Docker operativo (build/pull/run/stop — non ps/images)
    r'docker\s+(build|pull|push|run|stop|rm|rmi|create)|'
    r'docker-compose\s+(up|down|build|pull)|'
    # Git operativo (commit/push/pull/clone — non log/status/diff)
    r'git\s+(commit|push|pull|clone|fetch|merge|rebase|reset|checkout|add)|'
    # Altro
    r'sudo\s+reboot|sudo\s+shutdown|init\s+[0-6]'
    r')',
    re.IGNORECASE
)

def _is_informative(cmd: str) -> bool:
    """
    Ritorna True se l'output del comando va mandato intero al modello.
    Logica inversa: tutto è informativo TRANNE i comandi nella lista _NOISY_CMDS.
    """
    # Rimuovi sudo dal confronto
    c = re.sub(r'^\s*sudo\s+(-\w+\s+)?', '', cmd.strip())
    return not bool(_NOISY_CMDS.match(c))


def run_cmd(command, cwd, timeout=60):
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        out = (r.stdout or r.stderr or "").strip()
        # Stampa TUTTO a schermo senza limiti
        if out:
            print(out)

        if r.returncode != 0:
            # Errore: manda solo le righe di errore filtrate
            filtered = _extract_errors(out)
            model_out = filtered if filtered else out[:800]
        elif _is_informative(command):
            # Comando informativo: manda tutto al modello (è quello che ha chiesto)
            model_out = out if out else "✅ Completato"
        else:
            # Comando operativo ok: filtra warning, se nessun errore conferma successo
            filtered = _extract_errors(out)
            if filtered:
                model_out = filtered  # ci sono warning, passali
            else:
                model_out = "✅ Completato"  # tutto ok, il modello sa che può continuare
        return r.returncode, model_out
    except subprocess.TimeoutExpired:
        return -1, f"⏱️ Timeout ({timeout}s)"
    except Exception as e:
        return -1, f"❌ {e}"


def run_sudo_cmd(command, password, cwd, timeout=120):
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
        # Stampa TUTTO a schermo senza limiti
        if out:
            print(out)

        if proc.returncode != 0:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else out[:800]
        elif _is_informative(command):
            model_out = out if out else "✅ Completato"
        else:
            filtered = _extract_errors(out)
            model_out = filtered if filtered else "✅ Completato"
        return proc.returncode, model_out
    except Exception as e:
        return -1, f"❌ sudo: {e}"


# ─── Regex precompilate ───────────────────────────────────────────────────────
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


# ─── JARVIS ───────────────────────────────────────────────────────────────────
class Jarvis:
    BLOCKED = ['mkfs', 'dd if=', ':(){:|:&};:', '> /dev/sd']
    NONSENSE_CMDS = [
        'history', 'who discovered', 'who invented', 'who found',
        'who created', 'whois ', 'cat /home', 'grep -A',
    ]
    # Comandi macOS che non esistono su Linux
    MACOS_CMDS = re.compile(r"^open\s+(https?://\S+)", re.IGNORECASE)
    SAFE_PFX = [
        'ls', 'cat ', 'pwd', 'whoami', 'date', 'df', 'free', 'ps',
        'top', 'htop', 'uname', 'uptime', 'echo ', 'find ', 'grep ',
        'apt ', 'systemctl ', 'ping ', 'curl ', 'mkdir ', 'wc ',
    ]

    def __init__(self, model=DEFAULT_MODEL, vision_model=VISION_MODEL,
                 sudo_password=None, enable_tts=False, enable_discord=False,
                 memory_dir=str(Path.home() / "jarvis_memory"),
                 enable_search=True,
                 searxng_url="http://localhost:8080",
                 brave_api_key="",
                 gnews_key="",
                 tts_lang="it",
                 system_lang=None):
        self.model      = model
        # vmodel = vision model separato, solo se diverso dal principale
        self.vmodel = model  # unico modello
        self.tts_lang   = tts_lang
        self.sys_lang   = system_lang or _SYSTEM_LANG
        self._lang      = None  # impostato da main() dopo init
        self._agent     = None  # impostato dopo init se AGENT_OK
        self.sudo_pass  = sudo_password
        self.tts_on     = enable_tts and GTTS_OK
        self.disc_on    = enable_discord and DISCORD_OK and bool(DISCORD_TOKEN)
        self.mem_dir    = Path(memory_dir).expanduser().resolve()
        self.mem_dir.mkdir(exist_ok=True)
        self.cwd        = Path.home()
        os.chdir(self.cwd)
        self.permanent  = self._load_json(self.mem_dir / "permanent.json")
        self._history:  list[dict] = []
        self._pending:  list[dict] = []
        self._stats     = {"calls": 0, "cmds": 0, "denied": 0, "errors": 0}
        self._tts_player = find_tts_player()
        self._tts_q:    queue.Queue | None = None
        self._tts_thread: threading.Thread | None = None
        self._disc_bot  = None
        self._cooldowns: dict[int, float] = {}
        self._lock        = threading.Lock()
        self._executed_cmds: set[str] = set()
        self._search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        self.search = None
        if enable_search and SEARCH_OK:
            try:
                self.search = SearchModule(
                    ollama_url=OLLAMA_URL,
                    ollama_model=self.model,
                    searxng_url=searxng_url,
                    brave_api_key=brave_api_key,
                    gnews_key=gnews_key,
                    tavily_key=os.environ.get("TAVILY_API_KEY", ""),
                )
            except Exception as e:
                print(f"⚠️  SearchModule non inizializzato: {e}")

        if self.tts_on:
            if not self._tts_player:
                print("⚠️ Nessun player audio. Installa: sudo apt install mpg123")
                self.tts_on = False
            else:
                self._tts_q = queue.Queue(maxsize=8)
                self._tts_thread = threading.Thread(
                    target=self._tts_worker, daemon=True, name="tts"
                )
                self._tts_thread.start()

        if self.disc_on:
            self._init_discord()

        # Registra il cleanup di JARVIS nel cleanup manager globale
        _cleanup.register(self._shutdown)

        self._print_banner()

    def _shutdown(self):
        """Ferma ordinatamente tutti i componenti di JARVIS."""
        print("   🔇 Fermo TTS worker...")
        if self._tts_q is not None:
            try:
                self._tts_q.put_nowait(None)  # segnale di stop
            except queue.Full:
                pass

        print("   💾 Salvo memoria...")
        try:
            self._save_json(self.mem_dir / "permanent.json", self.permanent)
        except Exception:
            pass

        print("   💙 Fermo Discord...")
        if self._disc_bot:
            try:
                asyncio.run(self._disc_bot.close())
            except Exception:
                pass

    # ── Persistenza ──────────────────────────────────────────────────────────
    def _load_json(self, path):
        if path.exists():
            try:
                return json.loads(path.read_text('utf-8'))
            except Exception:
                pass
        return []

    def _save_json(self, path, data):
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
        except Exception as e:
            print(f"⚠️ save: {e}")

    # ── Directory helpers ─────────────────────────────────────────────────────
    def _xdg(self, key: str) -> str:
        """Ritorna il path XDG corretto per la lingua del sistema."""
        names_map = _XDG_NAMES.get(self.sys_lang, _XDG_NAMES["en"])
        # Prova prima il nome nella lingua del sistema, poi inglese come fallback
        candidates = [names_map.get(key, key), _XDG_NAMES["en"].get(key, key)]
        for name in candidates:
            p = Path.home() / name
            if p.exists():
                return str(p)
        return str(Path.home() / candidates[0])

    def _xdg_dirs(self):
        return [
            self._xdg("desktop"),
            self._xdg("downloads"),
            self._xdg("documents"),
            self._xdg("pictures"),
            self._xdg("music"),
            self._xdg("videos"),
            str(Path.home()),
        ]

    # ── Prompt dinamico ───────────────────────────────────────────────────────
    def _sys_prompt(self) -> str:
        mem = ""
        if self.permanent:
            facts = [m.get('fact', '') for m in self.permanent[-5:] if m.get('fact')]
            if facts:
                mem = " | Memoria: " + " ; ".join(facts)

        # Usa istruzione lingua dal language module se disponibile
        if self._lang:
            lang_instruction = self._lang.system_instruction
        else:
            lang_instruction = {
                "it": "RISPONDI SEMPRE E SOLO IN ITALIANO. Mai in cinese, inglese o altre lingue.",
                "fr": "RÉPONDS TOUJOURS ET UNIQUEMENT EN FRANÇAIS. Jamais en chinois ou autre langue.",
                "en": "ALWAYS RESPOND IN ENGLISH ONLY. Never in Chinese or other languages.",
            }.get(self.tts_lang, "ALWAYS RESPOND IN ENGLISH ONLY.")

        user = os.getenv('USER', 'user')
        return (
            f"You are JARVIS, personal AI assistant of {user}. "
            f"Your name is JARVIS. User's name is {user}. | "
            f"{lang_instruction} | "
            f"Dir: {self.cwd} | User: {user} | "
            f"Ora: {datetime.now().strftime('%H:%M')} | "
            f"Desktop={self._xdg('desktop')} | "
            f"Downloads={self._xdg('downloads')} | "
            f"Documents={self._xdg('documents')}"
            f"{mem}"
        )

    # ── Tool definition ───────────────────────────────────────────────────────
    def _list_models(self) -> list:
        """Ritorna lista modelli disponibili in Ollama."""
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=5)
            return r.json().get("models", [])
        except Exception:
            return []

    def _tools(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "execute_terminal_command",
                "description": (
                    "Execute a Linux terminal command. "
                    "Use for: files, folders, programs, installations, configurations. "
                    f"Desktop={self._xdg('desktop')} "
                    f"Downloads={self._xdg('downloads')} "
                    f"Documents={self._xdg('documents')}. "
                    "ABSOLUTE PATHS for rm/mv/cp. CASE SENSITIVE."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command":     {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["command", "explanation"],
                }
            }
        }]

        # web_search sempre disponibile se search module presente
        if self.search:
            tools.append({
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "Search the internet for any information. "
                        "Use for: flights, hotels, prices, weather, news, restaurants, "
                        "products, events, people, places — anything that needs current data. "
                        "No restrictions on what you can search. "
                        "Always search instead of guessing or making up information."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query — precise and specific"
                            },
                            "explanation": {
                                "type": "string",
                                "description": "Why you are searching"
                            },
                        },
                        "required": ["query", "explanation"],
                    }
                }
            })

        return tools

    # ── Chiamata modello ──────────────────────────────────────────────────────
    def _call_model(self, user_msg, with_image=False, history=None, web_context=""):
        self._stats["calls"] += 1
        user_entry = {"role": "user", "content": user_msg}
        # Usa vision solo se il vmodel è diverso dal modello principale
        use_vision = with_image and PIL_OK and self.vmodel != self.model
        if use_vision:
            img = self._capture_screen()
            if img:
                user_entry["images"] = [img]
            else:
                use_vision = False

        hist     = history if history is not None else self._history
        sys_content = self._sys_prompt()
        if web_context:
            sys_content += (
                "\n\n[RISULTATI RICERCA WEB — usa questi dati per rispondere:]\n"
                + web_context[:2500] +
                "\n[Fine risultati. Rispondi basandoti su questi dati reali. NON inventare prezzi o link.]"
            )
        messages = [
            {"role": "system", "content": sys_content},
            *hist,
            user_entry
        ]
        tools = self._tools()

        payload  = {
            "model":    self.vmodel if use_vision else self.model,
            "messages": messages,
            "stream":   True,
            "tools":    tools,
            "options":  {
                "num_ctx":     8192,
                "num_predict": 1024,
                "temperature": 0.3,
                "num_thread":  _CPU_THREADS,
            }
        }

        full_text, tool_calls = "", []
        tts_buf, json_buf, in_json = "", "", False
        try:
            with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = data.get('message', {})
                    if 'tool_calls' in msg:
                        tool_calls.extend(msg['tool_calls'])
                    chunk = msg.get('content', '')
                    if chunk:
                        full_text += chunk
                        if _RE_JSON_TOOL.search(chunk):
                            in_json = True
                        if in_json:
                            json_buf += chunk
                            if json_buf.count('{') > 0 and json_buf.count('{') <= json_buf.count('}'):
                                in_json = False
                        else:
                            print(chunk, end='', flush=True)
                            tts_buf += chunk
                            if any(c in chunk for c in ('.', '!', '?', '\n')):
                                self._tts_say(tts_buf.strip())
                                tts_buf = ""
                    if data.get('done'):
                        break

            if tts_buf.strip():
                self._tts_say(tts_buf.strip())
            print()

            if history is None:
                with self._lock:
                    self._history.append({"role": "user", "content": user_msg})
                    clean = self._clean_for_history(full_text)
                    if clean:
                        self._history.append({"role": "assistant", "content": clean})
                    if len(self._history) > MAX_HISTORY * 2:
                        self._history = self._history[-(MAX_HISTORY * 2):]

            return full_text, tool_calls

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response else "?"
            if code == 404:
                print(f"\n⚠️ Modello non in memoria (404) — ricarico '{self.model}'...", flush=True)
                try:
                    subprocess.Popen(
                        ["ollama", "run", self.model],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    print("⏳ Attendo che il modello si carichi (15s)...", flush=True)
                    time.sleep(15)
                    with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp2:
                        resp2.raise_for_status()
                        for line in resp2.iter_lines():
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                            except Exception:
                                continue
                            msg = data.get("message", {})
                            if "tool_calls" in msg:
                                tool_calls.extend(msg["tool_calls"])
                            chunk = msg.get("content", "")
                            if chunk:
                                full_text += chunk
                                print(chunk, end="", flush=True)
                            if data.get("done"):
                                break
                        print()
                    return full_text or "✅ Modello ricaricato, riprova.", tool_calls
                except Exception as e2:
                    print(f"\n❌ Impossibile ricaricare il modello: {e2}")
                    return f"❌ Modello non disponibile. Esegui: ollama run {self.model}", []
            print(f"\n⚠️ Ollama HTTP {code} — riprovo tra 3s...", flush=True)
            time.sleep(3)
            try:
                with requests.post(OLLAMA_URL, json=payload, timeout=300, stream=True) as resp2:
                    resp2.raise_for_status()
                    for line in resp2.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            full_text += chunk
                            print(chunk, end="", flush=True)
                        if data.get("done"):
                            break
                    print()
            except Exception as e2:
                print(f"\n❌ Retry fallito: {e2}")
            return full_text or f"❌ {e}", tool_calls
        except requests.exceptions.Timeout:
            self._stats["errors"] += 1
            err = "⏱️ Timeout Ollama"
            print(f"\n{err}")
            if self._stats["errors"] >= 3:
                self._restart_ollama()
            return err, []
        except requests.exceptions.ConnectionError:
            self._stats["errors"] += 1
            err = "❌ Ollama non risponde"
            print(f"\n{err}")
            if self._stats["errors"] >= 3:
                self._restart_ollama()
            return err, []
        except Exception as e:
            print(f"\n❌ {e}")
            return f"❌ {e}", []

    # ── Parse inline tools ────────────────────────────────────────────────────
    def _parse_inline_tools(self, text):
        found, seen = [], set()
        for m in _RE_INLINE_FN.finditer(text):
            cmd, expl = m.group(1).strip(), m.group(2).strip()
            if cmd and cmd not in seen:
                seen.add(cmd); found.append({'command': cmd, 'explanation': expl})
        for m in _RE_JSON_FULL.finditer(text):
            try:
                p   = json.loads(m.group(1))
                cmd = p.get('command', '').strip()
                if cmd and cmd not in seen:
                    seen.add(cmd); found.append({'command': cmd, 'explanation': p.get('explanation', '')})
            except Exception:
                pass
        for m in _RE_JSON_CMD.finditer(text):
            try:
                cmd  = m.group(1).replace('\\"', '"').replace("\\'", "'").strip()
                expl = m.group(2).replace('\\"', '"').strip()
                if cmd and cmd not in seen and 'execute_terminal' not in cmd:
                    seen.add(cmd); found.append({'command': cmd, 'explanation': expl})
            except Exception:
                pass
        for m in _RE_BASH_BLK.finditer(text):
            for line in m.group(1).strip().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and line not in seen:
                    seen.add(line); found.append({'command': line, 'explanation': 'blocco bash'})
        return found

    def _parse_inline_searches(self, text) -> list[dict]:
        """Cattura web_search scritti come JSON inline dal modello."""
        found, seen = [], set()
        for m in _RE_JSON_SEARCH.finditer(text):
            try:
                args  = json.loads(m.group(1))
                query = args.get('query', '').strip()
                expl  = args.get('explanation', '')
                if query and query not in seen:
                    seen.add(query)
                    found.append({'query': query, 'explanation': expl})
            except Exception:
                pass
        return found

    def _queue(self, command, explanation, kind="cmd"):
        self._pending.append({"command": command, "explanation": explanation, "type": kind})

    def _flush(self, history=None, user_msg="") -> list[dict]:
        """
        Esegue i comandi in coda.
        Ritorna lista di {"cmd": ..., "output": ..., "status": ...}
        per permettere al chiamante di elaborare i risultati.
        """
        if not self._pending:
            return []
        sep = "─" * 50
        print(f"\n{sep}\n⚙️ Esecuzione {len(self._pending)} comando/i\n{sep}")

        errors = []
        results_summary = []
        all_results = []

        for item in self._pending:
            if item.get('type') == 'search':
                result = self._execute_search(item['command'], item['explanation'])
            else:
                result = self._execute(item['command'], item['explanation'], history=history)
            out    = result.get("output", "")
            status = result.get("status", "")
            results_summary.append(f"CMD: {item['command']}\nRISULTATO: {out}")
            all_results.append({"cmd": item['command'], "output": out, "status": status})
            if status == "error":
                errors.append({"cmd": item['command'], "output": out})

        self._pending.clear()

        # Se ci sono errori, chiedi al modello di analizzarli e continuare
        # out contiene GIA' solo le righe di errore filtrate (non tutta la log)
        if errors and user_msg:
            err_text = "\n".join(
                f"- `{e['cmd']}` →\n{e['output']}" for e in errors if e['output']
            )
            if not err_text:
                err_text = "\n".join(f"- `{e['cmd']}` → errore generico (rc!=0)" for e in errors)
            followup = (
                f"Task originale: '{user_msg}'\n"
                f"\nErrori rilevati (solo righe significative):\n{err_text}"
                f"\n\nAnalizza la causa, trova la soluzione e continua autonomamente "
                f"con i comandi corretti. Esegui direttamente senza chiedere conferma."
            )
            print(f"\n🔄 Analisi errori e continuazione automatica...", flush=True)
            full_text, tool_calls = self._call_model(followup, history=history)

            # Esegui i nuovi comandi suggeriti dal modello
            new_pending = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                if fn.get("name") == "execute_terminal_command":
                    args = fn.get("arguments", {})
                    cmd  = args.get("command", "").strip()
                    if cmd:
                        new_pending.append({"command": cmd, "explanation": args.get("explanation", ""), "type": "cmd"})
                elif fn.get("name") == "web_search":
                    args  = fn.get("arguments", {})
                    query = args.get("query", "").strip()
                    if query:
                        new_pending.append({"command": query, "explanation": args.get("explanation", ""), "type": "search"})
            if not tool_calls:
                for c in self._parse_inline_tools(full_text):
                    new_pending.append(c)

            if new_pending:
                # Pulisci il dedup per i comandi di correzione
                self._executed_cmds.clear()
                print(f"\n{sep}\n⚙️ Correzione: {len(new_pending)} comando/i\n{sep}")
                for item in new_pending:
                    r2 = self._execute(item['command'], item['explanation'], history=history)
                    all_results.append({
                        "cmd":    item['command'],
                        "output": r2.get("output", ""),
                        "status": r2.get("status", "")
                    })

        return all_results

    def _execute_search(self, query: str, explanation: str) -> dict:
        """Esegue una web_search richiesta dal modello."""
        # Messaggi nella lingua TTS scelta
        _msgs = {
            "it": ("Cerco:",     "Trovato",  "Nessun risultato per:",  "Errore ricerca:"),
            "fr": ("Recherche:", "Trouvé",   "Aucun résultat pour:",   "Erreur recherche:"),
            "en": ("Searching:", "Found",    "No results for:",        "Search error:"),
        }
        m_search, m_found, m_none, m_err = _msgs.get(self.tts_lang, _msgs["en"])

        print()
        print(f"  🌐 {explanation}")
        print(f"  🔍 {m_search} {query}")
        if not self.search:
            return {"status": "error", "output": "Search module non disponibile"}
        try:
            # Usa Tavily direttamente se disponibile
            if self.search._tavily.available:
                raw = self.search._tavily.search(query, num=5)
            else:
                raw = self.search._ddg.search(query)

            if raw:
                parts = []
                for r in raw:
                    title   = r.get("title", "")
                    snippet = r.get("content", "")
                    url     = r.get("url", "")
                    if snippet:
                        line = f"- {title}: {snippet[:400]}"
                        if url:
                            line += f" ({url})"
                        parts.append(line)
                result = "\n".join(parts)
                if len(result) > 3000:
                    result = result[:3000] + "\n...(troncato)"
                print(f"  ✅ {m_found} ({len(result)} caratteri)")
                return {"status": "ok", "output": result}
            else:
                print(f"  ⚠️  {m_none} {query}")
                return {"status": "ok", "output": f"{m_none} {query}"}
        except Exception as e:
            print(f"  ❌ {m_err} {e}")
            return {"status": "error", "output": f"{m_err} {e}"}

    def _execute(self, command, explanation, history=None):
        cmd  = command.strip()
        hist = history if history is not None else self._history

        _DEDUP_EXEMPT = ('echo ', 'ls', 'date', 'free', 'df', 'ps', 'uptime')
        if not any(cmd.startswith(e) for e in _DEDUP_EXEMPT):
            if cmd in self._executed_cmds:
                msg = f"⚠️ Comando già eseguito, ignorato: {cmd[:60]}"
                print(msg)
                return {"status": "skipped", "output": msg}

        # Converti "open https://..." (macOS) in xdg-open (Linux)
        m_open = self.MACOS_CMDS.match(cmd)
        if m_open:
            cmd = f"xdg-open {m_open.group(1)}"
            print(f"  🔄 Convertito in: {cmd}")

        if _RE_HOME_ERR.search(cmd):
            user = os.getenv('USER', 'user')
            _path_err = {
                "it": f"❌ PATH ERRATO! Usa ~ o /home/{user}/",
                "fr": f"❌ CHEMIN INCORRECT ! Utilisez ~ ou /home/{user}/",
                "en": f"❌ WRONG PATH! Use ~ or /home/{user}/",
                "de": f"❌ FALSCHER PFAD! Verwende ~ oder /home/{user}/",
                "es": f"❌ RUTA INCORRECTA! Usa ~ o /home/{user}/",
            }.get(self.tts_lang if hasattr(self, 'tts_lang') else 'en',
                  f"❌ WRONG PATH! Use ~ or /home/{user}/")
            msg  = _path_err
            print(msg); return {"status": "error", "output": msg}

        if cmd == 'echo' or cmd.startswith('echo '):
            if any(c in cmd for c in ('|', '>', '<', ';', '&', '`', '$(')):
                msg = "🚫 Echo bloccato"
                print(msg); self._stats["denied"] += 1
                return {"status": "blocked", "output": msg}
            message = self._extract_echo(cmd) if cmd != 'echo' else explanation
            if not message:
                message = explanation
            if message and self._is_ghost(message, hist):
                return {"status": "skipped", "output": "ghost"}
            if message:
                print(f"\n💬 JARVIS: {message}")
                self._tts_say(message)
            return {"status": "echo", "output": message or ""}

        for b in self.BLOCKED:
            if b in cmd.lower():
                msg = f"🚫 Bloccato: '{b}'"
                print(msg); self._stats["denied"] += 1
                return {"status": "blocked", "output": msg}

        cmd_l = cmd.lower()
        for pat in self.NONSENSE_CMDS:
            if pat.lower() in cmd_l:
                if explanation and len(explanation) > 10:
                    if not self._is_ghost(explanation, hist):
                        print(f"\n💬 JARVIS: {explanation}")
                        self._tts_say(explanation)
                    return {"status": "echo", "output": explanation}
                return {"status": "skipped", "output": "nonsense"}

        print(f"\n▶ {cmd}")
        if explanation:
            print(f"  💡 {explanation}")

        if cmd.startswith('cd ') and not any(op in cmd for op in ('&&', '||', ';')):
            return self._do_cd(cmd[3:].strip())
        if 'cd ' in cmd:
            self._sync_cd(cmd)

        # Comandi distruttivi — richiede conferma SEMPRE
        DESTRUCTIVE = re.compile(
            r'\brm\b|\brmdir\b|\bdd\b|\bmkfs\b|\bshred\b|\bwipe\b',
            re.IGNORECASE
        )
        if DESTRUCTIVE.search(cmd):
            cmd = self._handle_rm_generic(cmd)
            if cmd is None:
                return {"status": "denied", "output": getattr(self, "_cancelled_msg", "❌ Cancelled")}
            resolved = self._resolve_rm_path(cmd)
            if resolved is None:
                return {"status": "error", "output": "Target non trovato"}
            cmd = resolved
            # Messaggio conferma localizzato
        _destr_msg = {
            "it": f"DISTRUTTIVO — Eseguire '{cmd}'?",
            "fr": f"DESTRUCTIF — Exécuter '{cmd}' ?",
            "en": f"DESTRUCTIVE — Execute '{cmd}'?",
            "de": f"DESTRUKTIV — Ausführen '{cmd}'?",
            "es": f"DESTRUCTIVO — ¿Ejecutar '{cmd}'?",
            "pt": f"DESTRUTIVO — Executar '{cmd}'?",
        }.get(self.tts_lang if hasattr(self, 'tts_lang') else 'en',
              f"DESTRUCTIVE — Execute '{cmd}'?")
        if not self._confirm(_destr_msg):
                self._stats["denied"] += 1
                return {"status": "denied", "output": getattr(self, "_cancelled_msg", "❌ Cancelled")}
        # Tutti gli altri comandi: esegui direttamente, nessuna conferma

        self._stats["cmds"] += 1
        is_sudo = bool(_RE_SUDO.match(cmd))
        if is_sudo and self.sudo_pass:
            clean = _RE_SUDO.sub('', cmd)
            rc, out = run_sudo_cmd(clean, self.sudo_pass, str(self.cwd))
        elif is_sudo:
            clean = _RE_SUDO.sub('', cmd)
            rc, out = run_cmd(clean, str(self.cwd))
        else:
            rc, out = run_cmd(cmd, str(self.cwd))

        self._executed_cmds.add(cmd)
        # run_cmd/run_sudo_cmd hanno già stampato tutto a schermo
        # out = "✅ Completato" se ok, oppure righe di errore filtrate se ko
        if rc == 0:
            # Stampa solo se non è già stato stampato da run_cmd
            if out and out != "✅ Completato":
                pass  # output informativo già stampato
            else:
                print("✅ Completato")
            return {"status": "ok", "output": out or "✅ Completato"}
        else:
            if not out:
                print(f"❌ Errore (rc={rc})")
            return {"status": "error", "output": out or f"❌ Errore rc={rc}"}

    def _is_ghost(self, message, history):
        msg_n = message.strip().lower()
        if len(msg_n) < 4:
            return False
        for entry in history:
            other = entry.get('content', '').strip().lower()
            if not other:
                continue
            if msg_n == other:
                return True
            short = min(len(msg_n), len(other))
            if short > 5:
                overlap = sum(a == b for a, b in zip(msg_n[:short], other[:short]))
                if overlap / short > 0.92:
                    return True
        return False

    def _clean_for_history(self, text):
        if not text:
            return ""
        text = _RE_BASH_BLK.sub('', text)
        text = _RE_JSON_FULL.sub('', text)
        text = re.sub(r'\{[^{}]{0,400}\}', '', text)
        _CMD_LINE = re.compile(
            r'^\s*(rm|ls|cd|mkdir|sudo|mv|cp|cat|echo|find|grep|apt|'
            r'systemctl|ping|curl|wget|df|free|uname|date|chmod|chown|'
            r'execute_terminal_command|"name"|"command"|"explanation")',
            re.IGNORECASE
        )
        lines = [l for l in text.splitlines() if not _CMD_LINE.match(l)]
        result = '\n'.join(lines).strip()
        return result if len(result) >= 3 else ""

    def _handle_rm_generic(self, cmd):
        generici = [
            'cartella_da_eliminare', 'file_da_eliminare', 'nome_cartella',
            'nome_file', 'your_folder', 'folder_name', 'file_name',
            '<nome', 'test_folder',
        ]
        cmd_l = cmd.lower()
        if not any(g in cmd_l for g in generici):
            return cmd
        print("⚠️ Nome generico rilevato.")
        real = input("  Nome esatto: ").strip()
        if not real:
            print("❌ Annullato"); return None
        for g in generici:
            if g in cmd_l:
                cmd = re.sub(re.escape(g), real, cmd, flags=re.IGNORECASE)
                break
        print(f"▶ Aggiornato: {cmd}")
        return cmd

    def _extract_rm_target(self, cmd):
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        targets = [p for p in parts[1:] if not p.startswith('-')]
        if not targets:
            return None
        t = targets[0]
        if t.startswith('~'):
            t = str(Path.home()) + t[1:]
        return t if Path(t).is_absolute() else str(self.cwd / t)

    def _resolve_rm_path(self, cmd):
        target = self._extract_rm_target(cmd)
        if not target:
            return cmd
        p = Path(target)
        if p.is_absolute():
            if not p.exists():
                print(f"⚠️ '{p}' non esiste.")
                return cmd
            name_l = p.name.lower()
            found  = None
            for d in [str(self.cwd)] + self._xdg_dirs():
                base = Path(d)
                if not base.exists():
                    continue
                exact = base / p.name
                if exact.exists():
                    found = exact; break
                try:
                    for child in base.iterdir():
                        if child.name.lower() == name_l:
                            found = child; break
                except PermissionError:
                    continue
                if found:
                    break
            if found is None:
                print(f"❌ '{p.name}' non trovato."); return None
            try:
                parts = shlex.split(cmd)
            except ValueError:
                parts = cmd.split()
            new_parts, replaced = [], False
            for part in parts:
                if not replaced and not part.startswith('-') and part != 'rm':
                    new_parts.append(str(found)); replaced = True
                else:
                    new_parts.append(part)
            new_cmd = ' '.join(shlex.quote(x) for x in new_parts)
            print(f"📍 Risolto: '{p.name}' → {found}")
            return new_cmd
        return cmd

    def _do_cd(self, target):
        if target.startswith('~'):
            target = str(Path.home()) + target[1:]
        path = Path(target) if Path(target).is_absolute() else self.cwd / target
        try:
            path = path.resolve()
            if not path.exists():
                return {"status": "error", "output": f"❌ Non esiste: {path}"}
            if not path.is_dir():
                return {"status": "error", "output": f"❌ Non è directory: {path}"}
            os.chdir(path)
            self.cwd = path
            print(f"📂 → {self.cwd}")
            return {"status": "ok", "output": str(path)}
        except Exception as e:
            return {"status": "error", "output": f"❌ {e}"}

    def _sync_cd(self, command):
        m = _RE_CD.search(command)
        if not m:
            return
        target = m.group(1)
        if target.startswith('~'):
            target = str(Path.home()) + target[1:]
        path = Path(target) if Path(target).is_absolute() else self.cwd / target
        try:
            path = path.resolve()
            if path.exists() and path.is_dir():
                os.chdir(path); self.cwd = path
        except Exception:
            pass

    def _is_safe(self, cmd):
        c = cmd.lower().strip()
        return any(c.startswith(p) for p in self.SAFE_PFX)

    def _confirm(self, prompt):
        # Risposte positive localizzate
        _yes = {
            "it": ("SI", "SÌ", "S", "YES", "Y"),
            "fr": ("OUI", "O", "YES", "Y"),
            "en": ("YES", "Y"),
            "de": ("JA", "J", "YES", "Y"),
            "es": ("SI", "SÍ", "S", "YES", "Y"),
            "pt": ("SIM", "S", "YES", "Y"),
            "ru": ("ДА", "Д", "YES", "Y"),
            "nl": ("JA", "J", "YES", "Y"),
            "pl": ("TAK", "T", "YES", "Y"),
            "tr": ("EVET", "E", "YES", "Y"),
            "sv": ("JA", "J", "YES", "Y"),
        }
        lang = self.tts_lang if hasattr(self, 'tts_lang') else "en"
        yes_words = _yes.get(lang, _yes["en"])

        # Label SI/no localizzato
        _label = {
            "it": "SI/no", "fr": "OUI/non", "en": "YES/no",
            "de": "JA/nein", "es": "SÍ/no", "pt": "SIM/não",
            "ru": "ДА/нет", "nl": "JA/nee", "pl": "TAK/nie",
            "tr": "EVET/hayır", "sv": "JA/nej",
        }
        label = _label.get(lang, "YES/no")

        # Annullato localizzato
        _cancelled = {
            "it": "❌ Annullato", "fr": "❌ Annulé", "en": "❌ Cancelled",
            "de": "❌ Abgebrochen", "es": "❌ Cancelado", "pt": "❌ Cancelado",
            "ru": "❌ Отменено", "nl": "❌ Geannuleerd", "pl": "❌ Anulowano",
            "tr": "❌ İptal edildi", "sv": "❌ Avbrutet",
        }
        self._cancelled_msg = _cancelled.get(lang, "❌ Cancelled")

        if _voice_mode_active and _active_voice_input is not None:
            # TTS nella lingua corrente
            _confirm_tts = {
                "it": f"Conferma: {prompt}. Sì o No?",
                "fr": f"Confirmation : {prompt}. Oui ou Non ?",
                "en": f"Confirm: {prompt}. Yes or No?",
                "de": f"Bestätigung: {prompt}. Ja oder Nein?",
                "es": f"Confirmar: {prompt}. ¿Sí o No?",
                "pt": f"Confirmar: {prompt}. Sim ou Não?",
            }
            self._tts_say(_confirm_tts.get(lang, f"Confirm: {prompt}. Yes or No?"))
            return _voice_confirm(prompt, _active_voice_input, lang=lang)
        else:
            risposta = input(f"\n⚠️  {prompt} ({label}): ").strip().upper()
            return risposta in yes_words

    def _extract_echo(self, cmd):
        for pat in (_RE_ECHO_DBL, _RE_ECHO_SGL, _RE_ECHO_RAW):
            m = pat.search(cmd)
            if m:
                return m.group(1).strip().strip('"\'')
        return cmd[5:].strip()

    def _needs_vision(self, msg):
        return False  # vision disabilitato

    def _capture_screen(self):
        try:
            shot = ImageGrab.grab().resize((800, 600))
            tmp  = self.mem_dir / "tmp_screen.png"
            shot.save(tmp)
            data = base64.b64encode(tmp.read_bytes()).decode()
            tmp.unlink(missing_ok=True)
            return data
        except Exception:
            return None

    def memorize(self, fact):
        entry = {"timestamp": datetime.now().isoformat(), "fact": fact}
        self.permanent.append(entry)
        self._save_json(self.mem_dir / "permanent.json", self.permanent)
        return f"✅ Memorizzato: '{fact}'"

    def show_memory(self):
        if not self.permanent:
            return "📭 Nessuna memoria"
        lines = [f"  {i+1}. {m.get('fact','')}" for i, m in enumerate(self.permanent)]
        return "📝 Memoria:\n" + "\n".join(lines)

    def forget_all(self):
        self.permanent.clear()
        self._save_json(self.mem_dir / "permanent.json", self.permanent)
        return "🗑️ Memoria cancellata"

    def _tts_say(self, text):
        if not self.tts_on or not text or len(text) < 4 or self._tts_q is None:
            return
        try:
            self._tts_q.put_nowait(text)
        except queue.Full:
            pass

    @staticmethod
    def _detect_lang(text: str) -> str:
        """Rileva lingua dal testo per gTTS."""
        it_words = re.compile(
            r'\b(il|lo|la|le|gli|un|una|è|sono|hai|ho|che|con|per|del|della|'
            r'dei|alle|come|non|questo|quello|ciao|grazie|prego|perfetto)\b',
            re.IGNORECASE
        )
        en_words = re.compile(
            r'\b(the|is|are|was|were|have|has|you|your|this|that|with|for|'
            r'and|but|hello|thanks|please|done|completed|error)\b',
            re.IGNORECASE
        )
        it = len(it_words.findall(text))
        en = len(en_words.findall(text))
        return 'en' if en > it else 'it'

    def _tts_worker(self):
        while True:
            try:
                text = self._tts_q.get(timeout=1)
            except queue.Empty:
                continue
            if text is None:
                break  # segnale di stop

            # Pulizia testo: rimuovi markdown, emoji, caratteri speciali
            clean = re.sub(r'[*_`#~>|]', '', text)
            clean = re.sub(r':[a-z_]+:', '', clean)          # :emoji:
            clean = re.sub(r'[\U00010000-\U0010ffff]', '', clean, flags=re.UNICODE)  # emoji unicode
            clean = re.sub(r'\s+', ' ', clean).strip()
            if not clean or len(clean) < 3:
                self._tts_q.task_done()
                continue

            tmp = self.mem_dir / f"tts_{int(time.time()*1000)}.mp3"
            try:
                gTTS(text=clean, lang=self.tts_lang, slow=False).save(str(tmp))

                if not tmp.exists() or tmp.stat().st_size < 100:
                    raise RuntimeError("File mp3 vuoto o non creato")

                player = self._tts_player
                cmd_map = {
                    'mpg123': ['mpg123', '-q', str(tmp)],
                    'ffplay': ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', str(tmp)],
                    'cvlc':   ['cvlc', '--play-and-exit', '-q', str(tmp)],
                }
                c = cmd_map.get(player, [player, str(tmp)])
                result = subprocess.run(
                    c, timeout=30,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                if result.returncode != 0:
                    err = result.stderr.decode(errors='ignore').strip()
                    print(f"\n⚠️ TTS player errore (rc={result.returncode}): {err[:100]}", flush=True)

            except Exception as e:
                print(f"\n⚠️ TTS errore: {e}", flush=True)
            finally:
                tmp.unlink(missing_ok=True)
            self._tts_q.task_done()

    def _restart_ollama(self):
        print("\n🔄 Riavvio Ollama...")
        try:
            subprocess.run(['sudo', 'systemctl', 'restart', 'ollama'], timeout=10, check=False)
            time.sleep(3)
            self._stats["errors"] = 0
            print("✅ Riavviato")
        except Exception as e:
            print(f"❌ {e}")

    # ── Process principale ────────────────────────────────────────────────────
    def process(self, user_msg: str) -> str:
        lower = user_msg.lower().strip()

        # Comandi slash
        # ── Tutti i comandi passano dal / ──────────────────────────────────────
        # IMPORTANTE: i comandi universali vanno controllati PRIMA del language
        # module — altrimenti handle_slash() li può intercettare e inghiottire.
        if lower.startswith('/'):
            cmd_part = lower.split()[0]
            rest     = user_msg.strip()[len(cmd_part):].strip()

            # Comandi universali (tutte le lingue)
            if cmd_part in ('/stats', '/info'):
                s = self._stats
                return (
                    f"📊 Chiamate: {s['calls']} | Comandi: {s['cmds']} | "
                    f"Negati: {s['denied']}\n"
                    f"📂 Dir: {self.cwd} | 🤖 {self.model} | 💾 {len(self.permanent)} | "
                    f"🧵 {_CPU_THREADS} thread CPU"
                )
            if cmd_part in ('/tts',):
                self.tts_on = not self.tts_on
                return f"🔊 TTS {'ON ✅' if self.tts_on else 'OFF ❌'}"
            if cmd_part in ('/agente', '/agent', '/agentic', '/auto'):
                # Forza modalità agente per il prossimo messaggio
                rest_agent = rest.strip()
                if rest_agent and AGENT_OK and self._agent:
                    return self._agent.run(rest_agent, history=list(self._history))
                elif not AGENT_OK:
                    return "❌ agent_module.py non trovato"
                return "⚠️  Uso: /agente <obiettivo>"
            if cmd_part in ('/modalità', '/modalita', '/mode', '/vocale', '/tastiera',
                            '/clavier', '/keyboard', '/tastatur', '/teclado'):
                return "__SWITCH_MODE__"
            if cmd_part in ('/esci', '/exit', '/quit', '/sortir', '/beenden',
                            '/salir', '/sair', '/выход', '/終了'):
                return "__EXIT__"

            # Comandi memoria — tutte le lingue
            _remember_cmds = ('/memorizza', '/remember', '/mémorise', '/memorize',
                               '/merken', '/recordar', '/lembrar', '/запомни',
                               '/記住', '/onthouden', '/zapamiętaj', '/hatırla',
                               '/kom ihåg', '/onthalen')
            _memory_cmds   = ('/memoria', '/ricordi', '/memory', '/mémoire',
                               '/gedächtnis', '/erinnerung', '/geheugen', '/pamięć',
                               '/bellek', '/minne', '/erënnerung', '/память',
                               '/記憶', '/기억')
            _forget_cmds   = ('/dimentica', '/forget', '/oublier', '/vergessen',
                               '/vergeten', '/zapomnij', '/unut', '/glöm',
                               '/vergiessen', '/забудь', '/忘记', '/忘れる')

            if cmd_part in _remember_cmds:
                if rest:
                    return self.memorize(rest)
                # Messaggio uso localizzato
                _use_msg = {
                    "it": "⚠️  Uso: /memorizza <fatto>",
                    "fr": "⚠️  Usage : /mémoriser <fait>",
                    "en": "⚠️  Usage: /remember <fact>",
                    "de": "⚠️  Verwendung: /merken <fakt>",
                }.get(self.tts_lang, "⚠️  Usage: /remember <fact>")
                return _use_msg
            if cmd_part in _memory_cmds:
                return self.show_memory()
            if cmd_part in _forget_cmds:
                return self.forget_all()

            # Meteo — NON rimuovere, è utile come comando diretto
            if self.search:
                if cmd_part in ('/meteo', '/tempo', '/weather', '/wetter',
                                '/météo', '/tiempo', '/tempo', '/погода'):
                    if rest:
                        return self.search.meteo(rest)
                    _use_meteo = {
                        "it": "⚠️  Uso: /meteo <città>",
                        "fr": "⚠️  Usage : /météo <ville>",
                        "en": "⚠️  Usage: /weather <city>",
                    }.get(self.tts_lang, "⚠️  Usage: /weather <city>")
                    return _use_meteo
                if cmd_part in ('/notizie', '/news', '/nouvelles', '/nachrichten', '/новости'):
                    return self.search.notizie(rest or None)
                if cmd_part in ('/wiki', '/wikipedia'):
                    if rest:
                        return self.search.wikipedia(rest)
                    return "⚠️  /wiki <topic>"
                # NOTA: /cerca RIMOSSO — le ricerche web le fa il modello con Tavily
                # Il modello usa web_search tool automaticamente quando serve

            # Language module come ultimo fallback per comandi slash non riconosciuti
            # (gestisce /lingua, /lingue, /cambia lingua, /aiuto, ecc.)
            if self._lang:
                result = self._lang.handle_slash(user_msg.strip())
                if result == "__STATS__":
                    s = self._stats
                    return (
                        f"📊 Chiamate: {s['calls']} | Comandi: {s['cmds']} | "
                        f"Negati: {s['denied']}\n"
                        f"📂 Dir: {self.cwd} | 🤖 {self.model} | 💾 {len(self.permanent)} | "
                        f"🧵 {_CPU_THREADS} thread CPU"
                    )
                if result == "__TTS__":
                    self.tts_on = not self.tts_on
                    return f"🔊 TTS {'ON ✅' if self.tts_on else 'OFF ❌'}"
                if result:
                    self.tts_lang = self._lang.tts_lang
                    return result

        # Attiva agente per richieste complesse
        if AGENT_OK and self._agent and should_use_agent(user_msg):
            print("\n🤖 Rilevata richiesta complessa — attivo agente", flush=True)
            return self._agent.run(user_msg, history=list(self._history))

        vision = self._needs_vision(user_msg)
        self._executed_cmds.clear()

        # Ricerca automatica — inietta nel system prompt per stabilità lingua
        web_context = ""
        if self.search and self.search._tavily.available:
            intent, query = self.search._intent.detect(user_msg)
            if intent in ("web", "news", "weather", "wikipedia"):
                if intent == "weather":
                    web_context = self.search._meteo.get(query or user_msg)
                elif intent == "news":
                    web_context = self.search._news.get(query or user_msg)
                elif intent == "wikipedia":
                    web_context = self.search._wiki.search(query or user_msg)
                else:
                    raw = self.search._tavily.search(query or user_msg, num=5)
                    if raw:
                        parts = []
                        for r in raw:
                            t = r.get("title",""); s = r.get("content",""); u = r.get("url","")
                            if s:
                                parts.append(f"- {t}: {s[:300]}" + (f" ({u})" if u else ""))
                        web_context = "\n".join(parts)

        full_text, tool_calls = self._call_model(user_msg, with_image=vision, web_context=web_context)

        search_results = []
        for tc in tool_calls:
            fn = tc.get('function', {})
            if fn.get('name') == 'execute_terminal_command':
                args = fn.get('arguments', {})
                cmd  = args.get('command', '').strip()
                if cmd:
                    self._queue(cmd, args.get('explanation', ''))
            elif fn.get('name') == 'web_search':
                args   = fn.get('arguments', {})
                query  = args.get('query', '').strip()
                expl   = args.get('explanation', '')
                if query:
                    res = self._execute_search(query, expl)
                    search_results.append({"query": query, "output": res["output"]})
        if not tool_calls:
            for c in self._parse_inline_tools(full_text):
                self._queue(c['command'], c['explanation'], kind='cmd')
            # Cattura anche web_search scritti come JSON inline
            for s in self._parse_inline_searches(full_text):
                res = self._execute_search(s['query'], s['explanation'])
                search_results.append({"query": s['query'], "output": res["output"]})

            # ── Fallback anti-invenzione ──────────────────────────────────────
            # Se il modello non ha cercato nulla ma la risposta contiene URL
            # falsi o placeholder, forza una ricerca reale con Tavily.
            _FAKE_URL = re.compile(
                r'https?://(?:example\.(?:com|org|net)|placeholder\.|link\s*\d+)',
                re.IGNORECASE
            )
            _NEEDS_SEARCH = re.compile(
                r'\b(romsfun|vimm|archive\.org|myrient|cdromance|emulatorgames'
                r'|github\.com|pypi\.org|huggingface\.co)\b',
                re.IGNORECASE
            )
            has_fake_urls  = bool(_FAKE_URL.search(full_text))
            site_in_query  = bool(_NEEDS_SEARCH.search(user_msg))

            if self.search and (has_fake_urls or site_in_query) and not search_results:
                # Costruisci una query pulita dal messaggio utente
                fallback_q = user_msg.strip()
                print(f"  ⚠️  Risposta con link finti rilevata — cerco davvero: {fallback_q[:60]}", flush=True)
                res = self._execute_search(fallback_q, "fallback automatico — modello aveva inventato risultati")
                search_results.append({"query": fallback_q, "output": res["output"]})
            # ─────────────────────────────────────────────────────────────────

        # Se il modello ha fatto ricerche, mandagli i risultati e chiedi di rispondere
        if search_results and not self._pending:
            parts = []
            for r in search_results:
                parts.append("🔍 Query: " + r["query"])
                parts.append(r["output"])
            search_text = chr(10).join(parts)
            lang_note = {
                "it": "Rispondi in italiano.",
                "fr": "Réponds en français.",
                "en": "Reply in English.",
            }.get(self.tts_lang, "")

            followup = (
                "Hai appena cercato su internet e questi sono i risultati REALI che hai trovato tu stesso:\n"
                + search_text + "\n\n"
                "Basati SOLO su questi dati per rispondere. "
                "Se hai trovato prezzi o link specifici, citali esattamente. "
                "NON dire 'ti consiglio di cercare su...' — hai già cercato tu. "
                "NON inventare nulla che non sia presente nei risultati. "
                + lang_note
            )
            print()
            final_text, final_tools = self._call_model(followup, history=[])
            # salva i risultati nella history
            with self._lock:
                self._history.append({
                    "role": "assistant",
                    "content": "Ricerche web eseguite:" + chr(10) + search_text
                })
            # se vuole eseguire comandi dopo la ricerca, fallo
            for tc in final_tools:
                fn = tc.get('function', {})
                if fn.get('name') == 'execute_terminal_command':
                    args = fn.get('arguments', {})
                    cmd  = args.get('command', '').strip()
                    if cmd:
                        self._queue(cmd, args.get('explanation', ''))
            if self._pending:
                self._flush(user_msg=user_msg)
            return final_text or full_text

        # Esegui i comandi e raccogli i risultati
        cmd_results = self._flush(user_msg=user_msg)

        # Costruisci il testo dei risultati da mandare al modello
        if cmd_results:
            parts = []
            for r in cmd_results:
                parts.append("$ " + r["cmd"] + " → " + r["output"])
            results_summary = chr(10).join(parts)

            # Aggiungi i risultati dei comandi nella history
            # così il modello sa cosa è già stato fatto nelle conversazioni future
            with self._lock:
                self._history.append({
                    "role": "assistant",
                    "content": "Comandi eseguiti:" + chr(10) + results_summary
                })
                if len(self._history) > MAX_HISTORY * 2:
                    self._history = self._history[-(MAX_HISTORY * 2):]

            # Chiedi al modello di rispondere all'utente con i risultati reali
            # Usiamo un messaggio "sistema" che non viene salvato di nuovo
            followup = (
                "Risultati dei comandi eseguiti per: " + chr(34) + user_msg + chr(34) + chr(10) +
                results_summary + chr(10) +
                "Rispondi all'utente in modo conciso. "
                "Se tutto è andato bene dillo chiaramente. "
                "Se c'è un errore analizzalo. "
                "Non ripetere comandi già completati con ✅."
            )
            print()
            # history=[] evita che _call_model salvi di nuovo nella history principale
            final_text, final_tools = self._call_model(followup, history=[])

            # Se il modello vuole altri comandi, eseguili
            if final_tools:
                for tc in final_tools:
                    fn = tc.get('function', {})
                    if fn.get('name') == 'execute_terminal_command':
                        args = fn.get('arguments', {})
                        cmd  = args.get('command', '').strip()
                        if cmd:
                            self._queue(cmd, args.get('explanation', ''))
                self._flush(user_msg=user_msg)
            else:
                for c in self._parse_inline_tools(final_text):
                    self._queue(c['command'], c['explanation'], kind='cmd')
                if self._pending:
                    self._flush(user_msg=user_msg)

            # Testo già stampato in streaming — ritorna "" per evitare duplicato
            return ""

        # Testo già stampato in streaming — ritorna "" per evitare duplicato
        return ""  # full_text già stampato chunk per chunk

    # ── Discord ───────────────────────────────────────────────────────────────
    def _process_discord(self, user_msg, ch_history):
        output_lines: list[str] = []
        lower = user_msg.lower().strip()

        if lower in ('stats', 'info'):
            s = self._stats
            return (
                f"📊 Chiamate: {s['calls']} | Comandi: {s['cmds']} | "
                f"Negati: {s['denied']}\n"
                f"📂 Dir: {self.cwd} | 🤖 {self.model} | 💾 {len(self.permanent)}"
            ), output_lines
        if lower.startswith('memorizza '):
            return self.memorize(user_msg[10:].strip()), output_lines
        if lower in ('memoria', 'ricordi'):
            return self.show_memory(), output_lines
        if lower == 'dimentica tutto':
            return self.forget_all(), output_lines
        if lower == 'tts':
            self.tts_on = not self.tts_on
            return f"🔊 TTS {'ON ✅' if self.tts_on else 'OFF ❌'}", output_lines

        # Attiva agente per richieste complesse
        if AGENT_OK and self._agent and should_use_agent(user_msg):
            print("\n🤖 Rilevata richiesta complessa — attivo agente", flush=True)
            return self._agent.run(user_msg, history=list(self._history))

        vision = self._needs_vision(user_msg)
        self._executed_cmds.clear()

        full_text, tool_calls = self._call_model(
            user_msg, with_image=vision, history=ch_history
        )

        # Processa tool calls: comandi e ricerche web
        pending: list[dict] = []
        search_results: list[dict] = []

        for tc in tool_calls:
            fn = tc.get('function', {})
            if fn.get('name') == 'execute_terminal_command':
                args = fn.get('arguments', {})
                cmd  = args.get('command', '').strip()
                if cmd:
                    pending.append({'command': cmd, 'explanation': args.get('explanation', ''), 'type': 'cmd'})
            elif fn.get('name') == 'web_search':
                args  = fn.get('arguments', {})
                query = args.get('query', '').strip()
                expl  = args.get('explanation', '')
                if query:
                    pending.append({'command': query, 'explanation': expl, 'type': 'search'})
        if not tool_calls:
            for c in self._parse_inline_tools(full_text):
                pending.append({'command': c['command'], 'explanation': c['explanation'], 'type': 'cmd'})

        # Esegui i pending
        cmd_results = []
        for item in pending:
            kind = item.get('type', 'cmd')
            cmd  = item['command'].strip()
            expl = item['explanation']

            if kind == 'search':
                output_lines.append(f"🔍 {cmd}")
                result = self._execute_search(cmd, expl)
                search_results.append({"query": cmd, "output": result["output"]})
                cmd_results.append({"cmd": cmd, "output": result["output"], "status": result["status"]})
            else:
                output_lines.append(f"▶ {cmd}")
                if expl:
                    output_lines.append(f"  💡 {expl}")
                result = self._execute(cmd, expl, history=ch_history)
                out = result.get('output', '')
                if out:
                    output_lines.append(out)
                cmd_results.append({"cmd": cmd, "output": out, "status": result.get("status", "ok")})

        # Se ci sono risultati (comandi o ricerche), chiedi al modello di rispondere
        if cmd_results:
            parts = []
            for r in cmd_results:
                parts.append("$ " + r["cmd"] + " → " + r["output"])
            results_text = chr(10).join(parts)
            followup = (
                "Risultati per: " + chr(34) + user_msg + chr(34) + chr(10) +
                results_text + chr(10) +
                "Rispondi in modo conciso basandoti su questi risultati reali."
            )
            final_text, _ = self._call_model(followup, history=ch_history)
            full_text = final_text or full_text

        # Salva nella history
        ch_history.append({"role": "user", "content": user_msg})
        clean = self._clean_for_history(full_text)
        if clean:
            ch_history.append({"role": "assistant", "content": clean})
        if cmd_results:
            ch_history.append({
                "role": "assistant",
                "content": "Eseguito: " + chr(10).join(r["cmd"] + " → " + r["output"] for r in cmd_results)
            })
        if len(ch_history) > MAX_HISTORY * 2:
            del ch_history[:-(MAX_HISTORY * 2)]

        return full_text, output_lines

    def _init_discord(self):
        try:
            intents = discord.Intents.default()
            intents.message_content = True
            self._disc_bot = dc_commands.Bot(
                command_prefix='!', intents=intents, help_command=None
            )
            ch_histories: dict[int, list] = {}
            executor = ThreadPoolExecutor(max_workers=max(2, _CPU_THREADS // 2))

            @self._disc_bot.event
            async def on_ready():
                print(f"💙 Discord: {self._disc_bot.user.name} online!")

            @self._disc_bot.event
            async def on_message(message):
                if message.author == self._disc_bot.user or message.content.startswith('!'):
                    return
                now  = time.time()
                last = self._cooldowns.get(message.author.id, 0)
                if now - last < 3:
                    await message.channel.send(f"⏳ Aspetta {3-(now-last):.1f}s")
                    return
                self._cooldowns[message.author.id] = now
                ch_id = message.channel.id
                if ch_id not in ch_histories:
                    ch_histories[ch_id] = []
                print(f"\n💙 Discord [{message.author}]: {message.content}")
                async with message.channel.typing():
                    try:
                        loop = asyncio.get_running_loop()
                        text, lines = await asyncio.wait_for(
                            loop.run_in_executor(
                                executor, self._process_discord,
                                message.content, ch_histories[ch_id],
                            ),
                            timeout=150.0,
                        )
                        output = "\n".join(l for l in lines if l.strip())
                        if output:
                            for chunk in [output[i:i+1900] for i in range(0, len(output), 1900)]:
                                await message.channel.send(f"```\n{chunk}\n```")
                        elif text:
                            await message.channel.send(text[:2000])
                        else:
                            await message.channel.send("✅ Fatto")
                    except asyncio.TimeoutError:
                        await message.channel.send("⏱️ Timeout (150s)")
                    except Exception as e:
                        await message.channel.send(f"❌ {str(e)[:200]}")
            print("✅ Discord inizializzato")
        except Exception as e:
            print(f"❌ Discord init: {e}")

    def start_discord(self):
        if not self._disc_bot:
            return
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._disc_bot.start(DISCORD_TOKEN))
            except Exception as e:
                print(f"❌ Discord run: {e}")
        threading.Thread(target=_run, daemon=True, name="discord").start()

    def _print_banner(self):
        if BANNER_OK:
            # Determina engine di ricerca attivi
            engines = []
            if hasattr(self, 'search') and self.search:
                s = self.search
                if hasattr(s, '_tavily') and s._tavily.available:
                    engines.append("Tavily")
                if hasattr(s, '_ddg'):
                    engines.append("DDG")
            eng_str = " + ".join(engines) if engines else ""

            _print_jarvis_banner(
                model        = self.model,
                tts_on       = self.tts_on,
                discord_on   = self.disc_on,
                memory_count = len(self.permanent),
                search_ok    = bool(hasattr(self, 'search') and self.search),
                ollama_ok    = True,
                speaker_ok   = False,  # aggiornato dopo VoiceModule init
                lang         = self.tts_lang,
                search_engines = eng_str,
            )
        else:
            # Fallback testo semplice se jarvis_banner.py non trovato
            sep = "=" * 52
            print(f"\n{sep}")
            print("🧠 JARVIS v6.0 — Core Stabile")
            print(sep)
            print(f"🤖 Modello:    {self.model}")
            print(f"🔊 TTS:        {'✅' if self.tts_on else '❌'}")
            print(f"💙 Discord:    {'✅' if self.disc_on else '❌'}")
            print(f"💾 Memorie:    {len(self.permanent)}")
            print(sep)


# ─── Loop vocale ──────────────────────────────────────────────────────────────
def voice_loop(jarvis: Jarvis, voice_input: VoiceInput) -> bool:
    session = WakeWordSession(timeout=WAKE_SESSION_TIMEOUT)

    if not voice_input.is_ready():
        print("⏳ Attendo Whisper...", end="\r", flush=True)
        while not voice_input.is_ready():
            time.sleep(0.5)
        print(" " * 30 + "\r", end="")

    print(f"\n{'─'*52}")
    print("🎙️  MODALITÀ VOCALE — Wake Word attiva")
    print(f"   Dì 'Jarvis' per iniziare")
    print(f"   Sessione attiva per {WAKE_SESSION_TIMEOUT//60} min dall'ultimo messaggio")
    print(f"{'─'*52}\n")

    _standby_shown = False

    while True:
        try:
            active = session.is_active()

            if not active:
                if not _standby_shown:
                    _lm_ui = jarvis._lang
                    _ui_standby = _lm_ui.ui_msg("standby") if _lm_ui else "😴 In standby — dì 'Jarvis'..."
                    print(_ui_standby, flush=True)
                    _standby_shown = True
            else:
                _standby_shown = False
                remaining = session.seconds_left()
                mins, secs = divmod(int(remaining), 60)
                print(f"🟢 Sessione attiva [{mins}:{secs:02d}] — parla:", end="\r", flush=True)

            text = voice_input.listen(timeout=10.0, silence_sec=1.5)

            if not text:
                continue

            text_clean = text.strip()
            print(f"\n🎙️  Trascritto: «{text_clean}»")

            lower = text_clean.lower()
            _lm_exit = jarvis._lang

            # Uscita — stesse parole in tutte le lingue
            _exit_words = {'esci', 'exit', 'quit', 'addio', 'arrivederci',
                           'au revoir', 'goodbye', 'tschüss', 'adiós', 'adeus',
                           'до свидания', '再见', 'tot ziens', 'do widzenia',
                           'hoşça kal', 'hej då', '/esci', '/exit', '/quit'}
            if any(w in lower for w in _exit_words):
                bye = _lm_exit.ui_msg("goodbye") if _lm_exit else "👋 Bye!"
                print(f"\n{bye}")
                voice_input.stop()
                return False

            # Cambio modalità — stesse parole in tutte le lingue
            _mode_words = {'tastiera', 'keyboard', 'clavier', 'tastatur',
                           'teclado', '/mode', '/modalità', '/tastiera', '/keyboard',
                           '/clavier', '/tastatur'}
            if any(w in lower for w in _mode_words):
                return True

            # Wake/sleep word dalla lingua corrente
            _lm = jarvis._lang  # sempre disponibile, impostato in main()
            has_wake  = _lm.is_wake(text_clean)  if _lm else _contains_wake_word(text_clean)
            has_sleep = _lm.is_sleep(text_clean) if _lm else False

            # Fallback fuzzy sleep: se sessione attiva e testo contiene wake + parola sleep
            if not has_sleep and has_wake:
                _SLEEP_KWORDS = {'dormi', 'riposa', 'sleep', 'dors', 'repos',
                                 'schlaf', 'schlafe', 'slaap', 'duerme', 'dorme'}
                lower_clean = text_clean.lower()
                if any(w in lower_clean for w in _SLEEP_KWORDS):
                    has_sleep = True

            if not active:
                if has_wake:
                    session.activate()
                    msg = _lm.strip_wake(text_clean) if _lm else _strip_wake_word(text_clean)
                    print(f"✅ Wake word! Sessione aperta per {WAKE_SESSION_TIMEOUT//60} min")
                    if msg:
                        print(f"⌨️  Tu: {msg}\n")
                        jarvis.process(msg)
                        session.touch()
                        print()
            else:
                # Sleep word — chiude sessione (es. "jarvis dormi", "dormi jarvis", "sleep jarvis")
                if has_sleep:
                    ui_sleep = _lm.ui_msg("sleeping") if _lm else "💤 Vado in standby."
                    print(f"\n{ui_sleep}")
                    jarvis._tts_say(ui_sleep)
                    session.deactivate()
                    _standby_shown = False
                    continue

                # Tutto passa per process() — stesso comportamento di tastiera
                print(f"⌨️  Tu: {text_clean}\n")
                result = jarvis.process(text_clean)
                if result == "__EXIT__":
                    print("\n👋 Ciao!")
                    voice_input.stop()
                    return False
                if result == "__SWITCH_MODE__":
                    return True
                session.touch()
                print()

        except KeyboardInterrupt:
            print("\n\n👋 Ctrl+C — uscita")
            voice_input.stop()
            return False
        except Exception as e:
            import traceback
            print(f"\n❌ voice_loop: {e}")
            traceback.print_exc()
            time.sleep(1)


def _strip_wake_word(text):
    clean  = re.sub(r"[^\w\s]", " ", text).strip()
    tokens = clean.split()
    if not tokens:
        return ""
    FILLER = {'hey', 'ok', 'ey', 'ehm', 'ah', 'oh', 'senti', 'allora', 'dai', 'su'}
    while tokens and tokens[0].lower() in FILLER:
        tokens.pop(0)
    if tokens and _contains_wake_word(tokens[0]):
        tokens.pop(0)
    while tokens and tokens[0].lower() in FILLER:
        tokens.pop(0)
    return " ".join(tokens)


_YES_WORDS = {
    # Italiano
    'si', 'sì', 'sí', 'certo', 'vai', 'procedi', 'conferma', 'confermo',
    'esatto', 'giusto', 'affermativo', 'dai', 'forza', 'avanti', 'fallo', 'esegui',
    # Inglese
    'yes', 'ok', 'okay', 'sure', 'confirm', 'confirmed', 'go', 'do it', 'proceed',
    # Francese
    'oui', 'ouais', 'bien sûr', 'confirme', 'vas-y', 'allez',
    # Tedesco
    'ja', 'jawohl', 'bestätigt', 'klar', 'gut',
    # Spagnolo
    'sí', 'claro', 'confirmo', 'adelante', 'dale',
    # Portoghese
    'sim', 'claro', 'confirmo', 'pode',
    # Russo
    'да', 'хорошо', 'подтверждаю',
    # Olandese
    'ja', 'oké', 'bevestig',
    # Polacco
    'tak', 'dobrze', 'potwierdzam',
    # Turco
    'evet', 'tamam', 'onaylıyorum',
    # Svedese
    'ja', 'okej', 'bekräftar',
    # Lussemburghese
    'jo', 'kloer', 'bestätegt',
}
_NO_WORDS = {
    # Italiano
    'no', 'nope', 'annulla', 'annullato', 'stop', 'blocca', 'fermati',
    'negativo', 'non', 'cancella', 'lascia perdere', 'basta', 'aspetta',
    'non farlo', 'non eseguire',
    # Inglese
    'cancel', 'cancelled', 'abort', 'stop', 'dont', "don't", 'negative',
    # Francese
    'non', 'annule', 'arrête', 'pas', 'négatif',
    # Tedesco
    'nein', 'abbrechen', 'stopp', 'halt', 'negativ',
    # Spagnolo
    'no', 'cancela', 'para', 'detente', 'negativo',
    # Portoghese
    'não', 'cancela', 'para', 'negativo',
    # Russo
    'нет', 'отмена', 'стоп', 'отменить',
    # Olandese
    'nee', 'annuleer', 'stop', 'niet',
    # Polacco
    'nie', 'anuluj', 'stop',
    # Turco
    'hayır', 'iptal', 'dur',
    # Svedese
    'nej', 'avbryt', 'stoppa',
}

def _voice_confirm(prompt, vi, lang="it"):
    _hints = {
        "it": ("(sì / no)", "non ho capito, ripeti (sì / no)"),
        "fr": ("(oui / non)", "je n'ai pas compris, répétez (oui / non)"),
        "en": ("(yes / no)", "didn't understand, repeat (yes / no)"),
        "de": ("(ja / nein)", "nicht verstanden, wiederholen (ja / nein)"),
        "es": ("(sí / no)", "no entendí, repite (sí / no)"),
        "pt": ("(sim / não)", "não entendi, repita (sim / não)"),
        "ru": ("(да / нет)", "не понял, повторите (да / нет)"),
        "nl": ("(ja / nee)", "niet begrepen, herhaal (ja / nee)"),
        "pl": ("(tak / nie)", "nie zrozumiałem, powtórz (tak / nie)"),
        "tr": ("(evet / hayır)", "anlamadım, tekrarlayın (evet / hayır)"),
        "sv": ("(ja / nej)", "förstod inte, upprepa (ja / nej)"),
    }
    _cancelled_msgs = {
        "it": "❌ Annullato", "fr": "❌ Annulé", "en": "❌ Cancelled",
        "de": "❌ Abgebrochen", "es": "❌ Cancelado", "pt": "❌ Cancelado",
        "ru": "❌ Отменено", "nl": "❌ Geannuleerd", "pl": "❌ Anulowano",
        "tr": "❌ İptal", "sv": "❌ Avbrutet",
    }
    _confirmed_msgs = {
        "it": "✅ Confermato", "fr": "✅ Confirmé", "en": "✅ Confirmed",
        "de": "✅ Bestätigt", "es": "✅ Confirmado", "pt": "✅ Confirmado",
        "ru": "✅ Подтверждено", "nl": "✅ Bevestigd", "pl": "✅ Potwierdzone",
        "tr": "✅ Onaylandı", "sv": "✅ Bekräftat",
    }
    hint1, hint2 = _hints.get(lang, _hints["en"])
    cancelled = _cancelled_msgs.get(lang, "❌ Cancelled")
    confirmed = _confirmed_msgs.get(lang, "✅ Confirmed")

    for attempt in range(2):
        hint = f" ({hint1})" if attempt == 0 else f" — {hint2}"
        print(f"\n⚠️  {prompt + hint}", flush=True)
        text = vi.listen(timeout=float(CONFIRM_SILENCE_TIMEOUT), silence_sec=1.2)
        if text is None:
            print(f"🔇 {cancelled}")
            return False
        lower = re.sub(r"[^\w\s]", "", text.lower()).strip()
        print(f"   «{text}»")
        if any(w in lower for w in _NO_WORDS):
            print(cancelled); return False
        if any(w in lower for w in _YES_WORDS):
            print(confirmed); return True
    print(f"⚠️  {cancelled}")
    return False


# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 52)
    print("🚀 JARVIS v6.0 — Core Stabile")
    print("=" * 52 + "\n")

    if not ensure_ollama():
        return

    try:
        r      = requests.get(OLLAMA_TAGS, timeout=5)
        models = [m['name'] for m in r.json().get('models', [])]
        jarvis_models = [m for m in models if 'jarvis' in m.lower()]
        print(f"📋 Modelli JARVIS: {jarvis_models or 'nessuno'}\n")
    except Exception:
        pass

    model = "jarvisQwen"
    vision_model_choice = None

    # ── Lingua (Language Module) ──────────────────────────────────────────
    lang_mgr = LanguageManager()
    if lang_mgr.is_first_run:
        lang_mgr.setup_first_run()
    tts_lang = lang_mgr.tts_lang
    sys_lang = lang_mgr.current
    print(f"🌍 {lang_mgr.format_status()}")

    sudo_pass = input("Password sudo (invio per saltare): ").strip() or None
    tts       = input("TTS voce? (s/N): ").strip().lower() == 's'
    disc      = input("Discord bot? (s/N): ").strip().lower() == 's'

    if disc and not DISCORD_TOKEN:
        print("⚠️ DISCORD_TOKEN non impostato — usa variabile d'ambiente DISCORD_TOKEN")
        disc = False

    # ── Modalità input ────────────────────────────────────────────────────────
    voice_input = None
    use_voice   = False

    print("\nModalità input:")
    print("  1. ⌨️  Tastiera")
    if SD_OK and WHISPER_OK:
        print("  2. 🎙️  Microfono + Wake Word 'Jarvis'")
    modo = input("Scelta [1]: ").strip() or "1"

    if modo == "2":
        if not SD_OK or not WHISPER_OK:
            print("⚠️ Dipendenze voce mancanti. Uso tastiera.")
        else:
            mic = choose_microphone()
            if mic:
                voice_input = VoiceInput(mic)
                use_voice   = True
                # Collega speaker verifier e TTS per ignore-self
                _speaker = SpeakerVerifier()
                voice_input._speaker_ref = _speaker
                voice_input._lang_ref = lang_mgr

    print()

    enable_search = SEARCH_OK
    searxng_url   = "http://localhost:8080"
    brave_key     = os.environ.get("BRAVE_API_KEY", "")
    gnews_key     = os.environ.get("GNEWS_API_KEY", "")

    bot = Jarvis(
        model=model,
        vision_model=model,
        tts_lang=tts_lang,
        system_lang=sys_lang,
        sudo_password=sudo_pass,
        enable_tts=tts,
        enable_discord=disc,
        enable_search=enable_search,
        searxng_url=searxng_url,
        brave_api_key=brave_key,
        gnews_key=gnews_key,
    )

    bot._lang = lang_mgr  # collega language manager

    # Crea agente autonomo se disponibile
    if AGENT_OK:
        bot._agent = JarvisAgent(
            call_model    = lambda msg, history=None: bot._call_model(msg, history=history or []),
            execute_cmd   = lambda cmd, expl: bot._execute(cmd, expl),
            execute_search= lambda q, expl: bot._execute_search(q, expl) if bot.search else None,
            lang          = lang_mgr,
        )
        print("🤖 Agente: ✅ attivo")
    else:
        print("🤖 Agente: ❌ agent_module.py non trovato")
    if bot.disc_on and bot._disc_bot:
        bot.start_discord()
        time.sleep(2)

    sep = "=" * 52
    print(f"\n{sep}")
    print("✅ JARVIS v6.0 PRONTO")
    print(sep)
    print("💾 /memorizza <fatto>  | 📝 /memoria | 🗑️ /dimentica")
    print("📊 /stats | 🔊 /tts | 🔄 /modalita | 👋 /esci")
    print("🔍 /cerca <q> | 🌤️ /meteo <città> | 📰 /notizie | 📖 /wiki <q>")
    print("🌍 /lingua | /cambia lingua | /aggiungi lingua")
    print("─" * 52)
    print("💡 Tutti i comandi iniziano con /")
    print(f"{sep}\n")

    # ── Loop principale ───────────────────────────────────────────────────────
    while True:
        try:
            if use_voice and voice_input:
                global _active_voice_input, _voice_mode_active
                # Collega TTS per aspettare fine risposta prima di ascoltare
                if hasattr(bot, '_tts_q'):
                    class _TTSRef:
                        def __init__(self, b): self._b = b
                        @property
                        def is_speaking(self): return not self._b._tts_q.empty() if self._b._tts_q else False
                    voice_input._tts_ref = _TTSRef(bot)
                _active_voice_input = voice_input
                _voice_mode_active  = True
                want_keyboard = voice_loop(bot, voice_input)
                _voice_mode_active  = False
                _active_voice_input = None
                if not want_keyboard:
                    break
                use_voice = False
                print(f"\n{sep}\n⌨️  MODALITÀ TASTIERA\n{sep}\n")
                continue

            user_input = input("⌨️  Tu: ").strip()

            if user_input.lower() in ('microfono', 'vocale', 'modalità', 'modalita',
                                      'cambia modalità', 'cambia modalita',
                                      'passa a vocale') and SD_OK and WHISPER_OK:
                if voice_input is None:
                    mic = choose_microphone()
                    if mic:
                        voice_input = VoiceInput(mic)
                if voice_input:
                    use_voice = True
                    print("🎙️  Passato a modalità vocale")
                else:
                    print("❌ Impossibile attivare il microfono")
                continue

            if not user_input:
                continue

            if user_input.lower() in ('esci', 'exit', 'quit', 'bye', '/esci', '/exit', '/quit'):
                print("\n👋 Ciao!")
                break

            print()
            result = bot.process(user_input)
            if result == "__EXIT__":
                print("\n👋 Ciao!")
                break
            elif result == "__SWITCH_MODE__":
                if SD_OK and WHISPER_OK:
                    if voice_input is None:
                        mic = choose_microphone()
                        if mic:
                            voice_input = VoiceInput(mic)
                    if voice_input:
                        use_voice = True
                        print("🎙️  Passato a modalità vocale")
                    else:
                        print("❌ Impossibile attivare il microfono")
                else:
                    print("⚠️ Dipendenze vocali non disponibili")
            else:
                if result:
                    # Solo per comandi slash — le risposte normali
                    # sono già stampate e lette dal TTS durante lo streaming
                    print(result)
                    bot._tts_say(result)
                print()

        except KeyboardInterrupt:
            print("\n\n👋 Ctrl+C")
            break
        except Exception as e:
            import traceback
            print(f"\n❌ {e}")
            traceback.print_exc()
            print()

    # Il cleanup viene eseguito automaticamente da CleanupManager via atexit
    # Non serve chiamarlo esplicitamente qui


if __name__ == "__main__":
    main()