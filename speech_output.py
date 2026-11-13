"""Interruptible speech pipeline, independent of the microphone and UI threads."""
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


class TTSEngine:
    def __init__(self, lang='it', mem_dir=Path.home() / 'jarvis_memory',
                 pa_name=None, event_fn=None):
        self.lang = lang
        self.mem_dir = Path(mem_dir)
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        self._player = next((p for p in ('mpg123', 'ffplay', 'cvlc') if shutil.which(p)), None)
        self._text_q, self._audio_q = queue.Queue(), queue.Queue()
        self._lock = threading.RLock()
        self._epoch = self._pending = 0
        self._closed = False
        self._speaking = False
        self._envelope = ()
        self._play_started = 0
        self._processes = {}
        self.last_playback = 0
        self._event_fn = event_fn or (lambda event: None)
        self._threads = [threading.Thread(target=target, daemon=True, name=name)
                         for target, name in ((self._gen_worker, 'tts-gen'), (self._play_worker, 'tts-play'))]
        for thread in self._threads:
            thread.start()

    @property
    def is_speaking(self):
        with self._lock:
            return self._speaking

    @property
    def is_busy(self):
        with self._lock:
            return self._pending > 0

    @property
    def playback_level(self):
        with self._lock:
            if not self._speaking:
                return 0
            index = int((time.monotonic()-self._play_started) / .025)
            return self._envelope[index] if 0 <= index < len(self._envelope) else .12

    @staticmethod
    def _audio_envelope(path):
        if not shutil.which('ffmpeg'):
            return ()
        try:
            import array
            import math
            result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                    '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'],
                    capture_output=True, timeout=3, check=True)
            samples = array.array('f')
            samples.frombytes(result.stdout[:8_000_000])
            if sys.byteorder != 'little':
                samples.byteswap()
            levels = []
            for start in range(0, len(samples), 200):
                frame = samples[start:start+200]
                levels.append(min(1, math.sqrt(sum(x*x for x in frame)/len(frame))*5))
            return tuple(levels)
        except (OSError, ValueError, subprocess.SubprocessError):
            return ()

    @staticmethod
    def _clean(text):
        text = re.sub(r'```[\s\S]*?```', ' Codice disponibile nella conversazione. ', text)
        text = re.sub(r'[*_`#~>|]', '', text)
        text = re.sub(r':[a-z_]+:|[\U00010000-\U0010ffff]', '', text)
        return re.sub(r'\s+', ' ', text).strip()

    @classmethod
    def _chunks(cls, text):
        # Start playback after one short sentence instead of synthesizing the entire answer.
        sentences = re.split(r'(?<=[.!?;:])\s+', cls._clean(text))
        for sentence in sentences:
            words, chunk = sentence.split(), ''
            for word in words:
                if len(chunk) + len(word) > 240 and chunk:
                    yield chunk
                    chunk = ''
                chunk = (chunk + ' ' + word).strip()
            if chunk:
                yield chunk

    def say(self, text):
        if not self._player:
            raise RuntimeError('Nessun lettore audio installato (mpg123 o ffplay).')
        chunks = list(self._chunks(text or ''))
        with self._lock:
            if self._closed:
                return
            self._pending += len(chunks)
            for chunk in chunks:
                self._text_q.put((self._epoch, chunk, self.lang))

    def _emit(self, status, **values):
        self._event_fn({'type': 'tts_state', 'status': status, **values})

    def stop(self):
        with self._lock:
            self._epoch += 1
            self._pending = 0
            self._speaking = False
            self.last_playback = time.monotonic()
            for process in tuple(self._processes.values()):
                try:
                    process.kill()
                except OSError:
                    pass
            for work_queue in (self._text_q, self._audio_q):
                while True:
                    try:
                        item = work_queue.get_nowait()
                    except queue.Empty:
                        break
                    if work_queue is self._audio_q and item is not None:
                        Path(item[1]).unlink(missing_ok=True)
                    work_queue.task_done()
        self._emit('stopped')

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.stop()
            self._text_q.put(None)
            self._audio_q.put(None)

    def _current(self, epoch):
        return not self._closed and epoch == self._epoch

    def _run(self, command, epoch, kind, timeout):
        with self._lock:
            if not self._current(epoch):
                return False
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._processes[kind] = process
        try:
            return process.wait(timeout=timeout) == 0
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            if kind=='playback':
                return True  # The decoded audio has ended; do not wait for a stalled output drain.
            raise RuntimeError('Il servizio vocale non risponde.' if kind == 'generation' else 'Riproduzione vocale interrotta.')
        finally:
            with self._lock:
                if self._processes.get(kind) is process:
                    self._processes.pop(kind, None)

    def _finished(self, epoch):
        with self._lock:
            if not self._current(epoch):
                return
            self._pending -= 1
            if not self._pending:
                self._emit('ready')

    def _synthesize(self, text, lang, path, epoch):
        # A separate process makes a stalled network request cancellable even with older gTTS.
        code = 'import sys; from gtts import gTTS; gTTS(text=sys.argv[1],lang=sys.argv[2],lang_check=False).save(sys.argv[3])'
        return self._run([sys.executable, '-c', code, text, lang, str(path)], epoch, 'generation', 15)

    def _gen_worker(self):
        while True:
            item = self._text_q.get()
            if item is None:
                self._text_q.task_done()
                return
            epoch, text, lang = item
            path = None
            handed_off = False
            try:
                with self._lock:
                    if not self._current(epoch):
                        continue
                handle, filename = tempfile.mkstemp(prefix='tts_', suffix='.mp3', dir=self.mem_dir)
                import os
                os.close(handle)
                path = Path(filename)
                if not self._synthesize(text, lang, path, epoch) or path.stat().st_size < 100:
                    raise RuntimeError('Sintesi vocale non disponibile.')
                envelope = self._audio_envelope(path)
                with self._lock:
                    if self._current(epoch):
                        self._audio_q.put((epoch, str(path), envelope))
                        handed_off = True
            except Exception as exc:
                if self._current(epoch):
                    self._emit('error', message=str(exc))
            finally:
                if not handed_off:
                    if path:
                        path.unlink(missing_ok=True)
                    self._finished(epoch)
                self._text_q.task_done()

    def _play_worker(self):
        commands = {'mpg123': ['mpg123', '-q'],
                    'ffplay': ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet'],
                    'cvlc': ['cvlc', '--play-and-exit', '-q']}
        while True:
            item = self._audio_q.get()
            if item is None:
                self._audio_q.task_done()
                return
            epoch, path, envelope = item
            try:
                with self._lock:
                    if not self._current(epoch):
                        continue
                    self._speaking = True
                    self.last_playback = time.monotonic()
                    self._play_started = self.last_playback
                    self._envelope = envelope
                self._emit('speaking')
                timeout=max(.5,len(envelope)*.025+.25) if envelope else 120
                if not self._run(commands[self._player] + [path], epoch, 'playback', timeout):
                    if self._current(epoch):
                        raise RuntimeError('Il lettore audio non ha riprodotto la risposta.')
            except Exception as exc:
                if self._current(epoch):
                    self._emit('error', message=str(exc))
            finally:
                Path(path).unlink(missing_ok=True)
                with self._lock:
                    if self._current(epoch):
                        ended=time.monotonic()
                        self.last_playback = min(ended,self._play_started+len(envelope)*.025) if envelope else ended
                        self._speaking = False
                self._finished(epoch)
                self._audio_q.task_done()
