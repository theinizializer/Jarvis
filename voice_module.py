#!/usr/bin/env python3
"""
JARVIS — Voice Module
Contiene tutto il sistema vocale estratto da jarvis_v6:
- Scansione e selezione microfono
- STT con faster-whisper (parecord + sounddevice)
- TTS con gTTS
- Wake word / Sleep word (fuzzy matching)
- Speaker Verification con resemblyzer
"""

import os, queue, re, subprocess, sys, tempfile, threading, time
from pathlib import Path
from typing import Optional

# ── Auto-install ──────────────────────────────────────────────────────────────
def _ensure_pkg(pip_name, import_name=None):
    name = import_name or pip_name
    try: __import__(name); return True
    except ImportError:
        subprocess.run([sys.executable,"-m","pip","install",pip_name,"-q","--break-system-packages"], capture_output=True)
        try: __import__(name); return True
        except ImportError: return False

try:
    from faster_whisper import WhisperModel
    WHISPER_OK = True
except ImportError:
    WHISPER_OK = _ensure_pkg("faster-whisper", "faster_whisper")
    if WHISPER_OK: from faster_whisper import WhisperModel

try:
    import sounddevice as sd, soundfile as sf, numpy as np
    SD_OK = True
except ImportError:
    SD_OK = _ensure_pkg("sounddevice") and _ensure_pkg("soundfile") and _ensure_pkg("numpy")
    if SD_OK: import sounddevice as sd, soundfile as sf, numpy as np

try:
    from gtts import gTTS; GTTS_OK = True
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","gtts","-q"], check=False)
    try: from gtts import gTTS; GTTS_OK = True
    except: GTTS_OK = False

try:
    from resemblyzer import VoiceEncoder, preprocess_wav; RESEMBLYZER_OK = True
except ImportError:
    RESEMBLYZER_OK = False

try:
    import noisereduce as nr; NOISEREDUCE_OK = True
except ImportError:
    NOISEREDUCE_OK = _ensure_pkg("noisereduce", "noisereduce")
    if NOISEREDUCE_OK:
        import noisereduce as nr

try:
    import torch
    from speechbrain.inference.separation import SepformerSeparation
    SEPFORMER_OK = True
except Exception:
    SEPFORMER_OK = False

# ── Costanti ──────────────────────────────────────────────────────────────────
SAMPLE_RATE       = 16000
SPEAKER_PROFILE   = Path.home() / "jarvis_memory" / "speaker_profile.npy"
SPEAKER_THRESHOLD = 0.70
SESSION_TIMEOUT   = 120

_WAKE_VARIANTS = {
    "jarvis","jarvi","jarvs","jarbi","jarwis","javis","jarves","jarfis",
    "zarvis","garvis","harvis","giorvis","giorvi","giervi","giorviz",
    "jervis","jervi","yervis","gervis","gervi","djarvis","djarvi",
}
_RE_WAKE  = re.compile(r'\b(?:jar|gar|ger|gio|gie|jer|yer|djar)[a-z]{1,5}\b', re.IGNORECASE)
_RE_SLEEP = re.compile(r'\b(dormi|sleep|dors|schlaf)\b', re.IGNORECASE)
_RE_MIC   = re.compile(r'mic|microphone|input|capture|headset|cuffie|analog|built.?in|internal|integrated|digital|acp|usb.?audio', re.IGNORECASE)

def _lev(a,b):
    if a==b: return 0
    if not a: return len(b)
    if not b: return len(a)
    m=[[0]*(len(b)+1) for _ in range(len(a)+1)]
    for i in range(len(a)+1): m[i][0]=i
    for j in range(len(b)+1): m[0][j]=j
    for i in range(1,len(a)+1):
        for j in range(1,len(b)+1):
            c=0 if a[i-1]==b[j-1] else 1
            m[i][j]=min(m[i-1][j]+1,m[i][j-1]+1,m[i-1][j-1]+c)
    return m[len(a)][len(b)]

def is_wake_word(text):
    clean=text.lower().strip()
    for t in re.findall(r'[a-zàèìòùéç]+',clean):
        if t in _WAKE_VARIANTS: return True
        if len(t)>=4 and _lev(t,"jarvis")<=2: return True
    for m in _RE_WAKE.finditer(clean):
        if _lev(m.group().lower(),"jarvis")<=3: return True
    return False

def is_sleep_word(text): return bool(_RE_SLEEP.search(text.lower()))

def strip_wake_word(text):
    return re.sub(r'^[,\s!]+','',_RE_WAKE.sub("",text).strip()).strip()

# ══════════════════════════════════════════════════════════════════════════════
# MICROFONO
# ══════════════════════════════════════════════════════════════════════════════

def _mic_sort(m):
    n=str(m.get('name','')).lower()
    if 'acp6x' in n or 'digital' in n: return 0
    if 'built' in n or 'internal' in n: return 1
    if 'usb' in n: return 2
    if 'monitor' in n: return 9
    return 5

def scan_microphones():
    mics=[]
    try:
        out=subprocess.check_output(['pactl','list','sources'],text=True,stderr=subprocess.DEVNULL)
        cur={}
        for line in out.splitlines():
            line=line.strip()
            if line.startswith('Nome:') or line.startswith('Name:'):
                name=line.split(':',1)[1].strip()
                if '.monitor' not in name:
                    cur={'source':'pactl','pa_name':name,'name':name,'index':None}
            elif (line.startswith('Descrizione:') or line.startswith('Description:')) and cur:
                cur['description']=line.split(':',1)[1].strip()
            elif (line.startswith('Specifica') or line.startswith('Sample')) and cur.get('pa_name'):
                mics.append(cur); cur={}
        if cur.get('pa_name'): mics.append(cur)
    except Exception: pass
    if SD_OK:
        try:
            for i,d in enumerate(sd.query_devices()):
                if d['max_input_channels']>0:
                    mics.append({'source':'sd','index':i,'name':d['name'],
                                 'samplerate':int(d['default_samplerate']),'channels':d['max_input_channels']})
        except Exception: pass
    mics.sort(key=_mic_sort)
    return mics

def choose_microphone():
    print("\n🔍 Scansione microfoni...")
    mics=scan_microphones()
    if not mics: print("❌ Nessun microfono trovato."); return None
    real=[m for m in mics if _RE_MIC.search(str(m.get('name','')))]
    pool=real if real else mics
    if len(pool)==1:
        m=pool[0]; print(f"🎙️  Microfono: {m.get('description') or m.get('name')}"); return m
    print("🎙️  Microfoni disponibili:")
    for i,m in enumerate(pool,1):
        label=m.get('description') or m.get('name')
        rate=m.get('samplerate','')
        print(f"  {i}. {label}"+(f" [{rate} Hz]" if rate else ""))
    scelta=input("Scelta [1]: ").strip() or "1"
    try: return pool[int(scelta)-1]
    except: return pool[0]

# ══════════════════════════════════════════════════════════════════════════════
# TTS
# ══════════════════════════════════════════════════════════════════════════════

def _mute_mic(pa_name: str = None):
    """Muta il microfono durante TTS per evitare che si senta da solo."""
    try:
        if pa_name:
            subprocess.run(['pactl','set-source-mute', pa_name, '1'], capture_output=True)
        else:
            subprocess.run(['pactl','set-source-mute', '@DEFAULT_SOURCE@', '1'], capture_output=True)
    except Exception:
        pass

def _unmute_mic(pa_name: str = None):
    """Riattiva il microfono dopo TTS."""
    try:
        if pa_name:
            subprocess.run(['pactl','set-source-mute', pa_name, '0'], capture_output=True)
        else:
            subprocess.run(['pactl','set-source-mute', '@DEFAULT_SOURCE@', '0'], capture_output=True)
    except Exception:
        pass

def find_tts_player():
    for p in ('mpg123','ffplay','aplay','cvlc'):
        if subprocess.run(['which',p],capture_output=True).returncode==0: return p
    return None

class TTSEngine:
    def __init__(self, lang="it", mem_dir=Path.home()/"jarvis_memory", pa_name=None):
        self.lang=lang; self.mem_dir=mem_dir; self.mem_dir.mkdir(parents=True,exist_ok=True)
        self._pa_name=pa_name
        self._player=find_tts_player()
        self._q=queue.Queue(maxsize=8)
        self._speaking=threading.Event()
        threading.Thread(target=self._worker,daemon=True,name="tts").start()

    @property
    def is_speaking(self): return self._speaking.is_set()

    def say(self,text):
        if not GTTS_OK or not text or len(text)<4: return
        try: self._q.put_nowait(text)
        except queue.Full: pass

    def stop(self):
        while not self._q.empty():
            try: self._q.get_nowait()
            except: pass

    def shutdown(self): self._q.put(None)

    def _worker(self):
        while True:
            try: text=self._q.get(timeout=1)
            except queue.Empty: continue
            if text is None: break
            clean=re.sub(r'[*_`#~>|]','',text)
            clean=re.sub(r':[a-z_]+:','',clean)
            clean=re.sub(r'[\U00010000-\U0010ffff]','',clean,flags=re.UNICODE)
            clean=re.sub(r'\s+',' ',clean).strip()
            if not clean or len(clean)<3: self._q.task_done(); continue
            self._speaking.set()
            _mute_mic(self._pa_name)  # muta mic — evita che si senta da solo
            tmp=self.mem_dir/f"tts_{int(time.time()*1000)}.mp3"
            try:
                gTTS(text=clean,lang=self.lang,slow=False).save(str(tmp))
                if not tmp.exists() or tmp.stat().st_size<100: raise RuntimeError("mp3 vuoto")
                cmd_map={'mpg123':['mpg123','-q',str(tmp)],'ffplay':['ffplay','-nodisp','-autoexit','-loglevel','quiet',str(tmp)],'cvlc':['cvlc','--play-and-exit','-q',str(tmp)]}
                subprocess.run(cmd_map.get(self._player,[self._player,str(tmp)]),timeout=30,capture_output=True)
            except Exception as e: print(f"\n⚠️ TTS: {e}",flush=True)
            finally:
                tmp.unlink(missing_ok=True)
                self._speaking.clear()
                time.sleep(0.4)        # pausa breve dopo TTS
                _unmute_mic(self._pa_name)  # riattiva mic
            self._q.task_done()

# ══════════════════════════════════════════════════════════════════════════════
# SPEAKER VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class SpeakerVerifier:
    # Soglia similarity per un singolo segmento
    SEGMENT_THRESHOLD = 0.72
    # Lunghezza segmento in secondi per il voto a maggioranza
    SEGMENT_SEC = 1.5
    # Quanti segmenti devono passare (es. 0.5 = almeno la metà)
    MAJORITY_RATIO = 0.5
    # Minimo segmenti validi per accettare (evita clip cortissimi)
    MIN_SEGMENTS = 1

    def __init__(self, profile_path=SPEAKER_PROFILE, threshold=SPEAKER_THRESHOLD):
        self._path=Path(profile_path); self._threshold=threshold
        self._profile=None; self._encoder=None
        if RESEMBLYZER_OK:
            self._encoder=VoiceEncoder(device='cpu'); self._load()

    def _load(self):
        if self._path.exists():
            try: self._profile=np.load(str(self._path)); print("👤 Speaker: ✅ profilo caricato"); return
            except: pass
        print("👤 Speaker: ⚠️  nessun profilo (usa --setup-speaker)")

    @property
    def has_profile(self): return self._profile is not None

    def _denoise(self, audio: np.ndarray) -> np.ndarray:
        """Rimuove rumore stazionario (ventola, AC, traffico lontano)."""
        if not NOISEREDUCE_OK:
            return audio
        try:
            # prop_decrease=0.8 — aggressivo ma senza artefatti eccessivi
            return nr.reduce_noise(y=audio, sr=SAMPLE_RATE,
                                   prop_decrease=0.8, stationary=True).astype(np.float32)
        except Exception:
            return audio

    def _cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    def _segment_embeddings(self, wav: np.ndarray) -> list:
        """Divide l'audio in segmenti e calcola l'embedding di ognuno."""
        seg_len = int(SAMPLE_RATE * self.SEGMENT_SEC)
        segments = []
        for start in range(0, len(wav), seg_len):
            chunk = wav[start:start + seg_len]
            # Scarta chunk troppo corti o silenziosi
            if len(chunk) < int(SAMPLE_RATE * 0.5):
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms < 0.002:
                continue
            try:
                emb = self._encoder.embed_utterance(chunk)
                segments.append(emb)
            except Exception:
                continue
        return segments

    def create_profile(self, audio):
        if not self._encoder: return False
        try:
            denoised = self._denoise(audio)
            wav = preprocess_wav(denoised, source_sr=SAMPLE_RATE)
            # Usa embed_utterance sull'audio intero per il profilo (più stabile)
            self._profile = self._encoder.embed_utterance(wav)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            np.save(str(self._path), self._profile)
            print("✅ Profilo vocale salvato!"); return True
        except Exception as e: print(f"❌ Profilo: {e}"); return False

    def verify(self, audio: np.ndarray) -> tuple:
        """
        Verifica con denoising + filtraggio segmenti.
        Ritorna (True/False, score_medio).
        Usare filter_audio() per ottenere l'audio ripulito da mandare a Whisper.
        """
        cleaned, avg_score, passed, total = self._filter_segments(audio)
        ok = cleaned is not None
        return ok, avg_score

    def filter_audio(self, audio: np.ndarray) -> tuple:
        """
        Ritorna (audio_filtrato, score_medio) — l'audio contiene SOLO
        i segmenti in cui parla il proprietario. I segmenti altrui vengono
        sostituiti con silenzio, così Whisper non li trascrive.
        Se nessun segmento passa, ritorna (None, score).
        """
        cleaned, avg_score, passed, total = self._filter_segments(audio)
        print(f"  🎙️  Speaker: {passed}/{total} segmenti ok, score medio={avg_score:.2f}", flush=True)
        return cleaned, avg_score

    def _filter_segments(self, audio: np.ndarray) -> tuple:
        """
        Logica centrale: denoising → segmentazione → filtraggio.
        Ritorna (audio_pulito_o_None, avg_score, n_passati, n_totali).
        """
        if not self._encoder or self._profile is None:
            return audio, 1.0, 1, 1
        try:
            # Step 1 — denoising
            denoised = self._denoise(audio)

            # Step 2 — preprocessing resemblyzer
            wav = preprocess_wav(denoised, source_sr=SAMPLE_RATE)

            seg_len = int(SAMPLE_RATE * self.SEGMENT_SEC)

            # Step 3 — segmentazione con indici originali
            segments = []  # lista di (start, end, chunk, rms)
            for start in range(0, len(wav), seg_len):
                chunk = wav[start:start + seg_len]
                if len(chunk) < int(SAMPLE_RATE * 0.3):
                    continue
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                segments.append((start, start + len(chunk), chunk, rms))

            if not segments:
                return None, 0.0, 0, 0

            # Step 4 — embedding e score per ogni segmento
            scores = []
            for (start, end, chunk, rms) in segments:
                if rms < 0.002:
                    # Silenzio — non è una voce altrui, tienilo
                    scores.append((start, end, 1.0, True))
                    continue
                try:
                    emb = self._encoder.embed_utterance(chunk)
                    sim = self._cosine(self._profile, emb)
                    passed = sim >= self.SEGMENT_THRESHOLD
                    scores.append((start, end, sim, passed))
                except Exception:
                    scores.append((start, end, 0.0, False))

            # Step 5 — ricostruisci audio sostituendo segmenti altrui con silenzio
            filtered = denoised.copy()
            n_passed = sum(1 for _, _, _, ok in scores if ok)
            n_total  = len(scores)
            sims     = [s for _, _, s, _ in scores]
            avg      = float(np.mean(sims)) if sims else 0.0

            for (start, end, sim, ok) in scores:
                if not ok:
                    # Azzera il segmento — Whisper VAD lo salterà
                    end_real = min(end, len(filtered))
                    filtered[start:end_real] = 0.0

            if n_passed == 0:
                return None, avg, 0, n_total

            return filtered.astype(np.float32), avg, n_passed, n_total

        except Exception:
            return audio, 1.0, 1, 1

# ══════════════════════════════════════════════════════════════════════════════
# TARGET SPEAKER EXTRACTOR — SepFormer + resemblyzer
# ══════════════════════════════════════════════════════════════════════════════

class TargetSpeakerExtractor:
    """
    Estrae la voce del proprietario da audio misto usando SepFormer + resemblyzer.

    Pipeline:
      1. SepFormer separa l'audio in N tracce indipendenti (tipicamente 2)
      2. resemblyzer calcola la similarity di ogni traccia col profilo
      3. La traccia più simile al profilo viene amplificata
      4. Le altre tracce vengono attenuate (non azzerate — evita artefatti)
      5. Si ricostruisce un singolo segnale audio pulito

    Se SepFormer non è disponibile, cade in graceful degradation:
    usa solo denoising + resemblyzer segment filtering.
    """

    # Modello HuggingFace — scaricato automaticamente al primo uso (~200MB)
    SEPFORMER_MODEL = "speechbrain/sepformer-wham"
    # Boost applicato alla traccia del proprietario
    OWNER_BOOST = 2.5
    # Attenuazione applicata alle tracce altrui (non 0 — evita artefatti click)
    OTHER_ATTENUATION = 0.05
    # Soglia similarity per riconoscere la traccia del proprietario
    OWNER_THRESHOLD = 0.65

    def __init__(self, speaker_verifier: "SpeakerVerifier"):
        self._verifier = speaker_verifier
        self._model = None
        self._model_lock = threading.Lock()
        self._ready = False

        if SEPFORMER_OK:
            threading.Thread(
                target=self._load_model, daemon=True, name="sepformer"
            ).start()
        else:
            print("⚠️  SepFormer non disponibile — solo denoising attivo")

    def _load_model(self):
        try:
            print("⏳ Carico SepFormer in background...", flush=True)
            model = SepformerSeparation.from_hparams(
                source=self.SEPFORMER_MODEL,
                savedir=str(Path.home() / "jarvis_memory" / "sepformer"),
                run_opts={"device": "cpu"}
            )
            with self._model_lock:
                self._model = model
                self._ready = True
            print("✅ SepFormer pronto — estrazione voce attiva", flush=True)
        except Exception as e:
            print(f"⚠️  SepFormer non caricato: {e}", flush=True)

    @property
    def is_ready(self):
        with self._model_lock:
            return self._ready

    def extract(self, audio: np.ndarray) -> np.ndarray:
        """
        Estrae e amplifica la voce del proprietario.
        Ritorna sempre un array numpy float32 a 16kHz.
        Se non può separare, ritorna l'audio denoised originale.
        """
        # Denoising sempre, anche senza SepFormer
        denoised = self._verifier._denoise(audio)

        with self._model_lock:
            model = self._model

        if model is None or not self._verifier.has_profile:
            # SepFormer non pronto o nessun profilo — ritorna solo denoised
            return denoised

        try:
            return self._separate_and_boost(denoised, model)
        except Exception as e:
            print(f"  ⚠️  SepFormer errore: {e} — uso audio denoised", flush=True)
            return denoised

    def _separate_and_boost(self, audio: np.ndarray, model) -> np.ndarray:
        """Logica principale di separazione e boost."""
        # SepFormer vuole tensore float32 shape [1, samples] a 8kHz o 16kHz
        # Il modello sepformer-wham lavora a 8kHz internamente
        import torchaudio

        # Converti a tensore
        wav_tensor = torch.from_numpy(audio).unsqueeze(0)  # [1, samples]

        # Resample a 8kHz per SepFormer (sepformer-wham è addestrato a 8kHz)
        wav_8k = torchaudio.functional.resample(wav_tensor, SAMPLE_RATE, 8000)

        # Separazione — ritorna [samples, n_sources]
        with torch.no_grad():
            est_sources = model.separate_batch(wav_8k)  # [1, samples, n_sources]

        n_sources = est_sources.shape[-1]

        # Risampla ogni sorgente a 16kHz e calcola similarity col profilo
        scores = []
        sources_16k = []
        for i in range(n_sources):
            src = est_sources[0, :, i]  # [samples] a 8kHz
            # Risampla a 16kHz
            src_16k = torchaudio.functional.resample(
                src.unsqueeze(0), 8000, SAMPLE_RATE
            ).squeeze(0).numpy()

            sources_16k.append(src_16k)

            # Calcola similarity con il profilo del proprietario
            try:
                from resemblyzer import preprocess_wav as prep
                wav_proc = prep(src_16k, source_sr=SAMPLE_RATE)
                if len(wav_proc) < int(SAMPLE_RATE * 0.3):
                    scores.append(0.0)
                    continue
                emb = self._verifier._encoder.embed_utterance(wav_proc)
                sim = self._verifier._cosine(self._verifier._profile, emb)
                scores.append(sim)
            except Exception:
                scores.append(0.0)

        print(
            f"  🎙️  SepFormer: {n_sources} tracce, "
            f"scores={[f'{s:.2f}' for s in scores]}",
            flush=True
        )

        # Identifica la traccia del proprietario (score più alto)
        best_idx = int(np.argmax(scores))
        best_score = scores[best_idx]

        if best_score < self.OWNER_THRESHOLD:
            # Nessuna traccia sembra il proprietario — ritorna audio denoised
            print(f"  🔒 Nessuna traccia riconosciuta (max={best_score:.2f})", flush=True)
            return self._verifier._denoise(audio)

        # Ricostruisci: boost sulla traccia del proprietario, attenuazione sulle altre
        target_len = len(audio)
        mixed = np.zeros(target_len, dtype=np.float32)

        for i, src_16k in enumerate(sources_16k):
            # Allinea lunghezza
            src_aligned = np.zeros(target_len, dtype=np.float32)
            copy_len = min(len(src_16k), target_len)
            src_aligned[:copy_len] = src_16k[:copy_len]

            if i == best_idx:
                mixed += src_aligned * self.OWNER_BOOST
            else:
                mixed += src_aligned * self.OTHER_ATTENUATION

        # Normalizza per evitare clipping
        peak = np.max(np.abs(mixed))
        if peak > 1.0:
            mixed /= peak

        return mixed.astype(np.float32)


# ══════════════════════════════════════════════════════════════════════════════
# VOICE INPUT — STT (uguale a jarvis_v6)
# ══════════════════════════════════════════════════════════════════════════════

class VoiceInput:
    WHISPER_MODEL_SIZE = "medium"

    def __init__(self, mic):
        self.mic=mic; self._model=None; self._model_lock=threading.Lock()
        self._rms_thresh=0.003; self._stop_event=threading.Event()
        self._tts_ref=None        # impostato da VoiceModule
        self._speaker_ref=None    # impostato da VoiceModule
        self._extractor_ref=None  # impostato da VoiceModule — TargetSpeakerExtractor
        self._lang_ref=None       # impostato da VoiceModule — localizzazione wake/sleep
        if not WHISPER_OK: print("❌ faster-whisper non disponibile"); return
        print(f"⏳ Carico Whisper '{self.WHISPER_MODEL_SIZE}' in background...")
        threading.Thread(target=self._load_whisper,daemon=True,name="whisper").start()

    def _load_whisper(self):
        try:
            m=WhisperModel(self.WHISPER_MODEL_SIZE,device="cpu",compute_type="int8",cpu_threads=os.cpu_count() or 4,num_workers=2)
            with self._model_lock: self._model=m
            print("✅ Whisper pronto")
        except Exception as e: print(f"❌ Whisper: {e}")

    def _wait_whisper(self, timeout=60.0):
        t=time.time()
        while time.time()-t<timeout:
            with self._model_lock:
                if self._model: return True
            time.sleep(0.5)
        return False

    def _parecord_capture(self, pa_name, duration):
        tmp=Path(tempfile.mktemp(suffix=".wav")); proc=None
        try:
            proc=subprocess.Popen(['parecord','--device',pa_name,'--file-format=wav',str(tmp)],stderr=subprocess.DEVNULL)
            deadline=time.time()+duration
            while time.time()<deadline:
                if self._stop_event.is_set(): break
                time.sleep(0.1)
            proc.terminate(); proc.wait(timeout=3)
            if tmp.exists() and tmp.stat().st_size>4096:
                data,sr=sf.read(str(tmp),dtype='float32')
                if len(data)<sr*0.3: return None
                mono=data.mean(axis=1) if data.ndim>1 else data.flatten()
                if sr!=SAMPLE_RATE:
                    try:
                        import scipy.signal as sps
                        mono=sps.resample(mono,int(len(mono)*SAMPLE_RATE/sr)).astype('float32')
                    except ImportError:
                        step=sr/SAMPLE_RATE; idx=np.round(np.arange(0,len(mono),step)).astype(int)
                        mono=mono[idx[idx<len(mono)]]
                return mono.astype(np.float32)
        except Exception as e: print(f"\n❌ parecord: {e}"); return None
        finally:
            if proc:
                try: proc.terminate()
                except: pass
            tmp.unlink(missing_ok=True)

    def _sd_capture(self, dev_idx, sr, ch, timeout, silence_sec):
        chunk_size=int(sr*0.1); sil_chunks=int(silence_sec/0.1); max_chunks=int(timeout/0.1)
        try:
            noise=sd.rec(int(sr*0.5),samplerate=sr,channels=ch,dtype='float32',device=dev_idx,blocking=True)
            mono_n=noise.mean(axis=1) if ch>1 else noise.flatten()
            self._rms_thresh=max(float(np.sqrt(np.mean(mono_n**2)))*3.0,0.002)
        except: self._rms_thresh=0.003
        frames,silent_count,started=[], 0, False
        try:
            with sd.InputStream(samplerate=sr,channels=ch,device=dev_idx,dtype='float32',blocksize=chunk_size) as stream:
                for _ in range(max_chunks):
                    if self._stop_event.is_set(): break
                    data,_=stream.read(chunk_size)
                    mono=data.mean(axis=1) if ch>1 else data.flatten()
                    rms=float(np.sqrt(np.mean(mono**2)))
                    if rms>self._rms_thresh:
                        started=True; silent_count=0; frames.append(mono.copy())
                    elif started:
                        silent_count+=1; frames.append(mono.copy())
                        if silent_count>=sil_chunks: break
        except Exception as e: print(f"\n❌ sounddevice: {e}"); return None
        if not frames or not started: return None
        audio=np.concatenate(frames).astype(np.float32)
        if sr!=SAMPLE_RATE:
            try:
                import scipy.signal as sps
                audio=sps.resample(audio,int(len(audio)*SAMPLE_RATE/sr)).astype(np.float32)
            except ImportError:
                step=sr/SAMPLE_RATE; idx=np.round(np.arange(0,len(audio),step)).astype(int)
                audio=audio[idx[idx<len(audio)]]
        return audio

    def _record_sd(self, timeout=10.0, silence_sec=1.5):
        has_parecord=subprocess.run(['which','parecord'],capture_output=True).returncode==0
        pa_name=self.mic.get('pa_name')
        if not pa_name and has_parecord:
            try:
                out=subprocess.check_output(['pactl','list','sources','short'],text=True,stderr=subprocess.DEVNULL)
                for line in out.splitlines():
                    parts=line.split('\t')
                    if len(parts)<2: continue
                    src=parts[1]
                    if '.monitor' in src: continue
                    if 'acp6x' in src.lower(): pa_name=src; break
                    if pa_name is None: pa_name=src
            except: pass
        if has_parecord and pa_name:
            result=self._parecord_capture(pa_name,duration=timeout)
            if result is not None: return result
        dev_idx=self.mic.get('index')
        if not isinstance(dev_idx,int): return None
        combos=[]
        try:
            info=sd.query_devices(dev_idx); native=int(info['default_samplerate']); max_ch=info['max_input_channels']
            for ch in range(max_ch,0,-1):
                for sr in (native,48000,44100,16000): combos.append((sr,ch))
        except: combos=[(48000,2),(48000,1),(44100,2),(44100,1)]
        wsr,wch=None,None
        for sr,ch in combos:
            try: sd.rec(int(sr*0.1),samplerate=sr,channels=ch,dtype='float32',device=dev_idx,blocking=True); wsr,wch=sr,ch; break
            except: continue
        if wsr is None: return None
        return self._sd_capture(dev_idx,wsr,wch,timeout,silence_sec)

    def _transcribe(self, audio, model=None):
        if model is None:
            with self._model_lock: model=self._model
        if model is None: return None
        try:
            if len(audio.shape)>1: audio=audio[:,0]
            # Usa la lingua dal language manager se disponibile, altrimenti None (auto-detect)
            whisper_lang = None
            if self._lang_ref and hasattr(self._lang_ref, 'current'):
                # Whisper usa codici ISO 639-1 — compatibili con quelli di JARVIS
                whisper_lang = self._lang_ref.current
            segs,_=model.transcribe(audio,language=whisper_lang,beam_size=5,vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400,speech_pad_ms=200,threshold=0.3))
            text=" ".join(s.text.strip() for s in segs).strip()
            print("\r"+" "*55+"\r",end="")
            return text if len(text)>1 else None
        except Exception as e: print(f"\n❌ Trascrizione: {e}"); return None

    def is_ready(self):
        with self._model_lock: return self._model is not None

    def listen(self, timeout=10.0, silence_sec=1.5):
        """Registra e trascrive con speaker verification — compatibile con jarvis_v6."""
        if not WHISPER_OK or self._stop_event.is_set():
            return None
        with self._model_lock:
            model = self._model
        if model is None:
            print("⏳ Whisper ancora in caricamento, attendi...")
            for _ in range(20):
                time.sleep(0.5)
                with self._model_lock:
                    if self._model is not None:
                        model = self._model
                        break
            if model is None:
                return None

        # Aspetta che il TTS finisca prima di ascoltare
        if self._tts_ref and self._tts_ref.is_speaking:
            while self._tts_ref.is_speaking:
                time.sleep(0.1)
            time.sleep(0.5)  # pausa extra dopo TTS

        audio = self._record_sd(timeout=timeout, silence_sec=silence_sec)
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            return None

        # Estrazione voce del proprietario:
        # Se SepFormer è pronto → separa le voci, boost sulla tua, attenuazione sulle altre
        # Altrimenti → fallback su segment filtering con resemblyzer
        if self._speaker_ref and self._speaker_ref.has_profile:
            if self._extractor_ref and self._extractor_ref.is_ready:
                # Pipeline completo: SepFormer + resemblyzer
                audio = self._extractor_ref.extract(audio)
                # Verifica finale — se l'estrazione ha prodotto silenzio o voce aliena
                ok, score = self._speaker_ref.verify(audio)
                if not ok:
                    print(f"  🔒 Voce non riconosciuta dopo estrazione (score={score:.2f})", flush=True)
                    return None
            else:
                # Fallback: segment filtering puro con resemblyzer
                audio_filtered, score = self._speaker_ref.filter_audio(audio)
                if audio_filtered is None:
                    print(f"  🔒 Nessun segmento tuo rilevato (score={score:.2f})", flush=True)
                    return None
                audio = audio_filtered

        return self._transcribe(audio, model)

    def stop(self): self._stop_event.set()

# ══════════════════════════════════════════════════════════════════════════════
# VOICE MODULE — coordinatore
# ══════════════════════════════════════════════════════════════════════════════

class VoiceModule:
    """Coordina VoiceInput + SpeakerVerifier + TTS + Wake/Sleep word."""

    def __init__(self, mic, lang="it", mem_dir=Path.home()/"jarvis_memory"):
        self.lang=lang; self._sleeping=True; self._session=False
        self._session_t=0.0; self._stop=threading.Event()
        self._voice=VoiceInput(mic)
        self._speaker=SpeakerVerifier()
        self._extractor=TargetSpeakerExtractor(self._speaker)
        pa_name=mic.get('pa_name')
        self._tts=TTSEngine(lang=lang,mem_dir=mem_dir,pa_name=pa_name)
        # Collega riferimenti per speaker verify, extractor e TTS wait in VoiceInput.listen()
        self._voice._tts_ref=self._tts
        self._voice._speaker_ref=self._speaker
        self._voice._extractor_ref=self._extractor

    def say(self, text): self._tts.say(text)
    def stop_speaking(self): self._tts.stop()
    @property
    def tts_speaking(self): return self._tts.is_speaking

    def setup_speaker(self):
        if not RESEMBLYZER_OK: print("❌ pip install resemblyzer"); return
        print("\n🎙️  Parla per 10 secondi per il profilo vocale...")
        audio=self._voice._record_sd(timeout=10.0,silence_sec=10.0)
        if audio is not None and len(audio)>SAMPLE_RATE*2:
            self._speaker.create_profile(audio)
        else: print("❌ Registrazione troppo corta")

    def listen(self):
        if not self._voice._wait_whisper(timeout=60):
            print("❌ Whisper non pronto"); return None
        while not self._stop.is_set():
            if self._sleeping:
                audio=self._voice._record_sd(timeout=2.5,silence_sec=2.5)
                if audio is None: continue
                text=self._voice._transcribe(audio)
                if not text: continue
                if is_wake_word(text):
                    ok,score=self._speaker.verify(audio)
                    if not ok: print(f"🔒 Voce non riconosciuta ({score:.2f})",flush=True); continue
                    print(f"\n☀️  Sveglio! ({score:.2f})",flush=True)
                    self._sleeping=False; self._session=True; self._session_t=time.time()
                    return self._get_command()
                continue
            if self._session and (time.time()-self._session_t<SESSION_TIMEOUT):
                return self._get_command()
            self._session=False; self._sleeping=True
        return None

    def _get_command(self):
        print("🎙️  Parla...",flush=True)
        audio=self._voice._record_sd(timeout=15.0,silence_sec=1.5)
        if audio is None: self._sleeping=True; self._session=False; return None
        ok,score=self._speaker.verify(audio)
        if not ok: print(f"🔒 ({score:.2f})",flush=True); return None
        text=self._voice._transcribe(audio)
        if not text: return None
        print(f"📝 '{text}'",flush=True)
        if is_wake_word(text) and is_sleep_word(text):
            print("💤 Sleep mode",flush=True); self._sleeping=True; self._session=False; return "__SLEEP__"
        self._session_t=time.time()
        cmd=strip_wake_word(text) if is_wake_word(text) else text
        return cmd.strip() or None

    def stop(self): self._stop.set(); self._voice.stop(); self._tts.shutdown()

    def status(self):
        return (f"🎙️  Voice Module:\n"
                f"   Whisper:  {'✅' if self._voice._model else '⏳ caricando...'}\n"
                f"   Speaker:  {'✅' if self._speaker.has_profile else '⚠️  nessun profilo'}\n"
                f"   TTS:      {'✅' if self._tts._player else '❌'}\n"
                f"   Lingua:   {self.lang}\n"
                f"   Stato:    {'💤 sleep' if self._sleeping else '👂 attivo'}")


if __name__=="__main__":
    print("="*50); print("🎙️  JARVIS Voice Module"); print("="*50)
    mic=choose_microphone()
    if not mic: sys.exit(1)
    vm=VoiceModule(mic=mic,lang="it")
    if "--setup-speaker" in sys.argv: vm.setup_speaker(); sys.exit(0)
    print(vm.status())
    print("\nDì 'Jarvis' per attivare | 'Jarvis dormi' per sleep | Ctrl+C per uscire\n")
    try:
        while True:
            result=vm.listen()
            if result is None: break
            if result=="__SLEEP__": print("💤 Standby\n"); continue
            print(f"✅ '{result}'\n"); vm.say(f"Hai detto: {result}")
    except KeyboardInterrupt: vm.stop(); print("\nStop.")
