"""Exercise the native voice workspace with injected recognition, without recording audio."""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp
from jarvis_hud import OrbitalCore
import subprocess

with tempfile.TemporaryDirectory(prefix='jarvis-voice-ui-') as tmp:
    home = Path(tmp)
    with patch.dict(os.environ, {'XDG_DATA_HOME': str(home/'data')}), patch('pathlib.Path.home', return_value=home), patch('voice_ui.messagebox.showinfo') as notified:
        host = Jarvis(settings=Settings(home/'cfg'))
        host.registry.init_admin('Admin', 'ADMIN77')
        host.authenticate('ADMIN77')
        app = JarvisApp(host)
        app._voice_listen_next = lambda:None  # This fixture exercises UI and approvals; capture has separate tests.
        app.root.geometry('850x620')
        errors = []
        app.root.report_callback_exception = lambda *args:errors.append(str(args[1]))
        output = Path(os.environ.get('JARVIS_TEST_ARTIFACTS', '/tmp/jarvis-voice-preview'))
        output.mkdir(exist_ok=True, parents=True)
        host._tts_say = lambda _: None
        session = Mock()
        session.listen.return_value = ''
        app._voice_session = session
        def guard(fn):
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc())
                app.close()
        def capture(name):
            subprocess.run(['import', '-window', str(app.root.winfo_id()), str(output/name)],check=True,timeout=10)
        def start():
            app._enter_voice()
            app.root.after(350, lambda:guard(voice))
        def voice():
            assert host.execution_role == 'secondary'
            assert not app.entry.winfo_ismapped()
            assert app.voice_bar.winfo_ismapped()
            assert isinstance(app.hud, OrbitalCore)
            capture('jarvis-voice-mode.png')
            app._desktop_visibility.close()
            app._desktop_visibility = Mock(fullscreen=False)
            with patch.object(app.root, 'focus_displayof', return_value=None):
                app._sync_floating_core()
            assert app._floating_core.winfo_exists()
            host._chat_completion = lambda messages, _: {'tool_calls':[{'id':'voice_write','type':'function',
                'function':{'name':'write_text_file','arguments':json.dumps({'path':str(home/'output.txt'),'content':'OK'})}}]} if messages[-1]['role']=='user' else {'content':'File creato.'}
            app.send('Crea un file di prova.')
            app.root.after(150, lambda:guard(pending))
        def pending():
            if app.busy:
                app.root.after(100, lambda:guard(pending))
                return
            assert host.tool_approval_pending and app.approval_frame.winfo_ismapped(), str((host.tool_approval_pending,app.busy,app.status.get(),host._transcript[-2:]))
            with patch.object(app, '_ask_credential', return_value='ADMIN77'):
                app._decide_tool(True)
            app.root.after(200, lambda:guard(approved))
        def approved():
            if app.busy:
                app.root.after(100, lambda:guard(approved))
                return
            assert (home/'output.txt').read_text() == 'OK'
            assert host.execution_role == 'secondary'
            app._leave_voice()
            app.root.after(250, lambda:guard(text))
        def text():
            assert host.execution_role == 'admin'
            assert app.entry.winfo_ismapped()
            assert not app.voice_bar.winfo_ismapped()
            assert app._floating_core is None
            app.show('Visione')
            app.root.after(200, lambda:guard(finish))
        def finish():
            assert app.nav['Visione'].winfo_ismapped()
            capture('jarvis-vision-settings.png')
            session.lang = 'it'
            app._enroll_voice_dialog(session)
            app.root.after(150, lambda:guard(enrollment))
        def enrollment_window():
            return next(w for w in app.root.winfo_children()
                        if w.winfo_class() == 'Toplevel' and w.title() == 'Crea il tuo profilo vocale')
        def recording_button():
            return next(w for frame in enrollment_window().winfo_children() for w in frame.winfo_children()
                        if w.winfo_class() == 'TButton' and w.cget('text') == 'Registra')
        def enrollment():
            window = enrollment_window()
            assert recording_button().winfo_ismapped()
            subprocess.run(['import', '-window', str(window.winfo_id()), str(output/'jarvis-enrollment.png')], check=True, timeout=10)
            good = {'audio':'sample','quality':{'ok':True,'message':'Registrazione riuscita','transcript':'Questa è la mia voce'}}
            session.record_sample.side_effect = [
                {'audio':None,'quality':{'ok':False,'message':'Nessuna voce'}}, good, good]
            recording_button().invoke()
            app.root.after(200, lambda:guard(rejected))
        def rejected():
            assert not app.busy and not session.enroll.called
            assert not recording_button().instate(['disabled'])
            recording_button().invoke()
            app.root.after(200, lambda:guard(first_sample))
        def first_sample():
            assert not app.busy and not session.enroll.called
            recording_button().invoke()
            app.root.after(250, lambda:guard(published))
        def published():
            if app.busy:
                app.root.after(100, lambda:guard(published))
                return
            session.enroll.assert_called_once()
            assert notified.called and host.voice_mode
            app.close()
        app.root.after(250, lambda:guard(start))
        app.root.after(10000, lambda:(errors.append('timeout'),app.close()) if not app.closed else None)
        app.run()
        host._shutdown()
        if errors:
            raise AssertionError(errors)
        print('Voce: UI, permessi ridotti, nucleo fluttuante, ritorno alla scrittura e pagina Visione OK')
