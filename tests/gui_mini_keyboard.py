"""Send real X keyboard events to the managed mini composer, rather than inserting text by API."""
import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jarvis_v10 import Jarvis
from app_config import Settings
from jarvis_app import JarvisApp

def type_keys(text):
    if os.environ.get('XDG_SESSION_TYPE')=='wayland' and shutil.which('wdotool'):
        for char in text:
            subprocess.run(['wdotool','--backend','kde','key',char],check=True,timeout=3,capture_output=True)
        return
    x11=ctypes.CDLL(ctypes.util.find_library('X11'))
    xtest=ctypes.CDLL(ctypes.util.find_library('Xtst'))
    x11.XOpenDisplay.argtypes=[ctypes.c_char_p];x11.XOpenDisplay.restype=ctypes.c_void_p
    x11.XStringToKeysym.argtypes=[ctypes.c_char_p];x11.XStringToKeysym.restype=ctypes.c_ulong
    x11.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];x11.XKeysymToKeycode.restype=ctypes.c_uint
    x11.XFlush.argtypes=[ctypes.c_void_p]
    x11.XCloseDisplay.argtypes=[ctypes.c_void_p]
    xtest.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
    display=x11.XOpenDisplay(None)
    if not display:raise RuntimeError('Display non disponibile')
    try:
        for char in text:
            code=x11.XKeysymToKeycode(display,x11.XStringToKeysym(char.encode()))
            xtest.XTestFakeKeyEvent(display,code,1,0);xtest.XTestFakeKeyEvent(display,code,0,0)
        x11.XFlush(display)
    finally:x11.XCloseDisplay(display)

with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'XDG_DATA_HOME':directory}):
    host=Jarvis(settings=Settings(Path(directory)/'cfg'))
    host.registry.init_admin('Admin','ADMIN77');host.authenticate('ADMIN77')
    host._chat_completion=lambda *_:{'content':'Ricevuto dalla mini chat.'}
    app=JarvisApp(host)
    errors=[]
    keys_at=[0]
    app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
    def guard(function):
        try:function()
        except Exception:errors.append(traceback.format_exc());app.close()
    def start():
        app._create_floating_core('text');app._toggle_floating_chat(True)
        app.root.after(600,lambda:guard(write))
    def write():
        assert app.root.focus_displayof() is app._floating_entry, str({'focus':str(app.root.focus_displayof()),'state':app._floating_core.state(),'expanded':app._floating_core._expanded,'fullscreen':app._desktop_visibility.fullscreen,'active_pid':app._desktop_visibility.active_pid,'pid':os.getpid(),'dock':app._desktop_visibility.dock_geometry,'error':app._desktop_visibility.error})
        assert app._desktop_visibility.active_pid==os.getpid(), 'The test window is not foreground in KDE'
        keys_at[0]=time.monotonic()
        type_keys('ciao')
        app.root.after(150,lambda:guard(check))
    def check():
        assert app._floating_entry.get('1.0','end').strip()=='ciao',app._floating_entry.get('1.0','end')
        assert app._last_user_input>=keys_at[0], 'Typing did not reset the inactivity clock'
        app._send_floating_message()
        app.root.after(150,lambda:guard(answered))
    def answered():
        if app.busy:app.root.after(100,lambda:guard(answered));return
        assert any(item['text']=='Ricevuto dalla mini chat.' for item in host._transcript)
        app.close()
    app.root.after(400,lambda:guard(start))
    app.root.after(12000,lambda:guard(lambda:(_ for _ in ()).throw(AssertionError('Keyboard timeout'))))
    app.run()
    assert not errors,'\n'.join(errors)
    print('Actual keyboard focus, real key events, mini composer input and shared answer: OK')
