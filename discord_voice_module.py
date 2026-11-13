#!/usr/bin/env python3
"""
discord_voice_module.py — JARVIS nei canali vocali Discord
===========================================================
NON è una telefonata: i bot non possono fare/ricevere chiamate DM (vietato
dai ToS). Questo modulo fa entrare JARVIS in un CANALE VOCALE del server,
dove ascolta, trascrive e risponde a voce.

Come si usa (lato utente):
  - AUTO-JOIN: entri nel canale vocale configurato -> JARVIS entra da solo.
  - `!vieni`  : JARVIS raggiunge il canale vocale in cui ti trovi.
  - `!esci`   : JARVIS lascia il canale.
  - Quando l'ultimo umano esce, JARVIS esce da solo.
  - In canale, JARVIS risponde SOLO alle frasi che iniziano con "jarvis"
    (wake word): il resto viene ignorato e scartato — niente trascrizione
    di tutta la conversazione, per privacy e per non bruciare quota.

Config (variabili d'ambiente):
  JARVIS_VOICE_AUTOJOIN  nome (o pezzo di nome) del canale per l'auto-join.
                         Default: "general" (matcha "General", "Generale"...).
                         "*"  = qualsiasi canale (SCONSIGLIATO: invadente
                                verso chi non sa di essere trascritto).
                         ""   = auto-join spento, solo !vieni.
  JARVIS_VOICE_WAKE      wake word, default "jarvis".

Sicurezza: le richieste passano da _process_discord() -> guardiano
command_policy con ruolo 'secondary' (solo read-only). Il nome utente
Discord identifica l'ACCOUNT, non la persona: per questo il canale vocale
non ottiene MAI privilegi admin.

Trascrizione: Groq whisper-large-v3-turbo (veloce, bucket separato dai
token LLM, MA: ~2000 richieste/giorno e minimo 10s fatturati a richiesta
sul free tier). Fallback automatico al faster-whisper locale su errore/429.

Dipendenze:  pip install discord-ext-voice-recv PyNaCl
             sistema: ffmpeg, libopus
Consenso: all'ingresso nel canale JARVIS annuncia in chat testuale che
l'audio con wake word viene trascritto via cloud.
"""

import asyncio
import io
import os
import struct
import threading
import time
import wave

try:
    import numpy as np
    NUMPY_OK = True
except ImportError:
    NUMPY_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    import discord
    from discord.ext import voice_recv
    VOICE_RECV_OK = True
except Exception:
    VOICE_RECV_OK = False

# ── Costanti audio ─────────────────────────────────────────────────────────────
DISCORD_SR   = 48_000   # Discord consegna PCM 48kHz 16-bit stereo
WHISPER_SR   = 16_000   # Whisper vuole 16kHz mono
SILENCE_SEC  = 1.1      # pausa che chiude una frase
MIN_UTT_SEC  = 0.4      # frasi più corte = rumore, scartate
MAX_UTT_SEC  = 30.0     # tetto di sicurezza per frase
WATCH_TICK   = 0.25     # frequenza del controllo buffer

GROQ_STT_URL   = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3-turbo"


# ══════════════════════════════════════════════════════════════════════════════
# Utility audio pure (testabili senza Discord)
# ══════════════════════════════════════════════════════════════════════════════

def pcm48k_stereo_to_16k_mono(pcm: bytes) -> bytes:
    """PCM 48kHz 16-bit stereo -> 16kHz 16-bit mono.
    48000/16000 = 3 esatto: media a gruppi di 3 (decimazione con media,
    che fa anche da mini filtro anti-alias — per il parlato è sufficiente).
    NOTA: niente audioop, rimosso in Python 3.13 — solo numpy."""
    if not NUMPY_OK or not pcm:
        return b""
    if len(pcm) % 2:                 # byte spaiato (pacchetto troncato): via
        pcm = pcm[:-1]
    if len(pcm) < 12:
        return b""
    arr = np.frombuffer(pcm, dtype=np.int16)
    # stereo -> mono
    if arr.size % 2:
        arr = arr[:-1]
    mono = arr.reshape(-1, 2).astype(np.int32).mean(axis=1)
    # 48k -> 16k
    usable = (mono.size // 3) * 3
    if usable == 0:
        return b""
    down = mono[:usable].reshape(-1, 3).mean(axis=1)
    return np.clip(down, -32768, 32767).astype(np.int16).tobytes()


def pcm16k_to_wav(pcm: bytes) -> bytes:
    """Impacchetta PCM 16kHz mono 16-bit in un WAV in memoria."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(WHISPER_SR)
        w.writeframes(pcm)
    return buf.getvalue()


def strip_wake(text: str, wake: str) -> str | None:
    """Se il testo inizia con la wake word ritorna il resto (pulito),
    altrimenti None. Tollera punteggiatura di Whisper: 'Jarvis, che ore sono?'"""
    t = " ".join((text or "").lower().split())
    if not t.startswith(wake.lower()):
        return None
    rest = t[len(wake):].lstrip(" ,.:;!?")
    return rest


# ══════════════════════════════════════════════════════════════════════════════
# Trascrizione: Groq con fallback locale
# ══════════════════════════════════════════════════════════════════════════════

class STTBackend:
    """Groq whisper-large-v3-turbo con fallback al faster-whisper locale.
    local_stt: callable(wav_bytes) -> str, opzionale (il Whisper già caricato
    da voice_module). Se Groq fallisce (rete/429) si passa al locale."""

    def __init__(self, groq_api_key: str = "", local_stt=None):
        self.key = groq_api_key or os.environ.get("GROQ_API_KEY", "")
        self.local_stt = local_stt
        self._groq_down_until = 0.0   # backoff dopo un 429

    def transcribe(self, wav_bytes: bytes, language: str = None) -> str:
        if self.key and REQUESTS_OK and time.time() >= self._groq_down_until:
            try:
                data = {"model": GROQ_STT_MODEL, "response_format": "json"}
                if language:
                    data["language"] = language
                r = requests.post(
                    GROQ_STT_URL,
                    headers={"Authorization": f"Bearer {self.key}"},
                    files={"file": ("utt.wav", wav_bytes, "audio/wav")},
                    data=data,
                    timeout=20,
                )
                if r.status_code == 429:
                    # quota oraria/giornaliera: non insistere per 5 minuti
                    self._groq_down_until = time.time() + 300
                    print("⚠️  Groq STT 429 — passo al Whisper locale per 5 min")
                elif r.ok:
                    return (r.json().get("text") or "").strip()
                else:
                    print(f"⚠️  Groq STT {r.status_code}: {r.text[:120]}")
            except Exception as e:
                print(f"⚠️  Groq STT errore: {e} — provo il locale")
        if self.local_stt:
            try:
                return (self.local_stt(wav_bytes) or "").strip()
            except Exception as e:
                print(f"❌ STT locale errore: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Buffer per-utente con chiusura frase a silenzio (logica pura, testabile)
# ══════════════════════════════════════════════════════════════════════════════

class UtteranceBuffers:
    """Accumula PCM per utente; una frase si chiude dopo SILENCE_SEC senza
    pacchetti. Discord separa già i flussi per utente: niente SepFormer,
    niente ECAPA — l'identità arriva dai metadati (nome account, non persona)."""

    def __init__(self, now_fn=time.time):
        self._buf: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._now = now_fn

    def feed(self, user_id: int, display_name: str, pcm: bytes):
        with self._lock:
            b = self._buf.setdefault(
                user_id, {"name": display_name, "chunks": [], "last": 0.0}
            )
            b["chunks"].append(pcm)
            b["last"] = self._now()
            b["name"] = display_name

    def pop_finished(self) -> list[tuple[str, bytes]]:
        """Ritorna [(nome, pcm48k_stereo)] delle frasi chiuse (silenzio o tetto)."""
        out = []
        now = self._now()
        with self._lock:
            for uid, b in list(self._buf.items()):
                if not b["chunks"]:
                    continue
                dur = sum(len(c) for c in b["chunks"]) / (DISCORD_SR * 2 * 2)
                if (now - b["last"]) >= SILENCE_SEC or dur >= MAX_UTT_SEC:
                    pcm = b"".join(b["chunks"])
                    b["chunks"].clear()
                    if dur >= MIN_UTT_SEC:
                        out.append((b["name"], pcm))
        return out


# ══════════════════════════════════════════════════════════════════════════════
# Setup: si aggancia al bot esistente di discord_module
# ══════════════════════════════════════════════════════════════════════════════

def setup_voice(bot, jarvis):
    """Registra comandi ed eventi vocali sul bot ESISTENTE.
    Chiamato da discord_module._init_discord() dopo la creazione del bot.
    `jarvis` è l'istanza Jarvis (per _process_discord, lingua, mem_dir)."""
    if not VOICE_RECV_OK:
        print("⚠️  discord-ext-voice-recv non installato — voce Discord spenta "
              "(pip install discord-ext-voice-recv PyNaCl)")
        return

    autojoin = os.environ.get("JARVIS_VOICE_AUTOJOIN", "general").strip().lower()
    wake     = os.environ.get("JARVIS_VOICE_WAKE", "jarvis").strip() or "jarvis"

    # STT: Groq + fallback al Whisper locale se voice_module l'ha caricato
    local = None
    vi = getattr(jarvis, "_voice_input", None)
    if vi is not None and hasattr(vi, "transcribe_wav_bytes"):
        local = vi.transcribe_wav_bytes
    from utils_module import GROQ_API_KEY
    stt = STTBackend(groq_api_key=GROQ_API_KEY, local_stt=local)

    buffers   = UtteranceBuffers()
    state     = {"vc": None, "watcher": None, "history": [], "announced": set()}
    play_lock = asyncio.Lock()

    # ── TTS della risposta nel canale ─────────────────────────────────────────
    async def _speak(vc, text: str):
        try:
            from gtts import gTTS
        except ImportError:
            return
        lang = getattr(getattr(jarvis, "_lang", None), "current", None) or "it"
        path = f"/tmp/jarvis_dv_{int(time.time()*1000)}.mp3"
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: gTTS(text=text[:500], lang=lang).save(path)
            )
            async with play_lock:                       # una risposta alla volta
                if vc and vc.is_connected():
                    done = asyncio.Event()
                    vc.play(discord.FFmpegPCMAudio(path),
                            after=lambda e: done.set())
                    await done.wait()
        except Exception as e:
            print(f"⚠️  TTS canale: {e}")
        finally:
            try: os.unlink(path)
            except OSError: pass

    # ── Elaborazione di una frase chiusa ──────────────────────────────────────
    async def _handle_utterance(name: str, pcm48: bytes):
        pcm16 = pcm48k_stereo_to_16k_mono(pcm48)
        if not pcm16:
            return
        wav = pcm16k_to_wav(pcm16)
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, stt.transcribe, wav)
        if not text:
            print(f"🗣️  [{name}] Whisper non ha trascritto nulla (audio vuoto/rumore?)")
            return
        print(f"\n🎙️  [{name}] Whisper: \"{text}\"")
        query = strip_wake(text, wake)
        if query is None:
            print(f"   (nessuna wake word '{wake}' → ignorata)")
            return
        if not query:
            await _speak(state["vc"], "Sì?")
            return
        # Pipeline Discord ESISTENTE: stesso guardiano 'secondary', stessa history
        reply, lines = await loop.run_in_executor(
            None, jarvis._process_discord, f"[{name} a voce] {query}",
            state["history"],
        )
        out = reply or "\n".join(lines) or "Fatto."
        await _speak(state["vc"], out)

    # ── Watcher: chiude le frasi a silenzio ───────────────────────────────────
    async def _watcher():
        print("🎧 Watcher frasi avviato.")
        while state["vc"] and state["vc"].is_connected():
            try:
                for name, pcm in buffers.pop_finished():
                    print(f"🗣️  Frase chiusa da {name} ({len(pcm)} byte) → trascrivo…")
                    asyncio.create_task(_handle_utterance(name, pcm))
            except Exception as e:
                print(f"❌ watcher: {type(e).__name__}: {e}")
            await asyncio.sleep(WATCH_TICK)
        print("🎧 Watcher terminato (disconnesso).")

    # ── Connessione a un canale ───────────────────────────────────────────────
    async def _join(channel):
        if state["vc"] and state["vc"].is_connected():
            if state["vc"].channel.id == channel.id:
                return state["vc"]
            await state["vc"].move_to(channel)
        else:
            state["vc"] = await channel.connect(cls=voice_recv.VoiceRecvClient)

        # Diagnostica CORRETTA: conto OGNI pacchetto PRIMA di scartare user=None.
        # La docs dell'estensione dice che il PacketRouter mappa l'SSRC->Member,
        # e finché quella mappatura non parte il callback riceve user=None.
        # Contare dopo lo scarto (come facevo prima) NASCONDE l'audio in arrivo.
        state.setdefault("pkt_count", 0)
        state.setdefault("pkt_named", 0)

        def on_packet(user, data):
            try:
                state["pkt_count"] += 1
                if state["pkt_count"] == 1:
                    print(f"🎧 PRIMO pacchetto ARRIVATO (user={user}) — l'audio "
                          f"raggiunge il bot. L'orecchio fisico funziona.")
                pcm = getattr(data, "pcm", None)
                if user is None:
                    # SSRC non ancora mappato a un membro: normale nei primi
                    # istanti. Se resta SEMPRE None, è quello il problema.
                    if state["pkt_count"] % 200 == 0:
                        print(f"⚠️  {state['pkt_count']} pacchetti ma user ancora None "
                              f"(SSRC non mappato) — attendo on_voice_member_connect…")
                    return
                if getattr(user, "bot", False) or not pcm:
                    return
                state["pkt_named"] += 1
                if state["pkt_named"] == 1:
                    print(f"🎧 Primo pacchetto CON UTENTE: {getattr(user,'display_name',user)} "
                          f"({len(pcm)} byte) — mappatura SSRC ok.")
                buffers.feed(user.id,
                             getattr(user, "display_name", str(user)), pcm)
            except Exception as e:
                print(f"❌ on_packet ha lanciato: {type(e).__name__}: {e}")

        state["vc"].listen(voice_recv.BasicSink(on_packet, decode=True))
        print("🎧 Sink agganciato. Parla: se NON vedi nemmeno 'PRIMO pacchetto "
              "ARRIVATO', Discord non manda audio (permesso/versione).")
        if state["watcher"] is None or state["watcher"].done():
            state["watcher"] = asyncio.create_task(_watcher())
        # Consenso: annuncio (una volta per canale) che l'audio va nel cloud
        if channel.id not in state["announced"]:
            state["announced"].add(channel.id)
            try:
                txt = channel.guild.system_channel or next(
                    (c for c in channel.guild.text_channels
                     if c.permissions_for(channel.guild.me).send_messages), None)
                if txt:
                    await txt.send(
                        f"🎙️ Sono in **{channel.name}**. Le frasi che iniziano con "
                        f"“{wake}” vengono trascritte (cloud Groq). `!esci` per farmi uscire."
                    )
            except Exception:
                pass
        print(f"🎙️  JARVIS nel canale vocale: {channel.name}")
        return state["vc"]

    async def _leave():
        if state["vc"] and state["vc"].is_connected():
            ch = state["vc"].channel.name
            await state["vc"].disconnect()
            print(f"🎙️  JARVIS ha lasciato: {ch}")
        state["vc"] = None

    # ── Comandi ───────────────────────────────────────────────────────────────
    @bot.command(name="vieni")
    async def _cmd_vieni(ctx):
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.send("Devi essere in un canale vocale del server, signore. "
                           "(Le chiamate private DM non sono raggiungibili dai bot.)")
            return
        ch = ctx.author.voice.channel
        perms = ch.permissions_for(ctx.guild.me)
        if not perms.connect:
            await ctx.send(f"❌ Non ho il permesso **Connetti** su '{ch.name}'. "
                           "Controlla il ruolo E i permessi del singolo canale "
                           "(rotellina sul canale → Permessi).")
            return
        try:
            await _join(ch)
            await ctx.message.add_reaction("🎙️")
            if not perms.speak:
                await ctx.send("⚠️ Sono entrato ma senza permesso **Parla**: "
                               "ascolto e basta.")
        except Exception as e:
            # MAI in silenzio: l'errore va in chat, così si può debuggare
            await ctx.send(f"❌ Non riesco a entrare: `{type(e).__name__}: {e}`")
            import traceback; traceback.print_exc()

    @bot.command(name="esci")
    async def _cmd_esci(ctx):
        await _leave()
        await ctx.message.add_reaction("👋")

    # ── Auto-join / auto-leave ────────────────────────────────────────────────
    @bot.listen("on_voice_state_update")
    async def _on_voice(member, before, after):
        if member.bot:
            return
        # auto-join: un umano entra nel canale configurato
        if autojoin and after.channel and (before.channel != after.channel):
            name = after.channel.name.lower()
            if autojoin == "*" or autojoin in name:
                try:
                    await _join(after.channel)
                except Exception as e:
                    print(f"⚠️  auto-join '{after.channel.name}' fallito: "
                          f"{type(e).__name__}: {e}")
            else:
                print(f"ℹ️  '{after.channel.name}' non matcha JARVIS_VOICE_AUTOJOIN="
                      f"'{autojoin}' — niente auto-join (usa !vieni o cambia il .env)")
        # auto-leave: JARVIS resta solo
        vc = state["vc"]
        if vc and vc.is_connected():
            humans = [m for m in vc.channel.members if not m.bot]
            if not humans:
                await _leave()

    @bot.listen("on_command_error")
    async def _on_cmd_err(ctx, error):
        # Un comando che esplode deve DIRLO, non sparire nel nulla.
        try:
            await ctx.send(f"❌ `{ctx.invoked_with}`: {type(error).__name__}: {error}")
        except Exception:
            pass
        print(f"❌ comando !{ctx.invoked_with}: {error}")

    print(f"ok Voce Discord pronta (auto-join: '{autojoin or 'off'}', wake: '{wake}')")
