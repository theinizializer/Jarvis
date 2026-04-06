"""
jarvis_banner.py — Banner ASCII art stile neofetch per JARVIS v6.0
Importa e chiama print_banner(stats_dict) all'avvio di jarvis_v6.py
"""

import os
import platform
import datetime
import subprocess
from pathlib import Path

# ── Colori ANSI ───────────────────────────────────────────────────────────────
CYAN  = "\033[96m"
GOLD  = "\033[93m"
GREEN = "\033[92m"
RED   = "\033[91m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RST   = "\033[0m"

# ── Logo JARVIS (16 righe, allineato a sinistra) ──────────────────────────────
_LOGO = [
    r"          ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗",
    r"          ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝",
    r"          ██║███████║██████╔╝██║   ██║██║███████╗",
    r"     ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║",
    r"     ╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║",
    r"      ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝",
    r"",
    r"     ─── Just A Rather Very Intelligent System ───",
    r"",
    r"                ╔═══════════════════╗",
    r"                ║  █▀▀▀▀▀▀▀▀▀▀▀▀▀█  ║",
    r"                ║  █ ╔══╗   ╔══╗ █  ║",
    r"                ║  █ ║██║   ║██║ █  ║",
    r"                ║  █ ╚══╝   ╚══╝ █  ║",
    r"                ║  █  ╚═══════╝  █  ║",
    r"                ║  █▄▄▄▄▄▄▄▄▄▄▄▄▄█  ║",
    r"                ╚═══════════════════╝",
]

# Righe con colore rosso (la "faccina" di JARVIS)
_RED_LINES = {10, 11, 12, 13, 14, 15}


def _get_uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        mins = int(secs // 60) % 60
        hrs  = int(secs // 3600)
        days = int(secs // 86400)
        if days > 0:
            return f"{days}g {hrs}h"
        if hrs > 0:
            return f"{hrs}h {mins}m"
        return f"{mins} min"
    except Exception:
        return "n/d"


def _get_ram() -> str:
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        total = used = 0
        for line in lines:
            if line.startswith("MemTotal"):
                total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable"):
                avail = int(line.split()[1]) // 1024
        used = total - avail
        return f"{used} MiB / {total} MiB"
    except Exception:
        return "n/d"


def _get_cpu() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    # Accorcia per non sforare la colonna
                    name = name.replace("with Radeon Graphics", "").strip()
                    return name[:45]
    except Exception:
        pass
    return platform.processor() or "n/d"


def _get_kernel() -> str:
    try:
        return platform.release()
    except Exception:
        return "n/d"


def _get_os() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME"):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return platform.system()


def _ok(flag: bool) -> str:
    return f"{GREEN}✔ attivo{RST}" if flag else f"{RED}✘ offline{RST}"


def print_banner(
    model: str = "jarvisQwen",
    tts_on: bool = False,
    discord_on: bool = False,
    memory_count: int = 0,
    search_ok: bool = False,
    ollama_ok: bool = True,
    speaker_ok: bool = False,
    lang: str = "it",
    search_engines: str = "",
):
    """
    Stampa il banner JARVIS stile neofetch.

    Parametri:
        model          — nome modello Ollama
        tts_on         — TTS attivo?
        discord_on     — Discord bot attivo?
        memory_count   — numero fatti in memoria
        search_ok      — modulo ricerca attivo?
        ollama_ok      — Ollama raggiungibile?
        speaker_ok     — profilo speaker caricato?
        lang           — lingua TTS corrente
        search_engines — stringa con engine attivi (es. "Tavily + DDG")
    """
    user     = os.getenv("USER", "radostin")
    hostname = platform.node()
    sep      = "─" * (len(user) + len(hostname) + 1)

    info = [
        (f"{BOLD}{user}{DIM}@{RST}{BOLD}{hostname}{RST}", ""),
        ("", sep),
        ("OS",      _get_os()),
        ("Kernel",  _get_kernel()),
        ("CPU",     _get_cpu()),
        ("RAM",     _get_ram()),
        ("Uptime",  _get_uptime()),
        ("", ""),
        ("Modello", f"{CYAN}{model}{RST}"),
        ("Lingua",  lang.upper()),
        ("Ollama",  _ok(ollama_ok)),
        ("TTS",     _ok(tts_on)),
        ("Discord", _ok(discord_on)),
        ("Ricerca", _ok(search_ok) + (f" {DIM}({search_engines}){RST}" if search_engines else "")),
        ("Speaker", _ok(speaker_ok)),
        ("Memoria", f"{memory_count} fatti"),
        ("Ora",     datetime.datetime.now().strftime("%H:%M")),
    ]

    logo_w = 52  # larghezza colonna logo (caratteri)

    print()
    for i, logo_line in enumerate(_LOGO):
        # Colore del logo
        if i in _RED_LINES:
            colored_logo = RED + logo_line + RST
        else:
            colored_logo = CYAN + logo_line + RST

        # Colonna info a destra
        if i < len(info):
            key, val = info[i]
            if key and key not in ("", sep):
                info_str = f"  {GOLD}{key:<8}{RST} {val}"
            elif key == "":
                info_str = f"  {val}"
            else:
                info_str = f"  {val}"
        else:
            info_str = ""

        # Padding logo a larghezza fissa (senza codici ANSI)
        raw_len = len(logo_line)
        pad = max(0, logo_w - raw_len)

        print(f"{colored_logo}{' ' * pad}{info_str}")

    print()
