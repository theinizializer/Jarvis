#!/usr/bin/env python3
"""
JARVIS v6.0 — Installer
Crea l'ambiente virtuale jarvisenv, installa tutte le dipendenze,
configura le API key, crea avvia_jarvis.sh e verifica il setup.
"""

import os
import sys
import json
import shutil
import subprocess
import platform
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════════════════

VENV_NAME    = "jarvisenv"
ENV_FILE     = ".env"
START_SCRIPT = "avvia_jarvis.sh"
PYTHON_MIN   = (3, 10)

# Dipendenze pip divise per categoria
PIP_CORE = [
    "requests>=2.31.0",
    "numpy>=1.24.0",
    "scipy>=1.11.0",
]

PIP_STT = [
    "faster-whisper>=1.0.0",
]

PIP_AUDIO = [
    "sounddevice>=0.4.6",
    "soundfile>=0.12.1",
]

PIP_TTS = [
    "gTTS>=2.5.0",
]

PIP_VOICE_OPTIONAL = [
    "resemblyzer>=0.1.4",   # speaker verification
    "webrtcvad>=2.0.10",    # VAD
]

PIP_SEARCH = [
    "ddgs>=0.1.0",
    "tavily-python>=0.3.0",
]

PIP_DISCORD = [
    "discord.py>=2.3.0",
]

# Pacchetti sistema (apt)
APT_REQUIRED = [
    ("mpg123",           "mpg123",           "TTS audio player"),
    ("parecord",         "pulseaudio-utils",  "microphone recording"),
    ("docker",           "docker.io",         "SearXNG local search (optional)"),
]

# ══════════════════════════════════════════════════════════════════════════════
# COLORI TERMINALE
# ══════════════════════════════════════════════════════════════════════════════

class C:
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    BLUE   = "\033[94m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def ok(msg):   print(f"{C.GREEN}✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}⚠️  {msg}{C.RESET}")
def err(msg):  print(f"{C.RED}❌ {msg}{C.RESET}")
def info(msg): print(f"{C.BLUE}ℹ️  {msg}{C.RESET}")
def bold(msg): print(f"{C.BOLD}{msg}{C.RESET}")

def step(n, total, msg):
    print(f"\n{C.BOLD}[{n}/{total}] {msg}{C.RESET}")
    print("─" * 50)

# ══════════════════════════════════════════════════════════════════════════════
# UTILITÀ
# ══════════════════════════════════════════════════════════════════════════════

def run(cmd, check=True, capture=False, input_text=None):
    kwargs = dict(check=check)
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    if input_text is not None:
        kwargs["input"] = input_text
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)

def cmd_exists(name):
    return shutil.which(name) is not None

def python_in_venv():
    venv = Path(VENV_NAME)
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"

def pip_in_venv():
    venv = Path(VENV_NAME)
    if platform.system() == "Windows":
        return venv / "Scripts" / "pip.exe"
    return venv / "bin" / "pip"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Controllo sistema
# ══════════════════════════════════════════════════════════════════════════════

def check_system():
    step(1, 7, "Controllo sistema")

    # Python version
    v = sys.version_info
    if (v.major, v.minor) < PYTHON_MIN:
        err(f"Python {PYTHON_MIN[0]}.{PYTHON_MIN[1]}+ richiesto, trovato {v.major}.{v.minor}")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")

    # OS
    os_name = platform.system()
    ok(f"Sistema: {os_name} {platform.release()}")
    if os_name == "Windows":
        warn("Windows: alcune funzionalità vocali potrebbero non funzionare")

    # Ollama
    if cmd_exists("ollama"):
        try:
            r = run(["ollama", "--version"], capture=True, check=False)
            ver = r.stdout.strip() if r.returncode == 0 else "?"
            ok(f"Ollama: {ver}")
        except Exception:
            ok("Ollama: installato")
    else:
        err("Ollama non trovato!")
        print("   Installa da: https://ollama.com")
        if input("   Continuare comunque? (s/N): ").strip().lower() != "s":
            sys.exit(1)

    # Git
    if cmd_exists("git"):
        ok("Git: disponibile")
    else:
        warn("Git non trovato — non necessario per l'installazione")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Pacchetti sistema (apt)
# ══════════════════════════════════════════════════════════════════════════════

def install_system_packages():
    step(2, 7, "Pacchetti sistema (apt)")

    if platform.system() != "Linux":
        warn("Pacchetti apt disponibili solo su Linux — salta")
        return

    if not cmd_exists("apt"):
        warn("apt non trovato — salta installazione pacchetti sistema")
        return

    to_install = []
    for cmd, pkg, desc in APT_REQUIRED:
        if cmd_exists(cmd):
            ok(f"{pkg}: già installato ({desc})")
        else:
            warn(f"{pkg}: mancante ({desc})")
            to_install.append(pkg)

    if not to_install:
        return

    print(f"\n   Pacchetti da installare: {', '.join(to_install)}")
    answer = input("   Installare con apt? (S/n): ").strip().lower()
    if answer in ("", "s", "y", "yes", "si", "sì"):
        try:
            run(["sudo", "apt", "update", "-qq"])
            run(["sudo", "apt", "install", "-y"] + to_install)
            ok(f"Pacchetti installati: {', '.join(to_install)}")
        except Exception as e:
            warn(f"Installazione apt fallita: {e}")
            warn("Installa manualmente: sudo apt install " + " ".join(to_install))
    else:
        warn("Saltato — installa manualmente se necessario")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Virtualenv jarvisenv
# ══════════════════════════════════════════════════════════════════════════════

def create_venv():
    step(3, 7, f"Ambiente virtuale ({VENV_NAME})")

    venv_path = Path(VENV_NAME)

    if venv_path.exists():
        answer = input(f"   {VENV_NAME}/ esiste già. Ricrearlo? (s/N): ").strip().lower()
        if answer in ("s", "y", "yes", "si"):
            info(f"Rimuovo {VENV_NAME}/...")
            shutil.rmtree(venv_path)
        else:
            ok(f"{VENV_NAME}/ esistente — mantenuto")
            return

    info(f"Creo {VENV_NAME}/...")
    try:
        run([sys.executable, "-m", "venv", VENV_NAME])
        ok(f"{VENV_NAME}/ creato")
    except Exception as e:
        err(f"Creazione venv fallita: {e}")
        info("Prova: sudo apt install python3-venv")
        sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Dipendenze Python
# ══════════════════════════════════════════════════════════════════════════════

def install_python_deps():
    step(4, 7, "Dipendenze Python")

    pip = str(pip_in_venv())

    # Aggiorna pip
    info("Aggiorno pip...")
    run([pip, "install", "--upgrade", "pip", "-q"])

    def install_group(name, packages, optional=False):
        print(f"\n   📦 {name}:")
        failed = []
        for pkg in packages:
            pkg_name = pkg.split(">=")[0].split("==")[0]
            print(f"      {pkg_name}...", end=" ", flush=True)
            try:
                r = run([pip, "install", pkg, "-q"], check=False, capture=True)
                if r.returncode == 0:
                    print(f"{C.GREEN}✅{C.RESET}")
                else:
                    print(f"{C.YELLOW}⚠️{C.RESET}")
                    failed.append(pkg)
            except Exception as e:
                print(f"{C.RED}❌{C.RESET}")
                failed.append(pkg)

        if failed and not optional:
            warn(f"Pacchetti falliti: {failed}")
        elif failed and optional:
            warn(f"Opzionali non installati: {failed} — alcune funzionalità disabilitate")
        return failed

    install_group("Core",               PIP_CORE)
    install_group("STT (Whisper)",       PIP_STT)
    install_group("Audio I/O",           PIP_AUDIO)
    install_group("TTS",                 PIP_TTS)
    install_group("Ricerca web",         PIP_SEARCH)
    install_group("Voice opzionali",     PIP_VOICE_OPTIONAL, optional=True)

    # Discord — chiede se installare
    print(f"\n   💙 Discord Bot (opzionale)")
    answer = input("      Installare discord.py? (s/N): ").strip().lower()
    if answer in ("s", "y", "yes", "si"):
        install_group("Discord", PIP_DISCORD)

    ok("Dipendenze Python installate")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — File .env con API key
# ══════════════════════════════════════════════════════════════════════════════

def setup_env():
    step(5, 7, "Configurazione API key (.env)")

    env_path = Path(ENV_FILE)

    # Carica valori esistenti
    existing = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
        info(f".env esistente trovato — aggiorno solo i campi vuoti")

    keys = {
        "DISCORD_TOKEN": {
            "desc":     "Discord Bot Token (opzionale — per usare JARVIS da Discord)",
            "hint":     "https://discord.com/developers/applications",
            "optional": True,
        },
        "TAVILY_API_KEY": {
            "desc":     "Tavily AI Search (opzionale — 1000 ricerche/mese gratis)",
            "hint":     "https://tavily.com",
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
    }

    new_values = dict(existing)

    print()
    for key, meta in keys.items():
        current = existing.get(key, "")
        print(f"   {C.BOLD}{key}{C.RESET}")
        print(f"   {meta['desc']}")
        print(f"   → {meta['hint']}")

        if current:
            print(f"   Valore attuale: {current[:8]}... (invio per mantenere)")
        else:
            print(f"   (invio per saltare)")

        val = input(f"   > ").strip()
        if val:
            new_values[key] = val
            ok(f"{key} impostata")
        elif current:
            ok(f"{key} mantenuta")
        else:
            warn(f"{key} non impostata — funzionalità opzionale disabilitata")
        print()

    # Scrivi .env
    lines = ["# JARVIS v6.0 — API Keys\n# Non committare questo file su git!\n"]
    for key, meta in keys.items():
        lines.append(f"# {meta['desc']}")
        val = new_values.get(key, "")
        lines.append(f"{key}={val}\n")

    env_path.write_text("\n".join(lines))

    # Proteggi il file (solo proprietario può leggere)
    if platform.system() != "Windows":
        os.chmod(env_path, 0o600)

    ok(f".env creato (permessi 600)")

    # Verifica .gitignore
    gitignore = Path(".gitignore")
    if gitignore.exists():
        content = gitignore.read_text()
        if ".env" not in content:
            gitignore.write_text(content + "\n.env\n")
            ok(".env aggiunto a .gitignore")
    else:
        gitignore.write_text(".env\njarvisenv/\n__pycache__/\n*.pyc\n")
        ok(".gitignore creato")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Script di avvio
# ══════════════════════════════════════════════════════════════════════════════

def create_start_script():
    step(6, 7, f"Script di avvio ({START_SCRIPT})")

    script_path = Path(START_SCRIPT)
    project_dir = Path.cwd()
    python_path = python_in_venv().resolve()

    script = f"""#!/bin/bash
# JARVIS v6.0 — Script di avvio
# Generato automaticamente dall'installer

cd "{project_dir}"

# AMD GPU: evita errori HIP con resemblyzer/torch
export HIP_VISIBLE_DEVICES=-1

# Carica variabili .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Avvia JARVIS
"{python_path}" jarvis_v6.py "$@"
"""

    script_path.write_text(script)

    if platform.system() != "Windows":
        os.chmod(script_path, 0o755)
        ok(f"{START_SCRIPT} creato (eseguibile)")
    else:
        # Windows: crea anche .bat
        bat = Path("avvia_jarvis.bat")
        bat.write_text(f'@echo off\ncd /d "{project_dir}"\n"{python_path}" jarvis_v6.py %*\n')
        ok(f"{START_SCRIPT} e avvia_jarvis.bat creati")

    # Script per setup speaker verification
    speaker_script = Path("setup_speaker.sh")
    speaker_script.write_text(f"""#!/bin/bash
# Configura il riconoscimento vocale del proprietario
cd "{project_dir}"
export HIP_VISIBLE_DEVICES=-1
"{python_path}" voice_module.py --setup-speaker
""")
    if platform.system() != "Windows":
        os.chmod(speaker_script, 0o755)
    ok("setup_speaker.sh creato")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Verifica finale
# ══════════════════════════════════════════════════════════════════════════════

def verify_installation():
    step(7, 7, "Verifica installazione")

    python = str(python_in_venv())
    all_ok = True

    checks = [
        ("requests",       "import requests"),
        ("numpy",          "import numpy"),
        ("faster_whisper", "from faster_whisper import WhisperModel"),
        ("sounddevice",    "import sounddevice"),
        ("soundfile",      "import soundfile"),
        ("gtts",           "from gtts import gTTS"),
        ("scipy",          "import scipy"),
        ("ddgs",           "from ddgs import DDGS"),
    ]

    optional_checks = [
        ("resemblyzer",    "from resemblyzer import VoiceEncoder"),
        ("webrtcvad",      "import webrtcvad"),
        ("discord",        "import discord"),
        ("tavily",         "from tavily import TavilyClient"),
    ]

    print("\n   Dipendenze obbligatorie:")
    for name, import_stmt in checks:
        r = run([python, "-c", import_stmt], check=False, capture=True)
        if r.returncode == 0:
            print(f"   {C.GREEN}✅{C.RESET} {name}")
        else:
            print(f"   {C.RED}❌{C.RESET} {name} — MANCANTE")
            all_ok = False

    print("\n   Dipendenze opzionali:")
    for name, import_stmt in optional_checks:
        r = run([python, "-c", import_stmt], check=False, capture=True)
        if r.returncode == 0:
            print(f"   {C.GREEN}✅{C.RESET} {name}")
        else:
            print(f"   {C.YELLOW}⚠️{C.RESET}  {name} — non installato (opzionale)")

    # Verifica Ollama
    print("\n   Servizi:")
    if cmd_exists("ollama"):
        r = run(["ollama", "list"], check=False, capture=True)
        if r.returncode == 0:
            models = [l for l in r.stdout.splitlines() if "jarvisQwen" in l or "qwen" in l.lower()]
            if models:
                print(f"   {C.GREEN}✅{C.RESET} Ollama + modello trovato")
            else:
                print(f"   {C.YELLOW}⚠️{C.RESET}  Ollama ok ma nessun modello JARVIS trovato")
                print(f"         Esegui: ollama pull qwen2.5:7b && ollama create jarvisQwen -f Modelfile")
        else:
            print(f"   {C.YELLOW}⚠️{C.RESET}  Ollama installato ma non in esecuzione")
    else:
        print(f"   {C.RED}❌{C.RESET} Ollama non trovato")
        all_ok = False

    return all_ok

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(success):
    print("\n" + "═" * 52)
    if success:
        print(f"{C.GREEN}{C.BOLD}✅ JARVIS installato con successo!{C.RESET}")
    else:
        print(f"{C.YELLOW}{C.BOLD}⚠️  Installazione completata con avvisi{C.RESET}")
    print("═" * 52)

    print(f"""
{C.BOLD}Prossimi passi:{C.RESET}

1. Crea il modello Ollama (se non già fatto):
   {C.BLUE}ollama pull qwen2.5:7b
   ollama create jarvisQwen -f Modelfile{C.RESET}

2. Avvia JARVIS:
   {C.BLUE}./{START_SCRIPT}{C.RESET}

3. (Opzionale) Configura riconoscimento vocale:
   {C.BLUE}./setup_speaker.sh{C.RESET}

4. (Opzionale) Avvia SearXNG per ricerche locali:
   {C.BLUE}docker compose up -d{C.RESET}

{C.BOLD}File creati:{C.RESET}
   📁 {VENV_NAME}/          virtualenv Python
   🔑 {ENV_FILE}              API keys (non committare!)
   🚀 {START_SCRIPT}    script di avvio
   🎙️  setup_speaker.sh  configura voce
""")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("═" * 52)
    print(f"{C.BOLD}🤖 JARVIS v6.0 — Installer{C.RESET}")
    print("═" * 52)
    print(f"Directory: {Path.cwd()}")
    print(f"Python: {sys.executable}")
    print()

    # Verifica che siamo nella directory giusta
    if not Path("jarvis_v6.py").exists():
        err("jarvis_v6.py non trovato!")
        err("Esegui l'installer dalla directory del progetto JARVIS.")
        sys.exit(1)

    try:
        check_system()
        install_system_packages()
        create_venv()
        install_python_deps()
        setup_env()
        create_start_script()
        success = verify_installation()
        print_summary(success)

    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}Installazione interrotta dall'utente.{C.RESET}")
        sys.exit(1)
    except Exception as e:
        err(f"Errore imprevisto: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
