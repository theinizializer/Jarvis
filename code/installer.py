#!/usr/bin/env python3
"""
JARVIS v9.0 — Installer cross-platform
Supporta: Linux (Ubuntu/Debian/Arch/CachyOS), macOS, Windows

Logica:
  - Rileva OS e configura di conseguenza
  - Installa Ollama automaticamente se mancante
  - Linux:   pacman/apt + venv jarvisenv in ~ (Python 3.12 richiesto per ROCm)
  - macOS:   Homebrew + nessun venv (Python sistema)
  - Windows: winget + nessun venv (Python sistema)
  - Cerca i file in ../code (JARVIS_MAIN/code)
  - Cerca il Modelfile in .. (JARVIS_MAIN/Modelfile)
  - Sposta i file in ~/Documenti/modelli (o equivalente per OS)
  - Applica patch OS-specific al codice (player audio, mute mic)
  - Configura .env con API key (Groq, Cerebras, NVIDIA, Tavily, ecc.)
  - Crea script di avvio appropriato per OS
  - Su CachyOS/Arch: configura ROCm per iGPU AMD (SepFormer GPU)
"""

import os, sys, json, shutil, subprocess, platform, tempfile, urllib.request
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════════
# RILEVAMENTO OS
# ════════════════════════════════════════════════════════════════════════════

OS = platform.system()          # "Linux" | "Darwin" | "Windows"
IS_LINUX   = OS == "Linux"
IS_MAC     = OS == "Darwin"
IS_WINDOWS = OS == "Windows"

PYTHON_MIN     = (3, 12)
PYTHON_MIN_STR = "3.12"   # richiesto per torch ROCm

# ── Cartella destinazione ──────────────────────────────────────────────────────
# Linux/Mac:  ~/Documenti/modelli  oppure  ~/Documents/modelli
# Windows:    %USERPROFILE%\Documents\modelli
def _find_documents() -> Path:
    home = Path.home()
    for name in ("Documenti", "Documents", "Documentos", "Dokumente"):
        p = home / name
        if p.exists():
            return p
    # Fallback: crea ~/Documents
    p = home / "Documents"
    p.mkdir(parents=True, exist_ok=True)
    return p

DEST_DIR  = _find_documents() / "modelli"
VENV_DIR  = Path.home() / "jarvisenv"   # solo Linux
ENV_FILE  = DEST_DIR / ".env"

# ── Percorsi sorgente (relative al repository) ─────────────────────────────────
# L'installer si trova in: jarvis-main/code/installer.py
# Quindi:
#   - code/ si trova a: ./
#   - Modelfile si trova a: ../
REPO_ROOT = Path(__file__).parent.parent
CODE_DIR  = REPO_ROOT / "code"
MODELFILE_PATH = REPO_ROOT / "Modelfile"

# ════════════════════════════════════════════════════════════════════════════
# COLORI
# ════════════════════════════════════════════════════════════════════════════

class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(m):   print(f"{C.GREEN}✅ {m}{C.RESET}")
def warn(m): print(f"{C.YELLOW}⚠️  {m}{C.RESET}")
def err(m):  print(f"{C.RED}❌ {m}{C.RESET}")
def info(m): print(f"{C.BLUE}ℹ️  {m}{C.RESET}")
def bold(m): print(f"{C.BOLD}{m}{C.RESET}")

def step(n, total, msg):
    print(f"\n{C.BOLD}[{n}/{total}] {msg}{C.RESET}")
    print("─" * 54)

def ask(prompt, default="") -> str:
    val = input(f"   {prompt} ").strip()
    return val if val else default

def confirm(prompt) -> bool:
    return ask(prompt + " (S/n):").lower() in ("", "s", "y", "si", "sì", "yes")

# ════════════════════════════════════════════════════════════════════════════
# UTILITÀ
# ════════════════════════════════════════════════════════════════════════════

def run(cmd, check=True, capture=False, input_text=None, shell=False):
    kwargs = dict(check=check, shell=shell)
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    if input_text is not None:
        kwargs["input"] = input_text
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)

def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None

def pip_cmd() -> list:
    """Ritorna il comando pip corretto per l'OS."""
    if IS_LINUX:
        return [str(VENV_DIR / "bin" / "pip")]
    if IS_MAC:
        return [sys.executable, "-m", "pip"]
    # Windows
    return [sys.executable, "-m", "pip"]

def python_cmd() -> list:
    if IS_LINUX:
        return [str(VENV_DIR / "bin" / "python")]
    return [sys.executable]

# ════════════════════════════════════════════════════════════════════════════
# STEP 1 — Controllo sistema
# ════════════════════════════════════════════════════════════════════════════

def check_system():
    step(1, 9, "Controllo sistema")

    v = sys.version_info
    if (v.major, v.minor) < PYTHON_MIN:
        err(f"Python {PYTHON_MIN_STR}+ richiesto, trovato {v.major}.{v.minor}")
        err("Su CachyOS/Arch: python3.12 dovrebbe essere disponibile")
        err("Avvia l'installer con: python3.12 installer.py")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
    ok(f"Sistema: {OS} {platform.release()}")
    ok(f"Destinazione: {DEST_DIR}")
    ok(f"Sorgente codice: {CODE_DIR}")

    # Rilevamento ROCm (AMD GPU) su Linux
    if IS_LINUX:
        rocminfo = shutil.which("rocminfo") or "/opt/rocm/bin/rocminfo"
        if Path(rocminfo).exists():
            r = run([rocminfo], capture=True, check=False)
            if "gfx" in (r.stdout if r.returncode == 0 else ""):
                ok("ROCm rilevato — GPU AMD disponibile per SepFormer")
                globals()["ROCM_AVAILABLE"] = True
            else:
                info("ROCm installato ma nessuna GPU gfx rilevata")
        else:
            info("ROCm non installato — SepFormer usera' CPU")

# ════════════════════════════════════════════════════════════════════════════
# STEP 2 — Installa Ollama (tutti gli OS)
# ════════════════════════════════════════════════════════════════════════════

def install_ollama():
    step(2, 8, "Ollama")

    if cmd_exists("ollama"):
        try:
            r = run(["ollama", "--version"], capture=True, check=False)
            ok(f"Ollama già installato: {r.stdout.strip()}")
        except Exception:
            ok("Ollama già installato")
        return

    warn("Ollama non trovato — installo automaticamente...")

    try:
        if IS_LINUX:
            # Script ufficiale Ollama
            info("Scarico installer Ollama (curl)...")
            run("curl -fsSL https://ollama.com/install.sh | sh", shell=True)

        elif IS_MAC:
            # Ollama su Mac si installa come .app — usa brew se disponibile
            if cmd_exists("brew"):
                run(["brew", "install", "ollama"])
            else:
                # Scarica il .dmg
                dmg = Path(tempfile.mktemp(suffix=".dmg"))
                info("Scarico Ollama.dmg...")
                urllib.request.urlretrieve(
                    "https://ollama.com/download/Ollama-darwin.dmg", str(dmg)
                )
                run(["hdiutil", "attach", str(dmg)])
                run(["cp", "-r", "/Volumes/Ollama/Ollama.app", "/Applications/"])
                run(["hdiutil", "detach", "/Volumes/Ollama"])
                dmg.unlink(missing_ok=True)
                # Aggiungi ollama al PATH
                info("Aggiungo ollama al PATH...")
                run(["ln", "-sf", "/Applications/Ollama.app/Contents/MacOS/ollama",
                     "/usr/local/bin/ollama"], check=False)

        elif IS_WINDOWS:
            # winget
            if cmd_exists("winget"):
                run(["winget", "install", "-e", "--id", "Ollama.Ollama",
                     "--accept-package-agreements", "--accept-source-agreements"])
            else:
                # Scarica installer .exe
                exe = Path(tempfile.mktemp(suffix=".exe"))
                info("Scarico OllamaSetup.exe...")
                urllib.request.urlretrieve(
                    "https://ollama.com/download/OllamaSetup.exe", str(exe)
                )
                run([str(exe), "/S"])  # silent install
                exe.unlink(missing_ok=True)

        if cmd_exists("ollama"):
            ok("Ollama installato con successo")
        else:
            warn("Ollama installato ma potrebbe richiedere un nuovo terminale per essere nel PATH")

    except Exception as e:
        err(f"Installazione Ollama fallita: {e}")
        err("Installa manualmente da: https://ollama.com")
        if not confirm("Continuare comunque?"):
            sys.exit(1)

# ════════════════════════════════════════════════════════════════════════════
# STEP 3 — Dipendenze sistema (apt / brew / winget)
# ════════════════════════════════════════════════════════════════════════════

def install_system_packages():
    step(3, 8, "Dipendenze sistema")

    if IS_LINUX:
        _install_linux()
    elif IS_MAC:
        _install_brew()
    elif IS_WINDOWS:
        _install_winget()

def _install_linux():
    """Installa pacchetti di sistema su qualsiasi distro Linux."""

    # Mappa package-manager → (cmd_update, cmd_install, pacchetti_necessari)
    # Ordine: verifica prima pacman (Arch/CachyOS/Manjaro), poi apt, poi dnf, poi zypper
    PM_CONFIGS = [
        # (binary_pm, update_cmd, install_cmd, pkgs_audio, pkgs_portaudio, pkgs_pulse)
        ("pacman",  ["sudo", "pacman", "-Sy", "--noconfirm"],
                    ["sudo", "pacman", "-S", "--noconfirm", "--needed"],
                    "mpg123", "portaudio", "pipewire-pulse"),
        ("apt",     ["sudo", "apt", "update", "-qq"],
                    ["sudo", "apt", "install", "-y"],
                    "mpg123", "portaudio19-dev", "pulseaudio-utils"),
        ("dnf",     ["sudo", "dnf", "check-update"],
                    ["sudo", "dnf", "install", "-y"],
                    "mpg123", "portaudio-devel", "pulseaudio-utils"),
        ("zypper",  ["sudo", "zypper", "refresh"],
                    ["sudo", "zypper", "install", "-y"],
                    "mpg123", "portaudio-devel", "pulseaudio-utils"),
    ]

    pm_found = None
    for entry in PM_CONFIGS:
        if cmd_exists(entry[0]):
            pm_found = entry
            break

    if pm_found is None:
        warn("Nessun package manager riconosciuto (apt/pacman/dnf/zypper) — salta")
        warn("Installa manualmente: mpg123, portaudio, pulseaudio-utils (o equivalenti)")
        return

    pm_bin, update_cmd, install_cmd, pkg_mpg, pkg_portaudio, pkg_pulse = pm_found
    info(f"Package manager rilevato: {pm_bin}")

    # Binary → pacchetto corrispondente per questa distro
    # Nota: 'parecord' non esiste su Arch — usiamo 'pw-record' (PipeWire) o 'arecord' come proxy
    pulse_binary = "parecord" if pm_bin != "pacman" else "pw-record"
    checks = [
        ("mpg123",       pkg_mpg,       "riproduzione audio TTS"),
        (pulse_binary,   pkg_pulse,     "registrazione microfono"),
        # portaudio è una libreria, non un binary — verifichiamo con pkg-config
    ]

    pkgs = []
    for binary, pkg, desc in checks:
        if cmd_exists(binary):
            ok(f"{pkg}: già presente ({desc})")
        else:
            warn(f"{pkg}: mancante ({desc})")
            pkgs.append(pkg)

    # Controlla portaudio via pkg-config (è una lib, non un eseguibile)
    portaudio_ok = run(["pkg-config", "--exists", "portaudio-2.0"],
                       check=False, capture=True).returncode == 0
    if portaudio_ok:
        ok(f"{pkg_portaudio}: già presente (sounddevice)")
    else:
        warn(f"{pkg_portaudio}: mancante (sounddevice)")
        pkgs.append(pkg_portaudio)

    if pkgs:
        info(f"Installo con {pm_bin}: {', '.join(pkgs)}")
        try:
            run(update_cmd, check=False)
            run(install_cmd + pkgs)
            ok(f"Pacchetti installati con {pm_bin}")
        except Exception as e:
            warn(f"{pm_bin} fallito: {e}")
            warn(f"Installa manualmente: {' '.join(pkgs)}")

def _install_brew():
    # Installa Homebrew se mancante
    if not cmd_exists("brew"):
        info("Installo Homebrew...")
        try:
            run('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                shell=True)
        except Exception as e:
            warn(f"Homebrew fallito: {e}")
            return

    pkgs = []
    checks = [
        ("mpg123",    "mpg123",    "riproduzione audio TTS"),
        ("portaudio", "portaudio", "sounddevice (Python audio)"),
    ]
    for binary, pkg, desc in checks:
        if cmd_exists(binary):
            ok(f"{pkg}: già presente ({desc})")
        else:
            warn(f"{pkg}: mancante ({desc})")
            pkgs.append(pkg)

    if pkgs:
        info(f"Installo con brew: {', '.join(pkgs)}")
        try:
            run(["brew", "install"] + pkgs)
            ok("Pacchetti brew installati")
        except Exception as e:
            warn(f"brew fallito: {e}")

def _install_winget():
    if not cmd_exists("winget"):
        warn("winget non disponibile — salta pacchetti sistema")
        return

    # Su Windows mpg123 non esiste — usiamo ffplay (parte di ffmpeg)
    if not cmd_exists("ffplay"):
        info("Installo ffmpeg (per riproduzione audio TTS)...")
        try:
            run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                 "--accept-package-agreements", "--accept-source-agreements"])
            ok("ffmpeg installato")
        except Exception as e:
            warn(f"ffmpeg fallito: {e}")

# ════════════════════════════════════════════════════════════════════════════
# STEP 4 — Virtualenv (solo Linux)
# ════════════════════════════════════════════════════════════════════════════

def create_venv():
    step(4, 8, "Ambiente virtuale Python")

    if not IS_LINUX:
        ok(f"{'macOS' if IS_MAC else 'Windows'}: venv non necessario — uso Python di sistema")
        return

    if VENV_DIR.exists():
        if confirm(f"{VENV_DIR} esiste già. Ricrearlo?"):
            info(f"Rimuovo {VENV_DIR}...")
            shutil.rmtree(VENV_DIR)
        else:
            ok(f"Venv esistente mantenuto: {VENV_DIR}")
            return

    info(f"Creo venv in {VENV_DIR}...")
    try:
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
        ok(f"Venv creato: {VENV_DIR}")
    except Exception as e:
        err(f"Creazione venv fallita: {e}")
        if cmd_exists("apt"):
            info("Prova: sudo apt install python3-venv")
        elif cmd_exists("pacman"):
            info("Prova: sudo pacman -S python (già incluso su Arch)")
        elif cmd_exists("dnf"):
            info("Prova: sudo dnf install python3")
        else:
            info("Assicurati che python3-venv sia installato per la tua distro")
        sys.exit(1)

# ════════════════════════════════════════════════════════════════════════════
# STEP 5 — Dipendenze Python
# ════════════════════════════════════════════════════════════════════════════

ROCM_AVAILABLE = False  # aggiornato da check_system()

PIP_PACKAGES = {
    "Core": [
        "requests>=2.31.0",
        "numpy>=1.24.0",
        "scipy>=1.11.0",
        "noisereduce>=3.0.0",
        "cryptography>=42.0.0",
        "chromadb>=0.5.0",
        "sentence-transformers>=3.0.0",
        "soundfile>=0.12.1",
    ],
    "STT": [
        "faster-whisper>=1.0.0",
    ],
    "Audio": [
        "sounddevice>=0.4.6",
    ],
    "TTS": [
        "gTTS>=2.5.0",
    ],
    "Ricerca": [
        "ddgs>=0.1.0",
        "tavily-python>=0.3.0",
    ],
    "TUI": [
        "textual>=0.60.0",
        "rich>=13.0.0",
    ],
    "Opzionali": [
        "resemblyzer>=0.1.4",
        "speechbrain>=1.0.0",
        "discord.py>=2.3.0",
        "webrtcvad>=2.0.10",
    ],
}

def install_python_deps():
    step(5, 9, "Dipendenze Python")

    pip = pip_cmd()

    info("Aggiorno pip...")
    run(pip + ["install", "--upgrade", "pip", "-q"], check=False)

    # Installa torch con ROCm se disponibile, altrimenti CPU
    print(f"\n   Torch (PyTorch):")
    if ROCM_AVAILABLE and IS_LINUX:
        print(f"      torch ROCm 6.1...", end=" ", flush=True)
        r = run(pip + ["install", "torch", "--index-url",
                       "https://download.pytorch.org/whl/rocm6.1", "-q"],
                check=False, capture=True)
        if r.returncode == 0:
            print(f"{C.GREEN}OK (ROCm — GPU AMD){C.RESET}")
        else:
            print(f"{C.YELLOW}fallback CPU{C.RESET}")
            run(pip + ["install", "torch", "-q"], check=False)
    else:
        print(f"      torch CPU...", end=" ", flush=True)
        r = run(pip + ["install", "torch", "-q"], check=False, capture=True)
        print(f"{C.GREEN}OK{C.RESET}" if r.returncode == 0 else f"{C.RED}ERRORE{C.RESET}")

    optional_groups = {"Opzionali"}

    for group, packages in PIP_PACKAGES.items():
        optional = group in optional_groups
        print(f"\n   {group}{' (opzionali)' if optional else ''}:")
        for pkg in packages:
            name = pkg.split(">=")[0].split("==")[0]
            print(f"      {name}...", end=" ", flush=True)
            r = run(pip + ["install", pkg, "-q"], check=False, capture=True)
            if r.returncode == 0:
                print(f"{C.GREEN}OK{C.RESET}")
            elif optional:
                print(f"{C.YELLOW}(opzionale){C.RESET}")
            else:
                print(f"{C.RED}ERRORE{C.RESET}")
                warn(f"{name} obbligatorio — riprova: pip install {pkg}")

# ════════════════════════════════════════════════════════════════════════════
# STEP 6 — Sposta file + patch OS-specific
# ════════════════════════════════════════════════════════════════════════════

# Patch da applicare ai file Python in base all'OS rilevato
# Ogni entry: (file, stringa_originale, sostituzione_linux, sostituzione_mac, sostituzione_win)
_PATCHES = [
    # Player audio TTS
    (
        "voice_module.py",
        "def find_tts_player():\n    for p in ('mpg123','ffplay','aplay','cvlc'):",
        # Linux — ordine preferenziale con mpg123 prima
        "def find_tts_player():\n    for p in ('mpg123','ffplay','aplay','cvlc'):",
        # macOS — afplay è nativo, ffplay come fallback
        "def find_tts_player():\n    for p in ('afplay','ffplay','mpg123','cvlc'):",
        # Windows — ffplay (ffmpeg), nessun mpg123
        "def find_tts_player():\n    for p in ('ffplay','mpg123','cvlc'):",
    ),
    # Mute mic: pactl disponibile solo su Linux
    (
        "voice_module.py",
        "def _mute_mic(pa_name):",
        # Linux — nessuna modifica
        "def _mute_mic(pa_name):",
        # macOS — sostituisci con osascript
        "def _mute_mic(pa_name):  # macOS — usa osascript",
        # Windows — sostituisci con nircmd/pycaw
        "def _mute_mic(pa_name):  # Windows — mute via nircmd",
    ),
]

# Blocchi completi da sostituire per mute/unmute su Mac e Windows
_MUTE_LINUX = '''def _mute_mic(pa_name):
    if not pa_name: return
    try: subprocess.run(['pactl','set-source-mute',pa_name,'1'],capture_output=True,timeout=2)
    except: pass

def _unmute_mic(pa_name):
    if not pa_name: return
    try: subprocess.run(['pactl','set-source-mute',pa_name,'0'],capture_output=True,timeout=2)
    except: pass'''

_MUTE_MAC = '''def _mute_mic(pa_name):
    try: subprocess.run(['osascript','-e','set volume input volume 0'],capture_output=True,timeout=2)
    except: pass

def _unmute_mic(pa_name):
    try: subprocess.run(['osascript','-e','set volume input volume 100'],capture_output=True,timeout=2)
    except: pass'''

_MUTE_WIN = '''def _mute_mic(pa_name):
    try:
        subprocess.run(['nircmd','mutesysvolume','1'],capture_output=True,timeout=2)
    except:
        pass  # nircmd opzionale su Windows

def _unmute_mic(pa_name):
    try:
        subprocess.run(['nircmd','mutesysvolume','0'],capture_output=True,timeout=2)
    except:
        pass'''

def move_and_patch_files():
    step(6, 8, f"Copia file da {CODE_DIR} → {DEST_DIR}")

    # Crea cartella destinazione
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    ok(f"Cartella: {DEST_DIR}")

    # Verifica che il source directory esista
    if not CODE_DIR.exists():
        err(f"Cartella sorgente non trovata: {CODE_DIR}")
        err(f"Assicurati di eseguire l'installer dalla cartella jarvis-main/code/")
        sys.exit(1)

    # File da copiare (sono nella cartella code/)
    files = [
        "jarvis_v8.py",
        "jarvis_memory_engine.py",
        "voice_module.py",
        "search_module.py",
        "language_module.py",
        "agent_module.py",
        "jarvis_banner.py",
        "jarvis_secrets.py",
        "ssh_module.py",
    ]

    for fname in files:
        src = CODE_DIR / fname
        dst = DEST_DIR / fname
        if src.exists():
            shutil.copy2(src, dst)
            ok(f"Copiato: {fname}")
        else:
            warn(f"Non trovato: {fname} — salta")

    # Copia Modelfile (si trova in REPO_ROOT, non in CODE_DIR)
    if MODELFILE_PATH.exists():
        if MODELFILE_PATH.resolve() != (DEST_DIR / "Modelfile").resolve():
            shutil.copy2(MODELFILE_PATH, DEST_DIR / "Modelfile")
        ok("Copiato: Modelfile")
    else:
        warn(f"Modelfile non trovato in {MODELFILE_PATH}")

    # Applica patch OS-specific
    info(f"Applico patch per {OS}...")
    _apply_os_patches()
    ok("Patch applicate")

def _apply_os_patches():
    """Applica le patch OS-specific ai file copiati in DEST_DIR."""
    voice_file = DEST_DIR / "voice_module.py"
    if not voice_file.exists():
        return

    with open(voice_file, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Player audio
    linux_player = "for p in ('mpg123','ffplay','aplay','cvlc'):"
    mac_player   = "for p in ('afplay','ffplay','mpg123','cvlc'):"
    win_player   = "for p in ('ffplay','mpg123','cvlc'):"

    if IS_MAC and linux_player in content:
        content = content.replace(linux_player, mac_player, 1)
        info("  Player audio: mpg123 → afplay (macOS)")
    elif IS_WINDOWS and linux_player in content:
        content = content.replace(linux_player, win_player, 1)
        info("  Player audio: mpg123 → ffplay (Windows)")

    # 2. Mute/unmute microfono
    if IS_MAC and _MUTE_LINUX in content:
        content = content.replace(_MUTE_LINUX, _MUTE_MAC, 1)
        info("  Mute mic: pactl → osascript (macOS)")
    elif IS_WINDOWS and _MUTE_LINUX in content:
        content = content.replace(_MUTE_LINUX, _MUTE_WIN, 1)
        info("  Mute mic: pactl → nircmd (Windows)")

    with open(voice_file, "w", encoding="utf-8") as f:
        f.write(content)

# ════════════════════════════════════════════════════════════════════════════
# STEP 7 — .env + API key
# ════════════════════════════════════════════════════════════════════════════

def setup_env():
    step(7, 8, "API Key (.env)")

    existing = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if v.strip():
                    existing[k.strip()] = v.strip()
        info(f".env trovato con {len(existing)} chiavi impostate")

    keys = {
        "GROQ_API_KEY": {
            "desc":     "Groq API Key — cervello principale 120B (OBBLIGATORIO per AI cloud)",
            "hint":     "Gratis su https://console.groq.com  →  API Keys  (inizia con gsk_...)",
            "optional": False,
        },
        "CEREBRAS_API_KEY": {
            "desc":     "Cerebras — fallback Groq, llama 70B velocissimo (CONSIGLIATO)",
            "hint":     "Gratis su https://cloud.cerebras.ai  →  API Keys",
            "optional": True,
        },
        "NVIDIA_API_KEY": {
            "desc":     "NVIDIA NIM — Qwen 397B vision + task pesanti (CONSIGLIATO)",
            "hint":     "Gratis su https://build.nvidia.com  →  Settings → API Keys  (nvapi-...)",
            "optional": True,
        },
        "TAVILY_API_KEY": {
            "desc":     "Tavily Search — ricerche web avanzate (CONSIGLIATO)",
            "hint":     "Gratis su https://app.tavily.com  →  API Keys",
            "optional": True,
        },
        "BRAVE_API_KEY": {
            "desc":     "Brave Search (opzionale — 2000 ricerche/mese gratis)",
            "hint":     "https://api.search.brave.com",
            "optional": True,
        },
        "GNEWS_API_KEY": {
            "desc":     "GNews (opzionale — notizie, fallback ANSA RSS se assente)",
            "hint":     "https://gnews.io",
            "optional": True,
        },
        "DISCORD_TOKEN": {
            "desc":     "Discord Bot Token (opzionale)",
            "hint":     "https://discord.com/developers/applications",
            "optional": True,
        },
    }

    new_values = dict(existing)
    print()

    for key, meta in keys.items():
        current = existing.get(key, "")
        tag = f"{C.YELLOW}(opzionale){C.RESET}" if meta["optional"] else f"{C.GREEN}(consigliato){C.RESET}"
        print(f"   {C.BOLD}{key}{C.RESET} {tag}")
        print(f"   {meta['desc']}")
        print(f"   → {meta['hint']}")
        if current:
            print(f"   Valore attuale: {current[:10]}... (invio = mantieni)")
        else:
            print(f"   (invio per saltare)")
        val = input("   > ").strip()
        if val:
            new_values[key] = val
            ok(f"{key} impostata")
        elif current:
            ok(f"{key} mantenuta")
        else:
            status = "disabilitata" if meta["optional"] else "MANCANTE — alcune ricerche useranno DDG"
            warn(f"{key} non impostata — {status}")
        print()

    # Scrivi .env in DEST_DIR
    lines = ["# JARVIS v8.0 — API Keys\n# Non committare questo file!\n"]
    for key, meta in keys.items():
        lines.append(f"# {meta['desc']}")
        lines.append(f"{key}={new_values.get(key, '')}\n")
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")

    if not IS_WINDOWS:
        os.chmod(ENV_FILE, 0o600)

    ok(f".env salvato in {ENV_FILE}")

# ════════════════════════════════════════════════════════════════════════════
# STEP 8 — Script di avvio + modello Ollama
# ════════════════════════════════════════════════════════════════════════════

def create_start_script():
    step(8, 8, "Script di avvio + modello Ollama")

    python = python_cmd()[0]

    # ── Script di avvio ───────────────────────────────────────────────────────
    if IS_WINDOWS:
        script = DEST_DIR / "avvia_jarvis.bat"
        script.write_text(
            f'@echo off\n'
            f'cd /d "{DEST_DIR}"\n'
            f'if exist .env for /f "tokens=1,* delims==" %%a in (.env) do set %%a=%%b\n'
            f'"{python}" jarvis_v8.py %*\n',
            encoding="utf-8"
        )
        ok(f"Script creato: {script}")
    else:
        # Linux / macOS
        env_loader = f'export $(grep -v \'^#\' "{ENV_FILE}" | grep -v \'^$\' | xargs)'
        if IS_LINUX:
            activate = f'source "{VENV_DIR}/bin/activate"'
            hip_fix  = "export HIP_VISIBLE_DEVICES=-1  # AMD GPU fix"
        else:
            activate = "# macOS: nessun venv"
            hip_fix  = ""

        script = DEST_DIR / "avvia_jarvis.sh"
        script.write_text(
            f'#!/bin/bash\n'
            f'cd "{DEST_DIR}"\n'
            f'# ROCm — AMD iGPU per SepFormer (speaker extraction)\n'
            f'export HSA_OVERRIDE_GFX_VERSION=10.3.5\n'
            f'export ROCR_VISIBLE_DEVICES=0\n'
            f'{activate}\n'
            f'if [ -f "{ENV_FILE}" ]; then\n'
            f'    {env_loader}\n'
            f'fi\n'
            f'"{python}" jarvis_v9.py "$@"\n',
            encoding="utf-8"
        )
        os.chmod(script, 0o755)
        ok(f"Script creato: {script}")

        # setup_speaker.sh
        speaker = DEST_DIR / "setup_speaker.sh"
        speaker.write_text(
            f'#!/bin/bash\n'
            f'cd "{DEST_DIR}"\n'
            f'{activate}\n'
            f'"{python}" voice_module.py --setup-speaker\n',
            encoding="utf-8"
        )
        os.chmod(speaker, 0o755)
        ok(f"Script creato: {speaker}")

    # ── Modello Ollama ───────────────────────────────────────────────────────
    if cmd_exists("ollama"):
        modelfile = DEST_DIR / "Modelfile"
        r = run(["ollama", "list"], capture=True, check=False)
        has_model = "jarvisQwen" in (r.stdout if r.returncode == 0 else "") or "jarvisqwen" in (r.stdout.lower() if r.returncode == 0 else "")

        if has_model:
            ok("Modello jarvisQwen già presente")
        else:
            info("Scarico modello base qwen2.5:7b (~4.7GB)...")
            print(f"   {C.YELLOW}Potrebbe richiedere qualche minuto...{C.RESET}")
            try:
                run(["ollama", "pull", "qwen2.5:7b"])
                ok("qwen2.5:7b scaricato")
            except Exception as e:
                warn(f"Pull fallito: {e} — esegui manualmente: ollama pull qwen2.5:7b")

            if modelfile.exists():
                info("Creo modello jarvisQwen...")
                try:
                    run(["ollama", "create", "jarvisQwen", "-f", str(modelfile)])
                    ok("Modello jarvisQwen creato")
                except Exception as e:
                    warn(f"Create fallito: {e}")
                    warn(f"Esegui: ollama create jarvisQwen -f {modelfile}")
            else:
                warn("Modelfile non trovato — crea il modello manualmente")
    else:
        warn("Ollama non disponibile — modello non creato")

# ══════════════════════════════════��═════════════════════════════════════════
# VERIFICA FINALE
# ════════════════════════════════════════════════════════════════════════════

def verify():
    print(f"\n{C.BOLD}Verifica installazione{C.RESET}")
    print("─" * 54)

    python = python_cmd()
    checks = [
        ("requests",       "import requests"),
        ("numpy",          "import numpy"),
        ("faster_whisper", "from faster_whisper import WhisperModel"),
        ("sounddevice",    "import sounddevice"),
        ("soundfile",      "import soundfile"),
        ("scipy",          "import scipy"),
        ("gtts",           "from gtts import gTTS"),
        ("ddgs",           "from ddgs import DDGS"),
        ("textual",        "import textual"),
        ("chromadb",       "import chromadb"),
    ]
    optional = [
        ("torch",          "import torch"),
        ("resemblyzer",    "from resemblyzer import VoiceEncoder"),
        ("speechbrain",    "import speechbrain"),
        ("noisereduce",    "import noisereduce"),
        ("discord",        "import discord"),
        ("tavily",         "from tavily import TavilyClient"),
    ]

    all_ok = True
    print("\n   Obbligatorie:")
    for name, stmt in checks:
        r = run(python + ["-c", stmt], check=False, capture=True)
        if r.returncode == 0:
            print(f"   {C.GREEN}✅{C.RESET} {name}")
        else:
            print(f"   {C.RED}❌{C.RESET} {name}")
            all_ok = False

    print("\n   Opzionali:")
    for name, stmt in optional:
        r = run(python + ["-c", stmt], check=False, capture=True)
        sym = f"{C.GREEN}✅{C.RESET}" if r.returncode == 0 else f"{C.YELLOW}⚠️ {C.RESET}"
        print(f"   {sym} {name}")

    return all_ok

# ════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════════════

def print_summary(success):
    print("\n" + "═" * 54)
    if success:
        print(f"{C.GREEN}{C.BOLD}✅ JARVIS installato con successo!{C.RESET}")
    else:
        print(f"{C.YELLOW}{C.BOLD}⚠️  Installazione completata con avvisi{C.RESET}")
    print("═" * 54)

    if IS_WINDOWS:
        avvia = f'cd "{DEST_DIR}" && avvia_jarvis.bat'
    else:
        avvia = f'{DEST_DIR}/avvia_jarvis.sh'

    print(f"""
{C.BOLD}File installati in:{C.RESET} {DEST_DIR}

{C.BOLD}Avvia JARVIS:{C.RESET}
   {C.BLUE}{avvia}{C.RESET}

{C.BOLD}(Opzionale) Configura riconoscimento vocale:{C.RESET}
   {C.BLUE}{DEST_DIR}/setup_speaker.sh{C.RESET}

{C.BOLD}(Opzionale) SearXNG ricerche locali:{C.RESET}
   {C.BLUE}docker compose up -d{C.RESET}
""")

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 54)
    print(f"{C.BOLD}{C.CYAN}JARVIS v9.0 — Installer{C.RESET}")
    print(f"   Sistema: {OS} {platform.release()}")
    print(f"   Python:  {sys.executable}")
    print("=" * 54)

    try:
        check_system()
        install_ollama()
        install_system_packages()
        create_venv()
        install_python_deps()
        move_and_patch_files()
        setup_env()
        create_start_script()
        ok_result = verify()
        print_summary(ok_result)

    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}Installazione interrotta.{C.RESET}")
        sys.exit(1)
    except Exception as e:
        err(f"Errore imprevisto: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
