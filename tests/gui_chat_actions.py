"""Real Tk buttons/search/delete/undo and small-window layout. No model/API calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory(prefix='jarvis-chat-ui-') as tmp:
    home=Path(tmp)
    with patch('pathlib.Path.home',return_value=home),patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}):
        host=Jarvis(settings=Settings(home/'cfg'));host.role='admin'
        host.record_chat_message('Tu','Progetto importante');host.record_chat_message('JARVIS','Codice BICICLETTA_42');first=host._chat_id
        host.new_conversation();host.record_chat_message('Tu','Altra conversazione');second=host._chat_id
        app=JarvisApp(host);app.root.geometry('850x620');errors=[]
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def guard(fn):
            try:fn()
            except Exception:errors.append(traceback.format_exc());app.close()
        def start():
            assert not app.sidebar.winfo_ismapped()
            app.archive_button.invoke()
            app.root.update_idletasks()
            assert app.sidebar.winfo_ismapped()
            i=app.chat_ids.index(first);app.chat_list.selection_clear(0,'end');app.chat_list.selection_set(i);app._select_chat()
            app.pin_chat_button.invoke()
            assert app.chat_ids[0]==first and host.conversations()[0]['pinned']
            app.chat_search_entry.insert(0,'bicicletta_42')
            app.root.after(350,lambda:guard(search))
        def search():
            assert app.chat_ids==[first]
            app.chat_search_entry.delete(0,'end');app.root.after(350,lambda:guard(remove))
        def remove():
            assert app.chat_ids[0]==first
            app.delete_chat_button.invoke()
            assert first not in app.chat_ids and host._chat_id is None
            app.root.update_idletasks();assert app.undo_chat_button.winfo_ismapped()
            app.undo_chat_button.invoke();assert first in app.chat_ids
            assert host.conversations()[0]['pinned']
            for button in (app.pin_chat_button,app.delete_chat_button,app.toolbar_nav['Servizi']):
                assert button.winfo_ismapped()
                assert button.winfo_rooty()+button.winfo_height()<=app.root.winfo_rooty()+app.root.winfo_height()
            app.root.update_idletasks()
            output=Path(os.environ.get('JARVIS_TEST_ARTIFACTS', ROOT/'artifacts'))/'jarvis-chat-actions.png'
            subprocess.run(['import','-window',str(app.root.winfo_id()),str(output)],check=True,capture_output=True,timeout=10)
            app.close()
        app.root.after(300,lambda:guard(start));app.run();host._shutdown()
        if errors:raise AssertionError(errors)
        print('PASS: pin, ricerca contenuti, eliminazione, annulla e layout 850x620 nella vera UI.')
