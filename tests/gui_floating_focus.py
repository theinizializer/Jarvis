"""Real keyboard-focus regression: clicking another process collapses the quick chat."""
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp

with tempfile.TemporaryDirectory(prefix='jarvis-focus-') as directory:
    home=Path(directory)
    with patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}), patch('pathlib.Path.home',return_value=home), \
            patch('floating_ui.DesktopVisibility',return_value=Mock(fullscreen=False)):
        host=Jarvis(settings=Settings(home/'cfg'))
        host.registry.init_admin('Admin','ADMIN77');host.authenticate('ADMIN77')
        app=JarvisApp(host)
        errors=[];external=[]
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def guard(function):
            try: function()
            except Exception:
                errors.append(traceback.format_exc());app.close()
        def start():
            app._create_floating_core('text')
            app._toggle_floating_chat(True)
            app._floating_entry.insert('1.0','Bozza conservata dopo il clic esterno')
            app.root.after(300,lambda:guard(leave))
        def leave():
            assert app.root.focus_displayof() is app._floating_entry, str({'focus':str(app.root.focus_displayof()),'window':app._floating_core.state(),'expanded':app._floating_core._expanded,'entry_mapped':app._floating_entry.winfo_ismapped()})
            external.append(subprocess.Popen([sys.executable,'-c',
                'import tkinter as tk; r=tk.Tk(); r.title("JARVIS · prova clic esterno"); '
                'r.geometry("430x220+30+30"); r.update(); r.focus_force(); r.after(3000,r.destroy); r.mainloop()']))
            app.root.after(900,lambda:guard(check))
        def check():
            assert not app._floating_core._expanded
            assert app._floating_core.winfo_width()<250
            assert app._floating_entry.get('1.0','end').strip()=='Bozza conservata dopo il clic esterno'
            app.close()
        app.root.after(250,lambda:guard(start))
        app.root.after(8000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('Focus timeout'))))
        app.run()
        for process in external:
            if process.poll() is None: process.terminate()
            process.wait(timeout=2)
        assert not errors,'\n'.join(errors)
        print('Real focus moved to another process: quick chat collapsed and draft preserved OK')
