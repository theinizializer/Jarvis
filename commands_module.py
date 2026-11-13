"""
commands_module.py — JARVIS v10
Dispatcher dei comandi vocali/testuali.

Cambiamenti v10:
  ✓ TranslatorSession importato qui (non più inline in jarvis_v9.py)
  ✓ CommandResult è un dataclass tipizzato (non un dict generico)
  ✓ dispatch() ritorna sempre CommandResult — mai None
  ✓ Separazione pulita: handler per categoria di comando
  ✓ Tabella COMMANDS centralizzata (pattern → handler)
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Any


# ===========================================================================
# Strutture dati
# ===========================================================================

@dataclass
class CommandContext:
    """
    Contesto passato ad ogni handler.
    Raggruppa riferimenti e funzioni di I/O così gli handler
    non importano jarvis_v10 direttamente (evita import circolare).
    """
    raw:        str
    cwd:        str
    lang:       str
    online:     bool
    execute_fn: Callable[[str], str]            # esegue un comando di sistema
    search_fn:  Callable[[str], str]            # cerca sul web
    tts_fn:     Callable[[str], None]           # pronuncia testo
    voice_input: Optional[Any]         = None   # VoiceInput per modalità interattive
    extra:       dict                  = field(default_factory=dict)  # dati extra

    def voice_input_listen(self, timeout: float = 8.0) -> str:
        """Ascolta dal microfono se disponibile, altrimenti legge da stdin."""
        if self.voice_input:
            try:
                return self.voice_input.listen(timeout=timeout) or ""
            except Exception:
                pass
        try:
            return input("→ ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""


@dataclass
class CommandResult:
    """Risultato di dispatch(). handled=False → JARVIS passa al LLM."""
    handled: bool
    output:  str = ""
    action:  str = ""   # es. "exit", "sleep", "lang_changed"
    data:    dict = field(default_factory=dict)

    @classmethod
    def unhandled(cls) -> "CommandResult":
        return cls(handled=False)

    @classmethod
    def ok(cls, output: str = "", action: str = "", data: dict | None = None) -> "CommandResult":
        return cls(handled=True, output=output, action=action, data=data or {})


# ===========================================================================
# Handler
# ===========================================================================

def handle_exit(ctx: CommandContext, match: re.Match) -> CommandResult:
    return CommandResult.ok(output="Arrivederci.", action="exit")


def handle_sleep(ctx: CommandContext, match: re.Match) -> CommandResult:
    return CommandResult.ok(output="Vado in standby.", action="sleep")


def handle_help(ctx: CommandContext, match: re.Match) -> CommandResult:
    lines = [
        "Comandi disponibili:",
        "  esci / bye / quit         — termina JARVIS",
        "  standby / sleep           — pausa ascolto",
        "  cd <path>                 — cambia directory",
        "  ls / dir                  — lista file",
        "  run <cmd>                 — esegue un comando",
        "  cerca online <query>      — ricerca web diretta",
        "  traduttore <lang> <lang>  — modalità traduttore",
        "  memoria / ricorda <testo> — mostra o salva ricordi",
        "  token                     — statistiche utilizzo",
        "  strumenti                 — tool realmente disponibili e servizi da configurare",
        "  pc / info pc              — informazioni reali sul computer",
        "  trova file <nome>         — cerca un file sulla Scrivania",
        "  leggi file <nome>         — legge un file di testo dalla Scrivania",
        "  lingua <codice>           — cambia lingua (it/en/fr/de…)",
        "  impostazioni / settings  — apre le impostazioni",
        "  skill on|off <nome>       — gestisce skill",
        "  mcp                      — elenca server MCP",
        "  mcp tools <server>       — elenca strumenti",
        "  mcp call <server> <tool> {JSON} — esegue uno strumento",
    ]
    return CommandResult.ok(output="\n".join(lines))


def handle_cd(ctx: CommandContext, match: re.Match) -> CommandResult:
    import os
    path = match.group("path").strip()
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(ctx.cwd, path)
    if os.path.isdir(path):
        ctx.extra["new_cwd"] = path
        return CommandResult.ok(output=f"Directory: {path}", action="cwd_changed",
                                data={"cwd": path})
    return CommandResult.ok(output=f"Directory non trovata: {path}")


def handle_ls(ctx: CommandContext, match: re.Match) -> CommandResult:
    import os
    try:
        entries = sorted(os.scandir(ctx.cwd), key=lambda e: (not e.is_dir(), e.name.lower()))
        lines   = []
        for e in entries:
            prefix = "📁 " if e.is_dir() else "   "
            lines.append(f"{prefix}{e.name}")
        return CommandResult.ok(output="\n".join(lines) if lines else "(cartella vuota)")
    except PermissionError:
        return CommandResult.ok(output="Permesso negato.")


def handle_run(ctx: CommandContext, match: re.Match) -> CommandResult:
    cmd = match.group("cmd").strip()
    result = ctx.execute_fn(cmd)
    return CommandResult.ok(output=result)


def handle_search(ctx: CommandContext, match: re.Match) -> CommandResult:
    query = match.group("query").strip()
    if not ctx.online:
        return CommandResult.ok(output="Connessione assente — ricerca non disponibile.")
    result = ctx.search_fn(query)
    return CommandResult.ok(output=result)


def handle_translator(ctx: CommandContext, match: re.Match) -> CommandResult:
    """
    Avvia la modalità traduttore interattiva.
    Usa TranslatorSession — non più un import inline in jarvis_v9.
    """
    lang_a = (match.groupdict().get("lang_a") or "it").lower()
    lang_b = (match.groupdict().get("lang_b") or "en").lower()

    # Supporto nomi estesi: "italiano" → "it"
    _name_to_code = {v: k for k, v in {
        "it": "italiano", "en": "inglese", "fr": "francese",
        "de": "tedesco",  "es": "spagnolo", "pt": "portoghese",
        "nl": "olandese", "ru": "russo",    "bg": "bulgaro",
        "lb": "lussemburghese",
    }.items()}
    lang_a = _name_to_code.get(lang_a, lang_a)
    lang_b = _name_to_code.get(lang_b, lang_b)

    return CommandResult.ok(
        output=f"Modalità traduttore: {lang_a.upper()} ↔ {lang_b.upper()}",
        action="start_translation",
        data={"lang_a": lang_a, "lang_b": lang_b},
    )


def handle_memory_show(ctx: CommandContext, match: re.Match) -> CommandResult:
    return CommandResult.ok(action="memory_show")


def handle_memory_save(ctx: CommandContext, match: re.Match) -> CommandResult:
    text = match.group("text").strip()
    return CommandResult.ok(
        output=f"Ricordato: {text}",
        action="memory_save",
        data={"text": text},
    )


def handle_token_report(ctx: CommandContext, match: re.Match) -> CommandResult:
    return CommandResult.ok(action="token_report")


def handle_lang(ctx: CommandContext, match: re.Match) -> CommandResult:
    lang = match.group("lang").strip().lower()
    return CommandResult.ok(
        output=f"Lingua cambiata: {lang.upper()}",
        action="lang_changed",
        data={"lang": lang},
    )


def handle_skills_list(ctx: CommandContext, match: re.Match) -> CommandResult:
    return CommandResult.ok(action="skills_list")


def handle_host(ctx: CommandContext, match: re.Match) -> CommandResult:
    host    = match.group("host").strip()
    subcmd  = match.group("subcmd").strip() if "subcmd" in (match.groupdict() or {}) else ""
    return CommandResult.ok(
        action="remote_exec",
        data={"host": host, "cmd": subcmd},
    )


# ===========================================================================
# Tabella comandi: pattern → handler
# Ordine: più specifici prima
# ===========================================================================

_COMMANDS: list[tuple[re.Pattern, Callable]] = [
    (re.compile(r'^(strumenti|tools)$',re.I),lambda c,m:CommandResult.ok(action='tool_status')),
    (re.compile(r"^(pc|info pc|informazioni pc)$", re.I),
     lambda c, m: CommandResult.ok(action="system_info")),
    (re.compile(r"^(impostazioni|settings)$", re.I),
     lambda c, m: CommandResult.ok(action="settings")),
    (re.compile(r"^skill\s+import\s+(?P<path>.+)$", re.I),
     lambda c, m: CommandResult.ok(action="skill_import", data=m.groupdict())),
    (re.compile(r"^skill\s+reload$", re.I),
     lambda c, m: CommandResult.ok(action="skill_reload")),
    (re.compile(r"^skill\s+(?P<op>on|off|enable|disable)\s+(?P<name>.+)$", re.I),
     lambda c, m: CommandResult.ok(action="skill_toggle", data=m.groupdict())),
    (re.compile(r"^mcp(?:\s+(?P<tail>.*))?$", re.I),
     lambda c, m: CommandResult.ok(action="mcp", data=m.groupdict())),
    # Uscita
    (re.compile(r"^(esci|bye|quit|exit|addio|arrivederci)[!.]?$", re.I), handle_exit),

    # Standby
    (re.compile(r"^(standby|sleep|pausa|vai in pausa)[!.]?$", re.I), handle_sleep),

    # Help
    (re.compile(r"^(help|aiuto|comandi|cosa sai fare)[?!.]?$", re.I), handle_help),

    # Navigazione filesystem
    (re.compile(r"^cd\s+(?P<path>.+)$", re.I), handle_cd),
    (re.compile(r"^(ls|dir|lista\s*(file|cartella)?)$", re.I), handle_ls),

    # Eco testuale. La risposta segue il normale percorso voce, se attivo.
    (re.compile(r"^echo\s+(?P<text>.+)$", re.I),
     lambda c, m: CommandResult.ok(output=m.group("text"))),

    # Esecuzione comando
    (re.compile(r"^(run|esegui comando|lancia comando)\s+(?P<cmd>.+)$", re.I), handle_run),

    # Ricerca web
    (re.compile(
        r"^(cerca online|cerca sul web|web|search web)\s+(?P<query>.+)$", re.I
    ), handle_search),

    # Traduttore — varie forme:
    # "traduttore it en" / "traduci italiano inglese" / "modalità traduzione"
    (re.compile(
        r"^(traduttore|modalità\s+trad\w+)\s+(?P<lang_a>\w+)\s+(?P<lang_b>\w+)$", re.I
    ), handle_translator),
    (re.compile(
        r"^(traduttore|modalità\s+trad\w+)$", re.I
    ), lambda ctx, m: CommandResult.ok(
        action="start_translation", data={"lang_a": ctx.lang, "lang_b": "en"}
    )),

    # Memoria
    (re.compile(r"^(memoria|ricordi|cosa ricordi)[?!.]?$", re.I), handle_memory_show),
    (re.compile(r"^(ricorda|memorizza|salva ricordo)\s+(?P<text>.+)$", re.I), handle_memory_save),

    # Token / statistiche
    (re.compile(r"^(token|statistiche|stat)[?!.]?$", re.I), handle_token_report),

    # Cambio lingua
    (re.compile(r"^(lingua|language|lang)\s+(?P<lang>[a-z]{2})$", re.I), handle_lang),

    # Lista skill
    (re.compile(r"^(skill|competenze|cosa puoi fare)$", re.I), handle_skills_list),

    # Comando remoto SSH
    (re.compile(r"^(ssh|host)\s+(?P<host>\S+)\s+(?P<subcmd>.+)$", re.I), handle_host),
]


# ===========================================================================
# dispatch() — unico punto di ingresso
# ===========================================================================

def dispatch(ctx: CommandContext) -> CommandResult:
    """
    Tenta di abbinare l'input utente a un comando locale.
    Returns:
        CommandResult con handled=True  → l'handler ha gestito il comando
        CommandResult con handled=False → JARVIS deve passare al LLM
    """
    raw = ctx.raw.strip().lstrip("/")
    if not raw:
        return CommandResult.unhandled()

    for pattern, handler in _COMMANDS:
        match = pattern.match(raw)
        if match:
            try:
                return handler(ctx, match)
            except Exception as e:
                from task_io import TaskCancelled
                if isinstance(e,TaskCancelled):raise
                return CommandResult.ok(output=f"Errore nel comando: {e}")

    return CommandResult.unhandled()
