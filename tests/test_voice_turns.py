import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from voice_session import VoiceSession


class VoiceTurnTests(unittest.TestCase):
    def session(self,engine):
        session=VoiceSession.__new__(VoiceSession)
        session.user,session.lang='owner','it'
        session.host=SimpleNamespace(session_user='owner',_cfg={'lang':'it'},_tts_engine=engine,events=queue.Queue())
        session.input=SimpleNamespace(_wait_whisper=lambda _:True,_model=None,
            capture_continuous=Mock(return_value=object()),_transcribe=lambda *_:'ciao')
        session.speaker=SimpleNamespace(match_profile=Mock(return_value=(True,.9)))
        return session

    def test_early_words_after_audio_end_survive_output_device_drain(self):
        now=time.monotonic()
        engine=SimpleNamespace(is_busy=False,is_speaking=False,last_playback=now-.2)
        session=self.session(engine)
        self.assertEqual(session.listen(),'ciao')
        boundary=session.input.capture_continuous.call_args.args[0]
        self.assertLess(boundary,now-.1,'Words spoken just after audio ended would be discarded')

    def test_capture_is_gated_during_jarvis_playback(self):
        session=self.session(SimpleNamespace(is_busy=True,is_speaking=True,last_playback=time.monotonic()))
        self.assertEqual(session.listen(),'')
        session.input.capture_continuous.assert_not_called()

    def test_a_turn_overlapping_new_playback_is_not_transcribed(self):
        engine=SimpleNamespace(is_busy=False,is_speaking=False,last_playback=0)
        session=self.session(engine)
        def overlap(boundary):
            engine.last_playback=time.monotonic()
            return object()
        session.input.capture_continuous.side_effect=overlap
        self.assertEqual(session.listen(),'')
        session.speaker.match_profile.assert_not_called()


if __name__=='__main__':unittest.main()
