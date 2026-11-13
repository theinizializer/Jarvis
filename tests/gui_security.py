"""Persist, disable and re-enable configurable idle timers through the real UI."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
from unittest.mock import patch
from tkinter import ttk
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)

with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
    host=Jarvis(settings=Settings(Path(directory)/'cfg'))
    host.registry.init_admin('Owner','OWNER77');host.authenticate('OWNER77')
    app=JarvisApp(host);app.root.geometry('850x620')
    errors=[]
    app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    def guard(function):
        try:function()
        except Exception:errors.append(traceback.format_exc());app.close()
    def widgets(kind):return [w for w in children(app.content) if isinstance(w,kind)]
    def save():
        next(w for w in widgets(ttk.Button) if w.cget('text')=='Salva sicurezza').invoke()
    def enter(widget,value):
        widget.delete(0,'end');widget.insert(0,str(value))
    def start():
        app.show('Sicurezza')
        assert app.nav['Sicurezza'].instate(['selected'])
        minutes=widgets(ttk.Spinbox)
        enter(minutes[0],0);save()
        assert app.busy==0 and host._cfg['session_lock_minutes']==2
        assert any('1440' in str(w.cget('text')) for w in widgets(ttk.Label))
        enter(minutes[0],15);enter(minutes[1],5)
        widgets(ttk.Checkbutton)[0].invoke()
        assert str(minutes[0].cget('state'))=='disabled'
        assert str(minutes[1].cget('state'))=='normal'
        app._settings_canvas.yview_moveto(1)
        save();app.root.after(200,lambda:guard(disabled))
    def disabled():
        if app.busy:app.root.after(50,lambda:guard(disabled));return
        cfg=Settings(host.settings.directory).values
        assert cfg['session_lock_enabled'] is False and cfg['session_lock_minutes']==15
        assert cfg['voice_standby_enabled'] is True and cfg['voice_standby_minutes']==5
        app._last_user_input=time.monotonic()-100000
        app._check_session_idle(time.monotonic());assert host.role=='admin'
        app.show('Conversazione');app.show('Sicurezza')
        minutes=widgets(ttk.Spinbox)
        assert str(minutes[0].cget('state'))=='disabled' and minutes[0].get()=='15'
        widgets(ttk.Checkbutton)[0].invoke();enter(minutes[0],3)
        widgets(ttk.Checkbutton)[1].invoke()
        save();app.root.after(200,lambda:guard(enabled))
    def enabled():
        if app.busy:app.root.after(50,lambda:guard(enabled));return
        cfg=Settings(host.settings.directory).values
        assert cfg['session_lock_enabled'] is True and cfg['session_lock_minutes']==3
        assert cfg['voice_standby_enabled'] is False
        app.root.geometry('1080x860')
        app._settings_canvas.yview_moveto(0)
        app.root.after(200,lambda:guard(finish))
    def finish():
        target=Path(__file__).resolve().parents[1]/'artifacts/jarvis-sicurezza.png'
        target.parent.mkdir(exist_ok=True)
        app.root.update_idletasks()
        subprocess.run(['import','-window',str(app.root.winfo_id()),str(target)],check=True,timeout=10)
        now=time.monotonic();app._last_user_input=now
        app._check_session_idle(now+179.9);assert host.role=='admin'
        app._check_session_idle(now+180);assert host.role=='locked'
        app.close()
    app.root.after(300,lambda:guard(start))
    app.root.after(10000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('UI timeout'))))
    app.run()
    assert not errors,'\n'.join(errors)
    print('Security page: validation, independent timers, persistence, disabled server lock, re-enabled 3-minute lock: OK')
