"""
jarvis_banner.py — Banner JARVIS v6.1
======================================
Layout a due zone:
  ZONA A (fissa, in cima) — banner + faccina animata + info + menu comandi
  ZONA B (scrolla)        — conversazione

La zona A si ridisegna in-place con ANSI escape codes (stile watch/htop).
La faccina batte lentamente sempre, e accelera quando JARVIS parla.
"""

import os
import sys
import platform
import datetime
import threading
import time

# ── Colori ANSI ───────────────────────────────────────────────────────────────
CYAN  = "\033[96m"
GOLD  = "\033[93m"
GREEN = "\033[92m"
RED   = "\033[91m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RST   = "\033[0m"

# ── Escape codes cursore ──────────────────────────────────────────────────────
def _up(n):    return f"\033[{n}A"
def _down(n):  return f"\033[{n}B"
def _home():   return "\033[H"         # cursore in 0,0
def _clear():  return "\033[2J"        # pulisce schermo intero
def _eol():    return "\033[K"         # cancella fino a fine riga
def _save():   return "\033[s"
def _restore():return "\033[u"

# ── Logo JARVIS ───────────────────────────────────────────────────────────────
_LOGO_TOP = [
    r"          ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗",
    r"          ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝",
    r"          ██║███████║██████╔╝██║   ██║██║███████╗",
    r"     ██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║",
    r"     ╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║",
    r"      ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝",
    r"",
    r"     ─── Just A Rather Very Intelligent System ───",
    r"",
]

# Faccina — 8 righe fisse più i 3 occhi variabili
_FACE_PRE  = r"                ╔═══════════════════╗"
_FACE_TOP  = r"                ║  █▀▀▀▀▀▀▀▀▀▀▀▀▀█  ║"
_FACE_BOT  = r"                ║  █  ╚═══════╝  █  ║"
_FACE_CHIN = r"                ║  █▄▄▄▄▄▄▄▄▄▄▄▄▄█  ║"
_FACE_CLOS = r"                ╚═══════════════════╝"

# Frames occhi: (riga1, riga2, riga3)
_EYES = [
    # 0 — normale aperto
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",
     r"                ║  █ ║██║   ║██║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    # 1 — semi-chiuso (battito)
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",
     r"                ║  █ ║──║   ║──║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    # 2 — parlando A (luminoso)
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",
     r"                ║  █ ║▓▓║   ║▓▓║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    # 3 — parlando B (alternato)
    (r"                ║  █ ╔──╗   ╔──╗ █  ║",
     r"                ║  █ ║▒▒║   ║▒▒║ █  ║",
     r"                ║  █ ╚──╝   ╚──╝ █  ║"),
]

# ── Menu comandi localizzato (senza emoji) ────────────────────────────────────
_CMD_MENU = {
    "it": [
        "/memorizza <fatto>   /memoria   /dimentica",
        "/stats   /tts   /modalita   /esci",
        "/meteo <citta>   /notizie   /wiki <q>",
        "/lingua   /aggiungi_voce   /profili_voce",
        "/dormi",
    ],
    "en": [
        "/remember <fact>   /memory   /forget",
        "/stats   /tts   /mode   /exit",
        "/weather <city>   /news   /wiki <q>",
        "/language   /add_voice   /voice_profiles",
        "/sleep",
    ],
    "fr": [
        "/memoriser <fait>   /memoire   /oublier",
        "/stats   /tts   /mode   /sortir",
        "/meteo <ville>   /nouvelles   /wiki <q>",
        "/langue   /ajouter_voix   /profils_voix",
        "/dors",
    ],
    "de": [
        "/merken <fakt>   /erinnerung   /vergessen",
        "/stats   /tts   /mode   /beenden",
        "/wetter <stadt>   /nachrichten   /wiki <q>",
        "/sprache   /stimme_hinzufuegen   /stimmenprofile",
        "/schlaf",
    ],
    "es": [
        "/recordar <hecho>   /memoria   /olvidar",
        "/stats   /tts   /modo   /salir",
        "/tiempo <ciudad>   /noticias   /wiki <q>",
        "/idioma   /agregar_voz   /perfiles_voz",
        "/dormir",
    ],
}

_READY_MSG = {
    "it": "JARVIS v6.0 PRONTO — tutti i comandi iniziano con /",
    "en": "JARVIS v6.0 READY — all commands start with /",
    "fr": "JARVIS v6.0 PRET — toutes les commandes commencent par /",
    "de": "JARVIS v6.0 BEREIT — alle Befehle beginnen mit /",
    "es": "JARVIS v6.0 LISTO — todos los comandos empiezan con /",
}

# ── Sys info helpers ──────────────────────────────────────────────────────────
def _get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        mins = int(secs // 60) % 60
        hrs  = int(secs // 3600)
        days = int(secs // 86400)
        if days > 0: return f"{days}g {hrs}h"
        if hrs > 0:  return f"{hrs}h {mins}m"
        return f"{mins} min"
    except Exception: return "n/d"

def _get_ram():
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        total = avail = 0
        for line in lines:
            if line.startswith("MemTotal"):    total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable"): avail = int(line.split()[1]) // 1024
        return f"{total - avail} MiB / {total} MiB"
    except Exception: return "n/d"

_CPU = None
def _get_cpu():
    global _CPU
    if _CPU: return _CPU
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    _CPU = line.split(":", 1)[1].strip().replace("with Radeon Graphics", "").strip()[:45]
                    return _CPU
    except Exception: pass
    _CPU = platform.processor() or "n/d"
    return _CPU

_KERNEL = None
def _get_kernel():
    global _KERNEL
    if _KERNEL: return _KERNEL
    _KERNEL = platform.release()
    return _KERNEL

_OS = None
def _get_os():
    global _OS
    if _OS: return _OS
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME"):
                    _OS = line.split("=", 1)[1].strip().strip('"')
                    return _OS
    except Exception: pass
    _OS = platform.system()
    return _OS

def _ok(flag): return f"{GREEN}attivo{RST}" if flag else f"{RED}offline{RST}"

# ── Costruisce le righe del banner ────────────────────────────────────────────
def _build_banner_lines(
    eye_frame: int,
    model:        str  = "jarvisQwen",
    tts_on:       bool = False,
    discord_on:   bool = False,
    memory_count: int  = 0,
    search_ok:    bool = False,
    ollama_ok:    bool = True,
    speaker_ok:   bool = False,
    lang:         str  = "it",
    search_engines:str = "",
) -> list[str]:
    """
    Ritorna la lista di stringhe (già colorate) che compongono la zona A.
    eye_frame: 0=normale, 1=battito, 2=parlando-A, 3=parlando-B
    """
    user     = os.getenv("USER", "user")
    hostname = platform.node()
    usep     = "─" * (len(user) + len(hostname) + 1)
    ora      = datetime.datetime.now().strftime("%H:%M")

    info = [
        (f"{BOLD}{user}{DIM}@{RST}{BOLD}{hostname}{RST}", ""),
        ("", usep),
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
        ("Ora",     ora),
    ]

    logo_w = 52
    lines  = []

    # ── Logo testo (9 righe) con info a destra
    for i, logo_line in enumerate(_LOGO_TOP):
        col = CYAN
        cl  = f"{col}{logo_line}{RST}"
        pad = " " * max(0, logo_w - len(logo_line))
        info_str = ""
        if i < len(info):
            k, v = info[i]
            if k and k != usep:
                info_str = f"  {GOLD}{k:<8}{RST} {v}"
            else:
                info_str = f"  {v}"
        lines.append(cl + pad + info_str)

    # ── Faccina (8 righe) con info a destra — continua indice info
    eyes = _EYES[eye_frame % len(_EYES)]
    face_rows = [
        _FACE_PRE,
        _FACE_TOP,
        eyes[0],
        eyes[1],
        eyes[2],
        _FACE_BOT,
        _FACE_CHIN,
        _FACE_CLOS,
    ]
    for j, frow in enumerate(face_rows):
        cl  = f"{RED}{frow}{RST}"
        pad = " " * max(0, logo_w - len(frow))
        info_str = ""
        ii = len(_LOGO_TOP) + j
        if ii < len(info):
            k, v = info[ii]
            if k and k != usep:
                info_str = f"  {GOLD}{k:<8}{RST} {v}"
            else:
                info_str = f"  {v}"
        lines.append(cl + pad + info_str)

    lines.append("")  # riga vuota

    # ── Menu comandi
    sep52  = "=" * 52
    sep52b = "─" * 52
    ready  = _READY_MSG.get(lang, _READY_MSG["en"])
    menu   = _CMD_MENU.get(lang, _CMD_MENU["en"])

    lines.append(f"{GOLD}{sep52}{RST}")
    lines.append(f"{BOLD}  {ready}{RST}")
    lines.append(sep52b)
    for ml in menu:
        lines.append(f"  {ml}")
    lines.append(sep52b)
    lines.append(f"{GOLD}{sep52}{RST}")
    lines.append("")  # riga vuota separatrice dalla conversazione

    return lines


# ── Numero di righe della zona A (costante una volta calcolato) ───────────────
def _banner_height(lang="it") -> int:
    return len(_build_banner_lines(0, lang=lang))


# ══════════════════════════════════════════════════════════════════════════════
# LiveBanner — thread che ridisegna la zona A in-place
# ══════════════════════════════════════════════════════════════════════════════
class LiveBanner:
    """
    Mantiene la zona A (banner + faccina + menu) sempre aggiornata in cima
    allo schermo, senza toccare la zona B (conversazione).

    Meccanismo:
      - Usa \033[H per tornare in cima, ridisegna tutte le righe della zona A,
        poi riposiziona il cursore dove era (tramite _save/_restore).
      - La conversazione continua a scorrere normalmente sotto.
    """

    IDLE_INTERVAL  = 3.0   # secondi tra ridisegni a riposo
    SPEAK_INTERVAL = 0.4   # secondi tra frame quando parla
    BLINK_EVERY    = 4     # ogni N cicli idle fa il battito (occhi semi-chiusi)

    def __init__(self, bot, lang_mgr=None):
        self._bot      = bot
        self._lang_mgr = lang_mgr
        self._speaking  = False
        self._frame     = 0
        self._idle_tick = 0
        self._lock      = threading.Lock()
        self._stop      = threading.Event()
        self._thread    = None
        self._height    = None   # calcolato al primo disegno
        self._initialized = False

    # ── API pubblica ──────────────────────────────────────────────────────────
    def set_speaking(self, flag: bool):
        with self._lock:
            if flag != self._speaking:
                self._speaking = flag
                self._frame = 0

    def set_lang(self, lang: str):
        # Forza un ridisegno immediato al prossimo ciclo
        with self._lock:
            self._frame = 0

    def start(self):
        """
        Pulisce lo schermo, calcola l'altezza del banner, imposta la scroll region
        nella zona B (sotto il banner), disegna il banner una prima volta, avvia il thread.
        """
        # Disegna il banner una volta per sapere quante righe occupa
        params = self._params()
        lines  = _build_banner_lines(0, **params)
        self._height = len(lines)
        zone_b_start = self._height + 1   # prima riga della zona conversazione

        buf = []
        # 1. Pulisci tutto lo schermo
        buf.append("\033[2J")
        # 2. Disegna il banner in cima (righe 1..height)
        buf.append("\033[H")
        for line in lines:
            buf.append(line + "\033[K\r\n")
        # 3. Imposta scroll region = solo zona B (righe zone_b_start .. 9999)
        #    \033[{top};{bot}r  — bot=999 = "fino in fondo"
        buf.append(f"\033[{zone_b_start};999r")
        # 4. Porta il cursore nella zona B
        buf.append(f"\033[{zone_b_start};1H")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

        self._initialized = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="live_banner")
        self._thread.start()

    def stop(self):
        # Ripristina scroll region normale prima di uscire
        sys.stdout.write("\033[r")
        sys.stdout.flush()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    # ── Logica interna ────────────────────────────────────────────────────────
    def _params(self) -> dict:
        bot = self._bot
        engines = []
        if hasattr(bot, 'search') and bot.search:
            s = bot.search
            if hasattr(s, '_tavily') and getattr(s._tavily, 'available', False):
                engines.append("Tavily")
            if hasattr(s, '_ddg'):
                engines.append("DDG")
        return dict(
            model          = bot.model,
            tts_on         = bot.tts_on,
            discord_on     = bot.disc_on,
            memory_count   = len(bot.permanent),
            search_ok      = bool(hasattr(bot, 'search') and bot.search),
            ollama_ok      = True,
            speaker_ok     = False,
            lang           = bot.tts_lang,
            search_engines = " + ".join(engines),
        )

    def _draw(self, eye_frame: int):
        """
        Ridisegna solo le righe della zona A (banner).
        La scroll region è già impostata sulla zona B, quindi questa operazione
        non disturba MAI il cursore o il testo nella zona B.
        """
        params = self._params()
        lines  = _build_banner_lines(eye_frame, **params)

        buf = []
        buf.append("\033[?25l")          # nascondi cursore
        buf.append("\033[s")             # salva posizione cursore (zona B)
        buf.append("\033[r")             # reset temporaneo scroll region = tutto lo schermo
        buf.append("\033[H")             # vai riga 1 col 1
        for line in lines:
            buf.append(line + "\033[K\r\n")
        # Ripristina scroll region zona B
        zone_b = self._height + 1
        buf.append(f"\033[{zone_b};999r")
        buf.append("\033[u")             # ripristina posizione cursore nella zona B
        buf.append("\033[?25h")          # mostra cursore
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                speaking = self._speaking
                frame    = self._frame
                self._frame += 1

            if speaking:
                # Alterna frame 2 e 3 (occhi luminosi e alternati)
                eye = 2 + (frame % 2)
                self._draw(eye_frame=eye)
                self._stop.wait(self.SPEAK_INTERVAL)
            else:
                # Ogni BLINK_EVERY cicli fa un battito veloce
                self._idle_tick += 1
                if self._idle_tick % self.BLINK_EVERY == 0:
                    self._draw(eye_frame=1)   # occhi semi-chiusi
                    self._stop.wait(0.15)     # battito breve
                    self._draw(eye_frame=0)   # occhi normali
                else:
                    self._draw(eye_frame=0)
                self._stop.wait(self.IDLE_INTERVAL)


# ── Istanza globale (unica) ───────────────────────────────────────────────────
_live_banner: LiveBanner | None = None


def init_live_banner(bot, lang_mgr=None) -> LiveBanner:
    """
    Crea e avvia il LiveBanner globale.
    Chiamare UNA VOLTA da main() dopo aver creato il bot.
    """
    global _live_banner
    _live_banner = LiveBanner(bot, lang_mgr)
    _live_banner.start()
    return _live_banner


def start_speaking_anim():
    """Chiamare quando JARVIS inizia a riprodurre TTS."""
    if _live_banner:
        _live_banner.set_speaking(True)


def stop_speaking_anim():
    """Chiamare quando JARVIS finisce di parlare."""
    if _live_banner:
        _live_banner.set_speaking(False)


def reprint_menu(lang: str):
    """
    Aggiorna la lingua nel banner e forza un ridisegno immediato.
    Chiamare dopo __LANG_CHANGED__.
    """
    if _live_banner:
        _live_banner.set_lang(lang)
        _live_banner._draw(eye_frame=0)


# ── print_banner compat (chiamato da Jarvis._print_banner al boot) ────────────
def print_banner(
    model="jarvisQwen", tts_on=False, discord_on=False,
    memory_count=0, search_ok=False, ollama_ok=True,
    speaker_ok=False, lang="it", search_engines="",
    speaking=False, anim_frame=0,
):
    """
    Stampa il banner UNA VOLTA (chiamato da _print_banner prima di init_live_banner).
    Dopo l'avvio, il LiveBanner ridisegna automaticamente — non chiamare di nuovo.
    """
    lines = _build_banner_lines(
        eye_frame      = 1 if speaking else 0,
        model          = model,
        tts_on         = tts_on,
        discord_on     = discord_on,
        memory_count   = memory_count,
        search_ok      = search_ok,
        ollama_ok      = ollama_ok,
        speaker_ok     = speaker_ok,
        lang           = lang,
        search_engines = search_engines,
    )
    print()
    for line in lines:
        print(line)
