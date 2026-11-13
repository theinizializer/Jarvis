"""Actual Stop button during a blocked model request, then another chat message."""
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis
from jarvis_app import JarvisApp


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.server.calls+=1;self.server.started.set()
        while not self.server.release.is_set():
            if select.select([self.connection],[],[],.03)[0]:
                try:
                    if not self.connection.recv(1,socket.MSG_PEEK):
                        self.server.disconnected.set();return
                except OSError:self.server.disconnected.set();return


server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
server.calls=0;server.started=threading.Event();server.disconnected=threading.Event();server.release=threading.Event()
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix='jarvis-stop-ui-') as tmp:
        home=Path(tmp)
        with patch('pathlib.Path.home',return_value=home),patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}):
            settings=Settings(home/'config');settings.secrets={}
            settings.values.update(provider='ollama',model='synthetic',ollama_host=f'http://127.0.0.1:{server.server_port}')
            host=Jarvis(settings=settings);host.role='admin';host._prepare_local_model=lambda *_:'synthetic'
            app=JarvisApp(host);errors=[];timing=[]
            app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))
            def guard(fn):
                try:fn()
                except Exception as exc:errors.append(repr(exc));app.close()
            def wait(predicate,then,count=100):
                if predicate():guard(then)
                elif count:app.root.after(40,lambda:wait(predicate,then,count-1))
                else:errors.append('Stop test timeout');app.close()
            def start():
                app.send('Prova interruzione del modello')
                wait(server.started.is_set,stop)
            def stop():
                assert host.agent_task_active and app.busy,'Model task did not start'
                assert str(app.stop_button.cget('state'))=='normal','Stop button is disabled during a request'
                timing.append(time.monotonic())
                app.stop_button.invoke()
                wait(lambda:not app.busy and not host.agent_task_active and server.disconnected.is_set(),stopped)
            def stopped():
                elapsed=time.monotonic()-timing[0]
                assert elapsed<1.5,elapsed
                assert server.disconnected.is_set(),'The model socket was not closed'
                assert 'Compito interrotto su richiesta' in app.transcript.get()
                assert not host.tool_approval_pending
                app.send('token')
                wait(lambda:not app.busy,continued)
                print(f'PASS: pulsante Interrompi → richiesta disconnessa e UI libera in {elapsed:.3f}s',flush=True)
            def continued():
                assert 'Token stimati' in app.transcript.get()
                assert server.calls==1,'Stop initiated another model or fallback request'
                assert str(app.send_button.cget('state'))=='normal'
                assert str(app.entry.cget('state'))=='normal'
                subprocess.run(['import','-window',str(app.root.winfo_id()),str(ROOT/'artifacts/jarvis-stop-confirmed.png')],check=True,timeout=10)
                app.close()
            app.root.after(200,lambda:guard(start));app.run();host._shutdown()
            assert not errors,errors
            print('PASS: nuova richiesta dopo Stop; nessun fallback o risultato tardivo applicato.')
finally:
    server.release.set();server.shutdown();server.server_close()
