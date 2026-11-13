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

from contextlib import contextmanager
import os
import math
import hashlib
import json
import queue
import re
import select
import shutil
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
    GTTS_OK = False

try:
    from resemblyzer import VoiceEncoder, preprocess_wav
    RESEMBLYZER_OK = True
except ImportError:
    RESEMBLYZER_OK = False

# ── ECAPA-TDNN (speaker verification — molto piu' accurato di Resemblyzer) ─────
# Stato dell'arte, gia' disponibile via SpeechBrain (usato anche dal SepFormer).
# Separa molto meglio "io" dagli "altri": margini ampi invece dei 10-20 punti
# stretti di Resemblyzer.
try:
    from speechbrain.inference.speaker import EncoderClassifier as _ECAPAClassifier
    ECAPA_OK = True
except Exception:
    try:
        from speechbrain.pretrained import EncoderClassifier as _ECAPAClassifier
        ECAPA_OK = True
    except Exception:
        ECAPA_OK = False

# ── SepFormer (speaker extraction — richiede torch ROCm/CUDA) ─────────────────
try:
    import torch
    TORCH_OK = True
    # ROCm espone CUDA via HIP — ma SpeechBrain vuole libcudart
    # Se non trova libcudart, usa CPU invece di crashare
    if torch.cuda.is_available():
        try:
            torch.zeros(1).cuda()  # test veloce
            TORCH_DEVICE = "cuda"
        except Exception:
            TORCH_DEVICE = "cpu"
    else:
        TORCH_DEVICE = "cpu"
except ImportError:
    TORCH_OK     = False
    TORCH_DEVICE = "cpu"

try:
    from scipy.signal import butter, sosfilt
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False

_sepformer_model = None
_sepformer_lock  = threading.Lock()

def _load_sepformer():
    """Carica SepFormer in lazy loading — GPU se disponibile, altrimenti CPU."""
    global _sepformer_model
    with _sepformer_lock:
        if _sepformer_model is not None:
            return _sepformer_model
        if not TORCH_OK:
            return None
        try:
            from speechbrain.inference.separation import SepformerSeparation
            # "cuda:0" invece di "cuda" — evita il warning di parsing
            device = "cuda:0" if TORCH_DEVICE == "cuda" else "cpu"
            _sepformer_model = SepformerSeparation.from_hparams(
                source="speechbrain/sepformer-wham16k-enhancement",
                run_opts={"device": device},
                savedir=str(Path.home() / "jarvis_memory" / "sepformer_cache"),
            )
            print(f"ok SepFormer caricato su {device.upper()}", flush=True)
            return _sepformer_model
        except Exception as e:
            if "libcudart" in str(e) or "cuda" in str(e).lower():
                try:
                    from speechbrain.inference.separation import SepformerSeparation
                    _sepformer_model = SepformerSeparation.from_hparams(
                        source="speechbrain/sepformer-wham16k-enhancement",
                        run_opts={"device": "cpu"},
                        savedir=str(Path.home() / "jarvis_memory" / "sepformer_cache"),
                    )
                    print("ok SepFormer caricato su CPU (ROCm fallback)", flush=True)
                    return _sepformer_model
                except Exception as e2:
                    print(f"  [ATTENZIONE] SepFormer CPU fallito: {e2}", flush=True)
            else:
                print(f"  [ATTENZIONE] SepFormer non disponibile: {e}", flush=True)
            return None

# ── Costanti ──────────────────────────────────────────────────────────────────
SAMPLE_RATE       = 16000
PROFILES_DIR      = Path.home() / "jarvis_memory" / "voice_profiles"
SPEAKER_PROFILE   = Path.home() / "jarvis_memory" / "speaker_profile.npy"  # legacy
SPEAKER_THRESHOLD = 0.70          # soglia Resemblyzer (fallback)
SPEAKER_THRESHOLD_ECAPA = 0.55    # soglia ECAPA — i punteggi sono distribuiti
                                  # diversamente: con ECAPA "io" sta tipicamente
                                  # 0.7-0.9 e gli altri sotto 0.45, quindi 0.55
                                  # separa bene senza escludere la voce giusta.
SESSION_TIMEOUT   = 120

# ── Wake / Sleep word ─────────────────────────────────────────────────────────
from voice_commands import _WAKE_VARIANTS, _RE_WAKE, _RE_SLEEP, _lev, is_wake_word, is_sleep_word, strip_wake_word
_RE_MIC   = re.compile(
    r"mic|microphone|input|capture|headset|cuffie|analog|"
    r"built.?in|internal|integrated|digital|acp|usb.?audio",
    re.IGNORECASE,
)

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
        out = subprocess.check_output(['pactl', '--format=json', 'list', 'sources'], text=True,
                                      stderr=subprocess.DEVNULL, timeout=5)
        for source in json.loads(out):
            props = source.get('properties', {})
            name = source.get('name', '')
            if props.get('device.class') == 'monitor' or name.endswith('.monitor'):
                continue
            card = props.get('api.alsa.pcm.card')
            device = props.get('api.alsa.pcm.device')
            if card is None:
                card = props.get('alsa.card')
            if device is None:
                device = props.get('alsa.device')
            identity = f'alsa:{card}:{device}' if card is not None and device is not None else None
            mics.append({'source': 'pactl', 'pa_name': name, 'name': name, 'index': None,
                         'description': source.get('description', name), 'physical_id': identity,
                         'physical': props.get('device.api') in {'alsa', 'bluez5', 'bluez'} or name.startswith(('alsa_input.', 'bluez_input.'))})
    except (OSError, ValueError, subprocess.SubprocessError):
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

def probe_microphones(microphones, stop_event, seconds=5):
    """Capture a short independent sample from each candidate and require real speech."""
    from audio_checks import recording_report
    results = queue.Queue()
    workers = []
    for microphone in microphones:
        def check(mic=microphone):
            try:
                recorder = VoiceInput.__new__(VoiceInput)
                recorder.mic = mic
                recorder._stop_event = stop_event
                recorder._rms_thresh = .003
                audio = recorder.record_sample(seconds)
                report = recording_report(audio, SAMPLE_RATE, minimum_duration=seconds * .8, minimum_voice=.6)
            except Exception as exc:
                report = {'ok': False, 'message': 'Ingresso non utilizzabile: ' + str(exc)}
            results.put({'microphone': mic, 'quality': report})
        worker = threading.Thread(target=check, daemon=True, name='microphone-check')
        workers.append(worker)
        worker.start()
    deadline = time.monotonic() + seconds + 8
    for worker in workers:
        while worker.is_alive() and not stop_event.is_set() and time.monotonic() < deadline:
            worker.join(timeout=.1)
    if any(worker.is_alive() for worker in workers):
        stop_event.set()
    checked = []
    while not results.empty():
        checked.append(results.get_nowait())
    return checked


@contextmanager
def open_microphone_input(microphone, **options):
    """Negotiate the selected device's actual rate/channels before yielding audio."""
    device = microphone.get('index')
    name = microphone.get('name')
    if name:
        devices = sd.query_devices()
        current = devices[device] if isinstance(device, int) and 0 <= device < len(devices) else None
        if current is None or current['name'] != name or current['max_input_channels'] < 1:
            matches = [(i, item) for i, item in enumerate(devices)
                       if item['name'] == name and item['max_input_channels'] > 0]
            if len(matches) != 1:
                raise RuntimeError('Il microfono salvato non è più identificabile. Selezionalo di nuovo dalla lista.')
            device, current = matches[0]
        info = current
    else:
        info = sd.query_devices(device, 'input')
    maximum = int(info['max_input_channels'])
    if maximum < 1:
        raise RuntimeError('Il dispositivo selezionato non ha ingressi audio.')
    rates = list(dict.fromkeys(int(rate) for rate in (
        info['default_samplerate'], microphone.get('samplerate') or SAMPLE_RATE,
        48000, 44100, SAMPLE_RATE, 32000, 96000) if rate and rate > 0))
    channel_counts = list(dict.fromkeys((1, min(2, maximum), maximum)))
    last_error = None
    for rate in rates:
        for channels in channel_counts:
            stream = None
            try:
                sd.check_input_settings(device=device, samplerate=rate, channels=channels, dtype='float32')
                stream = sd.InputStream(device=device, samplerate=rate, channels=channels,
                                        dtype='float32', **options)
                stream.start()
            except sd.PortAudioError as exc:
                last_error = exc
                if stream is not None:
                    stream.close()
                continue
            try:
                yield stream, rate, channels
            finally:
                stream.close()
            return
    raise RuntimeError('Il microfono non accetta i formati audio provati. '
                       'Scegli un altro ingresso o riconnetti il dispositivo. ' + str(last_error))


class MicrophoneMonitor:
    """Measure live levels without retaining audio or initializing speech models."""

    def __init__(self, microphones):
        self.microphones = microphones
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._levels = {}
        self._processes = set()
        self._threads = []

    def start(self):
        for index, microphone in enumerate(self.microphones):
            worker = threading.Thread(target=self._measure, args=(index, microphone),
                                      daemon=True, name=f'microphone-level-{index}')
            self._threads.append(worker)
            worker.start()

    def _publish(self, index, samples):
        if self._stop.is_set() or not len(samples):
            return
        samples = np.asarray(samples, dtype=np.float32)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(np.abs(samples)))
        if not math.isfinite(rms) or not math.isfinite(peak):
            return
        value = (max(-90.0, 20 * math.log10(max(rms, .00003))), peak >= .995, time.monotonic())
        with self._lock:
            self._levels[index] = value

    def snapshot(self):
        with self._lock:
            levels = dict(self._levels)
        now = time.monotonic()
        return {index: value if value is None or now - value[2] < 1 else None
                for index, value in levels.items()}

    def _measure(self, index, microphone):
        try:
            if microphone.get('source') == 'pactl' and microphone.get('pa_name'):
                self._pipewire(index, microphone['pa_name'])
            else:
                def audio(data, frames, timing, status):
                    self._publish(index, data)
                with open_microphone_input(microphone, callback=audio, blocksize=0):
                    self._stop.wait()
        except Exception:
            with self._lock:
                self._levels[index] = None

    def _pipewire(self, index, target):
        if shutil.which('pw-record'):
            command = ['pw-record', '--raw', '--rate', str(SAMPLE_RATE), '--channels', '1',
                       '--format', 's16', '--target', target, '-']
        else:
            command = ['parecord', '--raw', f'--rate={SAMPLE_RATE}', '--channels=1',
                       '--format=s16le', '--device=' + target]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with self._lock:
            self._processes.add(process)
        try:
            last_frame = time.monotonic()
            remainder = b''
            while not self._stop.is_set():
                if not select.select([process.stdout], [], [], .1)[0]:
                    if time.monotonic() - last_frame > 5:
                        raise RuntimeError('Il dispositivo non fornisce audio.')
                    continue
                block = os.read(process.stdout.fileno(), 3200)
                if not block:
                    if not self._stop.is_set():
                        raise RuntimeError('Dispositivo audio terminato.')
                    break
                last_frame = time.monotonic()
                block = remainder + block
                size = len(block) // 2 * 2
                remainder = block[size:]
                self._publish(index, np.frombuffer(block[:size], dtype=np.int16).astype(np.float32) / 32768)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
            with self._lock:
                self._processes.discard(process)

    def stop(self):
        self._stop.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    def wait(self, timeout=2):
        deadline = time.monotonic() + timeout
        for worker in self._threads:
            worker.join(timeout=max(0, deadline - time.monotonic()))


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

from speech_output import TTSEngine

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
                 threshold: float = None):
        self._dir       = Path(profiles_dir)
        self._encoder   = None
        self._backend   = None     # "ecapa" | "resemblyzer" | None
        self._profiles: dict[str, "np.ndarray"] = {}
        self.current_lang = "it"

        # Preferisci ECAPA (molto piu' accurato). Fallback a Resemblyzer.
        if ECAPA_OK:
            try:
                self._encoder = _ECAPAClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=str(Path.home() / "jarvis_memory" / "ecapa_cache"),
                    run_opts={"device": "cpu"},
                )
                self._backend = "ecapa"
                print("👤 Speaker engine: ECAPA-TDNN (alta precisione)")
            except Exception as e:
                print(f"⚠️  ECAPA non caricato ({e}) — provo Resemblyzer")

        if self._backend is None and RESEMBLYZER_OK:
            self._encoder = VoiceEncoder(device="cpu")
            self._backend = "resemblyzer"
            print("👤 Speaker engine: Resemblyzer (fallback)")

        # Soglia: usa quella ECAPA se attivo, altrimenti quella classica.
        if threshold is not None:
            self._threshold = threshold
        elif self._backend == "ecapa":
            self._threshold = SPEAKER_THRESHOLD_ECAPA
        else:
            self._threshold = SPEAKER_THRESHOLD

        if self._encoder:
            self._load_all()

    # ── Embedding (astrae il backend) ──────────────────────────────────────────

    @staticmethod
    def _l2norm(v: "np.ndarray") -> "np.ndarray":
        """Normalizza il vettore a lunghezza 1. Indispensabile per mediare piu'
        embedding senza che il campione piu' lungo/forte domini la media."""
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-9 else v

    @staticmethod
    def _trim_silence(audio: "np.ndarray", sr: int = SAMPLE_RATE) -> "np.ndarray":
        """Tiene solo i frame con energia sopra una soglia relativa al picco.
        Rende l'embedding indipendente da quanto silenzio c'e' nella registrazione
        — una delle cause principali di punteggi instabili. Applicato SIA in
        enrollment SIA a runtime, cosi' confronti sempre voce-con-voce."""
        if audio is None or len(audio) < sr // 2:
            return audio
        win = max(1, int(sr * 0.025))             # finestre da 25 ms
        n = len(audio) // win
        if n < 4:
            return audio
        frames = audio[:n * win].reshape(n, win)
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-9)
        thr = max(float(rms.max()) * 0.15, 0.005)  # 15% del picco
        keep = rms > thr
        if int(keep.sum()) < max(4, n // 10):     # troppo poco parlato: lascia stare
            return audio
        return frames[keep].reshape(-1).astype(np.float32)

    def _embed(self, audio: "np.ndarray") -> "np.ndarray":
        """Estrae l'embedding vocale col backend attivo. Ritorna un vettore 1D
        normalizzato (L2), su audio ripulito dai silenzi."""
        if self._backend == "ecapa":
            import torch
            audio = self._trim_silence(audio)     # coerenza enroll/runtime
            # ECAPA vuole un tensore [batch, samples] float32 a 16 kHz
            sig = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
            with torch.no_grad():
                emb = self._encoder.encode_batch(sig)
            return self._l2norm(emb.squeeze().cpu().numpy())
        else:
            wav = preprocess_wav(audio, source_sr=SAMPLE_RATE)
            return self._l2norm(self._encoder.embed_utterance(wav))

    # ── Persistenza ───────────────────────────────────────────────────────────

    def _key(self, name: str, lang: str) -> str:
        return f"{name.lower()}_{lang.lower()}"

    def _path(self, name: str, lang: str) -> Path:
        # Il file include il backend: i profili ECAPA e Resemblyzer NON sono
        # compatibili (dimensioni embedding diverse). Cosi' non si mischiano.
        suffix = "_ecapa" if self._backend == "ecapa" else ""
        return self._dir / f"{name.lower()}_{lang.lower()}{suffix}.npy"

    def _load_all(self):
        self._dir.mkdir(parents=True, exist_ok=True)

        # Migrazione profilo legacy (solo per Resemblyzer)
        if (self._backend == "resemblyzer" and SPEAKER_PROFILE.exists()
                and not list(self._dir.glob("*.npy"))):
            try:
                emb = np.load(str(SPEAKER_PROFILE))
                np.save(str(self._path("owner", "it")), emb)
                print("👤 Migrato profilo legacy → owner_it")
            except Exception:
                pass

        # Carica SOLO i profili del backend attivo (per non mischiare dimensioni).
        want_ecapa = (self._backend == "ecapa")
        for p in self._dir.glob("*.npy"):
            is_ecapa_file = p.stem.endswith("_ecapa")
            if is_ecapa_file != want_ecapa:
                continue  # profilo di un altro backend — salta
            try:
                # chiave senza il suffisso _ecapa
                key = p.stem[:-6] if is_ecapa_file else p.stem
                self._profiles[key] = np.load(str(p))
            except Exception:
                pass

        for path in self._dir.glob('*.npz'):
            ecapa_file = path.stem.endswith('_ecapa')
            if ecapa_file != want_ecapa:
                continue
            key = path.stem[:-6] if ecapa_file else path.stem
            try:
                with np.load(path, allow_pickle=False) as saved:
                    self._profiles[key] = self._l2norm(np.mean([saved['reading'], saved['spontaneous']], axis=0))
            except (OSError, ValueError, KeyError):
                continue

        if self._profiles:
            print(f"👤 Speaker: ok {len(self._profiles)} profilo/i — {', '.join(self._profiles)}")
        elif self._backend == "ecapa" and list(self._dir.glob("*.npy")):
            # Ci sono profili vecchi (Resemblyzer) ma nessuno ECAPA → vanno rifatti
            print("👤 Speaker: ⚠️  profili esistenti incompatibili con ECAPA — "
                  "ri-registrali con /aggiungi_voce")
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

    def delete_profile(self, name: str, lang: str) -> bool:
        """Rimuove un profilo vocale: cancella il file .npy su disco E la voce
        in memoria. Ritorna True se ha tolto almeno qualcosa. Chiamato da
        /profili_voce all'eliminazione di una lingua o di un account."""
        removed = False
        try:
            for p in (self._path(name, lang), self._path(name, lang).with_suffix('.npz')):
                if p.exists():
                    p.unlink()
                    removed = True
        except OSError:
            pass
        if self._profiles.pop(self._key(name, lang), None) is not None:
            removed = True
        return removed

    def add_profile(self, audio, name: str, lang: str) -> bool:
        """`audio` puo' essere un singolo array OPPURE una lista di registrazioni.
        Con piu' registrazioni si salva il centroide (media normalizzata): riduce
        la varianza del tuo stesso punteggio, cosi' stai stabilmente sopra soglia
        senza doverla abbassare. Il file resta un singolo .npy (stessa forma di
        prima), quindi compatibile col caricamento esistente."""
        if not self._encoder:
            print("❌ Nessun motore speaker disponibile")
            return False
        clips = audio if isinstance(audio, (list, tuple)) else [audio]
        try:
            embs = []
            for clip in clips:
                if clip is None or len(clip) < SAMPLE_RATE // 2:
                    continue
                embs.append(self._embed(clip))
            if not embs:
                print("❌ Nessuna registrazione valida")
                return False
            centroid = self._l2norm(np.mean(np.stack(embs), axis=0))
            self._dir.mkdir(parents=True, exist_ok=True)
            np.save(str(self._path(name, lang)), centroid)
            self._profiles[self._key(name, lang)] = centroid
            print(f"ok Profilo salvato: {self._key(name, lang)} "
                  f"[{self._backend}, {len(embs)} campioni]")
            return True
        except Exception as e:
            print(f"❌ Profilo: {e}")
            return False

    def has_user_profile(self, name, lang, verified=False):
        if self._key(name, lang) not in self._profiles:
            return False
        if not verified:
            return True
        try:
            with np.load(self._path(name, lang).with_suffix('.npz'), allow_pickle=False) as profile:
                return int(profile['quality_version']) == 1
        except (OSError, KeyError, ValueError):
            return False

    def enroll_samples(self, name, lang, reading, spontaneous, cancel_event=None):
        """Store both normalized speaking samples in one atomically published file."""
        if self._encoder is None:
            raise RuntimeError('Motore speaker non disponibile. Installa resemblyzer o SpeechBrain.')
        samples = []
        from audio_checks import recording_report
        for audio in (reading, spontaneous):
            quality = recording_report(audio)
            if not quality['ok']:
                raise ValueError(quality['message'])
            embedding = np.asarray(self._embed(audio), dtype=np.float32)
            if embedding.ndim != 1 or not embedding.size or not np.all(np.isfinite(embedding)) or np.linalg.norm(embedding) < .001:
                raise ValueError('Il campione non produce un profilo vocale valido. Ripeti la registrazione.')
            samples.append(embedding)
        if samples[0].shape != samples[1].shape:
            raise ValueError('I campioni vocali non sono compatibili. Ripeti la registrazione.')
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._path(name, lang).with_suffix('.npz')
        fd, temporary = tempfile.mkstemp(prefix='.speaker-', dir=self._dir)
        try:
            with os.fdopen(fd, 'wb') as output:
                np.savez(output, reading=samples[0], spontaneous=samples[1], quality_version=np.array(1))
                output.flush()
                os.fsync(output.fileno())
            with np.load(temporary, allow_pickle=False) as check:
                if not np.array_equal(check['reading'], samples[0]) or not np.array_equal(check['spontaneous'], samples[1]):
                    raise ValueError('Verifica del profilo non riuscita. Ripeti il salvataggio.')
            checksum = hashlib.sha256(Path(temporary).read_bytes()).digest()
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError('Registrazione annullata.')
            os.replace(temporary, target)
            if hashlib.sha256(target.read_bytes()).digest() != checksum:
                raise ValueError('Il profilo salvato non corrisponde ai campioni verificati.')
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._profiles[self._key(name, lang)] = self._l2norm(np.mean(samples, axis=0))

    def match_profile(self, audio, name, lang, threshold=.50):
        if self._encoder is None:
            return False, 0.0
        target = self._path(name, lang).with_suffix('.npz')
        try:
            if target.is_file():
                with np.load(target, allow_pickle=False) as saved:
                    references = [saved['reading'], saved['spontaneous']]
            else:
                references = [self._profiles[self._key(name, lang)]]
            embedding = self._embed(audio)
            score = max(float(np.dot(self._l2norm(reference), embedding)) for reference in references)
            return score >= threshold, score
        except (KeyError, ValueError, OSError):
            return False, 0.0

    def identify(self, audio: "np.ndarray") -> tuple[Optional[str], float]:
        """
        Identifica lo speaker. Ritorna (nome, score) o (None, score).
        Cerca prima nella lingua corrente, poi in tutte.
        """
        if not self._encoder or not self._profiles:
            return None, 0.0
        try:
            emb = self._embed(audio)
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
            return None, 0.0

    def verify(self, audio: "np.ndarray") -> tuple[bool, float]:
        name, score = self.identify(audio)
        return name is not None, score

    # Compatibilità legacy
    def create_profile(self, audio, name="owner", lang=None) -> bool:
        return self.add_profile(audio, name, lang or self.current_lang)

# ══════════════════════════════════════════════════════════════════════════════
# Voice Input — STT
# ══════════════════════════════════════════════════════════════════════════════

_SEPFORMER_SOCK = Path.home() / "jarvis_memory" / "sepformer.sock"

def _sepformer_available() -> bool:
    return _SEPFORMER_SOCK.exists()

class VoiceInput:
    # Modello Whisper configurabile via .env.
    #   PC (potente):  WHISPER_MODEL=medium  (default — più preciso)
    #   Pi (leggero):  WHISPER_MODEL=base    (o tiny — più veloce, meno preciso)
    WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "medium").strip()

    def __init__(self, mic: dict):
        self.mic           = mic
        self._model        = None
        self._model_lock   = threading.Lock()
        self._load_error   = None   # str se il caricamento Whisper fallisce
        self._load_done    = False  # True quando il thread di load è terminato (ok o errore)
        self._rms_thresh   = 0.003
        self._stop_event   = threading.Event()
        self._continuous_feed = None
        self.last_voice_at = 0
        # Riferimenti iniettati dall'esterno
        self._tts_ref:     Optional[TTSEngine]      = None
        self._speaker_ref: Optional[SpeakerVerifier] = None
        self._lang_ref     = None  # LanguageManager

        if not WHISPER_OK:
            print("❌ faster-whisper non disponibile")
            return
        print(f"⏳ Carico Whisper '{self.WHISPER_MODEL_SIZE}' in background...")
        threading.Thread(target=self._load_whisper, daemon=True, name="whisper").start()

    def transcribe_wav_bytes(self, wav_bytes: bytes, language: str = None) -> str:
        """Trascrive un WAV in memoria (16kHz mono). Usato dalla voce Discord
        come FALLBACK quando il Whisper di Groq non risponde (429/rete).
        Thread-safe: condivide il lock del modello con l'ascolto normale."""
        import io as _io
        model = self._model
        if model is None:
            return ""
        try:
            with self._model_lock:
                segs, _ = model.transcribe(
                    _io.BytesIO(wav_bytes),
                    language=language,
                    beam_size=1,
                    vad_filter=True,
                )
                return " ".join(s.text.strip() for s in segs).strip()
        except Exception as e:
            print(f"⚠️  transcribe_wav_bytes: {e}")
            return ""

    def _load_whisper(self):
        try:
            m = WhisperModel(
                self.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8",
                cpu_threads=min(4, os.cpu_count() or 4), num_workers=1,
            )
            with self._model_lock:
                self._model = m
            print("ok Whisper pronto")
        except (KeyboardInterrupt, SystemExit):
            # Chiusura durante il download — termina silenziosamente
            self._load_done = True
            return
        except Exception as e:
            # Registra l'errore in modo che voice_loop possa avvisare l'utente
            # invece di restare bloccato per sempre su is_ready().
            self._load_error = str(e)
            print(f"❌ Whisper: {e}")
            # Logga anche su file per diagnosi successiva
            try:
                from utils_module import _log_software_error
                _log_software_error(
                    Path.home() / "jarvis_memory" / "logs",
                    context="VoiceInput._load_whisper",
                    exc=e,
                    msg=f"Modello '{self.WHISPER_MODEL_SIZE}' non caricato"
                )
            except Exception:
                pass
        finally:
            self._load_done = True

    def is_ready(self) -> bool:
        with self._model_lock:
            return self._model is not None

    def load_failed(self) -> bool:
        """True se il caricamento di Whisper è terminato con errore."""
        return self._load_done and self._model is None

    def load_error_msg(self) -> str:
        """Messaggio d'errore del caricamento, se presente."""
        return self._load_error or ""

    def _wait_whisper(self, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            if self.load_failed() or self._stop_event.wait(.25):
                return False
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

    @property
    def capture_level(self) -> float:
        timestamp, level = getattr(self, '_capture_signal', (0.0, 0.0))
        return level if time.monotonic() - timestamp < .25 else 0.0

    def _publish_capture_level(self, samples):
        rms = float(np.sqrt(np.mean(samples ** 2)))
        self._capture_signal = (time.monotonic(), min(1.0, rms * 8))
        return rms

    def _pipewire_capture(self, duration: float, silence_sec=None) -> Optional["np.ndarray"]:
        command = ["pw-record", "--raw", "--rate", str(SAMPLE_RATE),
                   "--channels", "1", "--format", "s16"]
        if self.mic.get('pa_name'):
            command.extend(['--target', self.mic['pa_name']])
        command.append('-')
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            pcm = bytearray()
            fd = proc.stdout.fileno()
            deadline = time.monotonic() + duration
            last_speech = None
            try:
                while not self._stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if not select.select([fd], [], [], min(.1, remaining))[0]:
                        continue
                    block = os.read(fd, SAMPLE_RATE // 5)
                    if not block:
                        break
                    previous = len(pcm) & ~1
                    pcm.extend(block)
                    complete = len(pcm) & ~1
                    if complete > previous:
                        samples = np.frombuffer(pcm[previous:complete], dtype=np.int16)
                        rms = self._publish_capture_level(samples.astype(np.float32) / 32768)
                        if rms > max(.003, self._rms_thresh):
                            last_speech = time.monotonic()
                        elif silence_sec and last_speech and time.monotonic() - last_speech >= silence_sec:
                            break
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if len(pcm) >= SAMPLE_RATE * .6:
                return np.frombuffer(pcm, dtype=np.int16, count=len(pcm)//2).astype(np.float32) / 32768
            return None

    def record_sample(self, seconds=12):
        if self.mic.get('source') != 'sd':
            if shutil.which('pw-record'):
                return self._pipewire_capture(seconds)
            if self.mic.get('pa_name') and shutil.which('parecord'):
                return self._parecord_capture(self.mic['pa_name'], seconds)
        frames = []
        with open_microphone_input(self.mic) as (stream, rate, channels):
            remaining = int(seconds * rate)
            chunk_size = max(1, int(rate * .1))
            while remaining > 0:
                if self._stop_event.is_set():
                    return None
                count = min(chunk_size, remaining)
                data, _ = stream.read(count)
                mono = data[:, np.argmax(np.mean(data ** 2, axis=0))] if channels > 1 else data.flatten()
                self._publish_capture_level(mono)
                frames.append(mono.copy())
                remaining -= count
        audio = np.concatenate(frames).astype(np.float32)
        return self._resample(audio, rate) if rate != SAMPLE_RATE else audio

    def _sd_capture(self, dev_idx: int, sr: int, ch: int,
                    timeout: float, silence_sec: float) -> Optional["np.ndarray"]:
        from audio_checks import VoiceActivity
        detector = VoiceActivity()
        frames, silent_count, started = [], 0, False
        try:
            microphone = {'index': dev_idx, 'samplerate': sr}
            if self.mic.get('source') == 'sd' and self.mic.get('name'):
                microphone['name'] = self.mic['name']
            with open_microphone_input(microphone) as (stream, rate, channels):
                chunk_size = max(1, int(rate * .1))
                for _ in range(max(1, int(timeout / .1))):
                    if self._stop_event.is_set():
                        break
                    data, _ = stream.read(chunk_size)
                    mono = data[:, np.argmax(np.mean(data ** 2, axis=0))] if channels > 1 else data.flatten()
                    self._publish_capture_level(mono)
                    analysis = mono if rate in {8000, 16000, 32000, 48000} else self._resample(mono, rate)
                    analysis_rate = rate if rate in {8000, 16000, 32000, 48000} else SAMPLE_RATE
                    if detector.speech_seconds(analysis, analysis_rate) > 0:
                        started = True
                        silent_count = 0
                        frames.append(mono.copy())
                    elif started:
                        silent_count += 1
                        frames.append(mono.copy())
                        if silent_count >= max(1, int(silence_sec / .1)):
                            break
        except RuntimeError:
            raise
        except Exception as e:
            print(f"\n❌ sounddevice: {e}")
            return None
        if not frames or not started:
            return None
        audio = np.concatenate(frames).astype(np.float32)
        return self._resample(audio, rate) if rate != SAMPLE_RATE else audio

    @staticmethod
    def _resample(audio: "np.ndarray", src_sr: int) -> "np.ndarray":
        try:
            import scipy.signal as sps
            return sps.resample(audio, int(len(audio) * SAMPLE_RATE / src_sr)).astype(np.float32)
        except ImportError:
            step = src_sr / SAMPLE_RATE
            idx  = np.round(np.arange(0, len(audio), step)).astype(int)
            return audio[idx[idx < len(audio)]]

    @staticmethod
    def _separate_speaker(audio: "np.ndarray", sr: int = SAMPLE_RATE) -> "np.ndarray":
        """
        Invia audio al SepFormer server persistente via socket Unix.
        Retry automatico se il server non è ancora pronto.
        """
        if not _sepformer_available():
            return audio
        import pickle, socket as _socket, struct
        if audio.ndim > 1:
            audio = audio[:, 0]
        payload = pickle.dumps(audio)
        # Retry fino a 3 volte con attesa crescente (server in avvio)
        for attempt, wait in enumerate([0, 1.0, 2.0]):
            if wait:
                time.sleep(wait)
            try:
                with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                    s.settimeout(8.0)
                    s.connect(str(_SEPFORMER_SOCK))
                    s.sendall(struct.pack(">I", len(payload)) + payload)
                    header = s.recv(4)
                    if not header:
                        continue
                    length = struct.unpack(">I", header)[0]
                    if length == 0:
                        return audio
                    data = b""
                    while len(data) < length:
                        chunk = s.recv(min(65536, length - len(data)))
                        if not chunk:
                            break
                        data += chunk
                return pickle.loads(data).astype(np.float32)
            except ConnectionRefusedError:
                if attempt == 2:
                    print("  [ATTENZIONE] SepFormer server non raggiungibile", flush=True)
                continue
            except Exception as e:
                print(f"  [ATTENZIONE] SepFormer socket: {e}", flush=True)
                return audio
        return audio

    def _record_sd(self, timeout: float = 10.0, silence_sec: float = 1.5) -> Optional["np.ndarray"]:
        """
        Registra audio con questa priorità:
        1. pw-record  (PipeWire nativo — CachyOS/Wayland)
        2. parecord   (PulseAudio)
        3. sounddevice pipewire/default (fallback)
        """
        import tempfile

        if self.mic.get('source') == 'sd' and self.mic.get('index') is not None:
            return self._sd_capture(self.mic['index'], self.mic.get('samplerate', SAMPLE_RATE),
                                    1, timeout, silence_sec)

        # ── 1. pw-record (PipeWire nativo) ────────────────────────────────────
        has_pwrec = subprocess.run(["which", "pw-record"],
                                   capture_output=True).returncode == 0
        if has_pwrec:
            try:
                audio = self._pipewire_capture(timeout, silence_sec=silence_sec)
                if audio is not None:
                    return audio
            except OSError as e:
                print(f"\n⚠️  pw-record: {e}", flush=True)

        # ── 2. parecord (PulseAudio) ──────────────────────────────────────────
        has_parecord = subprocess.run(["which", "parecord"],
                                      capture_output=True).returncode == 0
        pa_name = self.mic.get("pa_name")
        if has_parecord and pa_name:
            result = self._parecord_capture(pa_name, duration=timeout)
            if result is not None:
                return result

        # ── 3. sounddevice — pipewire/default, evita hw: diretto ─────────────
        def _try_dev(dev_idx):
            try:
                with open_microphone_input({'index': dev_idx}) as (_, rate, channels):
                    return rate, channels
            except (sd.PortAudioError, RuntimeError, ValueError):
                return None, None

        dev_idx, wsr, wch = None, None, None
        try:
            for prefer in ("pipewire", "default", "pulse"):
                for i, d in enumerate(sd.query_devices()):
                    if prefer in d["name"].lower() and d["max_input_channels"] > 0:
                        wsr, wch = _try_dev(i)
                        if wsr:
                            dev_idx = i
                            print(f"  ℹ️  Mic: sounddevice '{d['name']}'", flush=True)
                            break
                if wsr:
                    break
        except Exception:
            pass

        if wsr is None:
            print("❌ Nessun backend audio funzionante (pw-record/parecord/sounddevice)",
                  flush=True)
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
                audio, language=lang, beam_size=1, vad_filter=True,
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
               lang: str = "it") -> tuple[Optional[str], Optional[str]]:
        """
        Registra audio e trascrive con speaker identification.
        Ritorna: (testo_trascritto, nome_speaker) oppure (None, None).
        """
        if not WHISPER_OK or self._stop_event.is_set():
            return None, None

        with self._model_lock:
            model = self._model
        if model is None:
            print("⏳ Whisper in caricamento, attendi...")
            if not self._wait_whisper(60):
                return None, None
            with self._model_lock:
                model = self._model

        # Aspetta fine TTS
        if self._tts_ref and self._tts_ref.is_speaking:
            while self._tts_ref.is_speaking:
                if self._stop_event.wait(.1):
                    return None, None
            time.sleep(0.5)

        audio = self._record_sd(timeout=timeout, silence_sec=silence_sec)
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            return None, None

        # Pulizia audio con noisereduce (CPU, stabile)
        try:
            import noisereduce as nr
            audio = nr.reduce_noise(y=audio, sr=SAMPLE_RATE,
                                    stationary=False,
                                    prop_decrease=0.75).astype(np.float32)
        except Exception:
            pass

        # Speaker identification
        speaker_name: Optional[str] = None
        if self._speaker_ref:
            if self._speaker_ref.has_profile:
                name, score = self._speaker_ref.identify(audio)
                if name is None:
                    print(f"  🔒 Voce non riconosciuta ({score:.2f})", flush=True)
                    return None, None
                speaker_name = name
                print(f"  👤 {name.capitalize()} ({score:.2f})", flush=True)
            else:
                speaker_name = "unknown"

        text = self._transcribe(audio, model, lang=lang)
        return text, speaker_name

    # ── MODALITÀ TRADUZIONE: niente speaker-id, lingua in autodetect ───────────
    def _transcribe_auto(self, audio: "np.ndarray", model=None):
        """Trascrive lasciando che Whisper RILEVI la lingua.
        Ritorna (testo, lingua, confidenza) — confidenza 0..1 del rilevamento."""
        if model is None:
            with self._model_lock:
                model = self._model
        if model is None:
            return None, None, 0.0
        try:
            if audio.ndim > 1:
                audio = audio[:, 0]
            segs, info = model.transcribe(
                audio, beam_size=5, vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=400,
                    speech_pad_ms=200,
                    threshold=0.3,
                ),
            )
            text = " ".join(s.text.strip() for s in segs).strip()
            lang = getattr(info, "language", None)
            prob = getattr(info, "language_probability", 0.0) or 0.0
            print("\r" + " " * 55 + "\r", end="")
            return (text if len(text) > 1 else None), lang, float(prob)
        except Exception as e:
            print(f"\n❌ Trascrizione: {e}")
            return None, None, 0.0

    def listen_translate(self, timeout: float = 12.0, silence_sec: float = 1.5):
        """Come listen() ma per la MODALITÀ TRADUZIONE:
          - NESSUNA speaker identification (chiunque parla viene trascritto)
          - lingua AUTODETECT con confidenza
        Ritorna (testo, lingua, confidenza) oppure (None, None, 0.0)."""
        if not WHISPER_OK or self._stop_event.is_set():
            return None, None, 0.0
        with self._model_lock:
            model = self._model
        if model is None:
            if not self._wait_whisper(60):
                return None, None, 0.0
            with self._model_lock:
                model = self._model
        if self._tts_ref and self._tts_ref.is_speaking:
            while self._tts_ref.is_speaking:
                if self._stop_event.wait(.1):
                    return None, None
            time.sleep(0.5)
        audio = self._record_sd(timeout=timeout, silence_sec=silence_sec)
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            return None, None, 0.0
        try:
            import noisereduce as nr
            audio = nr.reduce_noise(y=audio, sr=SAMPLE_RATE,
                                    stationary=False,
                                    prop_decrease=0.75).astype(np.float32)
        except Exception:
            pass
        # In traduzione NON si identifica il parlante: chiunque parla viene preso.
        return self._transcribe_auto(audio, model)

    def stop(self):
        self._stop_event.set()

    def start_continuous(self):
        if self._continuous_feed is None:
            from microphone_feed import MicrophoneFeed
            from audio_checks import VoiceActivity
            self._continuous_feed = MicrophoneFeed(self, open_microphone_input, VoiceActivity)

    def capture_continuous(self, not_before):
        self.start_continuous()
        return self._continuous_feed.capture(not_before)

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
                          ask_fn: Callable = None, n_samples: int = 4) -> bool:
        """
        Registra un nuovo profilo vocale da PIU' campioni brevi (default 4).
        Piu' campioni = profilo piu' stabile = meno rifiuti, senza toccare la soglia.
        ask_fn: funzione per chiedere input all'utente (default: input()).
        """
        if self._speaker._backend is None:
            print("❌ Nessun motore speaker disponibile")
            return False

        _ask = ask_fn or (lambda p: input(p).strip())
        lang = lang or self.lang

        if name is None:
            name = _ask("   Nome utente (es. radostin): ").strip().lower()
            if not name:
                print("❌ Nome non valido")
                return False

        print(f"\n🎙️  Registrazione profilo '{name}' [{lang}] — {n_samples} campioni")
        print("   Parla NATURALE, col tono che useresti con JARVIS (non scandire).")
        print("   Frasi diverse ogni volta vanno benissimo.")

        clips = []
        for i in range(1, n_samples + 1):
            _ask(f"   [{i}/{n_samples}] Invio e parla ~6 secondi...")
            audio = self._voice._record_sd(timeout=8.0, silence_sec=2.0)
            if audio is None or len(audio) < SAMPLE_RATE * 1.5:
                print("   ⚠️  campione troppo corto — questo non conta, vai avanti")
                continue
            clips.append(audio)
            print("   ok")

        if len(clips) < 2:
            print("❌ Servono almeno 2 campioni validi — riprova")
            return False

        ok = self._speaker.add_profile(clips, name, lang)
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
