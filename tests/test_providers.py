import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis


class ProviderRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{'XDG_DATA_HOME':self.temp.name+'/data'})
        self.environment.start()
        self.app=Jarvis(settings=Settings(Path(self.temp.name)/'config'))
        self.app.settings.secrets={'CEREBRAS_API_KEY':'fake-c','NVIDIA_API_KEY':'fake-n','GROQ_API_KEY':'fake-g'}
        self.app.role='admin'

    def tearDown(self):
        self.app._shutdown();self.environment.stop();self.temp.cleanup()

    def test_provider_switch_remembers_custom_models(self):
        self.app._cfg.update(provider='groq',model='custom-groq')
        self.app.select_provider('cerebras','custom-cerebras')
        self.assertEqual(self.app.model_for('groq'),'custom-groq')
        self.app.select_provider('nvidia','custom-nvidia')
        self.assertEqual(self.app.model_for('cerebras'),'custom-cerebras')
        self.assertEqual(self.app.model_for('nvidia'),'custom-nvidia')

    def test_cross_provider_probe_uses_correct_model_and_never_user_context(self):
        self.app._cfg.update(provider='groq',model='active-groq')
        self.app._history=[{'role':'user','content':'PRIVATE_HISTORY'}]
        with patch('jarvis_v10.request_json',return_value={'choices':[{'message':{'content':'OK'}}]}) as request:
            result=self.app.probe_provider('cerebras','gpt-oss-120b')
        self.assertIn('chat verificata',result)
        payload=request.call_args.args[1]
        self.assertEqual(payload['model'],'gpt-oss-120b')
        self.assertEqual(payload['messages'],[{'role':'user','content':'Reply only OK.'}])
        self.assertEqual(self.app._cfg['provider'],'groq')
        self.assertEqual(self.app._cfg['model'],'active-groq')

    def test_payloads_and_timeouts_are_specific_to_provider(self):
        for provider in ('cerebras','nvidia','groq'):
            with patch('jarvis_v10.request_json',return_value={'choices':[{'message':{'content':'OK'}}]}) as request:
                self.app._chat_completion([{'role':'user','content':'test'}],provider=provider)
            payload=request.call_args.args[1]
            if provider=='cerebras':
                self.assertEqual(payload['reasoning_effort'],'low')
                self.assertIn('max_completion_tokens',payload)
                self.assertNotIn('max_tokens',payload)
            elif provider=='nvidia':
                self.assertEqual(request.call_args.kwargs['timeout'],180)
                self.assertEqual(payload['chat_template_kwargs'],{'enable_thinking':False})
                self.assertIn('max_tokens',payload)
            else:
                self.assertNotIn('chat_template_kwargs',payload)
                self.assertEqual(payload['reasoning_effort'],'medium')
                self.assertEqual(payload['max_completion_tokens'],2048)
                self.assertFalse(payload['include_reasoning'])

    def test_paid_account_failure_is_not_reported_as_chat_success(self):
        error=HTTPError('https://api.cerebras.ai',402,'Payment required',{},io.BytesIO(b'{"message":"billing required"}'))
        with patch('jarvis_v10.request_json',side_effect=error):
            result=self.app.probe_provider('cerebras','gpt-oss-120b')
        self.assertIn('HTTP 402',result)
        self.assertIn('fatturazione',result)
        self.assertNotIn('chat verificata',result)

    def test_missing_nvidia_deployment_does_not_disclose_account_identifier(self):
        error=HTTPError('https://integrate.api.nvidia.com',404,'Not found',{},io.BytesIO(b'{"detail":"Function missing for account private-account-id"}'))
        result=self.app._provider_problem('nvidia',error)
        self.assertIn('HTTP 404',result)
        self.assertNotIn('private-account-id',result)

    def test_reasoning_exhaustion_is_an_error_instead_of_empty_answer(self):
        data={'choices':[{'finish_reason':'length','message':{'content':None,'reasoning_content':'internal'}}]}
        with patch('jarvis_v10.request_json',return_value=data):
            with self.assertRaisesRegex(RuntimeError,'risposta vuota'):
                self.app._chat_completion([{'role':'user','content':'test'}],provider='nvidia')

    def test_rejected_tool_generation_is_retried_once_without_disabling_validation(self):
        error=HTTPError('https://api.groq.com',400,'bad tool',{},io.BytesIO(b'{"error":{"code":"tool_use_failed","message":"Invalid tool name"}}'))
        tools=[{'type':'function','function':{'name':'system_info','parameters':{'type':'object','properties':{}}}}]
        with patch('jarvis_v10.request_json',side_effect=[error,{'choices':[{'message':{'content':'OK'}}]}]) as request:
            self.assertEqual(self.app._chat_completion([{'role':'user','content':'test'}],tools,provider='groq')['content'],'OK')
        self.assertEqual(request.call_count,2)
        retry=request.call_args.args[1]
        self.assertEqual(retry['tools'],tools)
        self.assertNotIn('disable_tool_validation',retry)
        self.assertIn('exact function names',retry['messages'][-1]['content'])

    def test_empty_groq_completion_gets_one_corrective_completion_without_restarting_tools(self):
        empty={'choices':[{'finish_reason':'stop','message':{'content':None}}]}
        with patch('jarvis_v10.request_json',side_effect=[empty,{'choices':[{'message':{'content':'Risposta recuperata'}}]}]) as request:
            self.assertEqual(self.app._chat_completion([{'role':'user','content':'test'}],provider='groq')['content'],'Risposta recuperata')
        self.assertEqual(request.call_count,2)
        self.assertIn('Do not repeat completed writes',request.call_args.args[1]['messages'][-1]['content'])

    def test_empty_groq_retries_are_bounded(self):
        with patch('jarvis_v10.request_json',return_value={'choices':[{'message':{'content':None}}]}) as request:
            with self.assertRaisesRegex(RuntimeError,'risposta vuota'):self.app._chat_completion([],provider='groq')
        self.assertEqual(request.call_count,2)


if __name__=='__main__':unittest.main()
