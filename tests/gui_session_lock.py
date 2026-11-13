"""Exercise timeout, cancellation of late callbacks, same-account PIN and explicit logout."""
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp
from app_config import Settings

with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
    host=Jarvis(settings=Settings(Path(directory)/'cfg'))
    host.registry.init_admin('Owner','OWNER77');host.registry.create_secondary('Guest','GUEST77')
    host.authenticate('OWNER77');owner=host.session_user
    app=JarvisApp(host)
    errors=[];callbacks=[];release=threading.Event();stale_release=threading.Event()
    app.root.report_callback_exception=lambda *args:(errors.append(str(args[1])),print('UI error:',args[1],flush=True))
    def guard(function):
        try:function()
        except Exception:
            print(traceback.format_exc(),flush=True);errors.append(traceback.format_exc());release.set();stale_release.set();app.close()
    def start():
        app._create_floating_core('text')
        app._floating_entry.insert('1.0','Bozza privata di Owner')
        app.append('Tu','Questa conversazione appartiene a Owner')
        app.submit(lambda:release.wait(2),lambda result:callbacks.append(result))
        app._last_user_input=time.monotonic()-121
        app._check_session_idle(time.monotonic())
        assert host.role=='admin','Inactivity locked a running operation'
        release.set()
        app.root.after(200,lambda:guard(completed))
    def completed():
        if app.busy:app.root.after(50,lambda:guard(completed));return
        assert callbacks==[True],'The completed result was lost'
        assert time.monotonic()-app._last_user_input<1,'Completion did not restart the inactivity window'
        app._last_user_input=time.monotonic()-121
        app._check_session_idle(time.monotonic())
        assert host.role=='locked' and host._reauth_user==owner and app.page=='Accesso'
        with patch.object(app,'_satellite_state') as satellite, \
                patch.object(app,'_desktop_visibility',Mock(fullscreen=False,active_pid=-1)), \
                patch.object(app.root,'focus_displayof',return_value=None):
            app._sync_floating_core()
            satellite.assert_called_once_with(True)
        app.root.after(200,lambda:guard(resume))
    def resume():
        if app.busy:app.root.after(50,lambda:guard(resume));return
        assert callbacks==[True]
        app.pin_value.set('GUEST77');app._unlock()
        assert host.role=='locked' and host.session_user==owner
        app.pin_value.set('OWNER77');app._unlock()
        assert host.role=='admin' and host.session_user==owner
        assert host._transcript[-1]['text']=='Questa conversazione appartiene a Owner'
        app._create_floating_core('text')
        assert app._floating_entry.get('1.0','end').strip()=='Bozza privata di Owner'
        app.submit(lambda:stale_release.wait(2),lambda result:callbacks.append('stale'))
        app._logout_session()
        stale_release.set()
        assert app._floating_saved_draft is None
        app.root.after(200,lambda:guard(login_guest))
    def login_guest():
        if app.busy:app.root.after(50,lambda:guard(login_guest));return
        app.pin_value.set('GUEST77');app._unlock()
        assert host.role=='secondary' and host.session_user!=owner
        assert host._transcript==[]
        app.root.after(200,lambda:guard(finish))
    def finish():
        if app.busy:app.root.after(50,lambda:guard(finish));return
        assert callbacks==[True],'A callback from the logged-out session was executed'
        app.close()
    app.root.after(300,lambda:guard(start))
    app.root.after(10000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('Timeout'))))
    app.run()
    assert not errors,'\n'.join(errors)
    print('Long-running operation survives idle timeout, result delivered, lock restarts after completion, logout discards stale callbacks: OK')
