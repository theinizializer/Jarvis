"""Native picker rendering and monitor cleanup, with controlled level readings."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp
from tkinter import ttk

MICROPHONES = [
    {'source':'sd','index':0,'name':'Microfono integrato'},
    {'source':'sd','index':1,'name':'Microfono USB'},
    {'source':'sd','index':2,'name':'Dispositivo scollegato'},
]

class Monitor:
    instances = []
    def __init__(self, microphones):
        self.stopped = False
        self.__class__.instances.append(self)
    def start(self):
        pass
    def snapshot(self):
        now = time.monotonic()
        return {0:(-88,False,now), 1:(-17,False,now), 2:None}
    def stop(self):
        self.stopped = True
    def wait(self):
        assert self.stopped

with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_DATA_HOME':tmp}):
    module = SimpleNamespace(scan_microphones=lambda:MICROPHONES, MicrophoneMonitor=Monitor)
    with patch.dict(sys.modules, {'voice_module':module}):
        host = Jarvis(settings=Settings(Path(tmp)/'cfg'))
        app = JarvisApp(host)
        errors = []
        app.root.report_callback_exception = lambda *args:errors.append(str(args[1]))
        def guard(fn):
            try:
                fn()
            except Exception:
                errors.append(traceback.format_exc())
                app.close()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def start():
            app._microphones(verified=MICROPHONES)
            app.root.after(250, lambda:guard(pick))
        def pick():
            window = next(w for w in app.root.winfo_children()
                          if w.winfo_class()=='Toplevel' and w.title()=='Scegli microfono')
            widgets = list(descendants(window))
            labels = [str(w.cget('text')) for w in widgets if w.winfo_class()=='Label']
            assert {'Quasi silenzioso','Segnale presente','Non disponibile'} <= set(labels)
            radios = [w for w in widgets if w.winfo_class()=='Radiobutton']
            radios[1].invoke()
            output = Path(os.environ.get('JARVIS_TEST_ARTIFACTS', '/tmp/jarvis-microphone-preview'))
            output.mkdir(exist_ok=True,parents=True)
            subprocess.run(['import','-window',str(window.winfo_id()),str(output/'jarvis-microphone-levels.png')],check=True,timeout=10)
            button = next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='Usa microfono')
            button.invoke()
            assert Monitor.instances[0].stopped
            app.root.after(250, lambda:guard(saved))
        def saved():
            assert host._cfg['microphone']['index']==1
            app._microphones(verified=MICROPHONES)
            app.root.after(250, lambda:guard(closed))
        def closed():
            window = next(w for w in app.root.winfo_children()
                          if w.winfo_class()=='Toplevel' and w.title()=='Scegli microfono')
            window.destroy()
            assert Monitor.instances[-1].stopped
            app.close()
        app.root.after(200, lambda:guard(start))
        app.root.after(8000, lambda:(errors.append('timeout'),app.close()) if not app.closed else None)
        app.run()
        host._shutdown()
        if errors:
            raise AssertionError(errors)
        print('Microfoni: livelli distinti, selezione, salvataggio e arresto del monitor alla chiusura OK')
