"""Regression for devices rejecting 16 kHz, mono input or an advertised native rate."""
from pathlib import Path
import sys
import threading
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_module as voice

opened = []
closed = []
class Stream:
    def __init__(self, *, samplerate, channels, **kwargs):
        self.rate, self.channels = samplerate, channels
        opened.append((samplerate, channels))
    def start(self):
        # Driver advertises 44.1 kHz but actually opens only 48 kHz stereo.
        if (self.rate, self.channels) != (48000, 2):
            raise voice.sd.PortAudioError('Invalid sample rate', -9997)
    def read(self, count):
        amplitude = .0001 if count > self.rate * .2 else .02
        return np.full((count,self.channels), amplitude, dtype=np.float32), False
    def close(self):
        closed.append((self.rate, self.channels))

class Speech:
    def is_speech(self, *_): return True
sys.modules['webrtcvad'] = SimpleNamespace(Vad=lambda _:Speech())

microphone = {'source':'sd', 'index':7, 'samplerate':16000}
with patch.object(voice.sd, 'query_devices', return_value={'default_samplerate':44100,'max_input_channels':2}), \
     patch.object(voice.sd, 'check_input_settings'), patch.object(voice.sd, 'InputStream', Stream):
    recorder = voice.VoiceInput.__new__(voice.VoiceInput)
    recorder.mic = microphone
    recorder._stop_event = threading.Event()
    recorder._rms_thresh = .003
    audio = recorder.record_sample(10)
    assert audio.shape == (160000,), audio.shape
    assert audio.dtype == np.float32
    assert np.allclose(audio, .02)
    assert opened[-1] == closed[-1] == (48000,2)
    assert len(opened) == len(closed), 'A rejected stream was left open'
    monitor = voice.MicrophoneMonitor([microphone])
    monitor._stop.set()
    monitor._measure(0, microphone)
    assert opened[-1] == closed[-1] == (48000,2)
    continuous = recorder._record_sd(timeout=.3, silence_sec=.1)
    assert continuous is not None and continuous.dtype == np.float32
    assert opened[-1] == closed[-1] == (48000,2)
    recorder._stop_event.set()
    assert recorder.record_sample() is None
    assert opened[-1] == closed[-1]

# A persisted PortAudio index may now refer to an entirely different input.
with patch.object(voice.sd, 'query_devices', return_value=[
        {'name':'Other device','default_samplerate':44100,'max_input_channels':2},
        {'name':'My microphone','default_samplerate':48000,'max_input_channels':2}]), \
     patch.object(voice.sd, 'check_input_settings') as settings, patch.object(voice.sd, 'InputStream', Stream):
    recorder.mic = {'source':'sd','index':0,'name':'My microphone','samplerate':16000}
    recorder._stop_event.clear()
    assert recorder.record_sample(10).shape == (160000,)
    assert settings.call_args.kwargs['device'] == 1
    assert recorder._record_sd(timeout=.3) is not None
    assert settings.call_args.kwargs['device'] == 1

print('Frequenze alternative, stereo, ricampionamento, monitor e cancellazione OK')
