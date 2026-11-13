"""Exercise actual Tk key/button bindings, authentication and the main event loop."""
import os
from pathlib import Path
import sys
import tempfile
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory() as directory:
    with patch.dict(os.environ, {'XDG_CONFIG_HOME': directory + '/config', 'XDG_DATA_HOME': directory + '/data'}):
        jarvis = Jarvis(settings=Settings(Path(directory) / 'config/jarvis'))
        jarvis.registry.init_admin('Test admin', 'TEST234')
        jarvis.role = 'locked'
        app = JarvisApp(jarvis)
        app.root.geometry('850x620')
        failures, steps = [], []
        app.root.report_callback_exception = lambda *args: failures.append(str(args[1]))
        output = Path(os.environ.get('JARVIS_TEST_ARTIFACTS', Path(__file__).resolve().parents[1] / 'artifacts'))
        output.mkdir(exist_ok=True)

        def capture(filename):
            result = subprocess.run(['import', '-window', str(app.root.winfo_id()), str(output / filename)],
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr

        def click(widget):
            widget.event_generate('<Enter>', x=8, y=8)
            widget.event_generate('<ButtonPress-1>', x=8, y=8)
            widget.event_generate('<ButtonRelease-1>', x=8, y=8)

        def type_text(widget, text):
            widget.focus_force()
            for character in text:
                widget.event_generate('<KeyPress>', keysym=character)
                widget.event_generate('<KeyRelease>', keysym=character)

        def check(name, fn, then, delay=220):
            def invoke():
                try:
                    fn()
                    steps.append(name)
                    print('PASS:', name, flush=True)
                    app.root.after(delay, then)
                except Exception as exc:
                    failures.append(name + ': ' + repr(exc))
                    app.close()
            return invoke

        def login_page():
            assert app.unlock_button.winfo_rooty() + app.unlock_button.winfo_height() < app.root.winfo_rooty() + app.root.winfo_height(), 'Login controls clipped in small window'
            assert app.page == 'Accesso'
            assert app.root.grab_current() is None, 'A modal grab disables the main window'
            assert app.pin_entry.winfo_ismapped()
            capture('jarvis-login.png')
            type_text(app.pin_entry, 'wrong')
            assert app.pin_value.get() == 'wrong', 'Real key bindings did not insert text'
            click(app.unlock_button)

        def wrong_pin():
            assert 'errato' in app.login_error.get()
            assert app.root.grab_current() is None
            type_text(app.pin_entry, 'TEST234')
            app.pin_entry.event_generate('<Return>')

        def send_by_button():
            assert app.page == 'Conversazione'
            assert jarvis.role == 'admin'
            assert app.entry.winfo_ismapped()
            type_text(app.entry, 'token')
            assert app.entry.get() == 'token'
            click(app.send_button)

        pages = iter(('Servizi', 'Profili', 'Voce', 'Skill', 'MCP', 'Comandi', 'Manutenzione', 'Conversazione'))
        def navigate():
            try:
                page = next(pages)
            except StopIteration:
                test_return()
                return
            if page == 'Servizi':
                click(app.toolbar_nav['Servizi'])
            else:
                click(app.toolbar_nav['Conversazione'] if page == 'Conversazione' else app.nav[page])
            assert app.page == page, 'Navigation click failed: ' + page
            assert app.root.grab_current() is None
            print('PASS: navigation click —', page, flush=True)
            app.root.after(180, lambda: guarded(navigate))

        def test_return():
            assert 'Token stimati' in app.transcript.get('1.0', 'end')
            type_text(app.entry, 'aiuto')
            app.entry.event_generate('<Return>')
            app.root.after(250, lambda: guarded(test_busy_navigation))

        def test_busy_navigation():
            import time
            original = jarvis.process
            def slow(text,**kwargs):
                time.sleep(0.6)
                return original(text,**kwargs)
            jarvis.process = slow
            type_text(app.entry, 'token')
            click(app.send_button)
            click(app.toolbar_nav['Servizi'])
            assert app.page == 'Servizi', 'UI blocked during a request'
            click(app.toolbar_nav['Conversazione'])
            jarvis._cfg['tts'] = True
            def failed_tts(_):
                raise RuntimeError('TEST: voce non disponibile')
            jarvis._tts_say = failed_tts
            app.root.geometry('1240x820')
            app.root.after(1100, lambda: guarded(finish))

        def finish():
            assert 'Comandi disponibili' in app.transcript.get('1.0', 'end')
            assert 'TEST: voce non disponibile' in app.transcript.get('1.0', 'end')
            assert app.root.grab_current() is None, 'Background error created a modal window'
            assert not failures, failures
            jarvis._cfg['tts'] = False
            app.transcript.configure(state='normal')
            app.transcript.delete('1.0', 'end')
            app.transcript.configure(state='disabled')
            app.append('JARVIS', 'In attesa delle sue istruzioni. Scriva qui sotto oppure scelga uno strumento dal menu.')
            type_text(app.entry, 'token')
            click(app.send_button)
            app.root.after(800, lambda: guarded(preview))

        def preview():
            app.status.set('Interfaccia pronta')
            capture('jarvis-desktop.png')
            app.root.geometry('850x620')
            app.root.after(220, lambda: guarded(small_window))

        def small_window():
            assert app.entry.winfo_ismapped()
            assert app.entry.winfo_rooty() + app.entry.winfo_height() <= app.root.winfo_rooty() + app.root.winfo_height()
            assert app.send_button.winfo_ismapped(), 'Send disappeared in the small composer'
            assert app.send_button.winfo_rootx()+app.send_button.winfo_width() <= app.root.winfo_rootx()+app.root.winfo_width()
            app.entry.focus_force()
            app.entry.event_generate('<KeyPress>',keysym='Return',state=1)
            assert '\n' in app.entry.get(), 'Shift+Return did not insert a newline'
            app.entry.delete(0,'end')
            capture('jarvis-desktop-small.png')
            print('PASS: main loop, login, typing, clicks, Return, background errors, busy navigation, small window', flush=True)
            app.close()

        def guarded(function):
            try:
                function()
            except Exception as exc:
                failures.append(repr(exc))
                app.close()

        app.root.after(250, check('nonmodal login and real keyboard input', login_page,
            check('wrong PIN and Return to authenticate', wrong_pin,
                check('send using button binding', send_by_button, lambda: guarded(navigate)))))
        app.root.after(15000, lambda: (failures.append('GUI test timeout'), app.close()) if not app.closed else None)
        app.run()
        jarvis._shutdown()
        if failures:
            raise AssertionError(failures)
        print('GUI interaction test passed.')
