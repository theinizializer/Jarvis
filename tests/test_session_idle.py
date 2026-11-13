import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app_config import Settings,inactivity_timeout,validate_idle_minutes
from jarvis_v10 import Jarvis
from session_idle import SessionIdleMixin


class IdleHarness(SessionIdleMixin):
    def __init__(self):
        self.jarvis=SimpleNamespace(role='admin',session_user='owner',voice_mode=False,
            voice_sleeping=False,_voice_input=SimpleNamespace(last_voice_at=0),_tts_engine=None,_cfg={})
        self._last_user_input=0
        self._pending_work=0
        self._idle_work_active=False
        self.suspended=False
    def _suspend_text_session(self):self.suspended=True
    def _handle_voice_text(self,text):self.jarvis.voice_sleeping=True


class SessionIdleTests(unittest.TestCase):
    def test_custom_timeout_and_disabling_local_lock_for_server(self):
        app=IdleHarness()
        app.jarvis._cfg.update(session_lock_minutes=5,session_lock_enabled=True)
        app._check_session_idle(299.9);self.assertFalse(app.suspended)
        app._check_session_idle(300);self.assertTrue(app.suspended)
        app.suspended=False
        app.jarvis._cfg['session_lock_enabled']=False
        app._check_session_idle(100000);self.assertFalse(app.suspended)
        app.jarvis.voice_mode=True
        app._check_session_idle(120);self.assertTrue(app.jarvis.voice_sleeping)

    def test_voice_standby_can_be_configured_or_disabled_separately(self):
        app=IdleHarness();app.jarvis.voice_mode=True
        app.jarvis._cfg.update(voice_standby_minutes=10,voice_standby_enabled=True)
        app._check_session_idle(599.9);self.assertFalse(app.jarvis.voice_sleeping)
        app._check_session_idle(600);self.assertTrue(app.jarvis.voice_sleeping)
        app.jarvis.voice_sleeping=False
        app.jarvis._cfg['voice_standby_enabled']=False
        app._check_session_idle(100000);self.assertFalse(app.jarvis.voice_sleeping)
        app.jarvis.voice_mode=False
        app._check_session_idle(120);self.assertTrue(app.suspended)

    def test_invalid_configuration_preserves_default_timeout(self):
        for value in (0,-1,1441,1.5,True,'invalid',None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):validate_idle_minutes(value)
                self.assertEqual(inactivity_timeout({'session_lock_minutes':value}),120)
        self.assertEqual(inactivity_timeout({'session_lock_minutes':' 5 '}),300)
        self.assertEqual(inactivity_timeout({'session_lock_minutes':1440}),86400)

    def test_security_preferences_persist_and_only_admin_can_change_them(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
            host=Jarvis(settings=Settings(Path(directory)/'cfg'))
            try:
                host.set_session_security(lock_enabled=False,lock_minutes=15,voice_enabled=True,voice_minutes=5)
                saved=Settings(host.settings.directory)
                self.assertIsNone(inactivity_timeout(saved.values))
                self.assertEqual(inactivity_timeout(saved.values,voice=True),300)
                for role in ('secondary','locked'):
                    host.role=role
                    with self.assertRaises(PermissionError):
                        host.set_session_security(lock_enabled=True,lock_minutes=2,voice_enabled=True,voice_minutes=2)
                host.role='admin'
                with self.assertRaises(ValueError):
                    host.set_session_security(lock_enabled=True,lock_minutes=0,voice_enabled=True,voice_minutes=2)
                self.assertFalse(host._cfg['session_lock_enabled'])
                self.assertFalse(Settings(host.settings.directory).values['session_lock_enabled'])
            finally:host._shutdown()

    def test_text_locks_after_120_seconds_and_activity_restarts_the_window(self):
        app=IdleHarness()
        app._check_session_idle(119.9);self.assertFalse(app.suspended)
        with patch('session_idle.time.monotonic',return_value=50):app._user_activity()
        app._check_session_idle(169.9);self.assertFalse(app.suspended)
        app._check_session_idle(170);self.assertTrue(app.suspended)

    def test_voice_timeout_is_silence_since_the_last_voice_or_reply(self):
        app=IdleHarness();app.jarvis.voice_mode=True
        app.jarvis._voice_input.last_voice_at=110
        app._check_session_idle(229.9);self.assertFalse(app.jarvis.voice_sleeping)
        app.jarvis._tts_engine=SimpleNamespace(is_busy=True)
        app._check_session_idle(230)
        self.assertFalse(app.jarvis.voice_sleeping)
        app.jarvis._tts_engine.is_busy=False
        app._check_session_idle(231)
        app._check_session_idle(350.9);self.assertFalse(app.jarvis.voice_sleeping)
        app._check_session_idle(351);self.assertTrue(app.jarvis.voice_sleeping)
        self.assertFalse(app.suspended)

    def test_long_operation_preserves_session_and_restarts_idle_window(self):
        app=IdleHarness();app._pending_work=1
        app._check_session_idle(500)
        self.assertFalse(app.suspended)
        self.assertEqual(app._last_user_input,0,'The countdown must stay unarmed while working')
        app._pending_work=0
        app._check_session_idle(1000);self.assertFalse(app.suspended)
        app._check_session_idle(1119.9);self.assertFalse(app.suspended)
        app._check_session_idle(1120);self.assertTrue(app.suspended)

    def test_voice_waits_for_processing_but_passive_listening_can_sleep(self):
        app=IdleHarness();app.jarvis.voice_mode=True;app._pending_work=1
        app._check_session_idle(500)
        self.assertFalse(app.jarvis.voice_sleeping)
        app._pending_work=0;app.busy=1;app._listening=True
        app._check_session_idle(600)
        app._check_session_idle(719.9);self.assertFalse(app.jarvis.voice_sleeping)
        app._check_session_idle(720)
        self.assertTrue(app.jarvis.voice_sleeping)

    def test_queue_and_speech_must_both_drain_before_counting_in_text_mode(self):
        app=IdleHarness();app._pending_work=2
        app._check_session_idle(300)
        app._pending_work=1
        app.jarvis._tts_engine=SimpleNamespace(is_busy=True)
        app._check_session_idle(600);self.assertFalse(app.suspended)
        app._pending_work=0
        app._check_session_idle(900);self.assertFalse(app.suspended)
        app.jarvis._tts_engine.is_busy=False
        app._check_session_idle(1200);self.assertFalse(app.suspended)
        self.assertEqual(app._last_user_input,1200)
        app._check_session_idle(1319.9);self.assertFalse(app.suspended)
        app._check_session_idle(1320);self.assertTrue(app.suspended)

    def test_discord_request_is_work_but_online_bot_is_not(self):
        app=IdleHarness()
        app.jarvis._discord=SimpleNamespace(is_busy=True,last_work_at=300)
        app._check_session_idle(500);self.assertFalse(app.suspended)
        app.jarvis._discord.is_busy=False
        app.jarvis._discord.last_work_at=600
        app._check_session_idle(600);self.assertFalse(app.suspended)
        app._check_session_idle(719.9);self.assertFalse(app.suspended)
        app._check_session_idle(720);self.assertTrue(app.suspended)

    def test_short_background_work_between_checks_restarts_countdown(self):
        app=IdleHarness()
        app.jarvis._discord=SimpleNamespace(is_busy=False,last_work_at=200)
        app._check_session_idle(201);self.assertFalse(app.suspended)
        app._check_session_idle(319.9);self.assertFalse(app.suspended)
        app._check_session_idle(320);self.assertTrue(app.suspended)

    def test_background_agent_protects_work_and_waiting_approval_can_time_out(self):
        app=IdleHarness()
        app.jarvis.agent_task_active=True
        app.jarvis.tool_approval_pending=False
        app._check_session_idle(600);self.assertFalse(app.suspended)
        app.jarvis.tool_approval_pending=True
        app._check_session_idle(700);self.assertFalse(app.suspended)
        app._check_session_idle(820);self.assertTrue(app.suspended)

    def test_suspended_account_requires_its_password_and_keeps_its_conversation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
            host=Jarvis(settings=Settings(Path(directory)/'cfg'))
            host.registry.init_admin('Owner','OWNER77')
            host.registry.create_secondary('Guest','GUEST77')
            host.authenticate('OWNER77')
            owner=host.session_user
            host.record_chat_message('Tu','Messaggio da conservare')
            chat_id=host._chat_id
            epoch=host._session_epoch
            host.lock_session()
            self.assertEqual(host.execution_role,'locked')
            self.assertGreater(host._session_epoch,epoch)
            self.assertFalse(host.authenticate('GUEST77'))
            self.assertEqual(host.session_user,owner)
            self.assertTrue(host.authenticate('OWNER77'))
            self.assertEqual(host._chat_id,chat_id)
            self.assertEqual(host._transcript[-1]['text'],'Messaggio da conservare')
            host.logout()
            self.assertIsNone(host.session_user)
            self.assertTrue(host.authenticate('GUEST77'))
            self.assertEqual(host.role,'secondary')
            self.assertEqual(host._transcript,[])
            host._shutdown()


if __name__=='__main__':unittest.main()
