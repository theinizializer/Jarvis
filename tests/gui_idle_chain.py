"""The whole queue and asynchronous speech must finish before inactivity starts."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
    host=Jarvis(settings=Settings(Path(directory)/'cfg'))
    host.registry.init_admin('Owner','OWNER77');host.authenticate('OWNER77')
    app=JarvisApp(host)
    errors=[];first=threading.Event();second=threading.Event()
    app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    engine=SimpleNamespace(is_busy=True,is_speaking=False,playback_level=0,
                           stop=lambda:None,shutdown=lambda:None)
    def guard(function):
        try:function()
        except Exception:
            errors.append(traceback.format_exc());first.set();second.set();app.close()
    def work(event,result):
        assert event.wait(3),'Test worker timed out'
        return result
    def start():
        app.submit(lambda:work(first,'Primo risultato'),lambda value:app.append('JARVIS',value))
        app.submit(lambda:work(second,'Secondo risultato'),lambda value:app.append('JARVIS',value))
        app._last_user_input=time.monotonic()-600
        app._check_session_idle(time.monotonic())
        assert app._pending_work==2 and host.role=='admin'
        first.set();app.root.after(150,lambda:guard(first_done))
    def first_done():
        if app._pending_work==2:app.root.after(50,lambda:guard(first_done));return
        assert app._pending_work==1
        app._check_session_idle(time.monotonic())
        assert host.role=='admin' and app._idle_work_active
        host._tts_engine=engine
        second.set();app.root.after(150,lambda:guard(queue_done))
    def queue_done():
        if app._pending_work:app.root.after(50,lambda:guard(queue_done));return
        assert [item['text'] for item in host._transcript]==['Primo risultato','Secondo risultato']
        app._check_session_idle(time.monotonic())
        assert host.role=='admin' and app._idle_work_active,'Speech was still active'
        assert time.monotonic()-app._last_user_input>120,'Countdown started before speech ended'
        engine.is_busy=False
        now=time.monotonic();app._check_session_idle(now)
        assert app._last_user_input==now and not app._idle_work_active
        app._check_session_idle(now+119.9)
        assert host.role=='admin'
        app._check_session_idle(now+120)
        assert host.role=='locked'
        app.close()
    app.root.after(300,lambda:guard(start))
    app.root.after(8000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('UI timeout'))))
    app.run()
    assert not errors,'\n'.join(errors)
    print('Two queued jobs delivered, asynchronous speech drained, then full 120-second countdown: OK')
