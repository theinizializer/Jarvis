#!/usr/bin/env python3
"""
JARVIS — Commands Module v8.1
Gestisce tutti i comandi speciali (__SWITCH_MODE__, __ADD_VOICE__, __LIST_VOICES__,
__HOST__, __TTS__, __SLEEP__, __LANG_CHANGED__, ecc.).

Ogni handler:
  - riceve `ctx` (CommandContext) con riferimenti a bot, voice_input, _ask, ecc.
  - non chiama mai input() direttamente — usa ctx.ask()
  - ritorna un CommandResult con status e messaggio
  - è testabile in isolamento

Importato da jarvis_v8.py nel _jarvis_worker().
"""

from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any


# ══════════════════════════════════════════════════════════════════════════════
# CommandContext — contenitore di tutto ciò che gli handler potrebbero servire
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CommandContext:
    """
    Passato a ogni handler. Raggruppa riferimenti e funzioni di I/O
    in modo che gli handler non dipendano da variabili globali.
    """
    # ── I/O ───────────────────────────────────────────────────────────────────
    ask:        Callable[[str, str, float], str]   # _ask(prompt, placeholder, timeout)
    print_fn:   Callable[[str], None]              # _chat_print o print

    # ── Stato sessione ────────────────────────────────────────────────────────
    bot:        Any                                # JarvisBot
    lang_mgr:   Any                                # LanguageManager
    vi_ref:     list                               # [VoiceInput | None]  — mutabile
    use_voice:  list                               # [bool]               — mutabile
    session_active: list                           # [bool]               — mutabile

    # ── Dipendenze opzionali ──────────────────────────────────────────────────
    SD_OK:      bool = False
    WHISPER_OK: bool = False

    @property
    def voice_input(self):
        return self.vi_ref[0]

    @voice_input.setter
    def voice_input(self, v):
        self.vi_ref[0] = v


# ══════════════════════════════════════════════════════════════════════════════
# CommandResult
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CommandResult:
    ok:      bool
    message: str = ""
    action:  str = ""   # "break", "continue", "switch_voice", ecc.


# ══════════════════════════════════════════════════════════════════════════════
# Helper interni
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_voice_input(ctx: CommandContext) -> tuple[Any, Any]:
    """
    Assicura che voice_input e speaker_ref siano inizializzati.
    Ritorna (voice_input, speaker_ref) — entrambi possono essere None se
    le dipendenze mancano.
    """
    # Import lazily per evitare import circolare
    try:
        from voice_module import VoiceInput, SpeakerVerifier, choose_microphone
    except ImportError:
        ctx.print_fn("❌ voice_module.py non trovato")
        return None, None

    vi = ctx.voice_input
    sp = getattr(vi, '_speaker_ref', None) if vi else None

    if vi is None and ctx.SD_OK and ctx.WHISPER_OK:
        ctx.print_fn("\n  🔍 Scegli il microfono:")
        mic = choose_microphone(ask_fn=lambda p: ctx.ask(p, "scelta microfono"))
        if mic:
            vi = VoiceInput(mic)
            sp = SpeakerVerifier()
            vi._speaker_ref = sp
            vi._lang_ref    = ctx.lang_mgr
            ctx.voice_input = vi
            ctx.print_fn("  ✅ Microfono pronto.")
        else:
            ctx.print_fn("  ❌ Nessun microfono selezionato.")

    return vi, sp


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: /tts
# ══════════════════════════════════════════════════════════════════════════════

def handle_tts(ctx: CommandContext, _arg: str = "") -> CommandResult:
    """
    Attiva/disattiva TTS (Text-To-Speech).
    Verifica che il player audio sia disponibile.
    """
    bot = ctx.bot
    was_on = getattr(bot, 'tts_on', False)
    bot.tts_on = not was_on

    # Controllo: player disponibile?
    if bot.tts_on:
        player_ok = False
        try:
            import subprocess
            for p in ('mpg123', 'ffplay', 'aplay', 'cvlc'):
                if subprocess.run(['which', p], capture_output=True).returncode == 0:
                    player_ok = True
                    break
        except Exception:
            pass

        if not player_ok:
            bot.tts_on = False
            ctx.print_fn("⚠️  TTS: nessun player audio trovato (mpg123/ffplay/aplay).")
            ctx.print_fn("   Installa con: sudo apt install mpg123")
            return CommandResult(ok=False, message="player non trovato")

        # Testa gTTS
        try:
            from gtts import gTTS
        except ImportError:
            bot.tts_on = False
            ctx.print_fn("⚠️  TTS: gTTS non installato. Esegui: pip install gtts")
            return CommandResult(ok=False, message="gTTS mancante")

        # Avvia TTSEngine se non già attivo
        if not hasattr(bot, '_tts_engine') or bot._tts_engine is None:
            try:
                from voice_module import TTSEngine
                mem_dir = getattr(bot, 'mem_dir', Path.home() / 'jarvis_memory')
                pa_name = None
                vi = ctx.voice_input
                if vi:
                    pa_name = vi.mic.get('pa_name') if hasattr(vi, 'mic') else None
                bot._tts_engine = TTSEngine(
                    lang=getattr(bot, 'tts_lang', 'it'),
                    mem_dir=mem_dir,
                    pa_name=pa_name,
                )
                # Collega _tts_say al nuovo engine
                bot._tts_engine_say = bot._tts_engine.say
            except Exception as e:
                bot.tts_on = False
                ctx.print_fn(f"⚠️  TTS: errore avvio engine — {e}")
                return CommandResult(ok=False, message=str(e))

    stato = "ON ✅" if bot.tts_on else "OFF ❌"
    msg = f"🔊 TTS {stato}"
    ctx.print_fn(msg)
    return CommandResult(ok=True, message=msg)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: /modalità (switch tastiera ↔ voce)
# ══════════════════════════════════════════════════════════════════════════════

def handle_switch_mode(ctx: CommandContext, _arg: str = "") -> CommandResult:
    """
    Passa da modalità tastiera a vocale o viceversa.
    """
    if ctx.use_voice[0]:
        # Era voce → torna a tastiera
        ctx.use_voice[0] = False
        ctx.print_fn("⌨️  Passato a modalità tastiera.")
        return CommandResult(ok=True, action="switch_keyboard")

    if not ctx.SD_OK or not ctx.WHISPER_OK:
        ctx.print_fn("⚠️  Dipendenze vocali non disponibili (sounddevice / faster-whisper).")
        return CommandResult(ok=False)

    vi, _sp = _ensure_voice_input(ctx)
    if vi is None:
        ctx.print_fn("❌ Impossibile attivare il microfono.")
        return CommandResult(ok=False)

    ctx.use_voice[0]       = True
    ctx.session_active[0]  = True   # in modalità vocale il PIN non serve
    ctx.print_fn("🎙️  Passato a modalità vocale.")
    return CommandResult(ok=True, action="switch_voice")


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: /aggiungi_voce
# ══════════════════════════════════════════════════════════════════════════════

def handle_add_voice(ctx: CommandContext, _arg: str = "") -> CommandResult:
    """
    Registra un nuovo profilo vocale per speaker verification.
    """
    if not ctx.SD_OK or not ctx.WHISPER_OK:
        ctx.print_fn("❌ Dipendenze audio mancanti (sounddevice / faster-whisper).")
        return CommandResult(ok=False, message="dipendenze mancanti")

    vi, sp = _ensure_voice_input(ctx)
    if vi is None or sp is None:
        ctx.print_fn("❌ Microfono o speaker verifier non disponibile.")
        return CommandResult(ok=False)

    # Chiede nome utente
    nome = ctx.ask("  Nome utente (es. radostin): ", "nome utente").lower().strip()
    if not nome:
        nome = "owner"

    lingua = getattr(ctx.bot, 'tts_lang', 'it')
    ctx.print_fn(f"\n  🎙️  Parla normalmente per 8-10 secondi in {lingua.upper()}...")
    ctx.print_fn("  (il silenzio interrompe automaticamente la registrazione)")

    try:
        audio = vi._record_sd(timeout=12.0, silence_sec=10.0)
    except Exception as e:
        ctx.print_fn(f"  ❌ Errore registrazione: {e}")
        return CommandResult(ok=False, message=str(e))

    if audio is None or len(audio) < 16000 * 2:  # min 2 secondi
        ctx.print_fn("  ❌ Registrazione troppo corta — riprova.")
        return CommandResult(ok=False, message="registrazione troppo corta")

    ok = sp.add_profile(audio, nome, lingua)
    if ok:
        profiles = sp.list_profiles() if hasattr(sp, 'list_profiles') else []
        prof_str = ', '.join(f'{n}_{l}' for n, l in profiles)
        msg = f"  ✅ Profilo '{nome}' [{lingua.upper()}] salvato.\n  Profili attivi: {prof_str}"
        ctx.print_fn(msg)
        return CommandResult(ok=True, message=msg)
    else:
        ctx.print_fn("  ❌ Salvataggio profilo fallito (resemblyzer non disponibile?).")
        return CommandResult(ok=False, message="salvataggio fallito")


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: /profili_voce (lista + gestione profili)
# ══════════════════════════════════════════════════════════════════════════════

def handle_list_voices(ctx: CommandContext, _arg: str = "") -> CommandResult:
    """
    Mostra profili vocali registrati. Permette di aggiungere o eliminare.
    """
    if not ctx.SD_OK or not ctx.WHISPER_OK:
        ctx.print_fn("❌ Dipendenze audio mancanti.")
        return CommandResult(ok=False)

    vi, sp = _ensure_voice_input(ctx)
    if sp is None:
        ctx.print_fn("❌ Speaker verifier non disponibile.")
        return CommandResult(ok=False)

    lang_bot = getattr(ctx.bot, 'tts_lang', 'it')

    while True:
        profiles = sp.list_profiles() if hasattr(sp, 'list_profiles') else []
        ctx.print_fn("")
        if profiles:
            ctx.print_fn("  👤 Profili vocali registrati:")
            for idx, (pn, pl) in enumerate(sorted(profiles), 1):
                marker = "  ← attivo" if pl == lang_bot else ""
                ctx.print_fn(f"    {idx}. {pn} [{pl.upper()}]{marker}")
        else:
            ctx.print_fn("  ℹ️  Nessun profilo registrato.")

        ctx.print_fn("")
        ctx.print_fn("  a) Aggiungi profilo")
        if profiles:
            ctx.print_fn("  d) Elimina profilo")
        ctx.print_fn("  q) Esci")
        ctx.print_fn("")

        scelta = ctx.ask("  Scelta: ", "a/d/q").lower().strip()

        # ── Esci ──────────────────────────────────────────────────────────────
        if scelta in ('q', ''):
            ctx.print_fn("")
            return CommandResult(ok=True, message="uscito da gestione profili")

        # ── Elimina ───────────────────────────────────────────────────────────
        elif scelta == 'd':
            if not profiles:
                ctx.print_fn("  Nessun profilo da eliminare.")
                continue
            _sorted = sorted(profiles)
            ctx.print_fn("")
            for idx, (pn, pl) in enumerate(_sorted, 1):
                ctx.print_fn(f"    {idx}. {pn} [{pl.upper()}]")
            ctx.print_fn("")

            del_in = ctx.ask("  Numero da eliminare (invio=annulla): ", "numero").strip()
            if not del_in:
                ctx.print_fn("  Annullato.")
                continue
            try:
                del_idx = int(del_in) - 1
                if not 0 <= del_idx < len(_sorted):
                    raise ValueError
            except ValueError:
                ctx.print_fn("  ❌ Numero non valido.")
                continue

            del_name, del_lang = _sorted[del_idx]
            conf = ctx.ask(
                f"  Elimina '{del_name} [{del_lang.upper()}]'? (s/N): ", "s/N"
            ).lower().strip()

            if conf == 's':
                key  = f"{del_name}_{del_lang}"
                path = sp._dir / f"{del_name}_{del_lang}.npy"
                sp._profiles.pop(key, None)
                if path.exists():
                    path.unlink()
                ctx.print_fn(f"  ✅ Profilo '{del_name} [{del_lang.upper()}]' eliminato.")
            else:
                ctx.print_fn("  Annullato.")
            ctx.print_fn("")

        # ── Aggiungi ──────────────────────────────────────────────────────────
        elif scelta == 'a':
            if vi is None:
                ctx.print_fn("  ❌ Microfono non disponibile.")
                continue

            nome = ctx.ask("  Nome utente (es. radostin): ", "nome").lower().strip()
            if not nome:
                ctx.print_fn("  ❌ Nome non valido.")
                continue

            lang_in = ctx.ask(
                f"  Lingua [{lang_bot.upper()}] (invio=conferma): ",
                f"default {lang_bot}",
            ).lower().strip()
            lingua = lang_in if lang_in else lang_bot

            ctx.print_fn(f"\n  🎙️  Parla per 8-10 secondi in {lingua.upper()}...")
            ctx.ask("  (invio per iniziare la registrazione)", "Invio per iniziare")
            ctx.print_fn("  🔴 Registrazione in corso...")

            try:
                audio = vi._record_sd(timeout=12.0, silence_sec=10.0)
            except Exception as e:
                ctx.print_fn(f"  ❌ Errore registrazione: {e}")
                continue

            if audio is None or len(audio) < 16000 * 2:
                ctx.print_fn("  ❌ Registrazione troppo corta — riprova.")
                continue

            ok = sp.add_profile(audio, nome, lingua)
            if ok:
                ctx.print_fn(f"  ✅ Profilo '{nome}' [{lingua.upper()}] salvato.")
            else:
                ctx.print_fn("  ❌ Salvataggio fallito (resemblyzer disponibile?).")
            ctx.print_fn("")

        else:
            ctx.print_fn("  ❌ Scelta non valida.")

    return CommandResult(ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: /host (SSH)
# ══════════════════════════════════════════════════════════════════════════════

def handle_host(ctx: CommandContext, arg: str = "") -> CommandResult:
    """
    Gestisce i sottocomandi SSH: list, add, remove, disconnect, <nome_host>.
    Ogni operazione interattiva usa ctx.ask().
    """
    ssh = getattr(ctx.bot, 'ssh', None)
    if ssh is None:
        ctx.print_fn("❌ ssh_module.py non trovato o non inizializzato.")
        return CommandResult(ok=False, message="ssh non disponibile")

    arg = arg.lstrip(":").strip().lower()

    # ── Lista host ────────────────────────────────────────────────────────────
    if not arg or arg in ("list", "lista", "ls"):
        try:
            result = ssh.cmd_list()
            ctx.print_fn(result if result else "  Nessun host configurato.")
            return CommandResult(ok=True, message=result)
        except Exception as e:
            ctx.print_fn(f"❌ Errore lista host: {e}")
            return CommandResult(ok=False, message=str(e))

    # ── Aggiungi host ─────────────────────────────────────────────────────────
    if arg in ("add", "aggiungi", "nuovo"):
        try:
            # cmd_add_interactive ora usa ask_fn se supportato
            if hasattr(ssh, 'cmd_add_interactive'):
                if 'ask_fn' in ssh.cmd_add_interactive.__code__.co_varnames:
                    ssh.cmd_add_interactive(ask_fn=ctx.ask)
                else:
                    # Fallback: parametri uno per uno
                    name  = ctx.ask("  Nome host (alias): ", "nome alias").strip()
                    if not name:
                        ctx.print_fn("  Annullato.")
                        return CommandResult(ok=False, message="annullato")
                    host  = ctx.ask("  Indirizzo (IP/hostname): ", "hostname").strip()
                    user  = ctx.ask("  Utente SSH: ", "utente").strip()
                    port  = ctx.ask("  Porta [22]: ", "porta").strip() or "22"
                    key   = ctx.ask("  Percorso chiave SSH (invio=password): ", "~/.ssh/id_rsa").strip()
                    try:
                        port_i = int(port)
                    except ValueError:
                        port_i = 22
                    ok, msg = ssh.add_host(name=name, host=host, user=user,
                                           port=port_i, key_path=key or None)
                    ctx.print_fn(f"  {'✅' if ok else '❌'} {msg}")
                    return CommandResult(ok=ok, message=msg)
            else:
                ctx.print_fn("❌ ssh_module non supporta cmd_add_interactive")
                return CommandResult(ok=False)
        except Exception as e:
            ctx.print_fn(f"❌ Errore aggiunta host: {e}")
            return CommandResult(ok=False, message=str(e))

    # ── Rimuovi host ──────────────────────────────────────────────────────────
    if arg in ("remove", "rimuovi", "elimina", "del"):
        try:
            if hasattr(ssh, 'cmd_remove_interactive'):
                if 'ask_fn' in ssh.cmd_remove_interactive.__code__.co_varnames:
                    result = ssh.cmd_remove_interactive(ask_fn=ctx.ask)
                else:
                    result = ssh.cmd_remove_interactive()
                ctx.print_fn(result or "")
                return CommandResult(ok=True, message=result or "")
            else:
                # Lista e chiede numero manualmente
                lista = ssh.cmd_list() or "Nessun host"
                ctx.print_fn(lista)
                nome = ctx.ask("  Nome host da rimuovere: ", "nome host").strip()
                if not nome:
                    ctx.print_fn("  Annullato.")
                    return CommandResult(ok=False, message="annullato")
                ok, msg = ssh.remove_host(nome)
                ctx.print_fn(f"  {'✅' if ok else '❌'} {msg}")
                return CommandResult(ok=ok, message=msg)
        except Exception as e:
            ctx.print_fn(f"❌ Errore rimozione host: {e}")
            return CommandResult(ok=False, message=str(e))

    # ── Disconnetti ───────────────────────────────────────────────────────────
    if arg in ("disconnect", "disconnetti", "close", "chiudi"):
        try:
            if ssh.is_connected():
                nome = ssh.active_host.name if ssh.active_host else "?"
                ssh.disconnect()
                msg = f"  ✅ Disconnesso da '{nome}'."
                ctx.print_fn(msg)
                return CommandResult(ok=True, message=msg)
            else:
                ctx.print_fn("  ℹ️  Nessun host connesso.")
                return CommandResult(ok=True)
        except Exception as e:
            ctx.print_fn(f"❌ Errore disconnessione: {e}")
            return CommandResult(ok=False, message=str(e))

    # ── Connetti a host per nome ──────────────────────────────────────────────
    try:
        ctx.print_fn(f"\n  🔗 Connessione a '{arg}'...")
        ok, msg = ssh.connect(arg)
        if ok:
            ah = ssh.active_host
            ctx.print_fn(f"  ✅ {msg}")
            ctx.print_fn(f"  OS:  {getattr(ah, 'os_type', '?')} | "
                         f"{ah.hw_info.get('os', '?') if hasattr(ah, 'hw_info') else '?'}")
            ctx.print_fn(f"  CPU: {ah.hw_info.get('cpu', '?') if hasattr(ah, 'hw_info') else '?'}")
            ctx.print_fn(f"  RAM: {ah.hw_info.get('ram', '?') if hasattr(ah, 'hw_info') else '?'}")
            ctx.print_fn(f"  Dir: {getattr(ah, 'cwd', '?')}")
        else:
            ctx.print_fn(f"  ❌ {msg}")
        ctx.print_fn("")
        return CommandResult(ok=ok, message=msg)
    except Exception as e:
        ctx.print_fn(f"❌ Errore connessione SSH: {e}")
        return CommandResult(ok=False, message=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: __LANG_CHANGED__
# ══════════════════════════════════════════════════════════════════════════════

def handle_lang_changed(ctx: CommandContext, _arg: str = "") -> CommandResult:
    """
    Aggiorna lingua nel bot e nel speaker verifier dopo cambio lingua.
    """
    try:
        ctx.bot.tts_lang = ctx.lang_mgr.tts_lang
        new_lang = ctx.bot.tts_lang

        # Sincronizza speaker verifier
        vi = ctx.voice_input
        if vi and hasattr(vi, '_speaker_ref'):
            sp = vi._speaker_ref
            if sp and hasattr(sp, 'current_lang'):
                sp.current_lang = new_lang

        # Aggiorna TTSEngine se attivo
        if hasattr(ctx.bot, '_tts_engine') and ctx.bot._tts_engine:
            ctx.bot._tts_engine.lang = new_lang

        ctx.print_fn(f"\n  🌍 Lingua cambiata → {new_lang.upper()}")
        return CommandResult(ok=True, message=new_lang, action="reprint_menu")
    except Exception as e:
        ctx.print_fn(f"❌ Errore cambio lingua: {e}")
        return CommandResult(ok=False, message=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: __SLEEP__
# ══════════════════════════════════════════════════════════════════════════════

def handle_sleep(ctx: CommandContext, _arg: str = "") -> CommandResult:
    ctx.session_active[0] = False
    ctx.print_fn("💤 Sessione bloccata — a presto!")
    return CommandResult(ok=True, action="continue")


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER: __EXIT__
# ══════════════════════════════════════════════════════════════════════════════

def handle_exit(ctx: CommandContext, _arg: str = "") -> CommandResult:
    ctx.print_fn("👋 Ciao!")
    return CommandResult(ok=True, action="break")


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher centrale
# ══════════════════════════════════════════════════════════════════════════════

# Mappa token → handler
_HANDLERS: dict[str, Callable] = {
    "__EXIT__":         handle_exit,
    "__SLEEP__":        handle_sleep,
    "__SWITCH_MODE__":  handle_switch_mode,
    "__LANG_CHANGED__": handle_lang_changed,
    "__ADD_VOICE__":    handle_add_voice,
    "__LIST_VOICES__":  handle_list_voices,
    "__TTS__":          handle_tts,
}

def dispatch(token: str, ctx: CommandContext) -> Optional[CommandResult]:
    """
    Smista il token al giusto handler.
    - Token esatto: cerca in _HANDLERS
    - Token __HOST__...: chiama handle_host con argomento
    - Token non riconosciuto: ritorna None (il chiamante lo gestisce)
    """
    if token in _HANDLERS:
        return _HANDLERS[token](ctx)

    if token and token.startswith("__HOST__"):
        arg = token[len("__HOST__"):]
        return handle_host(ctx, arg)

    return None   # non gestito qui — il chiamante usa il testo come risposta normale