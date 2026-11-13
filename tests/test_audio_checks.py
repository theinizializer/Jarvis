"""Microphone deduplication and rejection of empty, silent or distorted samples."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from audio_checks import physical_microphones, confirmed_microphones, recording_report
try:
    import numpy as np
except ImportError:
    np = None


class AudioInputGroupingTests(unittest.TestCase):
    def test_same_hardware_and_default_aliases_do_not_create_false_choices(self):
        pulse = {'source':'pactl','name':'alsa_input.test','pa_name':'alsa_input.test','physical_id':'alsa:2:0'}
        alsa = {'source':'sd','name':'Built in (hw:2,0)','index':4}
        other = {'source':'sd','name':'USB microphone (hw:3,0)','index':5}
        aliases = [{'source':'sd','name':name} for name in ('pipewire','pulse','default')]
        self.assertEqual(physical_microphones([alsa,*aliases,pulse,other]), [pulse,{**other,'physical_id':'alsa:3:0'}])
        self.assertEqual(physical_microphones([{'source':'pactl','name':'speakers.monitor'}]), [])

    def test_zero_one_or_multiple_confirmed_inputs(self):
        a = {'name':'USB','physical_id':'usb:1'}
        b = {'name':'Internal','physical_id':'alsa:1'}
        bad = {'microphone':b,'quality':{'ok':False}}
        good = {'microphone':a,'quality':{'ok':True}}
        self.assertEqual(confirmed_microphones([bad]), [])
        self.assertEqual(confirmed_microphones([good,bad,good]), [a])
        self.assertEqual(confirmed_microphones([good,{'microphone':b,'quality':{'ok':True}}]), [a,b])
        aliases=[{'microphone':{'name':name},'quality':{'ok':True}} for name in ('default','pulse')]
        self.assertEqual(len(confirmed_microphones(aliases)),1)


@unittest.skipIf(np is None, 'Optional voice dependencies are not installed')
class RecordingQualityTests(unittest.TestCase):
    def setUp(self):
        self.speech = (.03*np.sin(np.arange(192000)*.11)).astype(np.float32)

    def test_empty_silent_nonfinite_and_short_samples_are_rejected(self):
        for audio in (None, np.zeros(192000), np.full(192000,.02), np.full(192000,float('nan')), self.speech[:1000]):
            with self.subTest(audio_type=type(audio)):
                self.assertFalse(recording_report(audio)['ok'])

    def test_clipping_is_rejected_before_voice_acceptance(self):
        clipped=np.tile(np.array([-1.,1.]),96000)
        report=recording_report(clipped)
        self.assertFalse(report['ok'])
        self.assertIn('distorto',report['message'])

    def test_level_alone_is_insufficient_and_voice_detection_is_required(self):
        class Detector:
            def is_speech(self,*_): return False
        with patch.dict(sys.modules, {'webrtcvad':SimpleNamespace(Vad=lambda _:Detector())}):
            report=recording_report(self.speech)
        self.assertFalse(report['ok'])
        self.assertEqual(report['voice_seconds'],0)

    def test_verified_voice_has_measured_duration_and_speech_seconds(self):
        class Detector:
            def is_speech(self,*_): return True
        with patch.dict(sys.modules, {'webrtcvad':SimpleNamespace(Vad=lambda _:Detector())}):
            report=recording_report(self.speech)
        self.assertTrue(report['ok'])
        self.assertEqual(report['duration'],12)
        self.assertGreater(report['voice_seconds'],11)
