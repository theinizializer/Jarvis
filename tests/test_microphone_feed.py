import importlib.util
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from microphone_feed import MicrophoneFeed


@unittest.skipUnless(importlib.util.find_spec('numpy'),'optional voice dependency')
class MicrophoneFeedTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.state={'voice':8,'reads':0,'opens':0,'closes':0}
        self.owner=SimpleNamespace(mic={'source':'sd'},_stop_event=threading.Event(),last_voice_at=0,
                                   _publish_capture_level=lambda _:None)
        state=self.state
        class Stream:
            def read(self,count):
                time.sleep(.05)
                state['reads']+=1
                amplitude=.15 if state['voice'] else 0
                state['voice']=max(0,state['voice']-1)
                return np.full((count,1),amplitude,dtype=np.float32),False
        class Detector:
            def speech_seconds(self,audio,rate):return .03 if np.max(np.abs(audio))>.01 else 0
        @contextmanager
        def opener(mic):
            state['opens']+=1
            try:yield Stream(),16000,1
            finally:state['closes']+=1
        self.feed=MicrophoneFeed(self.owner,opener,Detector)

    def tearDown(self):
        self.owner._stop_event.set()
        self.feed.thread.join(.5)

    def test_microphone_stays_open_between_two_turns(self):
        first=self.feed.capture(time.monotonic(),timeout=1,silence_sec=.1)
        self.assertIsNotNone(first)
        before=self.state['reads']
        time.sleep(.15)  # Output playback happens while capture remains open.
        self.assertGreater(self.state['reads'],before)
        self.state['voice']=4
        second=self.feed.capture(time.monotonic(),timeout=1,silence_sec=.1)
        self.assertIsNotNone(second)
        self.assertEqual(self.state['opens'],1)
        self.assertEqual(self.state['closes'],0)
        self.assertGreater(self.owner.last_voice_at,0)

    def test_frames_from_the_reply_before_the_capture_boundary_are_discarded(self):
        self.state['voice']=3
        time.sleep(.25)
        result=self.feed.capture(time.monotonic(),timeout=.25,silence_sec=.1)
        self.assertIsNone(result)


if __name__=='__main__':unittest.main()
