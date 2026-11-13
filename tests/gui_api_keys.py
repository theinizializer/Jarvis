"""Real Tk, credential files and HTTP transport; synthetic local OpenAI server, no cloud APIs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from api_key_pool import entries,credentials,secret_ref
from jarvis_v10 import Jarvis,PROVIDERS
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory(prefix='jarvis-api-ui-') as tmp:
    home=Path(tmp);requests=[];second_id=[None];errors=[];steps=[]
    target=home/'once.txt'
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            key=self.headers.get('Authorization','').removeprefix('Bearer ')
            label='primary' if key=='synthetic-primary' else 'additional'
            messages=payload['messages'];probe=messages[-1].get('content')=='Reply only OK.'
            tool_result=messages[-1]['role']=='tool'
            requests.append({'key':label,'model':payload['model'],'tool_result':copy.deepcopy(messages[-1]) if tool_result else None,'probe':probe})
            status=200
            if probe:message={'content':'OK'}
            elif not tool_result:
                message={'tool_calls':[{'id':'once-write','type':'function','function':{'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'Written exactly once.'})}}]}
            elif label=='primary':status=429;message=None
            else:message={'content':'File creato una volta e verificato con la seconda API.'}
            body={'error':{'message':'TPM Limit 8000 Used 7990 Requested 100'}} if status==429 else {'choices':[{'message':message,'finish_reason':'stop'}]}
            raw=json.dumps(body).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            if status==429:self.send_header('retry-after','2')
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    with patch('pathlib.Path.home',return_value=home),patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}),patch.dict(PROVIDERS,{'groq':(f'http://127.0.0.1:{server.server_port}/chat','GROQ_API_KEY','openai/gpt-oss-120b')}):
        settings=Settings(home/'cfg');settings.secrets={'GROQ_API_KEY':'synthetic-primary'};settings.apply_environment()
        settings.values.update(provider='groq',model='openai/gpt-oss-120b',auto_fallback=False,groq_retry_wait=120)
        host=Jarvis(settings=settings);host.role='admin';app=JarvisApp(host)
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        output=ROOT/'artifacts';output.mkdir(exist_ok=True)
        def guard(fn):
            try:fn()
            except Exception:errors.append(traceback.format_exc());app.close()
        def wait_for(predicate,callback,tries=150):
            if predicate():guard(callback)
            elif tries:app.root.after(70,lambda:wait_for(predicate,callback,tries-1))
            else:errors.append('UI timeout');app.close()
        def controls():return app._api_key_controls
        def scroll_to(widget):
            app.root.update_idletasks();canvas=app._settings_canvas
            y=widget.winfo_rooty()-app.content.winfo_rooty()
            canvas.yview_moveto(max(0,(y-80)/max(1,app.content.winfo_height())))
            app.root.update_idletasks()
            assert widget.winfo_rootx()+widget.winfo_width()<=canvas.winfo_rootx()+canvas.winfo_width()+2,'Horizontal overflow'
            assert widget.winfo_rooty()>=canvas.winfo_rooty()-2,'Above viewport'
            assert widget.winfo_rooty()+widget.winfo_height()<=canvas.winfo_rooty()+canvas.winfo_height()+2,'Below viewport'
        def capture(name):
            app.root.update_idletasks();subprocess.run(['import','-window',str(app.root.winfo_id()),str(output/name)],check=True,capture_output=True,timeout=10)
        def start():
            app.show('Servizi');c=controls();assert str(c['key_entry'].cget('show'))=='●'
            c['name'].set('Account lavoro');c['key'].set('synthetic-work-key');c['models'].set('openai/gpt-oss-120b')
            scroll_to(c['buttons']['save']);c['buttons']['save'].invoke()
            wait_for(lambda:not app.busy and len(entries(settings,'groq'))==1,add_second)
        def add_second():
            assert not controls()['key'].get();steps.append('add and masked secret')
            c=controls();c['buttons']['new'].invoke();c['name'].set('Account personale');c['key'].set('synthetic-personal-key');c['models'].set('another-model')
            c['buttons']['save'].invoke();wait_for(lambda:not app.busy and len(entries(settings,'groq'))==2,reorder)
        def reorder():
            c=controls();second_id[0]=entries(settings,'groq')[1]['id'];c['table'].selection_set(second_id[0]);c['table'].event_generate('<<TreeviewSelect>>')
            c['buttons']['up'].invoke();wait_for(lambda:not app.busy and entries(settings,'groq')[0]['id']==second_id[0],disable)
        def disable():
            steps.append('priority order');c=controls();c['enabled'].set(False);c['buttons']['save'].invoke()
            wait_for(lambda:not app.busy and not entries(settings,'groq')[0]['enabled'],remove)
        def remove():
            steps.append('disable');c=controls();c['buttons']['remove'].invoke()
            wait_for(lambda:not app.busy and len(entries(settings,'groq'))==1,primary_off)
        def primary_off():
            assert secret_ref('groq',second_id[0]) not in settings.secrets;steps.append('remove')
            controls()['primary_button'].invoke();wait_for(lambda:not app.busy and not settings.values['provider_primary_key_enabled']['groq'],primary_on)
        def primary_on():
            assert [s.name for s in credentials(settings,'groq','openai/gpt-oss-120b')]==['Account lavoro'];steps.append('disable primary')
            controls()['primary_button'].invoke();wait_for(lambda:not app.busy and settings.values['provider_primary_key_enabled']['groq'],verify)
        def verify():
            c=controls();key_id=entries(settings,'groq')[0]['id'];c['table'].selection_set(key_id);c['table'].event_generate('<<TreeviewSelect>>')
            scroll_to(c['buttons']['verify']);c['buttons']['verify'].invoke()
            wait_for(lambda:not app.busy and any(r['probe'] for r in requests),wide)
        def wide():
            assert 'chat verificata' in controls()['message'].get();steps.append('selected key verification through real HTTP')
            app.root.geometry('1240x820');app.root.after(180,lambda:guard(wide_capture))
        def wide_capture():
            scroll_to(controls()['table']);app.root.after(200,lambda:guard(wide_painted))
        def wide_painted():
            capture('jarvis-api-keys-wide.png')
            app.root.geometry('850x620');app.root.after(180,lambda:guard(small_capture))
        def small_capture():
            scroll_to(controls()['buttons']['save']);app.root.after(200,lambda:guard(small_painted))
        def small_painted():
            capture('jarvis-api-keys-small.png')
            assert 'synthetic-work-key' not in settings.path.read_text()
            steps.append('responsive UI 850x620');app.show('Conversazione')
            app.send('Crea il file temporaneo di prova una sola volta e verifica il risultato.')
            wait_for(lambda:host.tool_approval_pending and not app.busy,approve)
        def approve():
            assert not target.exists();app._decide_tool(True)
            wait_for(lambda:not app.busy and not host.agent_task_active,finished)
        def finished():
            assert target.read_text()=='Written exactly once.'
            work=[r for r in requests if not r['probe']]
            assert [r['key'] for r in work]==['primary','primary','additional'],work
            assert len({r['model'] for r in work})==1
            assert work[1]['tool_result']==work[2]['tool_result']
            assert len([m for m in host._history if m.get('role')=='tool'])==1
            assert host._effective_api_key_name=='Account lavoro'
            steps.append('real HTTP 429 rotates same model; confirmed write is not repeated')
            capture('jarvis-api-key-rotation.png')
            (output/'api-key-rotation-report.json').write_text(json.dumps({'steps':steps,'requests':requests,'model':host._effective_model,'active_key_name':host._effective_api_key_name,'cloud_api_calls':0},ensure_ascii=False,indent=2))
            app.close()
        app.root.after(250,lambda:guard(start));app.root.after(35000,lambda:(errors.append('overall timeout'),app.close()) if not app.closed else None)
        try:app.run()
        finally:host.cancel_current();host._shutdown();server.shutdown();server.server_close()
        if errors:raise AssertionError(errors)
        print('PASS: '+', '.join(steps)+'. Nessuna API cloud contattata.')
