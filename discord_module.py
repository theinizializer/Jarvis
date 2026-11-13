"""
discord_module.py — Integrazione Discord di JARVIS
====================================================
Contiene il mixin JarvisDiscordMixin con i metodi:

  - _process_discord(user_msg, ch_history)  → elabora un messaggio Discord
  - _init_discord()                          → inizializza il bot Discord
  - start_discord()                          → avvia il bot in un thread separato

Uso in jarvis_v9.py:
    from discord_module import JarvisDiscordMixin

    class Jarvis(JarvisDiscordMixin, ...):
        ...

I metodi usano self.* già presenti in Jarvis:
    self._stats, self.cwd, self.model, self.permanent,
    self.tts_on, self._agent, self._history, self._lock,
    self._executed_cmds, self._cooldowns, self._disc_bot,
    self.memorize(), self.show_memory(), self.forget_all(),
    self._needs_vision(), self._call_model(), self._parse_inline_tools(),
    self._execute_search(), self._execute(), self._clean_for_history()
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from utils_module import (
    MAX_HISTORY,
    DISCORD_TOKEN,
    _CPU_THREADS,
)

# Guardiano per i comandi via Discord.
# Su Discord NON c'e' un terminale per confermare, quindi il ruolo giusto e'
# 'secondary' (default-deny): passa solo cio' che classify giudica 'auto'
# (comandi di sola lettura). Tutto il resto -> negato, si usa il terminale locale.
try:
    from command_policy import classify as _policy_classify
    _POLICY_OK = True
except Exception:
    _POLICY_OK = False
    import re as _re
    # Fallback conservativo se command_policy manca: blocca i distruttivi noti.
    _RE_DESTRUCTIVE_FALLBACK = _re.compile(
        r'rm\s+-\w*r|rm\s+[^-]|\brmdir\b|\bdd\s+if=|\bmkfs\b|'
        r'\bshred\b|\bwipe\b|\bmv\b|\bchmod\b|\bchown\b|\bsudo\b|'
        r'>\s|\bmkdir\b|\btee\b|\bsystemctl\b',
        _re.IGNORECASE,
    )


def _discord_cmd_allowed(cmd: str) -> bool:
    """True solo se il comando e' sicuro da eseguire senza conferma umana."""
    if _POLICY_OK:
        return _policy_classify(cmd, role="secondary") == "auto"
    return not _RE_DESTRUCTIVE_FALLBACK.search(cmd)

try:
    import discord
    from discord.ext import commands as dc_commands
    DISCORD_OK = True
except Exception:
    DISCORD_OK = False

try:
    from agent_module import should_use_agent
    AGENT_OK = True
except ImportError:
    AGENT_OK = False
    def should_use_agent(msg): return False


class JarvisDiscordMixin:
    """
    Mixin con tutti i metodi di integrazione Discord.
    Ereditato da Jarvis — non istanziare direttamente.
    """

    # ── Elaborazione messaggio Discord ────────────────────────────────────────

    def _process_discord(self, user_msg, ch_history):
        """
        Elabora un messaggio arrivato da Discord.
        Ritorna (testo_risposta, lista_output_comandi).
        """
        output_lines: list[str] = []
        lower = user_msg.lower().strip()

        # Comandi rapidi
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
            return f"🔊 TTS {'ON ok' if self.tts_on else 'OFF ❌'}", output_lines

        # Agente per richieste complesse
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
            elif not _discord_cmd_allowed(cmd):
                # Guardiano v3 (command_policy, ruolo 'secondary'): su Discord
                # non c'è terminale per confermare, quindi passa SOLO ciò che
                # è classificato 'auto' (read-only). Il resto va eseguito
                # dal terminale locale di JARVIS.
                msg = f"🛡️ Comando negato via Discord: `{cmd}` — usa il terminale locale."
                output_lines.append(msg)
                self._stats["denied"] += 1
                cmd_results.append({"cmd": cmd, "output": msg, "status": "denied"})
            else:
                output_lines.append(f"▶ {cmd}")
                if expl:
                    output_lines.append(f"  💡 {expl}")
                result = self._execute(cmd, expl, history=ch_history)
                out = result.get('output', '')
                if out:
                    output_lines.append(out)
                cmd_results.append({"cmd": cmd, "output": out, "status": result.get("status", "ok")})

        # Se ci sono risultati, chiedi al modello di rispondere
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

        # Salva nella history del canale
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

    # ── Inizializzazione bot Discord ──────────────────────────────────────────

    def _init_discord(self):
        """Inizializza il bot Discord con gli event handler."""
        if not DISCORD_OK:
            print("❌ discord.py non installato — Discord disabilitato")
            return
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
                if message.author == self._disc_bot.user:
                    return
                if message.content.startswith('!'):
                    # BUG STORICO: prima si faceva return e basta — sovrascrivere
                    # on_message SENZA chiamare process_commands significa che
                    # NESSUN comando '!' ha mai potuto funzionare. Ora funzionano.
                    await self._disc_bot.process_commands(message)
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
                            await message.channel.send("ok Fatto")
                    except asyncio.TimeoutError:
                        await message.channel.send("⏱️ Timeout (150s)")
                    except Exception as e:
                        await message.channel.send(f"❌ {str(e)[:200]}")

            # Voce nei canali vocali (opzionale: serve discord-ext-voice-recv)
            try:
                from discord_voice_module import setup_voice
                setup_voice(self._disc_bot, self)
            except ImportError as e:
                # MAI in silenzio: se il file manca dalla cartella va DETTO.
                print(f"⚠️  Voce Discord SPENTA — discord_voice_module non "
                      f"caricabile: {e}")
            except Exception as e:
                print(f"⚠️  Voce Discord non attivata: {e}")

            print("ok Discord inizializzato")
        except Exception as e:
            print(f"❌ Discord init: {e}")

    # ── Avvio bot in thread separato ──────────────────────────────────────────

    def start_discord(self):
        """Avvia il bot Discord in un thread daemon separato."""
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
