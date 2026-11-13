"""Regression checks for the v9 -> desktop agent capability restoration."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from commands_module import CommandContext, dispatch
from jarvis_v10 import Jarvis
from agent_runtime import find_files, read_text, CONFIRM_PREFIX


def tool(name, arguments, call_id='call_test'):
    return {'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}


class AgentRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.home / 'config'),
                                                   'XDG_DATA_HOME': str(self.home / 'data')})
        self.environment.start()
        self.home_patch = patch('pathlib.Path.home', return_value=self.home)
        self.home_patch.start()
        self.jarvis = Jarvis(settings=Settings(self.home / 'config/jarvis'))
        self.jarvis.role = 'admin'

    def tearDown(self):
        self.jarvis._shutdown()
        self.home_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_existing_output_update_requires_confirmation_hash_and_preserves_backup(self):
        target=self.home/'existing.txt';target.write_bytes(b'\xef\xbb\xbfOriginale')
        runtime=self.jarvis._agent_runtime()
        args={'path':str(target),'content':'Corretto','overwrite':True,'expected_sha256':read_text(target)['sha256']}
        self.assertTrue(runtime._tool('write_text_file',args,False).startswith('__NEEDS_CONFIRM__'))
        self.assertEqual(target.read_bytes(),b'\xef\xbb\xbfOriginale')
        result=runtime._tool('write_text_file',args,True)
        self.assertEqual(target.read_text(),'Corretto')
        self.assertEqual(Path(result['backup_path']).read_bytes(),b'\xef\xbb\xbfOriginale')
        self.assertTrue(result['verified'])

    def test_file_guard_rejects_unsafe_destinations_before_approval(self):
        runtime = self.jarvis._agent_runtime()
        pipe = self.home / 'input.pipe'
        os.mkfifo(pipe)
        for path in (self.home, self.home / '.ssh' / 'config', pipe):
            with self.subTest(path=path):
                for confirmed in (False, True):
                    with self.assertRaises(PermissionError):
                        runtime._tool('write_text_file', {'path': str(path), 'content': 'test'}, confirmed)
        destination = self.home / 'new.txt'
        args = {'path': str(destination), 'content': 'test'}
        self.assertEqual(runtime._tool('write_text_file', args, False), '__NEEDS_CONFIRM__write_text_file')
        self.assertFalse(destination.exists())

    def test_changed_existing_output_is_not_overwritten_after_approval(self):
        target=self.home/'changed.txt';target.write_text('Originale')
        args={'path':str(target),'content':'Sostituto','overwrite':True,'expected_sha256':read_text(target)['sha256']}
        target.write_text('Modifica esterna')
        with self.assertRaisesRegex(ValueError,'cambiato'):
            self.jarvis._agent_runtime()._tool('write_text_file',args,True)
        self.assertEqual(target.read_text(),'Modifica esterna')

    def test_url_access_cannot_be_claimed_without_a_tool_observation(self):
        self.jarvis._chat_completion=lambda *_:{'content':'Ho letto la pagina: errore HTTP 403.'}
        answer=self.jarvis.process('Leggi https://example.test e dimmi se è accessibile.')
        self.assertIn('Nessun dato del sito è stato confermato',answer)
        self.assertNotIn('403',answer)

    def test_negative_url_request_does_not_require_or_execute_web_access(self):
        self.jarvis._chat_completion=lambda *_:{'content':'Posso spiegare come funziona curl.'}
        self.assertIn('spiegare',self.jarvis.process('Non leggere https://example.test, spiegami solo curl.'))

    def test_curl_is_exposed_as_a_tool_and_does_not_require_terminal_execution(self):
        from unittest.mock import Mock
        self.jarvis.curl_fetch=Mock(return_value={'status':'ok','http_status':200,'body_excerpt':'{"price":42.35}'})
        outputs=iter([{'content':'Ho usato curl.'},{'tool_calls':[tool('curl_fetch',{'url':'https://example.test','find':'price'})]},{'content':'HTTP 200, prezzo nella risposta: 42.35.'}])
        def complete(messages,tools):
            self.assertIn('curl_fetch',{s['function']['name'] for s in tools})
            return next(outputs)
        self.jarvis._chat_completion=complete
        self.assertIn('HTTP 200',self.jarvis.process('Puoi provare con curl?'))
        self.jarvis.curl_fetch.assert_called_once()
        self.assertTrue(any(m.get('name')=='curl_fetch' for m in self.jarvis._history))

    def test_missing_curl_removes_all_web_tools_and_secondary_does_not_receive_them(self):
        with patch('shutil.which',return_value=None):
            names={t['function']['name'] for t in self.jarvis.available_tools()}
        self.assertFalse(names&{'web_search','read_url','curl_fetch'})
        self.jarvis.role='secondary'
        self.assertEqual([t['function']['name'] for t in self.jarvis.available_tools()],['system_info'])

    def test_explanation_about_curl_does_not_force_an_execution(self):
        self.jarvis._chat_completion=lambda *_:{'content':'curl scarica risorse HTTP.'}
        self.assertEqual(self.jarvis.process('Come posso usare curl?'),'curl scarica risorse HTTP.')
        self.assertEqual(self.jarvis.process('La ricerca usa curl?'),'curl scarica risorse HTTP.')

    def test_natural_tasks_are_not_intercepted_by_command_regex(self):
        phrases = [
            'virrei che efettui il test autonomamente che ce sulla scrivania sotto il nome di test per jarvis',
            'no il nome e "test per jarvis"',
            'esegui il test sulla scrivania', 'cerca il file test per jarvis',
            'dimmi le informazioni del mio pc', 'salva i risultati in una cartella',
            'traduci questa frase in francese', 'lista file sulla scrivania',
            'ricordi questo test?', 'esci dalla cartella attuale', 'su questo file fai il calcolo',
            'token presenti nel testo', 'aiuto con il test sulla scrivania',
        ]
        for phrase in phrases:
            ctx = CommandContext(phrase, str(self.home), 'it', True, lambda _: '', lambda _: '', lambda _: None)
            self.assertFalse(dispatch(ctx).handled, phrase)

    def test_fuzzy_filename_reports_the_actual_misspelled_name(self):
        desktop = self.home / 'Scrivania'; desktop.mkdir()
        file = desktop / 'teast per jarvis.txt'; file.write_text('test reale')
        result = find_files(str(desktop), 'test per jarvis')
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['similar_names'], [str(file)])
        self.assertEqual(result['status'], 'complete')

    def test_file_result_goes_back_to_model_and_stays_in_history(self):
        file = self.home / 'prova.txt'; file.write_text('Numeri: 9, 1, 3')
        def completion(messages, tools):
            if messages[-1]['role'] == 'user':
                return {'content': None, 'tool_calls': [tool('read_file', {'path': str(file)})]}
            self.assertEqual(messages[-1]['role'], 'tool')
            self.assertIn('9, 1, 3', messages[-1]['content'])
            self.assertEqual(messages[-1]['tool_call_id'], 'call_test')
            return {'content': 'Numeri ordinati: 1, 3, 9'}
        self.jarvis._chat_completion = completion
        self.assertIn('1, 3, 9', self.jarvis.process('leggi il file e ordina i numeri'))
        self.assertTrue(any(m['role'] == 'tool' for m in self.jarvis._history))

    def test_approval_resumes_same_task_after_real_write(self):
        target = self.home / 'risultato.txt'
        calls = []
        def completion(messages, tools):
            calls.append(messages[-1]['role'])
            if messages[-1]['role'] == 'user':
                return {'content': '', 'tool_calls': [tool('write_text_file', {'path': str(target), 'content': 'risultato'})]}
            self.assertEqual(json.loads(messages[-1]['content'])['status'], 'ok')
            return {'content': 'File creato e verificato.'}
        self.jarvis._chat_completion = completion
        answer = self.jarvis.process('crea un file con il risultato')
        self.assertTrue(answer.startswith(CONFIRM_PREFIX))
        self.assertFalse(target.exists())
        self.assertIn('verificato', self.jarvis.resume_pending_tool(True))
        self.assertEqual(target.read_text(), 'risultato')
        self.assertEqual(calls, ['user', 'tool'])
        self.assertFalse(self.jarvis.tool_approval_pending)

    def test_cancel_stops_remaining_tools_and_does_not_retry(self):
        target = self.home / 'annullato.txt'
        self.jarvis._chat_completion = lambda *_: {'tool_calls': [
            tool('write_text_file', {'path': str(target), 'content': 'x'}),
            tool('create_directory', {'path': str(self.home/'other')}, 'call_second')]}
        self.jarvis.process('crea i risultati')
        self.jarvis.resume_pending_tool(False)
        self.assertFalse(target.exists())
        self.assertFalse((self.home/'other').exists())
        results = [m for m in self.jarvis._history if m['role'] == 'tool']
        self.assertEqual(len(results), 2)
        self.assertTrue(all(json.loads(m['content'])['status'] == 'cancelled' for m in results))

    def test_changed_identity_cannot_approve_previous_request(self):
        target=self.home/'identity.txt'
        self.jarvis._chat_completion=lambda *_:{'tool_calls':[tool('write_text_file',{'path':str(target),'content':'x'})]}
        self.jarvis.process('crea un risultato')
        self.jarvis.session_user='another-user'
        self.jarvis.resume_pending_tool(True)
        self.assertFalse(target.exists())

    def test_file_credentials_and_binary_are_rejected(self):
        secret=self.home/'.env';secret.write_text('PRIVATE=hidden')
        with self.assertRaises(PermissionError):read_text(str(secret))
        binary=self.home/'binary.txt';binary.write_bytes(b'a\x00b')
        with self.assertRaises(ValueError):read_text(str(binary))

    def test_multiple_tool_results_retain_ids_and_order(self):
        first=self.home/'one.txt';first.write_text('one')
        second=self.home/'two.txt';second.write_text('two')
        def completion(messages, tools):
            if messages[-1]['role']=='user':
                return {'tool_calls':[tool('read_file',{'path':str(first)},'a'),tool('read_file',{'path':str(second)},'b')]}
            results=[m for m in messages if m['role']=='tool']
            self.assertEqual([m['tool_call_id'] for m in results],['a','b'])
            return {'content':'Entrambi letti'}
        self.jarvis._chat_completion=completion
        self.assertEqual(self.jarvis.process('leggi entrambi'),'Entrambi letti')

    def test_agent_commands_keep_returncode_and_guardian(self):
        self.assertIn('__NEEDS_CONFIRM__',self.jarvis._execute('python3 -V',agent=True))
        result=self.jarvis._execute('false',agent=True,confirmed=True)
        self.assertEqual(result['returncode'],1)
        self.assertEqual(result['status'],'error')
        self.assertIn('bloccato',self.jarvis._execute('cat ~/.env',agent=True))

    def test_existing_output_is_never_overwritten(self):
        target=self.home/'existing.txt';target.write_text('originale')
        def completion(messages, tools):
            if messages[-1]['role']=='user':return {'tool_calls':[tool('write_text_file',{'path':str(target),'content':'nuovo'})]}
            error=json.loads(messages[-1]['content'])
            self.assertEqual(error['status'],'error')
            self.assertEqual(error['error_type'],'FileExistsError')
            self.assertIn('Non ripetere',error['next_step'])
            return {'content':'File già presente: originale preservato'}
        self.jarvis._chat_completion=completion
        self.jarvis.process('crea un risultato')
        self.jarvis.resume_pending_tool(True)
        self.assertEqual(target.read_text(),'originale')

    def test_provider_failure_after_write_keeps_completed_trace(self):
        target=self.home/'completed-before-error.txt'
        def completion(messages, tools):
            if messages[-1]['role']=='user':return {'tool_calls':[tool('write_text_file',{'path':str(target),'content':'scritto'})]}
            raise OSError('provider offline')
        self.jarvis._chat_completion=completion
        self.jarvis.process('crea risultato')
        self.assertIn('non disponibile',self.jarvis.resume_pending_tool(True))
        self.assertEqual(target.read_text(),'scritto')
        self.assertTrue(any(m['role']=='tool' and 'completed-before-error.txt' in m['content'] for m in self.jarvis._history))
        self.assertFalse(self.jarvis.tool_approval_pending)

    def test_agent_mutating_legacy_allowlist_needs_confirmation(self):
        for command in ('git push', 'pip install some-package', 'find . -fprint OUT', 'sort -o OUT IN'):
            self.assertTrue(self.jarvis._execute(command,agent=True).startswith('__NEEDS_CONFIRM__'), command)
        self.assertIn('bloccato',self.jarvis._execute('cat ~/.codex/auth.json',agent=True))

    def test_relative_file_path_uses_current_directory(self):
        folder=self.home/'current';folder.mkdir();(folder/'file.txt').write_text('directory corrente')
        self.jarvis._cwd=str(folder)
        def completion(messages, tools):
            if messages[-1]['role']=='user':return {'tool_calls':[tool('read_file',{'path':'file.txt'})]}
            self.assertIn('directory corrente',messages[-1]['content'])
            return {'content':'letto'}
        self.jarvis._chat_completion=completion
        self.assertEqual(self.jarvis.process('leggi il file corrente'),'letto')

    def test_native_ollama_names_tool_results(self):
        self.jarvis._cfg.update(provider='ollama',model='local')
        messages=[{'role':'tool','tool_call_id':'a','name':'system_info','content':'ok'}]
        with patch('jarvis_v10.request_json',return_value={'message':{'content':'ok'}}) as request:
            self.jarvis._chat_completion(messages,[],provider='ollama')
        sent=request.call_args.args[1]['messages'][0]
        self.assertEqual(sent['tool_name'],'system_info')
        self.assertNotIn('tool_call_id',sent)

    def test_timeout_is_not_a_successful_command(self):
        import subprocess
        with patch('task_io.run_command',side_effect=subprocess.TimeoutExpired('test',30)):
            result=self.jarvis._execute('uname',agent=True)
        self.assertEqual(result['status'],'error')
        self.assertTrue(result['timed_out'])

    def test_number_tool_preserves_values_and_computes_sum(self):
        result=self.jarvis._agent_runtime()._tool('calculate_numbers',{'numbers':[5,1,3,2,4,1,-2]},False)
        self.assertEqual(result['sorted'],[-2,1,1,2,3,4,5]);self.assertEqual(result['sum'],14)
        self.assertEqual(result['count'],7)
        self.assertEqual(self.jarvis._agent_runtime()._tool('calculate_numbers',{'numbers':[.1,.2]},False)['sum'],.3)

    def test_number_tool_rejects_code_nonfinite_and_boolean_values(self):
        for numbers in ([],[True],[float('nan')],['1+2'],[float('inf')]):
            with self.subTest(numbers=numbers),self.assertRaises(ValueError):
                self.jarvis._agent_runtime()._tool('calculate_numbers',{'numbers':numbers},False)


if __name__ == '__main__':
    unittest.main()
