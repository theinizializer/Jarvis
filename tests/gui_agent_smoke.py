"""Actual Tk approval/resume and responsive preview, isolated from user data/network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory(prefix='jarvis-ui-check-') as directory:
    home=Path(directory)
    with patch('pathlib.Path.home',return_value=home), patch('app_config.ROOT',home), patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}):
        settings=Settings(home/'config');settings.secrets={}
        settings.values.update(provider='groq',model='openai/gpt-oss-120b')
        settings.values['profiles']={'default':{'name':'Anteprima','personality':'Risposte concise'}}
        host=Jarvis(settings=settings);target=home/'esempio.txt'
        copy_case='--copy-long-paths' in sys.argv
        if copy_case:
            target=home/('destinazione-lunga-'*5)/('altra-cartella-'*5)/'esempio.txt'
            source=home/('sorgente-lunga-'*6)/('sottocartella-'*5)/'originale.txt'
            source.parent.mkdir(parents=True);source.write_text('Esempio temporaneo verificato.')
        def completion(messages,tools):
            if messages[-1]['role']=='user':
                if copy_case:return {'tool_calls':[{'id':'preview_copy','type':'function','function':{
                    'name':'copy_file','arguments':json.dumps({'source':str(source),'destination':str(target)})}}]}
                return {'tool_calls':[{'id':'preview_write','type':'function','function':{
                    'name':'write_text_file','arguments':json.dumps({'path':str(target),'content':'Esempio temporaneo verificato.'})}}]}
            return {'content':'File di esempio creato e verificato.'}
        host._chat_completion=completion
        app=JarvisApp(host);app.root.geometry('1240x820')
        errors=[];app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        output=Path(os.environ.get('JARVIS_TEST_ARTIFACTS', ROOT/'artifacts'));output.mkdir(exist_ok=True)
        def capture(name):
            app.root.update_idletasks()
            result=subprocess.run(['import','-window',str(app.root.winfo_id()),str(output/name)],capture_output=True,text=True,timeout=10)
            assert result.returncode==0,result.stderr
        def guard(fn):
            try:fn()
            except Exception as exc:
                errors.append(traceback.format_exc());app.close()
        def wait_for(predicate, callback, tries=100):
            if predicate():guard(callback)
            elif tries:app.root.after(80,lambda:wait_for(predicate,callback,tries-1))
            else:errors.append('UI wait timeout');app.close()
        def start():
            capture('jarvis-agent-wide.png')
            app.send('Crea un file di esempio temporaneo.')
            wait_for(lambda:host.tool_approval_pending and not app.busy,approval)
        def approval():
            assert not target.exists()
            assert app.approval_frame.winfo_ismapped()
            assert str(app.entry.cget('state'))=='disabled', repr(app.entry.cget('state'))
            capture('jarvis-agent-approval.png')
            app.root.geometry('850x620')
            app.root.after(250,lambda:guard(small_approval))
        def small_approval():
            capture('jarvis-agent-approval-small.png')
            assert app.approval_frame.winfo_rooty()+app.approval_frame.winfo_height()<=app.root.winfo_rooty()+app.root.winfo_height()
            for child in app.approval_frame.winfo_children():
                for button in child.winfo_children():
                    if button.winfo_class()=='TButton':
                        assert button.winfo_ismapped()
                        assert button.winfo_height() >= button.winfo_reqheight(), 'Approval button clipped'
                        assert button.winfo_rooty()+button.winfo_height()<=app.root.winfo_rooty()+app.root.winfo_height()
            before=app.jobs.qsize();app.listen();assert app.jobs.qsize()==before
            app._decide_tool(True)
            wait_for(lambda:not app.busy and not host.tool_approval_pending,finished)
        def finished():
            assert target.read_text()=='Esempio temporaneo verificato.'
            assert 'File di esempio creato' in app.transcript.get('1.0','end')
            assert app.activity_entries
            app.root.geometry('1240x820')
            app.root.after(200,lambda:guard(chat_finished))
        def chat_finished():
            capture('jarvis-chat-modern.png')
            app.entry.insert(0,'Bozza')
            app._new_chat()
            assert not host._history
            assert not app.entry.get()
            assert 'Cosa facciamo oggi?' in app.transcript.get('1.0','end')
            app.show('Servizi')
            app.root.after(200,lambda:guard(services))
        def services():
            capture('jarvis-agent-services.png')
            app.close()
        app.root.after(250,lambda:guard(start))
        app.root.after(20000,lambda:(errors.append('overall UI timeout'),app.close()) if not app.closed else None)
        app.run();host._shutdown()
        if errors:raise AssertionError(errors)
        print('GUI agente: conferma, ripresa, microfono, layout e screenshot OK')
