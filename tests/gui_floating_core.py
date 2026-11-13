"""Exercise the detachable text composer, voice controls and fullscreen suppression."""
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_app import JarvisApp
from jarvis_v10 import Jarvis
from jarvis_hud import HudCore, OrbitalCore

with tempfile.TemporaryDirectory(prefix='jarvis-floating-') as directory:
    home = Path(directory)
    visibility = Mock(fullscreen=False)
    with patch.dict(os.environ, {'XDG_DATA_HOME':str(home/'data')}), patch('pathlib.Path.home', return_value=home), \
            patch('floating_ui.DesktopVisibility', return_value=visibility):
        host = Jarvis(settings=Settings(home/'config'))
        host.registry.init_admin('Admin', 'ADMIN77')
        host.authenticate('ADMIN77')
        host._chat_completion = lambda *_: {'content':'La chat rapida funziona.'}
        host._tts_say = lambda _:None
        app = JarvisApp(host)
        errors = []
        app.root.report_callback_exception = lambda *args: errors.append(str(args[1]))
        focused = patch.object(app.root, 'focus_displayof', return_value=None)
        focus_mock = focused.start()
        output = Path('/tmp/jarvis-floating-preview')
        output.mkdir(exist_ok=True)
        def guard(function):
            try:
                function()
            except Exception:
                errors.append(traceback.format_exc())
                app.close()
        def start():
            app._sync_floating_core()
            assert isinstance(app._floating_core._core, HudCore)
            app._toggle_floating_chat()
            focus_mock.return_value = app._floating_entry
            app._floating_entry.insert('1.0', 'Ciao, rispondi qui: perché funziona?')
            app._send_floating_message()
            app.root.after(200, lambda: guard(answered))
        def answered():
            if app.busy:
                app.root.after(80, lambda: guard(answered))
                return
            app._update_floating_chat()
            assert 'La chat rapida funziona.' in app._floating_transcript.get('1.0', 'end')
            assert app._floating_core.winfo_viewable()
            assert app._floating_entry.get('1.0', 'end').strip() == ''
            subprocess.run(['import','-window',str(app._floating_core.winfo_id()), str(output/'chat-rapida.png')], check=True, timeout=10)
            app._floating_entry.insert('1.0','Bozza da conservare')
            focus_mock.return_value = None
            app._sync_floating_core()
            assert not app._floating_core._expanded, 'Outside click must collapse the quick composer'
            app._toggle_floating_chat()
            focus_mock.return_value = app._floating_entry
            assert app._floating_entry.get('1.0','end').strip() == 'Bozza da conservare'
            visibility.fullscreen = True
            app._sync_floating_core()
            assert app._floating_core.state() == 'withdrawn'
            visibility.fullscreen = None
            app._sync_floating_core()
            assert app._floating_core.state() == 'withdrawn'
            visibility.fullscreen = False
            focus_mock.return_value = None
            app._sync_floating_core()
            app._voice_session = Mock()
            app._voice_session.listen.return_value = ''
            app._enter_voice()
            app._sync_floating_core()
            assert isinstance(app._floating_core._core, OrbitalCore)
            engine = host._tts_engine = Mock(is_busy=True, is_speaking=True, playback_level=.5)
            app._floating_stop.invoke()
            engine.stop.assert_called_once()
            host._tts_engine = None
            with patch.object(app, 'send') as send:
                app._handle_voice_text('dormi')
                assert host.voice_sleeping and app._hud_state(0)=='sleeping'
                app._handle_voice_text('apri firefox')
                send.assert_not_called()
                app._handle_voice_text('hey arvis, buongiorno')
                assert not host.voice_sleeping
                send.assert_called_once_with('buongiorno')
            app.root.after(150, lambda: guard(finish))
        def finish():
            app._leave_voice()
            app._sync_floating_core()
            assert type(app._floating_core._core) is HudCore
            focused.stop()
            with patch.object(app.root, 'focus_displayof', return_value=app.entry):
                app._sync_floating_core()
            assert app._floating_core.state() == 'withdrawn'
            app.close()
        app.root.after(250, lambda: guard(start))
        app.root.after(15000, lambda: guard(lambda: (_ for _ in ()).throw(AssertionError('UI timeout'))))
        app.run()
        focused.stop()
        assert not errors, '\n'.join(errors)
        print('Quick composer, shared answer, text/voice cores, interruption and fullscreen suppression: OK')
