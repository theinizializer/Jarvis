"""Voice speaker filtering never changes the credential-based authorization boundary."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app_config import Settings
from jarvis_v10 import Jarvis
from vision_runtime import coordinates


class VoicePermissionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.home/'data')})
        self.env.start()
        self.home_patch = patch('pathlib.Path.home', return_value=self.home)
        self.home_patch.start()
        self.host = Jarvis(settings=Settings(self.home/'config'))
        self.host.registry.init_admin('Admin', 'ADMIN77')
        self.host.authenticate('ADMIN77')
        self.host.voice_mode = True

    def tearDown(self):
        self.host._shutdown()
        self.home_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_voice_drops_admin_and_grant_is_thread_scoped_and_revoked(self):
        self.assertEqual(self.host.role, 'admin')
        self.assertEqual(self.host.execution_role, 'secondary')
        with self.assertRaises(PermissionError):
            self.host.require_admin()
        with self.assertRaises(PermissionError):
            with self.host.voice_admin_operation('wrong'):
                self.fail('Password errata accettata')
        with self.assertRaises(ValueError):
            with self.host.voice_admin_operation('ADMIN77'):
                self.host.require_admin()
                raise ValueError('failed operation')
        self.assertEqual(self.host.execution_role, 'secondary')
        with self.host.voice_request_scope():
            self.host.voice_mode = False
            with self.assertRaises(PermissionError):
                self.host.require_admin()
        self.assertEqual(self.host.execution_role, 'admin')

    def test_direct_command_requires_pin_but_catastrophic_deny_remains(self):
        self.assertTrue(self.host._execute('touch file.txt').startswith('__VOICE_CONFIRM__'))
        self.assertIn('bloccato', self.host._execute('rm -rf ~'))
        with self.host.voice_admin_operation('ADMIN77'):
            self.assertIn('bloccato', self.host._execute('rm -rf ~', confirmed=True))
        self.assertEqual(self.host.execution_role, 'secondary')

    def test_each_queued_mutation_requires_its_own_admin_password(self):
        outputs = iter([
            {'tool_calls': [{'id':str(i), 'type':'function', 'function':{'name':'write_text_file',
                            'arguments': json.dumps({'path':str(self.home/f'{i}.txt'),'content':'OK'})}}
                            for i in (1, 2)]},
            {'content':'finito'},
        ])
        self.host._chat_completion = lambda *_:next(outputs)
        answer = self.host.process('Crea due file di prova.')
        self.assertTrue(answer.startswith('__AGENT_CONFIRM__'))
        self.assertFalse((self.home/'1.txt').exists())
        with self.assertRaises(PermissionError):
            self.host.resume_pending_tool(True, admin_pin='wrong')
        self.assertTrue(self.host.tool_approval_pending)
        answer = self.host.resume_pending_tool(True, admin_pin='ADMIN77')
        self.assertTrue((self.home/'1.txt').exists())
        self.assertTrue(answer.startswith('__AGENT_CONFIRM__'))
        self.assertFalse((self.home/'2.txt').exists())
        self.assertEqual(self.host.execution_role, 'secondary')
        self.host.resume_pending_tool(True, admin_pin='ADMIN77')
        self.assertTrue((self.home/'2.txt').exists())
        self.assertEqual(self.host.execution_role, 'secondary')

    def test_generated_account_has_hashed_random_pin_and_language_uniqueness(self):
        registry = self.host.registry
        with self.assertRaises(PermissionError):
            registry.create_generated_secondary('Alice', 'it', 'wrong')
        key, password = registry.create_generated_secondary('Alice', 'it', 'ADMIN77')
        self.assertEqual(len(password), 6)
        self.assertTrue(password.isalnum())
        self.assertEqual(registry.identify_by_pin(password), (key, 'secondary'))
        self.assertNotIn(password, registry._path.read_text())
        with self.assertRaises(ValueError):
            registry.create_generated_secondary(' alice ', 'it', 'ADMIN77')
        other, _ = registry.create_generated_secondary('Alice', 'en', 'ADMIN77')
        self.assertNotEqual(key, other)

    def test_vision_rejects_fabricated_coordinates_and_bad_boxes(self):
        for raw in ('{"found":true,"x":true,"y":5,"confidence":0.9}',
                    '{"found":true,"x":1000,"y":5,"confidence":0.9}',
                    '{"found":true,"x":5,"y":5,"confidence":0.2}'):
            with self.assertRaises(ValueError):
                coordinates(raw, 100, 100)
        result = coordinates('{"found":true,"x":25,"y":30,"confidence":0.8,"bbox":[10,10,50,50]}',100,100)
        self.assertEqual(result['bbox'], [10,10,50,50])
