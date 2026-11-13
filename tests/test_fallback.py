"""Failover must preserve task state, tool results and the host's permissions."""
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from agent_runtime import CONFIRM_PREFIX
from jarvis_v10 import Jarvis
from provider_fallback import LocalOllama, ProviderFailure, provider_messages,quota_details,ContextBudgetExceeded
from task_io import TaskCancelled


def unavailable(status=402):
    return HTTPError('https://example.test/chat', status, 'unavailable', {}, io.BytesIO(b'{}'))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.home/'data')})
        self.env.start()
        self.roots = patch('app_config.ROOT', self.home); self.roots.start()
        self.home_patch = patch('pathlib.Path.home', return_value=self.home); self.home_patch.start()
        self.host = Jarvis(settings=Settings(self.home/'config'))
        self.host.role = 'admin'
        self.host._cfg.update(provider='groq', model='groq-model', auto_fallback=True)
        self.host._prepare_local_model = Mock(return_value='local-with-tools')

    def tearDown(self):
        self.host._shutdown()
        self.home_patch.stop(); self.roots.stop(); self.env.stop(); self.temp.cleanup()

    def test_order_and_preference_survive_billing_failure(self):
        attempts = []
        def complete(messages, tools, *, provider, model):
            attempts.append(provider)
            if provider != 'ollama': raise unavailable(429 if provider == 'groq' else 402)
            return {'content': 'Risposta locale'}
        self.host._complete_once = complete
        self.assertEqual(self.host._chat_completion([{'role':'user','content':'ciao'}])['content'], 'Risposta locale')
        self.assertEqual(attempts, ['groq','cerebras','ollama'])
        self.assertEqual(self.host._cfg['provider'], 'groq')
        self.assertEqual(self.host._cfg['model'], 'groq-model')
        self.assertEqual(self.host._effective_provider, 'ollama')

    def test_mid_task_failure_reuses_confirmed_write_without_reexecution(self):
        target = self.home/'created-once.txt'
        attempts, result_ids = [], []
        def complete(messages, tools, *, provider, model):
            attempts.append(provider)
            if messages[-1]['role'] == 'user':
                return {'tool_calls':[{'id':'write_1','type':'function','function':{
                    'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'Original result'})}}]}
            self.assertEqual(target.read_text(), 'Original result')
            result = json.loads(messages[-1]['content'])
            self.assertEqual(result['status'], 'ok')
            result_ids.append(messages[-1]['tool_call_id'])
            if provider == 'groq': raise unavailable(503)
            self.assertEqual(provider, 'cerebras')
            self.assertTrue(any(t['function']['name']=='write_text_file' for t in tools))
            return {'content':'Creazione completata e verificata.'}
        self.host._complete_once = complete
        self.assertTrue(self.host.process('crea un file').startswith(CONFIRM_PREFIX))
        self.assertFalse(target.exists())
        self.assertIn('verificata', self.host.resume_pending_tool(True))
        self.assertEqual(attempts, ['groq','groq','cerebras'])
        self.assertEqual(result_ids, ['write_1','write_1'])
        self.assertEqual(len([m for m in self.host._history if m['role']=='tool']), 1)
        self.assertEqual(target.read_text(), 'Original result')

    def test_secondary_role_keeps_same_restricted_schema_after_fallback(self):
        self.host.role = 'secondary'
        schemas = []
        def complete(messages, tools, *, provider, model):
            schemas.append(tools)
            if provider == 'groq': raise unavailable(503)
            return {'content':'OK'}
        self.host._complete_once = complete
        self.assertEqual(self.host.process('una domanda'), 'OK')
        self.assertEqual(schemas[0], schemas[1])
        self.assertFalse(any(t['function']['name']=='write_text_file' for t in schemas[1]))
        self.assertEqual(self.host.role, 'secondary')

    def test_cooldown_reset_and_disabled_fallback(self):
        attempts = []
        def complete(messages, tools, *, provider, model):
            attempts.append(provider)
            if provider=='groq': raise unavailable(402)
            return {'content':'OK'}
        self.host._complete_once = complete
        for _ in range(2): self.host._chat_completion([])
        self.assertEqual(attempts, ['groq','cerebras','cerebras'])
        self.host.reset_provider_health()
        self.host._cfg['auto_fallback'] = False
        with self.assertRaises(ProviderFailure):self.host._chat_completion([])
        self.assertEqual(attempts[-1], 'groq')
        self.assertEqual(attempts.count('cerebras'), 2)

    def test_cancellation_does_not_attempt_another_provider(self):
        def complete(*args, **kwargs):
            self.host._agent_cancel.set()
            raise URLError('cancelled')
        self.host._complete_once = Mock(side_effect=complete)
        with self.assertRaisesRegex(TaskCancelled, 'interrotto'):self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count, 1)

    def test_short_groq_quota_wait_retries_same_model_before_fallback(self):
        error=HTTPError('https://example.test',429,'quota',{'retry-after':'2'},io.BytesIO(b'{}'))
        self.host._complete_once=Mock(side_effect=[error,{'content':'OK'}])
        with patch.object(self.host._agent_cancel,'wait',return_value=False) as wait:
            self.assertEqual(self.host._chat_completion([])['content'],'OK')
        wait.assert_called_once_with(3)
        self.assertEqual(self.host._complete_once.call_count,2)
        self.assertTrue(all(call.kwargs['provider']=='groq' for call in self.host._complete_once.call_args_list))

    def test_stop_during_quota_wait_never_calls_a_reserve(self):
        error=HTTPError('https://example.test',429,'quota',{'retry-after':'2'},io.BytesIO(b'{}'))
        self.host._complete_once=Mock(side_effect=error)
        def cancel(_):self.host.cancel_current();return True
        with patch.object(self.host._agent_cancel,'wait',side_effect=cancel):
            with self.assertRaises(TaskCancelled):self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count,1)
        self.assertEqual(self.host._provider_router().failures,{})

    def test_successful_provider_is_kept_for_the_task_and_preference_retried_on_next_task(self):
        self.host._agent=Mock(state={'task_id':'first'})
        attempts=[]
        def complete(messages, tools, *, provider, model):
            attempts.append(provider)
            if len(attempts)==1:raise unavailable(503)
            return {'content':'OK'}
        self.host._complete_once=complete
        self.host._chat_completion([])
        self.host._provider_router().failures.clear()
        self.host._chat_completion([])
        self.host._agent.state={'task_id':'next'}
        self.host._chat_completion([])
        self.assertEqual(attempts,['groq','cerebras','cerebras','groq'])

    def test_manual_probe_never_falls_back(self):
        self.host._complete_once = Mock(side_effect=unavailable(402))
        self.assertIn('HTTP 402', self.host.probe_provider('cerebras'))
        self.assertEqual(self.host._complete_once.call_count, 1)
        self.assertEqual(self.host._complete_once.call_args.kwargs['provider'], 'cerebras')

    def test_groq_quota_can_use_smaller_groq_model_without_changing_preference(self):
        self.host._cfg['model']='openai/gpt-oss-120b'
        self.host._cfg.update(groq_small_fallback=True,groq_retry_wait=0)
        self.host._agent=Mock(state={'task_id':'same-task'})
        attempts=[]
        def complete(messages,tools,*,provider,model):
            attempts.append((provider,model))
            if model=='openai/gpt-oss-120b':raise unavailable(429)
            return {'content':'OK'}
        self.host._complete_once=complete
        self.host._chat_completion([]);self.host._chat_completion([])
        self.assertEqual(attempts,[('groq','openai/gpt-oss-120b'),('groq','openai/gpt-oss-20b'),('groq','openai/gpt-oss-20b')])
        self.assertEqual(self.host._cfg['model'],'openai/gpt-oss-120b')
        self.assertEqual(self.host._effective_model,'openai/gpt-oss-20b')

    def test_smaller_groq_reserve_is_disabled_by_default(self):
        self.host._cfg.update(model='openai/gpt-oss-120b',groq_retry_wait=0)
        attempts=[]
        def complete(messages,tools,*,provider,model):
            attempts.append((provider,model))
            if provider=='groq':raise unavailable(429)
            return {'content':'OK'}
        self.host._complete_once=complete;self.host._chat_completion([])
        self.assertNotIn(('groq','openai/gpt-oss-20b'),attempts)

    def test_oversized_request_does_not_wait_or_downgrade_the_active_task(self):
        self.host._cfg.update(model='openai/gpt-oss-120b',groq_small_fallback=True)
        attempts=[]
        def complete(messages,tools,*,provider,model):
            attempts.append((provider,model))
            raise HTTPError('https://test',429,'quota',{'retry-after':'50'},io.BytesIO(b'{"error":{"message":"TPM Limit 8000, Requested 10000"}}'))
        self.host._complete_once=complete
        with patch.object(self.host._agent_cancel,'wait') as wait,self.assertRaises(ContextBudgetExceeded):
            self.host._chat_completion([])
        wait.assert_not_called()
        self.assertEqual(attempts,[('groq','openai/gpt-oss-120b')]*2)

    def test_quota_diagnostic_preserves_numeric_facts_and_error_body(self):
        body=b'{"error":{"message":"TPM Limit 8000 Used 7000 Requested 2000 account secret-id"}}'
        error=HTTPError('https://test',429,'quota',{'retry-after':'2'},io.BytesIO(body))
        details=quota_details(error)
        self.assertEqual(details['requested'],2000);self.assertEqual(details['retry_seconds'],2)
        self.assertNotIn('secret-id',str(details));self.assertEqual(error.read(),body)
        error.close()

    def test_explicit_smaller_reserve_returns_to_preferred_after_quota_reset(self):
        self.host._cfg.update(model='openai/gpt-oss-120b',groq_small_fallback=True,groq_retry_wait=0)
        self.host._agent=Mock(state={'task_id':'same'})
        attempts=[]
        def complete(messages,tools,*,provider,model):
            attempts.append(model)
            if len(attempts)==1:raise unavailable(429)
            return {'content':'OK'}
        self.host._complete_once=complete;self.host._chat_completion([])
        self.host._provider_router().model_failures.clear()
        self.host._chat_completion([])
        self.assertEqual(attempts,['openai/gpt-oss-120b','openai/gpt-oss-20b','openai/gpt-oss-120b'])

    def test_cross_provider_reasoning_is_stripped_without_changing_history(self):
        history = [{'role':'assistant','_provider':'groq','content':'','reasoning':'internal',
                    'tool_calls':[{'id':'x','function':{'name':'read_file','arguments':'{}'}}]},
                   {'role':'tool','name':'read_file','tool_call_id':'x','content':'observed result'}]
        copy = provider_messages(history, 'cerebras')
        self.assertNotIn('_provider', copy[0]); self.assertNotIn('reasoning', copy[0])
        self.assertEqual(copy[1], history[1]); self.assertEqual(copy[0]['tool_calls'], history[0]['tool_calls'])
        self.assertEqual(history[0]['reasoning'], 'internal')
        self.assertNotIn('reasoning',provider_messages(history,'groq')[0])

    def test_empty_or_invalid_response_is_an_explicit_provider_failure(self):
        for data in ({}, {'choices':[]}, {'choices':[{}]}, {'choices':[{'message':None}]}, []):
            with self.subTest(data=data), patch('jarvis_v10.request_json', return_value=data):
                with self.assertRaises(ProviderFailure):
                    self.host._chat_completion([], provider='groq', api_key='synthetic')


class LocalModelTests(unittest.TestCase):
    def host(self, **cfg):
        host = Mock()
        host._cfg = {'ollama_host':'http://127.0.0.1:11434', 'ollama_autostart':True, **cfg}
        host.events = queue.Queue(); host._agent_cancel = threading.Event()
        return host

    def test_chooses_installed_tool_model_without_download_or_weakening_tools(self):
        def request(url, payload=None, **kwargs):
            if url.endswith('/api/tags'):return {'models':[{'name':'llama3.2:latest'},{'name':'qwen2.5:7b'}]}
            self.assertTrue(url.endswith('/api/show'))
            return {'capabilities':['completion','tools'] if payload['model']=='qwen2.5:7b' else ['completion']}
        local=LocalOllama(self.host(), request)
        self.assertEqual(local.model('llama3.2:latest', True), 'qwen2.5:7b')

    def test_no_installed_model_is_reported_instead_of_downloading(self):
        local=LocalOllama(self.host(), Mock(return_value={'models':[]}))
        with self.assertRaisesRegex(ProviderFailure,'non contiene modelli'):local.model('', True)

    def test_remote_host_and_disabled_autostart_never_start_process(self):
        for cfg in ({'ollama_host':'https://remote.test'}, {'ollama_autostart':False}):
            with self.subTest(cfg=cfg), patch('provider_fallback.subprocess.Popen') as process:
                local=LocalOllama(self.host(**cfg), Mock(side_effect=URLError('refused')))
                with self.assertRaises(ProviderFailure):local.model('', True)
                process.assert_not_called()

    def test_known_non_tool_models_do_not_get_selected_for_agent(self):
        request=Mock(side_effect=[{'models':[{'name':'text-only'}]}, {'capabilities':['completion']}])
        local=LocalOllama(self.host(),request)
        with self.assertRaisesRegex(ProviderFailure,'strumenti'):local.model('text-only', True)


if __name__ == '__main__':unittest.main()
