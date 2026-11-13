"""Real Tk archive selection, application restart, context and error recovery."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory(prefix='jarvis-chat-ui-') as directory:
    home=Path(directory)
    with patch('pathlib.Path.home',return_value=home),patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}):
        errors=[];saved=[]
        def make_host():
            settings=Settings(home/'config');settings.secrets={}
            host=Jarvis(settings=settings);host.role='admin';return host
        host=make_host()
        host._chat_completion=lambda *_:{'content':'Conversazione salvata con il suo contesto.'}
        app=JarvisApp(host)
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def guard(fn):
            try:fn()
            except Exception as exc:errors.append(repr(exc));app.close()
        def wait(predicate,then,count=100):
            if predicate():guard(then)
            elif count:app.root.after(80,lambda:wait(predicate,then,count-1))
            else:errors.append('Timeout GUI history');app.close()
        def first():
            app.send('Prova della cronologia')
            wait(lambda:not app.busy,stored)
        def stored():
            saved.append(host._chat_id)
            app._new_chat();app._refresh_chats()
            assert saved[0] in app.chat_ids
            index=app.chat_ids.index(saved[0]);app.chat_list.selection_clear(0,'end');app.chat_list.selection_set(index)
            app.chat_list.event_generate('<<ListboxSelect>>')
            assert 'Conversazione salvata' in app.transcript.get()
            assert host._history[-1]['content'].startswith('Conversazione salvata')
            app.close()
        app.root.after(200,lambda:guard(first));app.run();host._shutdown()
        assert not errors,errors

        host=make_host()
        def complete(messages,tools):
            if messages[-1]['content']=='Errore di prova':raise TimeoutError('timeout sintetico')
            assert any('Conversazione salvata' in str(m.get('content','')) for m in messages)
            return {'content':'Chat ripresa dopo il riavvio. Anche dopo un errore puoi continuare.'}
        host._chat_completion=complete
        app=JarvisApp(host);app.root.geometry('1240x820')
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def restarted():
            assert saved[0] in app.chat_ids
            index=app.chat_ids.index(saved[0]);app.chat_list.selection_set(index)
            app.chat_list.event_generate('<<ListboxSelect>>')
            app.send('Errore di prova');wait(lambda:not app.busy,failed)
        def failed():
            assert 'Risposta non disponibile' in app.transcript.get()
            assert not host.agent_task_active
            app.send('Continua la chat salvata');wait(lambda:not app.busy,recovered)
        def recovered():
            assert 'Chat ripresa dopo il riavvio' in app.transcript.get()
            app.root.after(180,lambda:guard(preview))
        def preview():
            output=ROOT/'artifacts/jarvis-chat-history.png'
            subprocess.run(['import','-window',str(app.root.winfo_id()),str(output)],check=True,timeout=10)
            app.root.geometry('850x620');app.root.after(180,lambda:guard(small))
        def small():
            assert app.send_button.winfo_ismapped()
            app.transcript.copy(all_text=True)
            assert 'Chat ripresa' in app.root.clipboard_get()
            subprocess.run(['import','-window',str(app.root.winfo_id()),str(ROOT/'artifacts/jarvis-chat-history-small.png')],check=True,timeout=10)
            app.close()
        app.root.after(200,lambda:guard(restarted));app.run();host._shutdown()
        assert not errors,errors
        print('PASS: selezione chat, riavvio, contesto conservato, errore e nuova risposta, copia, finestre grande/piccola.')
