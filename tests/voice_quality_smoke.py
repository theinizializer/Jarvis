"""Publishing requires valid samples, and cancellation preserves the old voice profile."""
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from voice_module import SpeakerVerifier
from voice_session import VoiceSession

class Speech:
    def is_speech(self,*_): return True

sample=(.03*np.sin(np.arange(192000)*.11)).astype(np.float32)
with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,{'webrtcvad':SimpleNamespace(Vad=lambda _:Speech())}):
    speaker=SpeakerVerifier.__new__(SpeakerVerifier)
    speaker._dir=Path(tmp)
    speaker._backend='resemblyzer'
    speaker._encoder=object()
    speaker._profiles={}
    speaker._embed=lambda _:np.array([1.,0.,0.],dtype=np.float32)
    speaker.enroll_samples('alice','it',sample,sample)
    assert speaker.has_user_profile('alice','it',verified=True)
    target=speaker._path('alice','it').with_suffix('.npz')
    original=target.read_bytes()
    try:
        speaker.enroll_samples('alice','it',np.zeros(192000),sample)
        raise AssertionError('Silent sample accepted')
    except ValueError: pass
    assert target.read_bytes()==original
    stopped=threading.Event(); stopped.set()
    try:
        speaker.enroll_samples('alice','it',sample,sample,cancel_event=stopped)
        raise AssertionError('Cancelled profile published')
    except RuntimeError: pass
    assert target.read_bytes()==original
    assert not list(Path(tmp).glob('.speaker-*'))
    session=VoiceSession.__new__(VoiceSession)
    session.host=SimpleNamespace(_tts_engine=None)
    session.lang='it'
    session.input=SimpleNamespace(record_sample=lambda _:sample,is_ready=lambda:True,
                                  _transcribe=lambda *_:'Questa è la mia voce',_model=object())
    result=session.record_sample()
    assert result['quality']['ok'] and result['quality']['transcript']=='Questa è la mia voce'
    session.input._transcribe=lambda *_:''
    assert not session.record_sample()['quality']['ok']
print('Campioni silenziosi respinti, trascrizione controllata, profilo riletto e cancellazione senza sovrascrittura OK')
