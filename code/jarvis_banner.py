"""
jarvis_banner.py — Banner JARVIS v8.0 (Textual TUI)
=====================================================
Layout:
  ZONA A (fissa, in cima)  — banner ASCII + faccina animata + info + menu
  ZONA B (scrollabile)     — conversazione con scroll mouse SOLO dentro la box

ARCHITETTURA:
  Textual gira nel MAIN THREAD (unico modo per evitare ValueError su signal).
  Il loop input/process di jarvis_v8.py gira in un thread worker separato.
  La comunicazione avviene tramite:
    - _msg_q     : worker → app  (testo da mostrare in chat)
    - on_input   : app    → worker (input utente dalla casella Input)

Dipendenze: textual >= 0.50  (pip install textual)

API pubblica invariata rispetto a v6.1:
  init_live_banner(bot, lang_mgr)  → LiveBanner
  run_jarvis_tui(live, worker_fn)  → avvia TUI + worker (NUOVO — vedi main)
  start_speaking_anim()
  stop_speaking_anim()
  reprint_menu(lang)
  print_banner(...)                ← no-op con Textual attivo
"""

import os
import sys
import platform
import datetime
import threading
import time
import queue
from typing import Optional, Callable

# ── Textual ───────────────────────────────────────────────────────────────────
try:
    from textual.app import App, ComposeResult
    from textual.widgets import RichLog, Static, Input
    TEXTUAL_OK = True
except ImportError:
    TEXTUAL_OK = False

# ── Colori ANSI (fallback + stringhe interne) ─────────────────────────────────
CYAN  = "\033[96m"
GOLD  = "\033[93m"
GREEN = "\033[92m"
RED   = "\033[91m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RST   = "\033[0m"

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

_FACE_PRE  = r"                ╔═══════════════════╗"
_FACE_TOP  = r"                ║  █▀▀▀▀▀▀▀▀▀▀▀▀▀█  ║"
_FACE_BOT  = r"                ║  █  ╚═══════╝  █  ║"
_FACE_CHIN = r"                ║  █▄▄▄▄▄▄▄▄▄▄▄▄▄█  ║"
_FACE_CLOS = r"                ╚═══════════════════╝"

_EYES = [
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",   # 0 aperto
     r"                ║  █ ║██║   ║██║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",   # 1 battito
     r"                ║  █ ║──║   ║──║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    (r"                ║  █ ╔══╗   ╔══╗ █  ║",   # 2 parlando A
     r"                ║  █ ║▓▓║   ║▓▓║ █  ║",
     r"                ║  █ ╚══╝   ╚══╝ █  ║"),
    (r"                ║  █ ╔──╗   ╔──╗ █  ║",   # 3 parlando B
     r"                ║  █ ║▒▒║   ║▒▒║ █  ║",
     r"                ║  █ ╚──╝   ╚──╝ █  ║"),
]

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
    "it": "JARVIS v8.0 PRONTO — tutti i comandi iniziano con /",
    "en": "JARVIS v8.0 READY — all commands start with /",
    "fr": "JARVIS v8.0 PRET — toutes les commandes commencent par /",
    "de": "JARVIS v8.0 BEREIT — alle Befehle beginnen mit /",
    "es": "JARVIS v8.0 LISTO — todos los comandos empiezan con /",
}

# ── Sys info helpers ──────────────────────────────────────────────────────────
def _get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        m = int(secs//60)%60; h = int(secs//3600); d = int(secs//86400)
        if d: return f"{d}g {h}h"
        if h: return f"{h}h {m}m"
        return f"{m} min"
    except Exception: return "n/d"

def _get_ram():
    try:
        with open("/proc/meminfo") as f: lines = f.readlines()
        total = avail = 0
        for l in lines:
            if l.startswith("MemTotal"):      total = int(l.split()[1])//1024
            elif l.startswith("MemAvailable"): avail = int(l.split()[1])//1024
        return f"{total-avail} MiB / {total} MiB"
    except Exception: return "n/d"

_CPU = None
def _get_cpu():
    global _CPU
    if _CPU: return _CPU
    try:
        with open("/proc/cpuinfo") as f:
            for l in f:
                if l.startswith("model name"):
                    _CPU = l.split(":",1)[1].strip().replace("with Radeon Graphics","").strip()[:45]
                    return _CPU
    except Exception: pass
    _CPU = platform.processor() or "n/d"; return _CPU

_KERNEL = None
def _get_kernel():
    global _KERNEL
    if not _KERNEL: _KERNEL = platform.release()
    return _KERNEL

_OS = None
def _get_os():
    global _OS
    if _OS: return _OS
    try:
        with open("/etc/os-release") as f:
            for l in f:
                if l.startswith("PRETTY_NAME"):
                    _OS = l.split("=",1)[1].strip().strip('"'); return _OS
    except Exception: pass
    _OS = platform.system(); return _OS


# ── Builder markup Rich (Zona A) ─────────────────────────────────────────────
def _ok(flag): return "[green]attivo[/green]" if flag else "[red]offline[/red]"

def _build_banner_markup(
    eye_frame=0, model="jarvisQwen", tts_on=False, discord_on=False,
    memory_count=0, search_ok=False, ollama_ok=True, speaker_ok=False,
    lang="it", search_engines="",
) -> str:
    user = os.getenv("USER","user"); hostname = platform.node()
    ora  = datetime.datetime.now().strftime("%H:%M")
    sep  = "─"*(len(user)+len(hostname)+1)
    info = [
        (f"[bold]{user}[/bold][dim]@[/dim][bold]{hostname}[/bold]", ""),
        ("", sep), ("OS", _get_os()), ("Kernel", _get_kernel()),
        ("CPU", _get_cpu()), ("RAM", _get_ram()), ("Uptime", _get_uptime()), ("",""),
        ("Modello", f"[cyan]{model}[/cyan]"), ("Lingua", lang.upper()),
        ("Ollama", _ok(ollama_ok)), ("TTS", _ok(tts_on)), ("Discord", _ok(discord_on)),
        ("Ricerca", _ok(search_ok)+(f" [dim]({search_engines})[/dim]" if search_engines else "")),
        ("Speaker", _ok(speaker_ok)), ("Memoria", f"{memory_count} fatti"), ("Ora", ora),
    ]
    logo_w = 52; lines = []
    for i, ll in enumerate(_LOGO_TOP):
        pad=" "*max(0,logo_w-len(ll)); s=""
        if i < len(info):
            k,v=info[i]
            s = f"  [yellow]{k:<8}[/yellow] {v}" if (k and k!=sep) else (f"  {v}" if v else "")
        lines.append(f"[cyan]{ll}[/cyan]{pad}{s}")
    eyes=_EYES[eye_frame%len(_EYES)]
    for j,frow in enumerate([_FACE_PRE,_FACE_TOP,eyes[0],eyes[1],eyes[2],_FACE_BOT,_FACE_CHIN,_FACE_CLOS]):
        pad=" "*max(0,logo_w-len(frow)); s=""
        ii=len(_LOGO_TOP)+j
        if ii<len(info):
            k,v=info[ii]
            s = f"  [yellow]{k:<8}[/yellow] {v}" if (k and k!=sep) else (f"  {v}" if v else "")
        lines.append(f"[red]{frow}[/red]{pad}{s}")
    lines.append("")
    sep52="═"*52; sep52b="─"*52
    ready=_READY_MSG.get(lang,_READY_MSG["en"]); menu=_CMD_MENU.get(lang,_CMD_MENU["en"])
    lines += [f"[yellow]{sep52}[/yellow]", f"[bold]  {ready}[/bold]", sep52b]
    for ml in menu: lines.append(f"  {ml}")
    lines += [sep52b, f"[yellow]{sep52}[/yellow]"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Textual Widgets + App
# ══════════════════════════════════════════════════════════════════════════════
if TEXTUAL_OK:

    class BannerWidget(Static):
        """Zona A: banner fisso aggiornato dal timer."""

        def __init__(self, bot, lang_mgr=None, **kwargs):
            super().__init__("", markup=True, **kwargs)
            self._bot = bot; self._lang_mgr = lang_mgr
            self._speaking = False; self._frame = 0
            self._idle_tick = 0; self._lock = threading.Lock()

        def _params(self):
            bot = self._bot; engines = []
            if hasattr(bot,"search") and bot.search:
                s=bot.search
                if hasattr(s,"_tavily") and getattr(s._tavily,"available",False): engines.append("Tavily")
                if hasattr(s,"_ddg"): engines.append("DDG")
            return dict(model=bot.model, tts_on=bot.tts_on, discord_on=bot.disc_on,
                        memory_count=len(bot.permanent),
                        search_ok=bool(hasattr(bot,"search") and bot.search),
                        ollama_ok=True, speaker_ok=False, lang=bot.tts_lang,
                        search_engines=" + ".join(engines))

        def set_speaking(self, flag: bool):
            with self._lock:
                if flag != self._speaking:
                    self._speaking = flag; self._frame = 0

        def tick(self):
            with self._lock:
                speaking = self._speaking; frame = self._frame; self._frame += 1
            if speaking:
                eye = 2 + (frame % 2)
            else:
                self._idle_tick += 1
                eye = 1 if (self._idle_tick % 4 == 0) else 0
            self.update(_build_banner_markup(eye_frame=eye, **self._params()))

        def set_lang(self, _: str):
            with self._lock: self._frame = 0
            self.tick()


    class ChatLog(RichLog):
        """Zona B: chat scrollabile con mouse — scroll solo dentro questa box."""
        DEFAULT_CSS = """
        ChatLog {
            height: 1fr;
            background: $background;
            scrollbar-gutter: stable;
            padding: 0 1;
            border-top: solid $primary-darken-3;
        }
        """


    class JarvisApp(App):
        """
        Layout:
          BannerWidget  — Zona A fissa
          ChatLog       — Zona B scrollabile (mouse)
          Input         — barra input in fondo
        """

        CSS = """
        Screen { layout: vertical; background: $background; }
        BannerWidget { height: auto; background: $background; padding: 0; }
        Input {
            height: 3;
            border: solid $primary-darken-2;
            background: $background;
            padding: 0 1;
        }
        """
        ENABLE_COMMAND_PALETTE = False

        def __init__(self, bot, lang_mgr=None,
                     on_input: Optional[Callable[[str], None]] = None, **kwargs):
            super().__init__(**kwargs)
            self._bot = bot; self._lang_mgr = lang_mgr; self._on_input = on_input
            self._banner:   Optional[BannerWidget] = None
            self._chat:     Optional[ChatLog]      = None
            self._ibox:     Optional[Input]        = None
            self._msg_q:    queue.Queue = queue.Queue()
            self._ready_cb  = None   # impostato da LiveBanner prima di run()

        def compose(self) -> ComposeResult:
            self._banner = BannerWidget(self._bot, self._lang_mgr)
            self._chat   = ChatLog(highlight=True, markup=True, wrap=True, auto_scroll=True)
            self._ibox   = Input(placeholder="Tu: ")
            yield self._banner
            yield self._chat
            yield self._ibox

        def on_mount(self):
            self.set_interval(0.4,  self._anim_tick)
            self.set_interval(0.05, self._drain_queue)
            if self._ibox: self._ibox.focus()
            # Segnala al worker che l'app è pronta
            if hasattr(self, '_ready_cb') and self._ready_cb:
                self._ready_cb()

        def _anim_tick(self):
            if self._banner: self._banner.tick()

        def _drain_queue(self):
            if not self._chat: return
            try:
                while True:
                    kind, text = self._msg_q.get_nowait()
                    if   kind == "user":   self._chat.write(f"[bold cyan]Tu:[/bold cyan] {text}")
                    elif kind == "jarvis": self._chat.write(f"[bold yellow]JARVIS:[/bold yellow] {text}")
                    elif kind == "system": self._chat.write(f"[dim]{text}[/dim]")
                    else:                  self._chat.write(text)
            except queue.Empty:
                pass

        def on_input_submitted(self, event: Input.Submitted):
            text = event.value.strip()
            if not text: return
            if self._ibox: self._ibox.clear(); self._ibox.focus()
            # Mostra subito nella chat
            self._msg_q.put(("user", text))
            # Passa il testo al worker via callback (mette in _input_queue)
            if self._on_input:
                self._on_input(text)

        # ── API thread-safe ───────────────────────────────────────────────────
        def push_message(self, kind: str, text: str):
            self._msg_q.put((kind, text))

        def set_speaking(self, flag: bool):
            if self._banner: self._banner.set_speaking(flag)

        def set_lang(self, lang: str):
            if self._banner: self._banner.set_lang(lang)

        def set_prompt(self, prompt: str):
            if self._ibox: self._ibox.placeholder = prompt


# ══════════════════════════════════════════════════════════════════════════════
# LiveBanner — API pubblica
# ══════════════════════════════════════════════════════════════════════════════
class LiveBanner:
    """
    Con Textual:
      - start() è BLOCCANTE nel main thread — gira l'app Textual.
      - Il loop JARVIS va in un thread worker avviato da run_jarvis_tui().
      - L'input utente viene dalla casella Input del TUI (non da stdin).

    Senza Textual (fallback ANSI):
      - Comportamento identico a v6.1: banner in thread, loop nel main.
    """

    IDLE_INTERVAL  = 3.0
    SPEAK_INTERVAL = 0.4
    BLINK_EVERY    = 4

    def __init__(self, bot, lang_mgr=None):
        self._bot      = bot
        self._lang_mgr = lang_mgr
        self._app:     Optional["JarvisApp"] = None
        self._fallback = not TEXTUAL_OK
        self._speaking = False
        self._frame    = 0
        self._idle_tick= 0
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._ready    = threading.Event()   # segnalato quando Textual è montato
        self._setup_done = threading.Event() # segnalato quando il setup è finito
        self._thread:  Optional[threading.Thread] = None
        self._on_input: Optional[Callable[[str],None]] = None

    def set_input_callback(self, cb: Callable[[str], None]):
        """
        Registra la funzione chiamata ogni volta che l'utente preme Invio
        nella casella Input del TUI. Deve essere impostata PRIMA di start().
        Ignorata in modalità fallback (usa stdin come prima).
        """
        self._on_input = cb

    def start(self):
        """
        Avvia il TUI.
        Con Textual: BLOCCANTE nel main thread.
        Con fallback: avvia thread banner e ritorna subito.
        """
        if self._fallback:
            self._start_fallback()
            return
        self._app = JarvisApp(self._bot, self._lang_mgr, on_input=self._on_input)
        self._app._ready_cb = self._ready.set   # callback per segnalare il worker
        self._app.run()   # bloccante — nessun problema di segnali Unix

    def stop(self):
        if self._fallback:
            self._stop_fallback(); return
        if self._app:
            try: self._app.exit()
            except Exception: pass
        self._stop.set()

    # ── Animazione ────────────────────────────────────────────────────────────
    def set_speaking(self, flag: bool):
        with self._lock: self._speaking = flag
        if self._app:
            self._app.call_from_thread(self._app.set_speaking, flag)

    def set_lang(self, lang: str):
        with self._lock: self._frame = 0
        if self._app:
            self._app.call_from_thread(self._app.set_lang, lang)

    # ── Scrittura chat ────────────────────────────────────────────────────────
    def push_message(self, kind: str, text: str):
        """Thread-safe. kind: 'user'|'jarvis'|'system'|'raw'"""
        if self._fallback:
            print({"user":"Tu: ","jarvis":"JARVIS: "}.get(kind,"")+text); return
        if self._app and hasattr(self._app, '_msg_q'):
            self._app._msg_q.put((kind, text))

    def write_user(self, text: str):   self.push_message("user",   text)
    def write_jarvis(self, text: str): self.push_message("jarvis", text)
    def write_system(self, text: str): self.push_message("system", text)

    # ── Fallback ANSI (identico a v6.1) ──────────────────────────────────────
    def _params_fb(self):
        bot=self._bot; engines=[]
        if hasattr(bot,"search") and bot.search:
            s=bot.search
            if hasattr(s,"_tavily") and getattr(s._tavily,"available",False): engines.append("Tavily")
            if hasattr(s,"_ddg"): engines.append("DDG")
        return dict(model=bot.model,tts_on=bot.tts_on,discord_on=bot.disc_on,
                    memory_count=len(bot.permanent),
                    search_ok=bool(hasattr(bot,"search") and bot.search),
                    ollama_ok=True,speaker_ok=False,lang=bot.tts_lang,
                    search_engines=" + ".join(engines))

    def _draw_fb(self, eye):
        lines=_build_banner_lines_ansi(eye_frame=eye,**self._params_fb())
        h=len(lines); z=h+1
        buf=["\033[2J","\033[H"]+[l+"\033[K\r\n" for l in lines]+[f"\033[{z};999r",f"\033[{z};1H"]
        sys.stdout.write("".join(buf)); sys.stdout.flush()
        self._fallback_height=h

    def _redraw_fb(self, eye):
        lines=_build_banner_lines_ansi(eye_frame=eye,**self._params_fb())
        h=getattr(self,"_fallback_height",len(lines)); z=h+1
        buf=["\033[?25l","\033[s","\033[r","\033[H"]+[l+"\033[K\r\n" for l in lines]+\
             [f"\033[{z};999r","\033[u","\033[?25h"]
        sys.stdout.write("".join(buf)); sys.stdout.flush()

    def notify_setup_done(self):
        """
        Chiamare dal main thread DOPO tutte le domande di setup (password, TTS, ecc.)
        e DOPO aver creato il bot. Il fallback ANSI aspetta questo segnale
        prima di pulire lo schermo e disegnare il banner, così le domande
        di setup non vengono sovrascritte/confuse con il banner animato.
        Con Textual (non-fallback) questo metodo non fa nulla — Textual
        gestisce il proprio ciclo di vita.
        """
        self._setup_done.set()

    def _start_fallback(self):
        # Lancia il thread banner — lui aspetterà _setup_done prima di disegnare
        self._thread = threading.Thread(target=self._run_fb, daemon=True, name="live_banner")
        self._thread.start()
        # Segnala subito che il LiveBanner è "pronto" (il worker può partire)
        self._ready.set()

    def _stop_fallback(self):
        sys.stdout.write("\033[r"); sys.stdout.flush(); self._stop.set()

    def _run_fb(self):
        # Aspetta che il setup (password, TTS, Discord, modalità) sia finito
        # prima di pulire lo schermo e disegnare il banner per la prima volta.
        self._setup_done.wait()
        self._draw_fb(0)   # primo disegno pulito
        while not self._stop.is_set():
            with self._lock: speaking=self._speaking; frame=self._frame; self._frame+=1
            if speaking:
                self._redraw_fb(2+(frame%2)); self._stop.wait(self.SPEAK_INTERVAL)
            else:
                self._idle_tick+=1
                if self._idle_tick%self.BLINK_EVERY==0:
                    self._redraw_fb(1); self._stop.wait(0.15)
                self._redraw_fb(0); self._stop.wait(self.IDLE_INTERVAL)


# ── Banner ANSI classico (per fallback) ──────────────────────────────────────
def _build_banner_lines_ansi(
    eye_frame=0,model="jarvisQwen",tts_on=False,discord_on=False,
    memory_count=0,search_ok=False,ollama_ok=True,speaker_ok=False,
    lang="it",search_engines="",
) -> list:
    def _ok_a(f): return f"{GREEN}attivo{RST}" if f else f"{RED}offline{RST}"
    user=os.getenv("USER","user"); hostname=platform.node()
    ora=datetime.datetime.now().strftime("%H:%M"); usep="─"*(len(user)+len(hostname)+1)
    info=[
        (f"{BOLD}{user}{DIM}@{RST}{BOLD}{hostname}{RST}",""),("",usep),
        ("OS",_get_os()),("Kernel",_get_kernel()),("CPU",_get_cpu()),
        ("RAM",_get_ram()),("Uptime",_get_uptime()),("",""),
        ("Modello",f"{CYAN}{model}{RST}"),("Lingua",lang.upper()),
        ("Ollama",_ok_a(ollama_ok)),("TTS",_ok_a(tts_on)),("Discord",_ok_a(discord_on)),
        ("Ricerca",_ok_a(search_ok)+(f" {DIM}({search_engines}){RST}" if search_engines else "")),
        ("Speaker",_ok_a(speaker_ok)),("Memoria",f"{memory_count} fatti"),("Ora",ora),
    ]
    logo_w=52; lines=[]
    for i,ll in enumerate(_LOGO_TOP):
        pad=" "*max(0,logo_w-len(ll)); s=""
        if i<len(info):
            k,v=info[i]; s=f"  {GOLD}{k:<8}{RST} {v}" if (k and k!=usep) else (f"  {v}" if v else "")
        lines.append(f"{CYAN}{ll}{RST}{pad}{s}")
    eyes=_EYES[eye_frame%len(_EYES)]
    for j,frow in enumerate([_FACE_PRE,_FACE_TOP,eyes[0],eyes[1],eyes[2],_FACE_BOT,_FACE_CHIN,_FACE_CLOS]):
        pad=" "*max(0,logo_w-len(frow)); s=""
        ii=len(_LOGO_TOP)+j
        if ii<len(info):
            k,v=info[ii]; s=f"  {GOLD}{k:<8}{RST} {v}" if (k and k!=usep) else (f"  {v}" if v else "")
        lines.append(f"{RED}{frow}{RST}{pad}{s}")
    lines.append("")
    sep52="="*52; sep52b="─"*52
    ready=_READY_MSG.get(lang,_READY_MSG["en"]); menu=_CMD_MENU.get(lang,_CMD_MENU["en"])
    lines+=[f"{GOLD}{sep52}{RST}",f"{BOLD}  {ready}{RST}",sep52b]
    for ml in menu: lines.append(f"  {ml}")
    lines+=[sep52b,f"{GOLD}{sep52}{RST}",""]
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# API pubblica globale
# ══════════════════════════════════════════════════════════════════════════════
_live_banner: Optional[LiveBanner] = None


def init_live_banner(bot, lang_mgr=None) -> LiveBanner:
    """Crea il LiveBanner globale. NON lo avvia — usa run_jarvis_tui() per farlo."""
    global _live_banner
    _live_banner = LiveBanner(bot, lang_mgr)
    return _live_banner


def run_jarvis_tui(live: LiveBanner, worker_fn: Callable):
    """
    Avvia il TUI e il loop JARVIS.

    Con Textual:
      - worker_fn() parte in un thread separato
      - live.start() blocca il main thread con l'app Textual

    Con fallback ANSI:
      - live.start() avvia il thread banner (non bloccante)
      - worker_fn() gira nel main thread come prima

    In jarvis_v8.py main(), SOSTITUISCI il while True con:

        def _jarvis_worker():
            # tutto il vecchio while True va qui dentro
            while True:
                ...

        _status_bar = init_live_banner(bot, lang_mgr)
        _status_bar.set_input_callback(_jarvis_worker_single)  # opzionale
        run_jarvis_tui(_status_bar, _jarvis_worker)
    """
    if not live._fallback:
        t = threading.Thread(target=worker_fn, daemon=True, name="jarvis_worker")
        t.start()
        live.start()   # bloccante nel main thread
    else:
        live.start()   # non bloccante
        worker_fn()    # bloccante nel main thread


def start_speaking_anim():
    if _live_banner: _live_banner.set_speaking(True)

def stop_speaking_anim():
    if _live_banner: _live_banner.set_speaking(False)

def reprint_menu(lang: str):
    if _live_banner: _live_banner.set_lang(lang)

def notify_setup_done():
    """Segnala che il setup è finito — il banner ANSI può disegnare."""
    if _live_banner: _live_banner.notify_setup_done()

def print_banner(
    model="jarvisQwen", tts_on=False, discord_on=False,
    memory_count=0, search_ok=False, ollama_ok=True,
    speaker_ok=False, lang="it", search_engines="",
    speaking=False, anim_frame=0,
):
    """Compatibilità boot — no-op con Textual, stampa ANSI senza Textual."""
    if TEXTUAL_OK: return
    lines = _build_banner_lines_ansi(
        eye_frame=1 if speaking else 0, model=model, tts_on=tts_on,
        discord_on=discord_on, memory_count=memory_count, search_ok=search_ok,
        ollama_ok=ollama_ok, speaker_ok=speaker_ok, lang=lang,
        search_engines=search_engines,
    )
    print()
    for line in lines: print(line)


# ══════════════════════════════════════════════════════════════════════════════
# Stdout redirect — cattura tutti i print() e li manda alla ChatLog
# ══════════════════════════════════════════════════════════════════════════════
class _TuiStdout:
    """
    Sostituisce sys.stdout dopo l'avvio del TUI.
    Strategia: accumula TUTTO nel buffer, scrive nella ChatLog SOLO su \n.
    flush() intermedi (da print(..., flush=True)) vengono ignorati —
    questo evita che i chunk dello streaming appaiano spezzati.
    Il print() finale senza argomenti emette \n e scarica tutto.
    """
    def __init__(self, live: "LiveBanner", original):
        self._live  = live
        self._orig  = original
        self._buf   = ""
        self._lock  = threading.Lock()

    def write(self, text: str):
        if not text:
            return
        with self._lock:
            self._buf += text
            # Scrivi nella chat solo quando arriva un newline
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self._live.push_message("raw", line)

    def flush(self):
        # Ignora flush() — i chunk arrivano senza \n e flush non deve
        # scrivere frammenti parziali. Il \n finale li scarica tutti.
        pass

    def fileno(self):
        return self._orig.fileno()

    def isatty(self):
        return self._orig.isatty()


def redirect_stdout_to_tui(live: "LiveBanner"):
    """
    Reindirizza sys.stdout alla ChatLog del TUI.
    Chiamare DOPO init_live_banner() e DOPO che _ready è stato segnalato.
    """
    if not TEXTUAL_OK or live._fallback:
        return
    sys.stdout = _TuiStdout(live, sys.stdout)
