"""Only multiple verified microphones should open the native selection list."""
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp
from tkinter import ttk

MICS=[{'source':'sd','index':i,'name':f'Microfono {i}','physical_id':f'hardware:{i}'} for i in range(2)]
mode=[1]
def probe(microphones, event):
    return [{'microphone':mic,'quality':{'ok':index<mode[0]}} for index,mic in enumerate(microphones)]
class Monitor:
    def __init__(self,*_): pass
    def start(self): pass
    def stop(self): pass
    def wait(self): pass
    def snapshot(self): return {0:(-20,False,time.monotonic()),1:(-22,False,time.monotonic())}

with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'XDG_DATA_HOME':tmp}):
    fake=SimpleNamespace(scan_microphones=lambda:MICS, probe_microphones=probe, MicrophoneMonitor=Monitor)
    with patch.dict(sys.modules,{'voice_module':fake}):
        host=Jarvis(settings=Settings(Path(tmp)/'cfg'))
        app=JarvisApp(host)
        errors=[]
        chosen=[]
        app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        def guard(fn):
            try: fn()
            except Exception:
                errors.append(traceback.format_exc());app.close()
        def find(title):
            return next(w for w in app.root.winfo_children() if w.winfo_class()=='Toplevel' and w.title()==title)
        def descendants(w):
            for child in w.winfo_children():
                yield child
                yield from descendants(child)
        def click_test(next_step):
            window=find('Verifica microfono')
            next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='Avvia prova').invoke()
            app.root.after(250,lambda:guard(next_step))
        def start():
            app._microphones(on_chosen=lambda:chosen.append(True))
            app.root.after(200,lambda:guard(lambda:click_test(single)))
        def single():
            assert chosen and host._cfg['microphone']['index']==0
            assert not any(w.winfo_class()=='Toplevel' for w in app.root.winfo_children())
            mode[0]=2
            app._microphones()
            app.root.after(200,lambda:guard(lambda:click_test(multiple)))
        def multiple():
            window=find('Scegli microfono')
            assert sum(w.winfo_class()=='Radiobutton' for w in descendants(window))==2
            window.destroy()
            mode[0]=0
            app._microphones()
            app.root.after(200,lambda:guard(lambda:click_test(none)))
        def none():
            window=find('Verifica microfono')
            assert any(isinstance(w,ttk.Button) and w.cget('text')=='Riprova parlando' for w in descendants(window))
            assert host._cfg['microphone']['index']==0
            assert len(chosen)==1
            app.close()
        app.root.after(150,lambda:guard(start))
        app.root.after(8000,lambda:(errors.append('timeout'),app.close()) if not app.closed else None)
        app.run();host._shutdown()
        if errors: raise AssertionError(errors)
        print('Un microfono: scelta automatica. Più microfoni: lista. Nessuna voce: riprova, senza selezione arbitraria. OK')
