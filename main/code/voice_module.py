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
    """
    TTS con pipeline a due stadi:
      - _gen_worker  : prende testo dalla coda, genera mp3 in anticipo
      - _play_worker : prende mp3 già pronti e li riproduce in sequenza

    Mentre una frase è in riproduzione, quella successiva viene già
    generata — elimina la pausa tra frasi.
    """

    def __init__(self, lang="it", mem_dir=Path.home()/"jarvis_memory", pa_name=None):
        self.lang=lang; self.mem_dir=mem_dir; self.mem_dir.mkdir(parents=True,exist_ok=True)
        self._pa_name=pa_name
        self._player=find_tts_player()
        # Coda testo in ingresso (frasi da sintetizzare)
        self._text_q=queue.Queue(maxsize=12)
        # Coda mp3 già generati, pronti per la riproduzione
        self._audio_q=queue.Queue(maxsize=6)
        self._speaking=threading.Event()
        threading.Thread(target=self._gen_worker, daemon=True, name="tts-gen").start()
        threading.Thread(target=self._play_worker, daemon=True, name="tts-play").start()

    @property
    def is_speaking(self): return self._speaking.is_set()

    def say(self, text):
        if not GTTS_OK or not text or len(text) < 4: return
        try: self._text_q.put_nowait(text)
        except queue.Full: pass

    def stop(self):
        """Svuota entrambe le code e cancella i file mp3 in attesa."""
        while not self._text_q.empty():
            try: self._text_q.get_nowait()
            except: pass
        while not self._audio_q.empty():
            try:
                path = self._audio_q.get_nowait()
                if path and Path(path).exists():
                    Path(path).unlink(missing_ok=True)
            except: pass

    def shutdown(self):
        self._text_q.put(None)

    def _clean(self, text: str) -> str:
        t = re.sub(r'[*_`#~>|]', '', text)
        t = re.sub(r':[a-z_]+:', '', t)
        t = re.sub(r'[\U00010000-\U0010ffff]', '', t, flags=re.UNICODE)
        return re.sub(r'\s+', ' ', t).strip()

    def _gen_worker(self):
        """Stage 1 — genera mp3 in anticipo e li mette nella coda audio."""
        while True:
            try: text = self._text_q.get(timeout=1)
            except queue.Empty: continue
            if text is None:
                # Segnale di stop — propagalo al play worker
                self._audio_q.put(None)
                break
            clean = self._clean(text)
            if not clean or len(clean) < 3:
                self._text_q.task_done()
                continue
            tmp = self.mem_dir / f"tts_{int(time.time()*1000)}.mp3"
            try:
                gTTS(text=clean, lang=self.lang, slow=False).save(str(tmp))
                if not tmp.exists() or tmp.stat().st_size < 100:
                    raise RuntimeError("mp3 vuoto")
                # mp3 pronto — mettilo in coda per il play worker
                self._audio_q.put(str(tmp))
            except Exception as e:
                print(f"\n⚠️ TTS genera: {e}", flush=True)
                tmp.unlink(missing_ok=True)
            self._text_q.task_done()

    def _play_worker(self):
        """Stage 2 — riproduce mp3 già pronti in sequenza."""
        cmd_map = {
            'mpg123': ['mpg123', '-q'],
            'ffplay': ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet'],
            'cvlc':   ['cvlc', '--play-and-exit', '-q'],
        }
        while True:
            try: path = self._audio_q.get(timeout=1)
            except queue.Empty: continue
            if path is None: break
            self._speaking.set()
            _mute_mic(self._pa_name)
            try:
                cmd = cmd_map.get(self._player, [self._player]) + [path]
                subprocess.run(cmd, timeout=30, capture_output=True)
            except Exception as e:
                print(f"\n⚠️ TTS play: {e}", flush=True)
            finally:
                Path(path).unlink(missing_ok=True)
                self._speaking.clear()
                # Riattiva mic solo se non ci sono altre frasi in attesa
                if self._audio_q.empty() and self._text_q.empty():
                    time.sleep(0.4)
                    _unmute_mic(self._pa_name)
            self._audio_q.task_done()

# ══════════════════════════════════════════════════════════════════════════════
# SPEAKER VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class SpeakerVerifier:
    def __init__(self, profile_path=SPEAKER_PROFILE, threshold=SPEAKER_THRESHOLD):
        self._path=Path(profile_path); self._threshold=threshold
        self._profile=None; self._encoder=None
        if RESEMBLYZER_OK:
            import torch
            self._encoder=VoiceEncoder(device='cpu'); self._load()

    def _load(self):
        if self._path.exists():
            try: self._profile=np.load(str(self._path)); print("👤 Speaker: ✅ profilo caricato"); return
            except: pass
        print("👤 Speaker: ⚠️  nessun profilo (usa --setup-speaker)")

    @property
    def has_profile(self): return self._profile is not None

    def create_profile(self, audio):
        if not self._encoder: return False
        try:
            wav=preprocess_wav(audio,source_sr=SAMPLE_RATE)
            self._profile=self._encoder.embed_utterance(wav)
            self._path.parent.mkdir(parents=True,exist_ok=True)
            np.save(str(self._path),self._profile)
            print("✅ Profilo vocale salvato!"); return True
        except Exception as e: print(f"❌ Profilo: {e}"); return False

    def verify(self, audio):
        if not self._encoder or self._profile is None: return True, 1.0
        try:
            wav=preprocess_wav(audio,source_sr=SAMPLE_RATE)
            emb=self._encoder.embed_utterance(wav)
            sim=float(np.dot(self._profile,emb)/(np.linalg.norm(self._profile)*np.linalg.norm(emb)+1e-9))
            return sim>=self._threshold, sim
        except: return True, 1.0

# ══════════════════════════════════════════════════════════════════════════════
# VOICE INPUT — STT (uguale a jarvis_v6)
# ══════════════════════════════════════════════════════════════════════════════

class VoiceInput:
    WHISPER_MODEL_SIZE = "medium"

    def __init__(self, mic):
        self.mic=mic; self._model=None; self._model_lock=threading.Lock()
        self._rms_thresh=0.003; self._stop_event=threading.Event()
        self._tts_ref=None      # impostato da VoiceModule
        self._speaker_ref=None  # impostato da VoiceModule
        self._lang_ref=None     # impostato da VoiceModule — localizzazione wake/sleep
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
            segs,_=model.transcribe(audio,language="it",beam_size=5,vad_filter=True,
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

        # Speaker verification — ignora voci che non sono il proprietario
        if self._speaker_ref and self._speaker_ref.has_profile:
            ok, score = self._speaker_ref.verify(audio)
            if not ok:
                print(f"  🔒 Voce ignorata ({score:.2f})", flush=True)
                return None

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
        pa_name=mic.get('pa_name')
        self._tts=TTSEngine(lang=lang,mem_dir=mem_dir,pa_name=pa_name)
        # Collega riferimenti per speaker verify e TTS wait in VoiceInput.listen()
        self._voice._tts_ref=self._tts
        self._voice._speaker_ref=self._speaker

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