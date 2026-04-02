#!/usr/bin/env python3
"""
JARVIS — Installer
==================
Crea un ambiente virtuale (jarvisenv), installa tutte le dipendenze
Python necessarie e guida l'utente nella configurazione delle API key.

Uso:
    python installer.py

Al termine troverai:
    - jarvisenv/          → ambiente virtuale con tutte le dipendenze
    - .env                → file con le tue API key (NON committare su git!)
    - avvia_jarvis.sh     → script di avvio rapido
"""

import os
import sys
import subprocess
import shutil
import platform
from pathlib import Path


# ─── Colori per il terminale ──────────────────────────────────────────────────
# Usati per rendere l'output più leggibile durante l'installazione
class C:
    OK    = "\033[92m"   # verde
    WARN  = "\033[93m"   # giallo
    ERR   = "\033[91m"   # rosso
    BOLD  = "\033[1m"
    CYAN  = "\033[96m"
    RESET = "\033[0m"

def ok(msg):   print(f"{C.OK}✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.WARN}⚠️  {msg}{C.RESET}")
def err(msg):  print(f"{C.ERR}❌ {msg}{C.RESET}")
def info(msg): print(f"{C.CYAN}ℹ️  {msg}{C.RESET}")
def bold(msg): print(f"{C.BOLD}{msg}{C.RESET}")


# ─── Dipendenze Python richieste ──────────────────────────────────────────────
# Suddivise per categoria per chiarezza. Ogni riga è:
#   "nome-pip"  →  da installare con pip install <nome-pip>
#
# CORE — obbligatorie, JARVIS non parte senza queste
DEPS_CORE = [
    "requests",           # HTTP client — usato ovunque per API e ricerche
    "tenacity",           # retry automatico con backoff esponenziale (search_module)
]

# VOCE — necessarie solo se vuoi usare il microfono e la sintesi vocale
DEPS_VOICE = [
    "faster-whisper",     # STT: trascrizione vocale offline con Whisper
    "sounddevice",        # cattura audio dal microfono
    "soundfile",          # lettura/scrittura file audio
    "numpy",              # elaborazione array audio
    "gtts",               # TTS: sintesi vocale tramite Google Translate (online)
]

# RICERCA — necessarie per le funzioni di ricerca web
DEPS_SEARCH = [
    "duckduckgo_search",  # backend DDG per ricerche senza API key
]

# OPZIONALI — migliorano le funzionalità ma non sono obbligatorie
DEPS_OPTIONAL = [
    "resemblyzer",        # speaker verification: riconosce la voce del proprietario
    "discord.py",         # bot Discord integrato in JARVIS
    "scipy",              # resampling audio di qualità superiore
    "Pillow",             # screenshot e analisi immagini
]

# Tutte le dipendenze in un unico dizionario per l'installazione
ALL_DEPS = {
    "🔧 Core (obbligatorie)":    (DEPS_CORE,     True),
    "🎙️  Voce e STT/TTS":        (DEPS_VOICE,    True),
    "🔍 Ricerca web":            (DEPS_SEARCH,   True),
    "🔧 Opzionali (consigliate)": (DEPS_OPTIONAL, False),
}


# ─── Variabili d'ambiente (API key) ──────────────────────────────────────────
# Ogni entry è: (NOME_VAR, descrizione, url_per_registrarsi, obbligatoria)
ENV_VARS = [
    (
        "DISCORD_TOKEN",
        "Token bot Discord",
        "https://discord.com/developers/applications → New Application → Bot → Reset Token",
        False,   # opzionale: Discord non è necessario per usare JARVIS
    ),
    (
        "BRAVE_API_KEY",
        "Brave Search API key",
        "https://api.search.brave.com/ → piano gratuito disponibile",
        False,   # opzionale: DDG viene usato come fallback gratuito
    ),
    (
        "GNEWS_API_KEY",
        "GNews API key (notizie)",
        "https://gnews.io/ → piano gratuito con 100 req/giorno",
        False,   # opzionale: ANSA RSS viene usato come fallback gratuito
    ),
    (
        "TAVILY_API_KEY",
        "Tavily Search API key (risultati AI-ready)",
        "https://tavily.com/ → piano gratuito disponibile",
        False,   # opzionale: usata solo se disponibile, altrimenti SearXNG/DDG
    ),
]


# ─── Funzioni di utilità ─────────────────────────────────────────────────────

def check_python_version():
    """Verifica che Python sia >= 3.10 (richiesto per type hints moderne)."""
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 10):
        err(f"Python {major}.{minor} non supportato. Serve Python 3.10 o superiore.")
        sys.exit(1)
    ok(f"Python {major}.{minor} — OK")


def check_system():
    """Avvisa se il sistema operativo non è Linux (JARVIS è ottimizzato per Linux)."""
    system = platform.system()
    if system != "Linux":
        warn(f"Sistema rilevato: {system}. JARVIS è ottimizzato per Linux.")
        warn("Alcune funzioni (voce, PulseAudio, systemctl) potrebbero non funzionare.")
    else:
        ok(f"Sistema: {system} — OK")


def check_ollama():
    """Controlla se Ollama è installato e in esecuzione."""
    if shutil.which("ollama"):
        ok("Ollama installato")
        try:
            import urllib.request
            urllib.request.urlopen("http://localhost:11434", timeout=2)
            ok("Ollama in esecuzione")
        except Exception:
            warn("Ollama installato ma non in esecuzione.")
            warn("Avvialo con: ollama serve  oppure  sudo systemctl start ollama")
    else:
        warn("Ollama non trovato. JARVIS non funzionerà senza Ollama.")
        info("Installa Ollama da: https://ollama.ai/download")
        info("Poi crea il modello con: ollama create jarvisQwen -f Modelfile")


def check_audio_tools():
    """Controlla se sono presenti i tool audio di sistema (PulseAudio, mpg123)."""
    tools = {
        "pactl":    "PulseAudio (gestione audio)",
        "parecord": "PulseAudio recording",
        "mpg123":   "player MP3 per TTS (installa con: sudo apt install mpg123)",
    }
    for tool, desc in tools.items():
        if shutil.which(tool):
            ok(f"{tool} — {desc}")
        else:
            warn(f"{tool} non trovato — {desc}")


def create_venv(venv_path: Path) -> Path:
    """Crea l'ambiente virtuale Python in jarvisenv/."""
    if venv_path.exists():
        warn(f"Ambiente virtuale già esistente in '{venv_path}' — lo uso così com'è.")
        warn("Se vuoi ricrearlo da zero, cancella la cartella jarvisenv/ e rilancia l'installer.")
    else:
        bold(f"\n📦 Creo ambiente virtuale in '{venv_path}'...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        ok(f"Ambiente virtuale creato in '{venv_path}'")

    # Ritorna il path del python dentro l'env
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def install_deps(python_bin: Path, deps: list, label: str, optional: bool = False):
    """Installa una lista di pacchetti pip nell'ambiente virtuale."""
    bold(f"\n{label}")
    failed = []
    for pkg in deps:
        print(f"  📥 {pkg}...", end="", flush=True)
        result = subprocess.run(
            [str(python_bin), "-m", "pip", "install", pkg, "-q", "--upgrade"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f" {C.OK}✅{C.RESET}")
        else:
            print(f" {C.ERR}❌{C.RESET}")
            failed.append(pkg)
            if not optional:
                warn(f"  Errore: {result.stderr.strip()[:200]}")

    if failed:
        if optional:
            warn(f"Pacchetti opzionali non installati: {', '.join(failed)}")
            info("Puoi installarli manualmente in seguito se ne hai bisogno.")
        else:
            err(f"Pacchetti obbligatori non installati: {', '.join(failed)}")
            err("JARVIS potrebbe non funzionare correttamente.")
    else:
        ok(f"Tutti i pacchetti installati correttamente.")


def ask_api_keys() -> dict:
    """
    Chiede interattivamente le API key all'utente.
    Le key opzionali possono essere saltate premendo Invio.
    Ritorna un dizionario {NOME_VAR: valore}.
    """
    bold("\n🔑 Configurazione API Key")
    print("─" * 52)
    print("Le API key vengono salvate in un file .env nella cartella del progetto.")
    print("Il file .env NON viene committato su git (è nel .gitignore).")
    print("Premi Invio per saltare le key opzionali.\n")

    existing_env = {}
    env_file = Path(".env")
    if env_file.exists():
        # Carica le key già presenti nel .env per non sovrascriverle
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing_env[k.strip()] = v.strip()

    result = {}
    for var_name, description, url, required in ENV_VARS:
        current = existing_env.get(var_name, "")
        label = f"{'[OBBLIGATORIA]' if required else '[opzionale]'}"

        if current:
            # Mostra solo i primi/ultimi caratteri per sicurezza
            masked = current[:4] + "..." + current[-4:] if len(current) > 8 else "***"
            print(f"🔑 {description} ({label})")
            print(f"   Valore attuale: {masked}")
            print(f"   Premi Invio per mantenere, oppure inserisci un nuovo valore.")
            print(f"   Registrazione: {url}")
            val = input(f"   {var_name}: ").strip()
            result[var_name] = val if val else current
        else:
            print(f"🔑 {description} ({label})")
            print(f"   Registrazione: {url}")
            val = input(f"   {var_name} (Invio per saltare): ").strip()
            result[var_name] = val

        if result[var_name]:
            ok(f"{var_name} configurata")
        else:
            if required:
                warn(f"{var_name} non configurata — potrebbe causare errori")
            else:
                info(f"{var_name} non configurata — verrà usato il fallback gratuito")
        print()

    return result


def write_env_file(api_keys: dict):
    """Scrive il file .env con tutte le API key configurate."""
    env_file = Path(".env")
    lines = [
        "# JARVIS — configurazione API key",
        "# NON committare questo file su git!",
        "# Viene caricato automaticamente da JARVIS all'avvio.",
        "",
        "# ── Discord Bot ──────────────────────────────────────────",
        "# Necessario solo se vuoi il bot Discord integrato.",
        "# Ottieni il token su: https://discord.com/developers/applications",
        f"DISCORD_TOKEN={api_keys.get('DISCORD_TOKEN', '')}",
        "",
        "# ── Ricerche Web ─────────────────────────────────────────",
        "# Brave Search: risultati web di qualità (piano gratuito disponibile)",
        "# https://api.search.brave.com/",
        f"BRAVE_API_KEY={api_keys.get('BRAVE_API_KEY', '')}",
        "",
        "# GNews: notizie in tempo reale (100 richieste/giorno gratis)",
        "# https://gnews.io/",
        f"GNEWS_API_KEY={api_keys.get('GNEWS_API_KEY', '')}",
        "",
        "# Tavily: motore di ricerca ottimizzato per AI (piano gratuito disponibile)",
        "# https://tavily.com/",
        f"TAVILY_API_KEY={api_keys.get('TAVILY_API_KEY', '')}",
        "",
    ]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    ok(f"File .env scritto in '{env_file.resolve()}'")


def write_launch_script(venv_path: Path):
    """Crea uno script shell per avviare JARVIS con l'ambiente virtuale corretto."""
    script = Path("avvia_jarvis.sh")
    content = f"""#!/bin/bash
# Script di avvio JARVIS
# Attiva l'ambiente virtuale e avvia jarvis_v6.py

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
cd "$SCRIPT_DIR"

# Carica le API key dal file .env se esiste
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Attiva l'ambiente virtuale
source "{venv_path}/bin/activate"

# Avvia JARVIS
python jarvis_v6.py "$@"
"""
    script.write_text(content, encoding="utf-8")
    script.chmod(0o755)  # rende lo script eseguibile
    ok(f"Script di avvio creato: {script.resolve()}")
    info("Puoi avviare JARVIS con: ./avvia_jarvis.sh")


def write_gitignore():
    """Crea o aggiorna il .gitignore per escludere file sensibili e temporanei."""
    gitignore = Path(".gitignore")
    entries_needed = [
        "# Ambiente virtuale",
        "jarvisenv/",
        ".venv/",
        "venv/",
        "",
        "# API key e segreti — NON committare mai questo file!",
        ".env",
        "*.env",
        "",
        "# Dati personali di JARVIS (memoria, profilo vocale)",
        "jarvis_memory/",
        "speaker_profile.npy",
        "",
        "# Python",
        "__pycache__/",
        "*.pyc",
        "*.pyo",
        "*.pyd",
        ".Python",
        "*.egg-info/",
        "dist/",
        "build/",
        "",
        "# Editor e OS",
        ".DS_Store",
        ".idea/",
        ".vscode/",
        "*.swp",
        "*.swo",
        "Thumbs.db",
    ]

    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    to_add = [e for e in entries_needed if e and e not in existing]

    if to_add:
        with gitignore.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(entries_needed) + "\n")
        ok(f".gitignore aggiornato")
    else:
        ok(f".gitignore già aggiornato")


def write_requirements(venv_python: Path):
    """Genera requirements.txt dall'ambiente virtuale installato."""
    result = subprocess.run(
        [str(venv_python), "-m", "pip", "freeze"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        Path("requirements.txt").write_text(result.stdout, encoding="utf-8")
        ok("requirements.txt generato")
    else:
        warn("Impossibile generare requirements.txt automaticamente")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 52)
    print(f"{C.BOLD}🚀 JARVIS — Installer{C.RESET}")
    print("=" * 52)
    print("Questo script prepara l'ambiente per eseguire JARVIS.")
    print("Verrà creato un ambiente virtuale Python isolato (jarvisenv)")
    print("e installate tutte le dipendenze necessarie.\n")

    # ── 1. Verifiche preliminari ──────────────────────────────────────────────
    bold("🔍 Verifiche di sistema")
    print("─" * 52)
    check_python_version()
    check_system()
    check_ollama()
    check_audio_tools()

    # ── 2. Ambiente virtuale ──────────────────────────────────────────────────
    venv_path = Path("jarvisenv")
    venv_python = create_venv(venv_path)

    # Aggiorna pip nell'env prima di installare
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"],
        capture_output=True
    )

    # ── 3. Installazione dipendenze ───────────────────────────────────────────
    for label, (deps, required) in ALL_DEPS.items():
        install_deps(venv_python, deps, label, optional=not required)

    # ── 4. API Key ────────────────────────────────────────────────────────────
    api_keys = ask_api_keys()
    write_env_file(api_keys)

    # ── 5. File di supporto ───────────────────────────────────────────────────
    bold("\n📁 Creazione file di supporto")
    print("─" * 52)
    write_gitignore()
    write_launch_script(venv_path)
    write_requirements(venv_python)

    # ── 6. Riepilogo finale ───────────────────────────────────────────────────
    print("\n" + "=" * 52)
    bold("✅ Installazione completata!")
    print("=" * 52)
    print()
    info("Per avviare JARVIS:")
    print(f"   {C.BOLD}./avvia_jarvis.sh{C.RESET}          ← modo raccomandato (carica .env automaticamente)")
    print()
    print(f"   oppure manualmente:")
    print(f"   {C.BOLD}source jarvisenv/bin/activate{C.RESET}")
    print(f"   {C.BOLD}python jarvis_v6.py{C.RESET}")
    print()

    # Avvisi su dipendenze di sistema mancanti
    missing_tools = [t for t in ("pactl", "mpg123") if not shutil.which(t)]
    if missing_tools:
        warn("Tool di sistema mancanti per la voce:")
        print(f"   sudo apt install {' '.join(missing_tools)}")
        print()

    if not shutil.which("ollama"):
        warn("Ollama non installato — JARVIS non funzionerà senza!")
        info("Installa Ollama: https://ollama.ai/download")
        info("Poi crea il modello: ollama create jarvisQwen -f Modelfile")
        print()

    print("📖 Per maggiori informazioni leggi il README.md")
    print("=" * 52 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Installazione interrotta dall'utente.")
        sys.exit(1)
    except Exception as e:
        err(f"Errore imprevisto: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
