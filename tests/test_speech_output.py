import subprocess
import sys
import tempfile
import time
import unittest
import wave
import struct
import math
import shutil
from pathlib import Path
from unittest.mock import patch

from speech_output import TTSEngine


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out')
        time.sleep(.01)


class LocalSpeech(TTSEngine):
    def __init__(self, path, events):
        self.duration = .08
        self.played = []
        super().__init__(mem_dir=path, event_fn=events.append)
        self._player = 'mpg123'

    def _synthesize(self, text, lang, path, epoch):
        if text == 'Slow generation.':
            return super()._run([sys.executable, '-c', 'import time; time.sleep(30)'],
                               epoch, 'generation', 40)
        path.write_bytes(b'mp3' * 100)
        return True

    def _run(self, command, epoch, kind, timeout):
        self.played.append(epoch)
        return super()._run([sys.executable, '-c', f'import time; time.sleep({self.duration})'],
                           epoch, kind, timeout)


class SpeechOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.events = []
        with patch('speech_output.shutil.which', return_value='/usr/bin/mpg123'):
            self.engine = LocalSpeech(Path(self.directory.name), self.events)

    def tearDown(self):
        self.engine.shutdown()
        for thread in self.engine._threads:
            thread.join(2)
        self.directory.cleanup()

    def test_ready_follows_actual_player_exit_without_fixed_pause(self):
        self.engine.say('Buongiorno.')
        wait_for(lambda: self.engine.is_speaking)
        wait_for(lambda: not self.engine.is_busy)
        self.assertFalse(self.engine.is_speaking)
        self.assertLess(time.monotonic() - self.engine.last_playback, .1)
        self.assertEqual(self.events[-1]['status'], 'ready')
        self.assertFalse(list(Path(self.directory.name).glob('*.mp3')))

    def test_stop_kills_active_playback_and_discards_remaining_sentences(self):
        self.engine.duration = 30
        self.engine.say('Prima frase. Seconda frase. Terza frase.')
        wait_for(lambda: 'playback' in self.engine._processes)
        process = self.engine._processes['playback']
        start = time.monotonic()
        self.engine.stop()
        self.assertFalse(self.engine.is_busy)
        self.assertFalse(self.engine.is_speaking)
        wait_for(lambda: process.poll() is not None)
        self.assertLess(time.monotonic()-start, .5)
        wait_for(lambda: not list(Path(self.directory.name).glob('*.mp3')))
        self.assertEqual(len(self.engine.played), 1)

    def test_stop_cancels_inflight_synthesis_then_accepts_new_speech(self):
        self.engine.say('Slow generation.')
        wait_for(lambda: 'generation' in self.engine._processes)
        process = self.engine._processes['generation']
        self.engine.stop()
        self.engine.say('Nuova risposta.')
        wait_for(lambda: not self.engine.is_busy)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.engine.played, [1])
        self.assertNotIn('error', [event['status'] for event in self.events])

    def test_output_backend_cannot_hold_the_microphone_after_decoded_audio_ends(self):
        self.engine.duration=30
        with patch.object(self.engine,'_audio_envelope',return_value=(.2,)*4):
            started=time.monotonic()
            self.engine.say('Risposta breve.')
            wait_for(lambda:not self.engine.is_busy,timeout=1.5)
        self.assertLess(time.monotonic()-started,1)
        self.assertFalse(self.engine.is_speaking)

    def test_shutdown_is_idempotent_and_unblocks_workers(self):
        self.engine.shutdown()
        self.engine.shutdown()
        for thread in self.engine._threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg optional')
    def test_audio_amplitude_contains_silence_and_speech_changes(self):
        path=Path(self.directory.name)/'amplitude.wav'
        samples=[0]*2400 + [int(7000*math.sin(i*math.tau*180/8000)) for i in range(4000)] + [0]*1600
        with wave.open(str(path),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(8000)
            audio.writeframes(struct.pack('<'+'h'*len(samples),*samples))
        levels=self.engine._audio_envelope(path)
        self.assertEqual(len(levels),40)
        self.assertEqual(levels[0],0)
        self.assertGreater(levels[18],.5)
        self.assertEqual(levels[-1],0)


if __name__ == '__main__':
    unittest.main()
