import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis
from installer import install_project

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config'),
            'XDG_DATA_HOME': str(self.root / 'data')})
        self.environment.start()
        self.settings = Settings(self.root / 'config/jarvis')
        self.jarvis = Jarvis(settings=self.settings)

    def tearDown(self):
        self.jarvis._shutdown()
        self.environment.stop()
        self.temp.cleanup()

    def test_startup_is_lazy(self):
        code = 'import sys; from jarvis_v10 import Jarvis; from app_config import Settings; j=Jarvis(settings=Settings(sys.argv[1])); assert not set(["torch","chromadb","voice_module","jarvis_app","requests","skills.linux_skill"]) & sys.modules.keys(); j._shutdown()'
        subprocess.run([sys.executable, '-c', code, str(self.root / 'isolated')], cwd=ROOT, check=True)
        self.assertIsNone(self.jarvis._memory)
        self.assertIsNone(self.jarvis._skills)

    def test_dispatch_actions_and_spaces(self):
        target = self.root / 'a b'
        target.mkdir()
        self.jarvis.process('cd ' + str(target))
        self.assertEqual(self.jarvis._cwd, str(target))
        self.assertEqual(self.jarvis.process('ls'), '(cartella vuota)')
        self.assertIn('Token', self.jarvis.process('/token'))
        self.jarvis.process('lingua fr')
        self.assertEqual(self.settings.values['lang'], 'fr')
        self.jarvis.process('/impostazioni')
        events=[]
        while not self.jarvis.events.empty():events.append(self.jarvis.events.get_nowait())
        self.assertIn('settings',events)
        self.jarvis.process('esci')
        self.assertEqual(self.jarvis.events.get_nowait(), 'exit')

    def test_memory_real_sqlite(self):
        self.jarvis.process('ricorda progetto di prova')
        self.assertIn('progetto di prova', self.jarvis.process('memoria'))
        self.jarvis.forget_all()
        self.assertEqual(self.jarvis.show_memory(), 'Nessun ricordo salvato.')

    def test_confirmation_never_reads_stdin(self):
        target = self.root / 'output.txt'
        with patch('builtins.input', side_effect=AssertionError('stdin must not be read')):
            answer = self.jarvis.process('run echo ok > ' + str(target))
        self.assertTrue(answer.startswith('__NEEDS_CONFIRM__'))
        self.assertFalse(target.exists())
        self.jarvis._execute(answer.removeprefix('__NEEDS_CONFIRM__'), confirmed=True)
        self.assertEqual(target.read_text().strip(), 'ok')

    def test_ssh_confirmation_preserves_destination_and_rechecks_policy(self):
        ssh = self.jarvis._ssh = Mock()
        ssh.connect.return_value = True, 'ok'
        ssh.run_remote.return_value = 0, 'remote output'
        answer = self.jarvis.process('ssh laboratorio systemctl restart servizio')
        self.assertTrue(answer.startswith('__REMOTE_CONFIRM__'))
        request = json.loads(answer.removeprefix('__REMOTE_CONFIRM__'))
        self.assertEqual(request, {'host': 'laboratorio', 'command': 'systemctl restart servizio'})
        ssh.connect.assert_not_called()
        self.assertEqual(self.jarvis._execute_remote(**request, confirmed=True), 'remote output')
        ssh.connect.assert_called_once_with('laboratorio')
        ssh.run_remote.assert_called_once_with(request['command'])
        self.jarvis.role = 'secondary'
        self.assertIn('bloccato', self.jarvis._execute_remote(**request, confirmed=True))
        ssh.connect.assert_called_once()

    def test_echo_is_literal_text(self):
        self.assertEqual(self.jarvis.process('echo $HOME > destinazione.txt'), '$HOME > destinazione.txt')

    def test_translation_and_stop(self):
        self.jarvis._llm = lambda _: 'Hello'
        self.assertIn('attivo', self.jarvis.process('traduttore italiano inglese'))
        self.assertEqual(self.jarvis.process('ciao amico'), 'Hello')
        self.assertIn('terminata', self.jarvis.process('stop').lower())
        self.assertFalse(self.jarvis._translator.active)

    def test_profiles_and_personality_in_prompt(self):
        self.settings.values['profiles']['default'] = {'name': 'Tester', 'personality': 'Risposte brevi'}
        prompt = self.jarvis._sys_prompt()
        self.assertIn('Tester', prompt)
        self.assertIn('Risposte brevi', prompt)
        self.assertNotIn('Stile delle risposte:', prompt)
        self.settings.values['profiles']['default']['tone'] = 'Tecnico'
        self.assertIn('Usa termini tecnici precisi', self.jarvis._sys_prompt())
        self.assertIn('Risposte brevi', self.jarvis._sys_prompt())

    def test_keys_preserved_and_private(self):
        self.settings.save_secrets({'TEST_API_KEY': 'a b=$()"', 'TEST_OTHER_KEY': 'keep'})
        self.settings.save_secrets({'TEST_API_KEY': ''})
        self.assertEqual(self.settings.secrets['TEST_OTHER_KEY'], 'keep')
        self.assertEqual((self.settings.directory / '.env').stat().st_mode & 0o777, 0o600)
        self.assertNotIn('TEST_OTHER_KEY', self.settings.values)
        with self.assertRaises(ValueError):
            self.settings.save_secrets({'BAD KEY': 'value'})

    def test_registry_authentication(self):
        ok, key = self.jarvis.registry.init_admin('Admin', 'ABC234')
        self.assertTrue(ok)
        self.jarvis.registry.create_secondary('Guest', 'XYZ789')
        self.jarvis.role = 'locked'
        self.assertFalse(self.jarvis.authenticate('wrong'))
        self.assertIn('bloccata', self.jarvis.process('token'))
        self.assertTrue(self.jarvis.authenticate('XYZ789'))
        self.assertEqual(self.jarvis.role, 'secondary')
        with self.assertRaises(PermissionError):
            self.jarvis.enable_skill('Linux Sysadmin')
        self.assertIn('bloccato', self.jarvis._execute('touch /tmp/example'))
        self.assertFalse(self.jarvis.authenticate('ABC234'))
        self.jarvis.logout()
        self.assertTrue(self.jarvis.authenticate('ABC234'))
        self.assertEqual(self.jarvis.role, 'admin')

    def test_skill_persistence_and_markdown(self):
        name = self.jarvis.list_skills()[0]['name']
        self.assertTrue(self.jarvis.enable_skill(name))
        saved = Settings(self.settings.directory)
        self.assertTrue(saved.values['skills'][name]['enabled'])
        self.assertTrue(self.jarvis.disable_skill(name))
        source = self.root / 'my-skill/SKILL.md'
        source.parent.mkdir()
        source.write_text('Use short sentences.')
        self.jarvis.skills.install_markdown(source)
        self.jarvis.enable_skill('my-skill')
        self.assertIn('Use short sentences.', self.jarvis._sys_prompt())

    def test_failed_llm_retains_a_recoverable_request(self):
        with patch.object(self.jarvis, '_chat_completion', side_effect=OSError('offline')):
            self.assertIn('non disponibile', self.jarvis.process('ciao'))
        self.assertEqual([m['role'] for m in self.jarvis._history], ['user','assistant'])
        self.assertEqual(self.jarvis._history[0]['content'],'ciao')
        self.assertFalse(self.jarvis.agent_task_active)

    def test_voice_uses_current_module_contract(self):
        class Voice:
            def listen(self, **kwargs):
                return 'parole riconosciute', None
        self.jarvis._voice_input = Voice()
        self.assertEqual(self.jarvis.listen_once(), 'parole riconosciute')

    def test_utils_import_from_worker_thread(self):
        code = "import threading; errors=[]; t=threading.Thread(target=lambda: __import__('utils_module')); t.start(); t.join()"
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertNotIn('Traceback', result.stderr)

    def test_mcp_configuration_commands(self):
        self.jarvis.process('mcp add sample {"command":"python3","args":[]}')
        self.assertFalse(self.settings.values['mcp_servers']['sample']['enabled'])
        self.jarvis.process('mcp on sample')
        self.assertTrue(self.settings.values['mcp_servers']['sample']['enabled'])
        self.jarvis.process('mcp remove sample')
        self.assertNotIn('sample', self.settings.values['mcp_servers'])

    def test_migration_merges_memories_without_changing_source(self):
        self.jarvis._ensure_memory()
        legacy = self.root / 'old-memory'
        legacy.mkdir()
        original = '[{"fact":"vecchio ricordo"},{"fact":"vecchio ricordo"}]'
        (legacy / 'permanent.json').write_text(original)
        self.jarvis.migrate_legacy_memory([legacy])
        self.jarvis.migrate_legacy_memory([legacy])
        self.assertEqual(self.jarvis._memory.count(), 1)
        self.assertEqual((legacy / 'permanent.json').read_text(), original)

    def test_user_can_change_pin_with_current_credential(self):
        ok, key = self.jarvis.registry.init_admin('Admin', 'ABC234')
        self.assertFalse(self.jarvis.registry.change_pin(key, 'wrong', 'NEW234')[0])
        self.assertTrue(self.jarvis.registry.change_pin(key, 'ABC234', 'NEW234')[0])
        self.assertFalse(self.jarvis.registry.verify(key, 'ABC234'))
        self.assertTrue(self.jarvis.registry.verify(key, 'NEW234'))

    def test_mcp_commands_require_review(self):
        self.settings.values['mcp_servers']['example'] = {'command': 'dummy', 'enabled': True}
        reply = self.jarvis.process('mcp call example tool {"x": 1}')
        self.assertTrue(reply.startswith('__MCP_CONFIRM__'))
        self.assertEqual(self.jarvis._mcp, {})
        self.jarvis.process('mcp off example')
        self.assertFalse(self.settings.values['mcp_servers']['example']['enabled'])

class MCPTests(unittest.TestCase):
    def test_protocol_tools_call_and_shutdown(self):
        from mcp_client import MCPClient
        with tempfile.TemporaryDirectory() as directory:
            server = Path(directory) / 'server.py'
            server.write_text(textwrap.dedent('''
                import json, sys
                for line in sys.stdin:
                    message = json.loads(line)
                    if 'id' not in message:
                        continue
                    method = message['method']
                    if method == 'initialize':
                        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}}
                    elif method == 'tools/list':
                        result = {'tools': [{'name': 'echo', 'inputSchema': {'type': 'object'}}]}
                    else:
                        result = {'content': [{'type': 'text', 'text': message['params']['arguments']['value']}]}
                    print(json.dumps({'jsonrpc':'2.0', 'id':message['id'], 'result':result}), flush=True)
            '''))
            client = MCPClient({'command': sys.executable, 'args': [str(server)]}, timeout=2)
            try:
                self.assertIsNone(client.process)
                self.assertEqual(client.connect()[0]['name'], 'echo')
                process = client.process
                self.assertEqual(client.call('echo', {'value': 'hello'})['content'][0]['text'], 'hello')
                with self.assertRaises(ValueError):
                    client.call('missing', {})
            finally:
                client.close()
            self.assertIsNotNone(process.poll())

    def test_timeout_terminates_server(self):
        from mcp_client import MCPClient
        client = MCPClient({'command': sys.executable, 'args': ['-c', 'import time; time.sleep(10)']}, timeout=0.05)
        with self.assertRaises(TimeoutError):
            client.connect()
        self.assertIsNone(client.process)

class InstallerTests(unittest.TestCase):
    def test_install_reinstall_and_secret_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'app with spaces'
            dest.mkdir()
            secrets = {'.env': b'TEST_KEY=unchanged\n', '.env.enc': b'opaque', 'users.json': b'{}'}
            for name, value in secrets.items():
                (dest / name).write_bytes(value)
            result = install_project('local', ROOT, dest, desktop=False)
            self.assertIn('completata', result['result'])
            self.assertTrue((dest / 'venv/bin/python').exists())
            # Corruption is replaced using the clean installed source snapshot.
            (dest / 'jarvis_v10.py').write_text('broken')
            install_project('reinstall', destination=dest, desktop=False)
            self.assertIn('class Jarvis:', (dest / 'jarvis_v10.py').read_text())
            for name, value in secrets.items():
                self.assertEqual((dest / name).read_bytes(), value)
            subprocess.run([str(dest / 'venv/bin/python'), '-c', 'import jarvis_v10'], cwd=dest, check=True)

    def test_incomplete_package_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            source, dest = Path(directory) / 'empty', Path(directory) / 'dest'
            source.mkdir()
            with self.assertRaises(ValueError):
                install_project('import', source, dest, desktop=False)
            self.assertFalse(dest.exists())

    def test_rollback_on_registration_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'dest'
            dest.mkdir()
            (dest / 'jarvis_v10.py').write_text('original')
            (dest / '.env').write_text('KEY=preserve')
            with patch('installer.install_desktop', side_effect=OSError('registration failed')):
                with self.assertRaises(OSError):
                    install_project('local', ROOT, dest)
            self.assertEqual((dest / 'jarvis_v10.py').read_text(), 'original')
            self.assertEqual((dest / '.env').read_text(), 'KEY=preserve')
            self.assertFalse((dest / '.install-lock').exists())
            self.assertFalse((dest / 'venv').exists())

    def test_failed_reinstall_restores_environment_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'app'
            install_project('local', ROOT, dest, desktop=False)
            old_env = (dest / 'venv').readlink()
            old_source = (dest / '.install-source/jarvis_v10.py').read_bytes()
            old_manifest = (dest / '.install-manifest.json').read_bytes()
            with patch('installer.install_desktop', side_effect=OSError('desktop unavailable')):
                with self.assertRaises(OSError):
                    install_project('reinstall', destination=dest, desktop=True)
            self.assertEqual((dest / 'venv').readlink(), old_env)
            self.assertEqual((dest / '.install-source/jarvis_v10.py').read_bytes(), old_source)
            self.assertEqual((dest / '.install-manifest.json').read_bytes(), old_manifest)

    def test_desktop_registration_and_launcher_with_spaces(self):
        from installer import install_desktop
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home with spaces'
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(home / '.local/share')}):
                applications=home/'.local/share/applications'
                applications.mkdir(parents=True)
                shortcut=applications/'net.local.jarvis.desktop'
                shortcut.write_text('[Desktop Entry]\nExec=jarvis\nNoDisplay=true\nType=Application\nIcon=old\n')
                scalable=home/'.local/share/icons/hicolor/scalable/apps/jarvis.svg'
                scalable.parent.mkdir(parents=True);scalable.write_text('old SVG')
                desktop = install_desktop(ROOT, home=home)
                self.assertIn('Terminal=false', desktop.read_text())
                self.assertIn('jarvis.png',shortcut.read_text())
                self.assertIn('NoDisplay=true',shortcut.read_text())
                self.assertFalse(scalable.exists())
                self.assertEqual((home/'.local/share/icons/hicolor/256x256/apps/jarvis.png').read_bytes(),
                                 (ROOT/'assets/icons/jarvis-256.png').read_bytes())
                launcher = home / '.local/bin/jarvis'
                result = subprocess.run([str(launcher), '--help'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'dest'
            plan = install_project('import', ROOT, dest, desktop=False, dry_run=True)
            self.assertEqual(plan['mode'], 'import')
            self.assertFalse(dest.exists())

if __name__ == '__main__':
    unittest.main()
