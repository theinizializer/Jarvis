#!/usr/bin/env python3
"""
JARVIS — Voice Module v8.1
Contiene tutto il sistema vocale:
  - Scansione e selezione microfono
  - STT con faster-whisper (parecord + sounddevice)
  - TTS con gTTS (pipeline a 2 stadi: genera + riproduce in anticipo)
  - Wake word / Sleep word (fuzzy matching multi-lingua)
  - Speaker Verification con resemblyzer (profili multipli)
  - ask_modal(): interfaccia modale per conferme vocali e testuali
"""

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Callable

# ── Auto-install dipendenze opzionali ─────────────────────────────────────────
def _ensure_pkg(pip_name: str, import_name: str = None) -> bool:
    name = import_name or pip_name
    try:
        __import__(name)
        return True
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_name, "-q", "--break-system-packages"],
            capture_output=True,
        )
        try:
            __import__(name)
            return True
        except ImportError:
            return False

try:
    from faster_whisper import WhisperModel
    WHISPER_OK = True
except ImportError:
    WHISPER_OK = _ensure_pkg("faster-whisper", "faster_whisper")
    if WHISPER_OK:
        from faster_whisper import WhisperModel

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    SD_OK = True
except ImportError:
    ok = _ensure_pkg("sounddevice") and _ensure_pkg("soundfile") and _ensure_pkg("numpy")
    SD_OK = ok
    if SD_OK:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np

try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "gtts", "-q"], check=False)
    try:
        from gtts import gTTS
        GTTS_OK = True
    except Exception:
        GTTS_OK = False

try:
    from resemblyzer import VoiceEncoder, preprocess_wav
    RESEMBLYZER_OK = True
except ImportError:
    RESEMBLYZER_OK = False

# ── Costanti ──────────────────────────────────────────────────────────────────
SAMPLE_RATE       = 16000
PROFILES_DIR      = Path.home() / "jarvis_memory" / "voice_profiles"
SPEAKER_PROFILE   = Path.home() / "jarvis_memory" / "speaker_profile.npy"  # legacy
SPEAKER_THRESHOLD = 0.70
SESSION_TIMEOUT   = 120

# ── Wake / Sleep word ─────────────────────────────────────────────────────────
_WAKE_VARIANTS = {
    "jarvis", "jarvi", "jarvs", "jarbi", "jarwis", "javis", "jarves", "jarfis",
    "zarvis", "garvis", "harvis", "giorvis", "giorvi", "giervi", "giorviz",
    "jervis", "jervi", "yervis", "gervis", "gervi", "djarvis", "djarvi",
}
_RE_WAKE  = re.compile(r"\b(?:jar|gar|ger|gio|gie|jer|yer|djar)[a-z]{1,5}\b", re.IGNORECASE)
_RE_SLEEP = re.compile(r"\b(dormi|sleep|dors|schlaf)\b", re.IGNORECASE)
_RE_MIC   = re.compile(
    r"mic|microphone|input|capture|headset|cuffie|analog|"
    r"built.?in|internal|integrated|digital|acp|usb.?audio",
    re.IGNORECASE,
)

def _lev(a: str, b: str) -> int:
    """Distanza di Levenshtein."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    m = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        m[i][0] = i
    for j in range(len(b) + 1):
        m[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            m[i][j] = min(m[i - 1][j] + 1, m[i][j - 1] + 1, m[i - 1][j - 1] + c)
    return m[len(a)][len(b)]

def is_wake_word(text: str) -> bool:
    clean = text.lower().strip()
    for t in re.findall(r"[a-zàèìòùéç]+", clean):
        if t in _WAKE_VARIANTS:
            return True
        if len(t) >= 4 and _lev(t, "jarvis") <= 2:
            return True
    for m in _RE_WAKE.finditer(clean):
        if _lev(m.group().lower(), "jarvis") <= 3:
            return True
    return False

def is_sleep_word(text: str) -> bool:
    return bool(_RE_SLEEP.search(text.lower()))

def strip_wake_word(text: str) -> str:
    return re.sub(r"^[,\s!]+", "", _RE_WAKE.sub("", text).strip()).strip()

# ── Microfono: scansione e selezione ─────────────────────────────────────────

def _mic_sort(m: dict) -> int:
    n = str(m.get("name", "")).lower()
    if "acp6x" in n or "digital" in n:
        return 0
    if "built" in n or "internal" in n:
        return 1
    if "usb" in n:
        return 2
    if "monitor" in n:
        return 9
    return 5

def scan_microphones() -> list:
    """Rileva microfoni disponibili via pactl e sounddevice."""
    mics = []
    try:
        out = subprocess.check_output(
            ["pactl", "list", "sources"], text=True, stderr=subprocess.DEVNULL
        )
        cur = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith(("Nome:", "Name:")):
                name = line.split(":", 1)[1].strip()
                if ".monitor" not in name:
                    cur = {"source": "pactl", "pa_name": name, "name": name, "index": None}
            elif line.startswith(("Descrizione:", "Description:")) and cur:
                cur["description"] = line.split(":", 1)[1].strip()
            elif line.startswith(("Specifica", "Sample")) and cur.get("pa_name"):
                mics.append(cur)
                cur = {}
        if cur.get("pa_name"):
            mics.append(cur)
    except Exception:
        pass

    if SD_OK:
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    mics.append({
                        "source": "sd",
                        "index": i,
                        "name": d["name"],
                        "samplerate": int(d["default_samplerate"]),
                        "channels": d["max_input_channels"],
                    })
        except Exception:
            pass

    mics.sort(key=_mic_sort)
    return mics

def choose_microphone(ask_fn: Callable[[str], str] = None) -> Optional[dict]:
    """
    Sceglie il microfono.
    ask_fn: funzione per chiedere input all'utente (default: input() di Python).
            Passare _get_user_input del main per compatibilità TUI.
    """
    _ask = ask_fn or (lambda p: input(p).strip())

    print("\n🔍 Scansione microfoni...")
    mics = scan_microphones()
    if not mics:
        print("❌ Nessun microfono trovato.")
        return None

    real = [m for m in mics if _RE_MIC.search(str(m.get("name", "")))]
    pool = real if real else mics

    if len(pool) == 1:
        m = pool[0]
        print(f"🎙️  Microfono: {m.get('description') or m.get('name')}")
        return m

    print("🎙️  Microfoni disponibili:")
    for i, m in enumerate(pool, 1):
        label = m.get("description") or m.get("name")
        rate  = m.get("samplerate", "")
        print(f"  {i}. {label}" + (f" [{rate} Hz]" if rate else ""))

    scelta = _ask("Scelta [1]: ") or "1"
    try:
        return pool[int(scelta) - 1]
    except Exception:
        return pool[0]

# ── TTS helpers ───────────────────────────────────────────────────────────────

def _mute_mic(pa_name: str = None):
    """Muta il microfono durante TTS per evitare eco."""
    src = pa_name or "@DEFAULT_SOURCE@"
    try:
        subprocess.run(["pactl", "set-source-mute", src, "1"], capture_output=True)
    except Exception:
        pass

def _unmute_mic(pa_name: str = None):
    """Riattiva il microfono dopo TTS."""
    src = pa_name or "@DEFAULT_SOURCE@"
    try:
        subprocess.run(["pactl", "set-source-mute", src, "0"], capture_output=True)
    except Exception:
        pass

def find_tts_player() -> Optional[str]:
    for p in ("mpg123", "ffplay", "aplay", "cvlc"):
        if subprocess.run(["which", p], capture_output=True).returncode == 0:
            return p
    return None

# ══════════════════════════════════════════════════════════════════════════════
# TTS Engine — pipeline a 2 stadi (genera + riproduce in parallelo)
# ══════════════════════════════════════════════════════════════════════════════

class TTSEngine:
    """
    Stage 1 (_gen_worker)  — prende testo, genera mp3 in anticipo.
    Stage 2 (_play_worker) — prende mp3 pronti e li riproduce in sequenza.
    Mentre una frase è in riproduzione, la successiva viene già generata.
    """

    def __init__(self, lang: str = "it",
                 mem_dir: Path = Path.home() / "jarvis_memory",
                 pa_name: str = None):
        self.lang     = lang
        self.mem_dir  = Path(mem_dir)
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self._pa_name = pa_name
        self._player  = find_tts_player()

        self._text_q  = queue.Queue(maxsize=12)   # frasi da sintetizzare
        self._audio_q = queue.Queue(maxsize=6)    # path mp3 pronti
        self._speaking = threading.Event()

        threading.Thread(target=self._gen_worker,  daemon=True, name="tts-gen").start()
        threading.Thread(target=self._play_worker, daemon=True, name="tts-play").start()

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def say(self, text: str):
        if not GTTS_OK or not text or len(text) < 4:
            return
        try:
            self._text_q.put_nowait(text)
        except queue.Full:
            pass

    def stop(self):
        """Svuota le code e cancella gli mp3 in attesa."""
        for q in (self._text_q, self._audio_q):
            while not q.empty():
                try:
                    item = q.get_nowait()
                    if isinstance(item, str) and Path(item).exists():
                        Path(item).unlink(missing_ok=True)
                except Exception:
                    pass

    def shutdown(self):
        self._text_q.put(None)

    # ── Interno ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str) -> str:
        t = re.sub(r"[*_`#~>|]", "", text)
        t = re.sub(r":[a-z_]+:", "", t)
        t = re.sub(r"[\U00010000-\U0010ffff]", "", t, flags=re.UNICODE)
        return re.sub(r"\s+", " ", t).strip()

    def _gen_worker(self):
        while True:
            try:
                text = self._text_q.get(timeout=1)
            except queue.Empty:
                continue
            if text is None:
                self._audio_q.put(None)
                break
            clean = self._clean(text)
            if not clean or len(clean) < 3:
                self._text_q.task_done()
                continue
            tmp = self.mem_dir / f"tts_{int(time.time() * 1000)}.mp3"
            try:
                gTTS(text=clean, lang=self.lang, slow=False).save(str(tmp))
                if not tmp.exists() or tmp.stat().st_size < 100:
                    raise RuntimeError("mp3 vuoto")
                self._audio_q.put(str(tmp))
            except Exception as e:
                print(f"\n⚠️ TTS gen: {e}", flush=True)
                tmp.unlink(missing_ok=True)
            self._text_q.task_done()

    def _play_worker(self):
        _CMD = {
            "mpg123": ["mpg123", "-q"],
            "ffplay": ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
            "cvlc":   ["cvlc", "--play-and-exit", "-q"],
        }
        while True:
            try:
                path = self._audio_q.get(timeout=1)
            except queue.Empty:
                continue
            if path is None:
                break
            self._speaking.set()
            _mute_mic(self._pa_name)
            try:
                cmd = _CMD.get(self._player, [self._player]) + [path]
                subprocess.run(cmd, timeout=30, capture_output=True)
            except Exception as e:
                print(f"\n⚠️ TTS play: {e}", flush=True)
            finally:
                Path(path).unlink(missing_ok=True)
                self._speaking.clear()
                if self._audio_q.empty() and self._text_q.empty():
                    time.sleep(0.4)
                    _unmute_mic(self._pa_name)
            self._audio_q.task_done()

# ══════════════════════════════════════════════════════════════════════════════
# Speaker Verification
# ══════════════════════════════════════════════════════════════════════════════

class SpeakerVerifier:
    """
    Gestisce profili vocali multipli (nome × lingua).
    File: ~/jarvis_memory/voice_profiles/radostin_it.npy
    Migra automaticamente il vecchio speaker_profile.npy → owner_it.
    """

    def __init__(self, profiles_dir: Path = PROFILES_DIR,
                 threshold: float = SPEAKER_THRESHOLD):
        self._dir       = Path(profiles_dir)
        self._threshold = threshold
        self._encoder   = None
        self._profiles: dict[str, "np.ndarray"] = {}
        self.current_lang = "it"

        if RESEMBLYZER_OK:
            self._encoder = VoiceEncoder(device="cpu")
            self._load_all()

    # ── Persistenza ───────────────────────────────────────────────────────────

    def _key(self, name: str, lang: str) -> str:
        return f"{name.lower()}_{lang.lower()}"

    def _path(self, name: str, lang: str) -> Path:
        return self._dir / f"{name.lower()}_{lang.lower()}.npy"

    def _load_all(self):
        self._dir.mkdir(parents=True, exist_ok=True)

        # Migrazione profilo legacy
        if SPEAKER_PROFILE.exists() and not list(self._dir.glob("*.npy")):
            try:
                emb = np.load(str(SPEAKER_PROFILE))
                np.save(str(self._path("owner", "it")), emb)
                print("👤 Migrato profilo legacy → owner_it")
            except Exception:
                pass

        for p in self._dir.glob("*.npy"):
            try:
                self._profiles[p.stem] = np.load(str(p))
            except Exception:
                pass

        if self._profiles:
            print(f"👤 Speaker: ok {len(self._profiles)} profilo/i — {', '.join(self._profiles)}")
        else:
            print("👤 Speaker: ⚠️  nessun profilo (usa /aggiungi_voce)")

    # ── API pubblica ──────────────────────────────────────────────────────────

    @property
    def has_profile(self) -> bool:
        return any(k.endswith(f"_{self.current_lang}") for k in self._profiles)

    def list_profiles(self) -> list[tuple[str, str]]:
        """Ritorna lista di (nome, lang)."""
        result = []
        for key in self._profiles:
            parts = key.rsplit("_", 1)
            if len(parts) == 2:
                result.append((parts[0], parts[1]))
        return result

    def add_profile(self, audio: "np.ndarray", name: str, lang: str) -> bool:
        if not self._encoder:
            print("❌ resemblyzer non disponibile")
            return False
        try:
            wav = preprocess_wav(audio, source_sr=SAMPLE_RATE)
            emb = self._encoder.embed_utterance(wav)
            self._dir.mkdir(parents=True, exist_ok=True)
            np.save(str(self._path(name, lang)), emb)
            self._profiles[self._key(name, lang)] = emb
            print(f"ok Profilo salvato: {self._key(name, lang)}")
            return True
        except Exception as e:
            print(f"❌ Profilo: {e}")
            return False

    def identify(self, audio: "np.ndarray") -> tuple[Optional[str], float]:
        """
        Identifica lo speaker. Ritorna (nome, score) o (None, score).
        Cerca prima nella lingua corrente, poi in tutte.
        """
        if not self._encoder or not self._profiles:
            return "owner", 1.0
        try:
            wav = preprocess_wav(audio, source_sr=SAMPLE_RATE)
            emb = self._encoder.embed_utterance(wav)
            candidates = {k: v for k, v in self._profiles.items()
                          if k.endswith(f"_{self.current_lang}")}
            if not candidates:
                candidates = self._profiles

            best_name, best_score = None, 0.0
            for key, prof_emb in candidates.items():
                score = float(
                    np.dot(prof_emb, emb) /
                    (np.linalg.norm(prof_emb) * np.linalg.norm(emb) + 1e-9)
                )
                if score > best_score:
                    best_score = score
                    best_name = key.rsplit("_", 1)[0]

            return (best_name, best_score) if best_score >= self._threshold else (None, best_score)
        except Exception:
            return "owner", 1.0

    def verify(self, audio: "np.ndarray") -> tuple[bool, float]:
        name, score = self.identify(audio)
        return name is not None, score

    # Compatibilità legacy
    def create_profile(self, audio, name="owner", lang=None) -> bool:
        return self.add_profile(audio, name, lang or self.current_lang)

# ══════════════════════════════════════════════════════════════════════════════
# Voice Input — STT
# ══════════════════════════════════════════════════════════════════════════════

class VoiceInput:
    WHISPER_MODEL_SIZE = "medium"

    def __init__(self, mic: dict):
        self.mic           = mic
        self._model        = None
        self._model_lock   = threading.Lock()
        self._rms_thresh   = 0.003
        self._stop_event   = threading.Event()
        # Riferimenti iniettati dall'esterno
        self._tts_ref:     Optional[TTSEngine]      = None
        self._speaker_ref: Optional[SpeakerVerifier] = None
        self._lang_ref     = None  # LanguageManager

        if not WHISPER_OK:
            print("❌ faster-whisper non disponibile")
            return
        print(f"⏳ Carico Whisper '{self.WHISPER_MODEL_SIZE}' in background...")
        threading.Thread(target=self._load_whisper, daemon=True, name="whisper").start()

    def _load_whisper(self):
        try:
            m = WhisperModel(
                self.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8",
                cpu_threads=os.cpu_count() or 4, num_workers=2,
            )
            with self._model_lock:
                self._model = m
            print("ok Whisper pronto")
        except (KeyboardInterrupt, SystemExit):
            # Chiusura durante il download — termina silenziosamente
            return
        except Exception as e:
            print(f"❌ Whisper: {e}")

    def is_ready(self) -> bool:
        with self._model_lock:
            return self._model is not None

    def _wait_whisper(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            time.sleep(0.5)
        return False

    # ── Cattura audio ─────────────────────────────────────────────────────────

    def _parecord_capture(self, pa_name: str, duration: float) -> Optional["np.ndarray"]:
        tmp = Path(tempfile.mktemp(suffix=".wav"))
        proc = None
        try:
            proc = subprocess.Popen(
                ["parecord", "--device", pa_name, "--file-format=wav", str(tmp)],
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + duration
            while time.time() < deadline:
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)
            proc.terminate()
            proc.wait(timeout=3)

            if tmp.exists() and tmp.stat().st_size > 4096:
                data, sr = sf.read(str(tmp), dtype="float32")
                if len(data) < sr * 0.3:
                    return None
                mono = data.mean(axis=1) if data.ndim > 1 else data.flatten()
                if sr != SAMPLE_RATE:
                    mono = self._resample(mono, sr)
                return mono.astype(np.float32)
        except Exception as e:
            print(f"\n❌ parecord: {e}")
            return None
        finally:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            tmp.unlink(missing_ok=True)

    def _sd_capture(self, dev_idx: int, sr: int, ch: int,
                    timeout: float, silence_sec: float) -> Optional["np.ndarray"]:
        chunk_size  = int(sr * 0.1)
        sil_chunks  = int(silence_sec / 0.1)
        max_chunks  = int(timeout / 0.1)

        # Calibrazione soglia rumore di fondo
        try:
            noise = sd.rec(int(sr * 0.5), samplerate=sr, channels=ch,
                           dtype="float32", device=dev_idx, blocking=True)
            mono_n = noise.mean(axis=1) if ch > 1 else noise.flatten()
            self._rms_thresh = max(float(np.sqrt(np.mean(mono_n ** 2))) * 3.0, 0.002)
        except Exception:
            self._rms_thresh = 0.003

        frames, silent_count, started = [], 0, False
        try:
            with sd.InputStream(samplerate=sr, channels=ch, device=dev_idx,
                                dtype="float32", blocksize=chunk_size) as stream:
                for _ in range(max_chunks):
                    if self._stop_event.is_set():
                        break
                    data, _ = stream.read(chunk_size)
                    mono = data.mean(axis=1) if ch > 1 else data.flatten()
                    rms  = float(np.sqrt(np.mean(mono ** 2)))
                    if rms > self._rms_thresh:
                        started = True
                        silent_count = 0
                        frames.append(mono.copy())
                    elif started:
                        silent_count += 1
                        frames.append(mono.copy())
                        if silent_count >= sil_chunks:
                            break
        except Exception as e:
            print(f"\n❌ sounddevice: {e}")
            return None

        if not frames or not started:
            return None
        audio = np.concatenate(frames).astype(np.float32)
        if sr != SAMPLE_RATE:
            audio = self._resample(audio, sr)
        return audio

    @staticmethod
    def _resample(audio: "np.ndarray", src_sr: int) -> "np.ndarray":
        try:
            import scipy.signal as sps
            return sps.resample(audio, int(len(audio) * SAMPLE_RATE / src_sr)).astype(np.float32)
        except ImportError:
            step = src_sr / SAMPLE_RATE
            idx  = np.round(np.arange(0, len(audio), step)).astype(int)
            return audio[idx[idx < len(audio)]]

    def _record_sd(self, timeout: float = 10.0, silence_sec: float = 1.5) -> Optional["np.ndarray"]:
        """Registra audio scegliendo parecord se disponibile, altrimenti sounddevice."""
        has_parecord = subprocess.run(["which", "parecord"], capture_output=True).returncode == 0
        pa_name = self.mic.get("pa_name")

        # Tenta parecord
        if has_parecord and pa_name:
            result = self._parecord_capture(pa_name, duration=timeout)
            if result is not None:
                return result

        # Fallback sounddevice
        dev_idx = self.mic.get("index")
        if not isinstance(dev_idx, int):
            return None

        # Trova la combinazione samplerate/canali supportata
        try:
            info    = sd.query_devices(dev_idx)
            native  = int(info["default_samplerate"])
            max_ch  = info["max_input_channels"]
            combos  = [(sr, ch) for ch in range(max_ch, 0, -1)
                       for sr in (native, 48000, 44100, 16000)]
        except Exception:
            combos = [(48000, 2), (48000, 1), (44100, 2), (44100, 1)]

        wsr, wch = None, None
        for sr, ch in combos:
            try:
                sd.rec(int(sr * 0.1), samplerate=sr, channels=ch,
                       dtype="float32", device=dev_idx, blocking=True)
                wsr, wch = sr, ch
                break
            except Exception:
                continue

        if wsr is None:
            return None
        return self._sd_capture(dev_idx, wsr, wch, timeout, silence_sec)

    # ── Trascrizione ──────────────────────────────────────────────────────────

    def _transcribe(self, audio: "np.ndarray",
                    model=None, lang: str = "it") -> Optional[str]:
        if model is None:
            with self._model_lock:
                model = self._model
        if model is None:
            return None
        try:
            if audio.ndim > 1:
                audio = audio[:, 0]
            segs, _ = model.transcribe(
                audio, language=lang, beam_size=5, vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=400,
                    speech_pad_ms=200,
                    threshold=0.3,
                ),
            )
            text = " ".join(s.text.strip() for s in segs).strip()
            print("\r" + " " * 55 + "\r", end="")
            return text if len(text) > 1 else None
        except Exception as e:
            print(f"\n❌ Trascrizione: {e}")
            return None

    # ── Listen pubblico ───────────────────────────────────────────────────────

    def listen(self, timeout: float = 10.0, silence_sec: float = 1.5,
               lang: str = "it") -> Optional[str]:
        """
        Registra audio e trascrive con speaker verification.
        Aspetta automaticamente che il TTS finisca prima di ascoltare.
        """
        if not WHISPER_OK or self._stop_event.is_set():
            return None

        with self._model_lock:
            model = self._model
        if model is None:
            print("⏳ Whisper in caricamento, attendi...")
            if not self._wait_whisper(60):
                return None
            with self._model_lock:
                model = self._model

        # Aspetta fine TTS
        if self._tts_ref and self._tts_ref.is_speaking:
            while self._tts_ref.is_speaking:
                time.sleep(0.1)
            time.sleep(0.5)

        audio = self._record_sd(timeout=timeout, silence_sec=silence_sec)
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            return None

        # Speaker verification
        if self._speaker_ref and self._speaker_ref.has_profile:
            ok, score = self._speaker_ref.verify(audio)
            if not ok:
                print(f"  🔒 Voce ignorata ({score:.2f})", flush=True)
                return None

        return self._transcribe(audio, model, lang=lang)

    def stop(self):
        self._stop_event.set()

# ══════════════════════════════════════════════════════════════════════════════
# Voice Module — coordinatore principale
# ══════════════════════════════════════════════════════════════════════════════

class VoiceModule:
    """
    Coordina VoiceInput + SpeakerVerifier + TTSEngine + Wake/Sleep word.

    Nuova in v8.1:
      ask_modal(prompt, choices, timeout) — permette al TUI di ricevere
      conferme vocali in modo ordinato senza sovrapporre l'ascolto
      normale al loop di conferma.
    """

    def __init__(self, mic: dict, lang: str = "it",
                 mem_dir: Path = Path.home() / "jarvis_memory"):
        self.lang         = lang
        self._sleeping    = True
        self._session     = False
        self._session_t   = 0.0
        self._stop        = threading.Event()
        self._last_speaker: Optional[str] = None

        pa_name = mic.get("pa_name")
        self._voice   = VoiceInput(mic)
        self._speaker = SpeakerVerifier()
        self._speaker.current_lang = lang
        self._tts     = TTSEngine(lang=lang, mem_dir=Path(mem_dir), pa_name=pa_name)

        # Collega riferimenti interni
        self._voice._tts_ref     = self._tts
        self._voice._speaker_ref = self._speaker

        # Coda modale per ask_modal() — impostata da main() se TUI attivo
        # Quando è None usa ascolto vocale diretto.
        self._modal_q: Optional[queue.Queue] = None

    # ── TTS ───────────────────────────────────────────────────────────────────

    def say(self, text: str):
        self._tts.say(text)

    def stop_speaking(self):
        self._tts.stop()

    @property
    def tts_speaking(self) -> bool:
        return self._tts.is_speaking

    # ── Lingua ────────────────────────────────────────────────────────────────

    def set_lang(self, lang: str):
        self.lang = lang
        self._speaker.current_lang = lang

    # ── Modal input (usato da _confirm e dal worker) ──────────────────────────

    def ask_modal(self, prompt: str, valid_yes: set, valid_no: set,
                  timeout: float = 30.0) -> Optional[bool]:
        """
        Chiede conferma vocale o testuale.

        - Se _modal_q è impostata (TUI attivo): aspetta testo dalla coda.
        - Altrimenti: ascolta voce.

        Ritorna True (confermato), False (negato), None (timeout/errore).
        """
        if self._modal_q is not None:
            # Modalità TUI: il testo arriva dalla casella input
            try:
                answer = self._modal_q.get(timeout=timeout)
                answer_l = answer.strip().lower()
                if any(w in answer_l for w in valid_no):
                    return False
                if any(w in answer_l for w in valid_yes):
                    return True
                return None
            except queue.Empty:
                return None
        else:
            # Modalità solo voce: ascolta direttamente
            self.say(prompt)
            audio = self._voice._record_sd(timeout=timeout, silence_sec=2.0)
            if audio is None:
                return None
            text = self._voice._transcribe(audio, lang=self.lang)
            if not text:
                return None
            text_l = text.lower()
            if any(w in text_l for w in valid_no):
                return False
            if any(w in text_l for w in valid_yes):
                return True
            return None

    # ── Profili vocali ────────────────────────────────────────────────────────

    def add_voice_profile(self, name: str = None, lang: str = None,
                          ask_fn: Callable = None) -> bool:
        """
        Registra un nuovo profilo vocale.
        ask_fn: funzione per chiedere testo all'utente (default: input).
        """
        if not RESEMBLYZER_OK:
            print("❌ pip install resemblyzer")
            return False

        _ask = ask_fn or (lambda p: input(p).strip())
        lang = lang or self.lang

        if name is None:
            name = _ask("   Nome utente (es. radostin): ").strip().lower()
            if not name:
                print("❌ Nome non valido")
                return False

        print(f"\n🎙️  Registrazione profilo '{name}' [{lang}]")
        print("   Parla normalmente per 8-10 secondi...")

        audio = self._voice._record_sd(timeout=12.0, silence_sec=10.0)
        if audio is None or len(audio) < SAMPLE_RATE * 2:
            print("❌ Registrazione troppo corta — riprova")
            return False

        ok = self._speaker.add_profile(audio, name, lang)
        if ok:
            profiles = self._speaker.list_profiles()
            print(f"   Profili attivi: {', '.join(f'{n}_{l}' for n, l in profiles)}")
        return ok

    def list_voice_profiles(self) -> str:
        profiles = self._speaker.list_profiles()
        if not profiles:
            return "⚠️  Nessun profilo vocale registrato"
        lines = ["👤 Profili vocali registrati:"]
        for name, lang in sorted(profiles):
            marker = " ◀ attivo" if lang == self.lang else ""
            lines.append(f"   • {name} [{lang}]{marker}")
        return "\n".join(lines)

    # ── Ascolto principale ────────────────────────────────────────────────────

    def listen(self) -> Optional[str]:
        """
        Loop di ascolto: aspetta wake word, poi ritorna il comando.
        Gestisce sleep word e timeout sessione.
        """
        if not self._voice._wait_whisper(timeout=60):
            print("❌ Whisper non pronto")
            return None

        while not self._stop.is_set():
            if self._sleeping:
                audio = self._voice._record_sd(timeout=2.5, silence_sec=2.5)
                if audio is None:
                    continue
                text = self._voice._transcribe(audio, lang=self.lang)
                if not text:
                    continue
                if is_wake_word(text):
                    ok, score = self._speaker.verify(audio)
                    if not ok:
                        print(f"🔒 Voce non riconosciuta ({score:.2f})", flush=True)
                        continue
                    print(f"\n☀️  Sveglio! ({score:.2f})", flush=True)
                    self._sleeping   = False
                    self._session    = True
                    self._session_t  = time.time()
                    return self._get_command()
                continue

            # Sessione attiva
            if self._session and (time.time() - self._session_t < SESSION_TIMEOUT):
                return self._get_command()

            # Sessione scaduta
            self._session  = False
            self._sleeping = True

        return None

    def _get_command(self) -> Optional[str]:
        print("🎙️  Parla...", flush=True)
        audio = self._voice._record_sd(timeout=15.0, silence_sec=1.5)
        if audio is None:
            self._sleeping = True
            self._session  = False
            return None

        speaker_name, score = self._speaker.identify(audio)
        if speaker_name is None:
            print(f"🔒 Voce non riconosciuta ({score:.2f})", flush=True)
            return None
        self._last_speaker = speaker_name
        print(f"👤 {speaker_name} ({score:.2f})", flush=True)

        text = self._voice._transcribe(audio, lang=self.lang)
        if not text:
            return None
        print(f"📝 '{text}'", flush=True)

        if is_wake_word(text) and is_sleep_word(text):
            print("💤 Sleep mode", flush=True)
            self._sleeping = True
            self._session  = False
            return "__SLEEP__"

        self._session_t = time.time()
        cmd = strip_wake_word(text) if is_wake_word(text) else text
        return cmd.strip() or None

    def stop(self):
        self._stop.set()
        self._voice.stop()
        self._tts.shutdown()

    def status(self) -> str:
        profiles  = self._speaker.list_profiles()
        prof_str  = ", ".join(f"{n}_{l}" for n, l in profiles) if profiles else "⚠️  nessuno"
        whisper_s = "ok" if self._voice.is_ready() else "⏳ caricando..."
        tts_s     = "ok" if self._tts._player else "❌"
        state_s   = "💤 sleep" if self._sleeping else "👂 attivo"
        return (
            f"🎙️  Voice Module v8.1:\n"
            f"   Whisper:  {whisper_s}\n"
            f"   Profili:  {prof_str}\n"
            f"   TTS:      {tts_s}\n"
            f"   Lingua:   {self.lang}\n"
            f"   Stato:    {state_s}"
        )


# ── Esecuzione diretta (test) ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("🎙️  JARVIS Voice Module v8.1")
    print("=" * 50)

    mic = choose_microphone()
    if not mic:
        sys.exit(1)

    vm = VoiceModule(mic=mic, lang="it")

    if "--setup-speaker" in sys.argv:
        vm.add_voice_profile()
        sys.exit(0)

    print(vm.status())
    print("\nDì 'Jarvis' per attivare | 'Jarvis dormi' per sleep | Ctrl+C per uscire\n")

    try:
        while True:
            result = vm.listen()
            if result is None:
                break
            if result == "__SLEEP__":
                print("💤 Standby\n")
                continue
            print(f"ok '{result}'\n")
            vm.say(f"Hai detto: {result}")
    except KeyboardInterrupt:
        vm.stop()
        print("\nStop.")
