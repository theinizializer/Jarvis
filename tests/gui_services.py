"""Exercise service settings with synthetic credentials and no external transport."""
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import Mock,patch
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)

with tempfile.TemporaryDirectory() as directory:
    home=Path(directory)
    with patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}),patch('app_config.ROOT',home):
        settings=Settings(home/'cfg')
        settings.secrets={'DISCORD_TOKEN':'synthetic-bot-token'}
        host=Jarvis(settings=settings)
        host.registry.init_admin('Admin','ADMIN77');host.authenticate('ADMIN77')
        runtime=host.discord_service()
        runtime.start=Mock(return_value='Connessione simulata')
        runtime.stop=Mock(return_value='Arresto simulato')
        host.email_action=Mock(return_value='Gmail verificato in sola lettura')
        app=JarvisApp(host)
        app.root.geometry('1100x880')
        errors=[]
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def guard(function):
            try:function()
            except Exception:errors.append(traceback.format_exc());app.close()
        def button(text):
            return next(w for w in children(app.content) if isinstance(w,ttk.Button) and w.cget('text')==text)
        def start():
            app.show('Discord')
            entries=[w for w in children(app.content) if isinstance(w,ttk.Entry)]
            assert entries[0].cget('show')=='●'
            entries[1].insert(0,'bad-channel')
            button('Avvia bot').invoke()
            assert 'solo cifre' in app._discord_status.get()
            runtime.start.assert_not_called()
            entries[1].delete(0,'end')
            button('Avvia bot').invoke()
            runtime.start.assert_called_once()
            assert settings.secret('DISCORD_TOKEN')=='synthetic-bot-token'
            runtime._state('error','Errore di connessione simulato')
            app.root.after(200,lambda:guard(status))
        def status():
            assert app._discord_status.get()=='Errore di connessione simulato'
            button('Ferma bot').invoke();runtime.stop.assert_called_once()
            app.show('Email');button('Verifica Gmail').invoke()
            app.root.after(200,lambda:guard(finish))
        def finish():
            host.email_action.assert_called_once_with('email_status')
            assert app.icon.width()==128
            app.close()
        app.root.after(300,lambda:guard(start))
        app.root.after(8000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('UI timeout'))))
        app.run()
        assert not errors,'\n'.join(errors)
        print('Discord settings, masked token, ID validation, visible errors, Gmail verification and new icon: OK')
