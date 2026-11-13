"""Keep the selected microphone open across voice replies; only retain a short RAM buffer."""
import collections
import queue
import threading
import time
import os
import select
import shutil
import subprocess


class MicrophoneFeed:
    def __init__(self, owner, opener, detector_factory):
        self.owner, self.opener, self.detector_factory = owner, opener, detector_factory
        self.frames = queue.Queue(maxsize=60)
        self.ready = threading.Event()
        self.error = None
        self.opens = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name='voice-microphone')
        self.thread.start()

    def _run(self):
        import numpy as np
        try:
            if self.owner.mic.get('source')!='sd' and self.owner.mic.get('pa_name'):
                self._pipewire()
                return
            with self.opener(self.owner.mic) as (stream, rate, channels):
                self.opens += 1
                self.ready.set()
                while not self.owner._stop_event.is_set():
                    data, _ = stream.read(max(1, int(rate*.05)))
                    mono = data[:, np.argmax(np.mean(data**2, axis=0))] if channels>1 else data.flatten()
                    self.owner._publish_capture_level(mono)
                    audio = self.owner._resample(mono, rate) if rate!=16000 else mono.copy()
                    self._push(audio)
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()

    def _push(self,audio):
        if self.frames.full():
            try:self.frames.get_nowait()
            except queue.Empty:pass
        self.frames.put_nowait((time.monotonic(),audio))

    def _pipewire(self):
        import numpy as np
        name=self.owner.mic['pa_name']
        if shutil.which('pw-record'):
            command=['pw-record','--raw','--rate','16000','--channels','1','--format','s16','--target',name,'-']
        elif shutil.which('parecord'):
            command=['parecord','--raw','--format=s16le','--rate=16000','--channels=1','--device',name]
        else:raise RuntimeError('PipeWire e PulseAudio non disponibili.')
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        self.opens+=1
        pending=bytearray()
        try:
            while not self.owner._stop_event.is_set():
                if not select.select([process.stdout],[],[],.1)[0]:continue
                data=os.read(process.stdout.fileno(),3200)
                if not data:raise RuntimeError('Il microfono ha chiuso il flusso audio.')
                pending.extend(data)
                while len(pending)>=1600:
                    audio=np.frombuffer(bytes(pending[:1600]),dtype='<i2').astype(np.float32)/32768
                    del pending[:1600]
                    self.owner._publish_capture_level(audio)
                    self._push(audio)
                    self.ready.set()
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=.3)
                except subprocess.TimeoutExpired:process.kill();process.wait()

    def capture(self, not_before, timeout=10, silence_sec=.6):
        import numpy as np
        if not self.ready.wait(3):
            raise RuntimeError('Il microfono non si è avviato.')
        if self.error:
            raise RuntimeError(str(self.error))
        detector = self.detector_factory()
        frames, pre_roll = [], collections.deque(maxlen=3)
        deadline, last_speech = time.monotonic()+timeout, None
        while not self.owner._stop_event.is_set() and time.monotonic()<deadline:
            if self.error:
                raise RuntimeError(str(self.error))
            try:
                stamp, audio = self.frames.get(timeout=.1)
            except queue.Empty:
                continue
            if stamp<not_before:
                continue
            speech = detector.speech_seconds(audio,16000)>0
            if speech:
                self.owner.last_voice_at = stamp
                if last_speech is None:
                    frames.extend(pre_roll)
                    deadline = time.monotonic()+20
                last_speech = stamp
            if last_speech is not None:
                frames.append(audio)
                if stamp-last_speech>=silence_sec:
                    break
            else:
                pre_roll.append(audio)
        if not frames or self.owner._stop_event.is_set():
            return None
        return np.concatenate(frames).astype(np.float32)
