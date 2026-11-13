"""Private, non-blocking UI channel to the transparent core process."""
import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path


class SatelliteClient:
    def __init__(self):
        base_python=Path(sys.base_prefix)/'bin/python3'
        python=sys.executable if importlib.util.find_spec('PySide6') else (str(base_python) if base_python.exists() else shutil.which('python3'))
        if not python:raise RuntimeError('Qt non disponibile per il nucleo trasparente.')
        environment=os.environ.copy()
        environment['QT_QPA_PLATFORM']='wayland' if environment.get('XDG_SESSION_TYPE')=='wayland' else 'xcb'
        self.process=subprocess.Popen([python,str(Path(__file__).with_name('satellite_core.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1,env=environment)
        self.events=queue.Queue()
        self.visible=False
        self._last=None
        threading.Thread(target=self._read,daemon=True,name='satellite-events').start()

    @property
    def pid(self):return self.process.pid

    def _read(self):
        for line in self.process.stdout:
            try:self.events.put(json.loads(line))
            except ValueError:pass

    def update(self, **state):
        if self.process.poll() is not None:
            raise RuntimeError('Il processo del nucleo trasparente si è chiuso. Installa requirements-desktop.txt.')
        if state==self._last:return
        self._last=state
        self.visible=state.get('visible',False)
        self.process.stdin.write(json.dumps(state)+'\n')
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"quit":true}\n');self.process.stdin.flush()
                self.process.stdin.close()
                self.process.wait(timeout=1)
            except (OSError,subprocess.TimeoutExpired):self.process.kill()
