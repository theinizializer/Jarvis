"""Persistence, interrupted tools and isolation between local users/profiles."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from chat_store import ChatStore
from agent_runtime import CONFIRM_PREFIX,repair_tool_history


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'XDG_DATA_HOME':str(self.root/'data')});self.env.start()
        self.home=patch('pathlib.Path.home',return_value=self.root);self.home.start()
        self.sources=patch('app_config.ROOT',self.root);self.sources.start()
        self.host=Jarvis(settings=Settings(self.root/'config'));self.host.role='admin'

    def tearDown(self):
        self.host._shutdown();self.sources.stop();self.home.stop();self.env.stop();self.temp.cleanup()

    def test_new_chat_preserves_old_chat_and_restart_restores_model_context(self):
        self.host._chat_completion=lambda *_:{'content':'Risposta salvata'}
        self.host.record_chat_message('Tu','Ricordi questo test?')
        self.host.record_chat_message('JARVIS',self.host.process('Ricordi questo test?'))
        saved_id=self.host._chat_id
        self.host.new_conversation()
        self.assertEqual(self.host._history,[])
        self.assertEqual(self.host.conversations()[0]['id'],saved_id)
        restored=Jarvis(settings=Settings(self.root/'config'));restored.role='admin'
        transcript=restored.open_conversation(saved_id)
        self.assertEqual(transcript[-1]['text'],'Risposta salvata')
        self.assertEqual(restored._history[0]['content'],'Ricordi questo test?')
        self.assertEqual(restored._history[-1]['content'],'Risposta salvata')
        self.assertEqual(restored._archive().path.stat().st_mode&0o777,0o600)
        restored._shutdown()

    def test_owners_and_profiles_cannot_open_each_others_chats(self):
        self.host.record_chat_message('Tu','Privato');saved_id=self.host._chat_id
        self.host.session_user='another-user'
        self.assertEqual(self.host.conversations(),[])
        with self.assertRaises(KeyError):self.host.open_conversation(saved_id)
        self.host.session_user=None;self.host._cfg['active_profile']='other-profile'
        self.assertEqual(self.host.conversations(),[])
        with self.assertRaises(KeyError):self.host.open_conversation(saved_id)

    def test_reopening_a_pending_approval_never_executes_it(self):
        target=self.root/'must-not-exist.txt'
        self.host._chat_completion=lambda *_:{'tool_calls':[{'id':'write','type':'function','function':{
            'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'pending'})}}]}
        self.host.record_chat_message('Tu','Crea un file')
        self.assertTrue(self.host.process('Crea un file').startswith(CONFIRM_PREFIX))
        saved_id=self.host._chat_id
        restored=Jarvis(settings=Settings(self.root/'config'));restored.role='admin'
        messages=restored.open_conversation(saved_id)
        self.assertFalse(target.exists());self.assertFalse(restored.agent_task_active)
        self.assertIn('autorizzazioni',messages[-1]['text'])
        results=[m for m in restored._history if m['role']=='tool']
        self.assertEqual(json.loads(results[-1]['content'])['status'],'interrupted')
        restored._shutdown()

    def test_provider_error_after_tool_result_can_be_followed_by_another_request(self):
        source=self.root/'test.txt';source.write_text('dati letti')
        def first(messages,tools):
            if messages[-1]['role']=='user':return {'tool_calls':[{'id':'read','function':{'name':'read_file','arguments':json.dumps({'path':str(source)})}}]}
            raise TimeoutError('synthetic timeout')
        self.host._chat_completion=first
        self.assertIn('non disponibile',self.host.process('leggi il test'))
        self.assertFalse(self.host.agent_task_active)
        def resumed(messages,tools):
            self.assertTrue(any(m['role']=='tool' and 'dati letti' in m['content'] for m in messages))
            return {'content':'Ripreso dal risultato conservato'}
        self.host._chat_completion=resumed
        self.assertIn('Ripreso',self.host.process('riprendi'))

    def test_repair_keeps_completed_results_and_closes_only_missing_calls(self):
        calls=[{'id':key,'function':{'name':'read_file','arguments':'{}'}} for key in ('a','b')]
        messages=[{'role':'assistant','tool_calls':calls},{'role':'tool','tool_call_id':'a','name':'read_file','content':'actual result'}]
        fixed=repair_tool_history(messages)
        self.assertEqual(fixed[1],messages[1]);self.assertEqual(fixed[2]['tool_call_id'],'b')
        self.assertEqual(json.loads(fixed[2]['content'])['status'],'interrupted')
        self.assertEqual(len(messages),2)

    def test_non_string_tool_detail_is_a_tool_error_instead_of_crashing_the_task(self):
        def complete(messages,tools):
            if messages[-1]['role']=='user':return {'tool_calls':[{'id':'bad','function':{'name':'read_file','arguments':'{"path":123}'}}]}
            return {'content':'Il risultato dello strumento è stato ricevuto'}
        self.host._chat_completion=complete
        self.assertIn('ricevuto',self.host.process('leggi'))
        self.assertFalse(self.host.agent_task_active)

    def test_ollama_timeout_allows_real_prompt_processing(self):
        with patch('jarvis_v10.request_json',return_value={'message':{'content':'OK'}}) as request:
            self.host._chat_completion([{'role':'user','content':'test'}],provider='ollama')
        self.assertEqual(request.call_args.kwargs['timeout'],300)

    def test_archive_does_not_store_private_reasoning(self):
        archive=ChatStore(self.root/'separate.sqlite3')
        chat_id=archive.save(None,'owner','profile',[{'role':'assistant','content':'OK','reasoning':'private'}],[])
        self.assertNotIn('reasoning',archive.load(chat_id,'owner','profile')['history'][0])

    def test_internal_command_results_are_part_of_resumable_context(self):
        self.assertIn('Token stimati',self.host.process('token'))
        self.assertEqual(self.host._history[0]['content'],'token')
        self.assertIn('Token stimati',self.host._history[-1]['content'])

    def test_system_prefix_does_not_change_every_minute(self):
        with patch('jarvis_v10.datetime') as clock:
            clock.now.side_effect=[datetime(2026,11,2,10,0),datetime(2026,11,2,10,5)]
            first=self.host._sys_prompt();second=self.host._sys_prompt()
        self.assertEqual(first,second)
        self.assertIn('2026-11-02',first)

    def test_only_configured_services_are_advertised_to_every_model(self):
        self.host.settings.secrets={}
        keys={k:'' for k in ('GMAIL_ADDRESS','GMAIL_APP_PASSWORD','EMAIL_ADDRESS','EMAIL_PASSWORD')}
        with patch.dict(os.environ,keys):
            self.host._cfg['mcp_servers']={}
            names=lambda:{t['function']['name'] for t in self.host.available_tools()}
            cloud=names();self.host._cfg['provider']='ollama'
            self.assertEqual(cloud,names())
            self.assertIn('read_file',cloud);self.assertIn('write_text_file',cloud)
            self.assertNotIn('email_send',cloud);self.assertNotIn('mcp_call',cloud)
            self.host.settings.secrets={'GMAIL_ADDRESS':'test@example.invalid','GMAIL_APP_PASSWORD':'synthetic'}
            self.host._cfg['mcp_servers']={'test':{'command':'test','enabled':True}}
            self.assertIn('email_send',names());self.assertIn('mcp_call',names())
            self.host.role='secondary'
            self.assertEqual(names(),{'system_info'})

    def test_tool_status_is_local_and_does_not_require_a_model(self):
        with patch.object(self.host,'_chat_completion') as request:
            result=self.host.process('strumenti')
        request.assert_not_called();self.assertIn('read_file',result);self.assertIn('admin',result)

    def test_file_tools_verify_published_bytes_and_return_read_back(self):
        import hashlib
        runtime=self.host._agent_runtime();target=self.root/'result.txt'
        result=runtime._tool('write_text_file',{'path':str(target),'content':'Risultato: 45'},True)
        self.assertTrue(result['verified']);self.assertEqual(result['read_back'],target.read_text())
        copied=runtime._tool('copy_file',{'source':str(target),'destination':str(self.root/'copy.txt')},True)
        self.assertTrue(copied['verified'])
        self.assertEqual(copied['sha256'],hashlib.sha256(target.read_bytes()).hexdigest())

    def test_stop_before_queued_request_never_calls_model_and_next_request_works(self):
        event=self.host.prepare_operation();self.host.cancel_current()
        with patch.object(self.host,'_chat_completion') as complete:
            answer=self.host.process('richiesta annullata',cancel_event=event)
            complete.assert_not_called()
        self.assertIn('interrotto',answer)
        self.assertIn('Token stimati',self.host.process('token'))

    def test_late_model_result_after_stop_never_runs_its_tool(self):
        target=self.root/'late.txt'
        def completion(*args):
            self.host.cancel_current()
            return {'tool_calls':[{'id':'late','function':{'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'late'})}}]}
        self.host._chat_completion=completion
        self.assertIn('interrotto',self.host.process('crea dopo la risposta'))
        self.assertFalse(target.exists());self.assertFalse(self.host.agent_task_active)
        self.assertFalse(self.host.tool_approval_pending)

    def test_stop_during_pending_confirmation_cancels_all_remaining_tools(self):
        target=self.root/'pending.txt'
        self.host._chat_completion=lambda *_:{'tool_calls':[{'id':'pending','function':{
            'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'no'})}}]}
        self.assertTrue(self.host.process('crea').startswith(CONFIRM_PREFIX))
        self.host.cancel_current()
        self.assertIn('interrotto',self.host.resume_pending_tool(True))
        self.assertFalse(target.exists());self.assertFalse(self.host.agent_task_active)


if __name__=='__main__':unittest.main()
