"""Credential storage and failover: same model/task, isolated failures, no secret leakage."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from api_key_pool import save_key,remove_key,move_key,enable_primary,credentials,entries,secret_ref
from jarvis_v10 import Jarvis
from agent_runtime import CONFIRM_PREFIX
from provider_fallback import ProviderFailure,ContextBudgetExceeded
from task_io import TaskCancelled


def failure(status):
    return HTTPError('https://example.test',status,'unavailable',{'retry-after':'2'},io.BytesIO(b'{}'))


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'XDG_DATA_HOME':str(self.root/'data')},clear=True);self.env.start()
        self.home=patch('pathlib.Path.home',return_value=self.root);self.home.start()
        self.sources=patch('app_config.ROOT',self.root);self.sources.start()
        self.settings=Settings(self.root/'cfg');self.settings.secrets={'GROQ_API_KEY':'synthetic-primary-key'}
        self.settings.values.update(provider='groq',model='openai/gpt-oss-120b',auto_fallback=False,groq_retry_wait=0)
        self.host=Jarvis(settings=self.settings);self.host.role='admin'

    def tearDown(self):
        self.host._shutdown();self.sources.stop();self.home.stop();self.env.stop();self.temp.cleanup()

    def add(self,name='Secondaria',value='synthetic-secondary-key',models=(),enabled=True):
        return save_key(self.settings,'groq',name,value,models,enabled)

    def test_values_stay_in_private_env_and_are_not_in_config_or_repr(self):
        key_id=self.add();ref=secret_ref('groq',key_id)
        self.assertNotIn('synthetic-secondary-key',self.settings.path.read_text())
        self.assertNotIn('synthetic-secondary-key',repr(credentials(self.settings,'groq','model')))
        self.assertEqual((self.settings.directory/'.env').stat().st_mode&0o777,0o600)
        loaded=Settings(self.settings.directory)
        self.assertEqual(loaded.secrets[ref],'synthetic-secondary-key');self.assertEqual(entries(loaded,'groq')[0]['name'],'Secondaria')

    def test_order_model_filter_disabled_keys_and_no_duplicate_credentials(self):
        a=self.add('Solo 120B','key-model-120b',models=['openai/gpt-oss-120b'])
        b=self.add('Generale','key-all-models')
        self.add('Spenta','key-disabled',enabled=False)
        move_key(self.settings,'groq',b,-1)
        self.assertEqual([s.id for s in credentials(self.settings,'groq','openai/gpt-oss-120b')],['default',b,a])
        self.assertEqual([s.id for s in credentials(self.settings,'groq','another-model')],['default',b])
        with self.assertRaisesRegex(ValueError,'già presente'):self.add('Duplicata','synthetic-primary-key')
        with self.assertRaisesRegex(ValueError,'già presente'):self.add('Duplicata','key-all-models')

    def test_edit_blank_preserves_key_and_remove_clears_env_without_touching_primary(self):
        key_id=self.add();ref=secret_ref('groq',key_id)
        save_key(self.settings,'groq','Rinominata','',key_id=key_id)
        self.assertEqual(self.settings.secrets[ref],'synthetic-secondary-key')
        remove_key(self.settings,'groq',key_id)
        self.assertNotIn(ref,os.environ);self.assertNotIn(ref,self.settings.secrets)
        self.assertNotIn(ref,(self.settings.directory/'.env').read_text())
        self.assertEqual(self.settings.secrets['GROQ_API_KEY'],'synthetic-primary-key')

    def test_failed_config_save_restores_both_files_and_runtime_values(self):
        self.add();before_env=(self.settings.directory/'.env').read_bytes();before_cfg=self.settings.path.read_bytes();before_values=copy.deepcopy(self.settings.values)
        with patch.object(self.settings,'save',side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):self.add('Terza','synthetic-third-key')
        self.assertEqual((self.settings.directory/'.env').read_bytes(),before_env);self.assertEqual(self.settings.path.read_bytes(),before_cfg)
        self.assertEqual(self.settings.values,before_values)

    def test_invalid_config_cannot_reference_another_service_secret(self):
        self.settings.secrets['EMAIL_PASSWORD']='never-send-to-groq'
        self.settings.values['provider_keys']={'groq':[{'id':'EMAIL_PASSWORD','name':'Injected','enabled':True}]}
        self.assertEqual([s.id for s in credentials(self.settings,'groq','model')],['default'])

    def test_429_rotates_immediately_to_next_key_same_model_before_waiting(self):
        self.add();attempts=[]
        def complete(messages,tools,*,provider,model,api_key):
            attempts.append((provider,model,api_key))
            if api_key=='synthetic-primary-key':raise failure(429)
            return {'content':'OK'}
        self.host._complete_once=complete
        with patch.object(self.host._agent_cancel,'wait') as wait:
            self.assertEqual(self.host._chat_completion([])['content'],'OK')
        wait.assert_not_called()
        self.assertEqual([x[2] for x in attempts],['synthetic-primary-key','synthetic-secondary-key'])
        self.assertTrue(all(x[:2]==('groq','openai/gpt-oss-120b') for x in attempts))
        self.assertEqual(self.host._effective_api_key_name,'Secondaria')

    def test_disabled_primary_is_not_sent_and_one_extra_can_supply_the_provider(self):
        self.add();enable_primary(self.settings,'groq',False)
        self.host._complete_once=Mock(return_value={'content':'OK'})
        self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_args.kwargs['api_key'],'synthetic-secondary-key')

    def test_rotated_key_stays_for_active_task_and_does_not_reexecute_confirmed_write(self):
        self.add();target=self.root/'only-once.txt';attempts=[];evidence=[]
        def complete(messages,tools,*,provider,model,api_key):
            attempts.append(api_key)
            if messages[-1]['role']=='user':
                return {'tool_calls':[{'id':'write-once','function':{'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'original'})}}]}
            evidence.append(messages[-1]['tool_call_id'])
            self.assertEqual(target.read_text(),'original')
            if api_key=='synthetic-primary-key':raise failure(429)
            return {'content':'Verificato'}
        self.host._complete_once=complete
        self.assertTrue(self.host.process('Crea un file una volta').startswith(CONFIRM_PREFIX))
        self.assertEqual(self.host.resume_pending_tool(True),'Verificato')
        self.assertEqual(evidence,['write-once','write-once'])
        self.assertEqual(len([m for m in self.host._history if m.get('role')=='tool']),1)
        self.assertEqual(self.host.role,'admin')

    def test_failed_key_is_skipped_on_next_request_and_new_secret_gets_new_health(self):
        key_id=self.add();attempts=[]
        def complete(*args,api_key,**kwargs):
            attempts.append(api_key)
            if api_key=='synthetic-primary-key':raise failure(401)
            return {'content':'OK'}
        self.host._complete_once=complete;self.host._chat_completion([]);self.host._chat_completion([])
        self.assertEqual(attempts,['synthetic-primary-key','synthetic-secondary-key','synthetic-secondary-key'])
        self.settings.secrets['GROQ_API_KEY']='replacement-primary-key'
        self.host._chat_completion([]);self.assertEqual(attempts[-1],'replacement-primary-key')

    def test_cancellation_never_sends_to_another_key(self):
        self.add()
        def complete(*args,**kwargs):self.host.cancel_current();raise URLError('cancelled')
        self.host._complete_once=Mock(side_effect=complete)
        with self.assertRaises(TaskCancelled):self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count,1)

    def test_bad_request_is_not_retried_with_every_key(self):
        self.add();self.host._complete_once=Mock(side_effect=failure(400))
        with self.assertRaises(ProviderFailure):self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count,1)

    def test_provider_fallback_runs_after_all_keys_fail(self):
        self.add();self.host._cfg['auto_fallback']=True;attempts=[]
        def complete(*args,provider,**kwargs):
            attempts.append(provider)
            if provider=='groq':raise failure(401)
            return {'content':'Cerebras OK'}
        self.host._complete_once=complete
        self.assertEqual(self.host._chat_completion([])['content'],'Cerebras OK')
        self.assertEqual(attempts,['groq','groq','cerebras'])

    def test_manual_test_is_only_for_selected_key_and_does_not_rotate(self):
        self.add();self.host._complete_once=Mock(side_effect=failure(401))
        self.assertIn('401',self.host.probe_provider('groq',api_key='test-only-key'))
        self.assertEqual(self.host._complete_once.call_count,1)

    def test_errors_redact_all_configured_api_values(self):
        self.add()
        body=b'{"error":{"message":"failure synthetic-secondary-key"}}'
        text=self.host._provider_problem('groq',HTTPError('https://test',500,'error',{},io.BytesIO(body)))
        self.assertNotIn('synthetic-secondary-key',text);self.assertIn('rimosso',text)

    def test_nonadmin_and_active_task_cannot_edit_credentials(self):
        self.host.role='secondary'
        with self.assertRaises(PermissionError):self.host.edit_api_keys('save','groq',name='x',value='new')
        self.host.role='admin';self.host._agent=Mock(state={'task_id':'active'})
        with self.assertRaises(RuntimeError):self.host.edit_api_keys('save','groq',name='x',value='new')

    def test_expired_account_failure_does_not_shadow_active_model_quota(self):
        self.add();router=self.host._provider_router();slot=credentials(self.settings,'groq',self.host._cfg['model'])[0]
        router.key_failures[('groq',slot.identity,'*')]=(time.monotonic()-1,'old auth',401)
        router.key_failures[('groq',slot.identity,self.host._cfg['model'])]=(time.monotonic()+50,'active quota',429)
        self.host._complete_once=Mock(return_value={'content':'OK'})
        self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count,1)
        self.assertEqual(self.host._complete_once.call_args.kwargs['api_key'],'synthetic-secondary-key')

    def test_context_too_large_on_one_account_can_try_next_account_same_model(self):
        self.add()
        def complete(*args,api_key,**kwargs):
            if api_key=='synthetic-primary-key':raise ContextBudgetExceeded('small account input cap')
            return {'content':'OK'}
        self.host._complete_once=complete
        self.assertEqual(self.host._chat_completion([])['content'],'OK')
        self.assertEqual(self.host._effective_model,'openai/gpt-oss-120b')

    def test_wait_on_exhausted_pool_is_cancellable_without_calling_more_keys(self):
        self.add();self.host._cfg['groq_retry_wait']=120
        self.host._complete_once=Mock(side_effect=lambda *a,**k:failure_raise())
        def cancel(_):self.host.cancel_current();return True
        def failure_raise():raise failure(429)
        with patch.object(self.host._agent_cancel,'wait',side_effect=cancel):
            with self.assertRaises(TaskCancelled):self.host._chat_completion([])
        self.assertEqual(self.host._complete_once.call_count,2)


if __name__=='__main__':unittest.main()
