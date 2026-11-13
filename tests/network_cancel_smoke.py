"""Loopback-only proof: cancel blocked sockets and process groups, then continue."""
import json
import os
from pathlib import Path
import select
import shlex
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from jarvis_v10 import request_json
from task_io import TaskCancelled,run_command


class Server(ThreadingHTTPServer):
    daemon_threads=True


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        self.server.started.set()
        if self.path=='/ok':
            self.send_response(200);self.end_headers();self.wfile.write(b'{"ok":true}');return
        if self.path in {'/body','/error'}:
            self.send_response(429 if self.path=='/error' else 200)
            self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{');self.wfile.flush()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if select.select([self.connection],[],[],.03)[0]:
                try:
                    if not self.connection.recv(1,socket.MSG_PEEK):
                        self.server.disconnected.set();return
                except OSError:self.server.disconnected.set();return


server=Server(('127.0.0.1',0),Handler);server.started=threading.Event();server.disconnected=threading.Event()
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
try:
    for path in ('/headers','/body','/error'):
        cancel=threading.Event();errors=[]
        server.started.clear();server.disconnected.clear()
        def blocked():
            try:request_json(base+path,{'synthetic':True},timeout=300,cancel_event=cancel)
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=blocked);worker.start()
        assert server.started.wait(2),'Server request did not start'
        started=time.monotonic();cancel.set();worker.join(1)
        elapsed=time.monotonic()-started
        assert not worker.is_alive(),'Cancellation waited for the HTTP timeout'
        assert len(errors)==1 and isinstance(errors[0],TaskCancelled),errors
        assert server.disconnected.wait(1),'Connection was left open after cancellation'
        assert request_json(base+'/ok',{},cancel_event=threading.Event())=={'ok':True}
        print(f'PASS: {path} cancelled and disconnected in {elapsed:.3f}s; next request OK',flush=True)
finally:server.shutdown();server.server_close()

with tempfile.TemporaryDirectory(prefix='jarvis-cancel-process-') as tmp:
    root=Path(tmp);ready=root/'ready';late=root/'must-not-exist'
    code=f"from pathlib import Path;import time;Path({str(ready)!r}).write_text('ready');time.sleep(1);Path({str(late)!r}).write_text('unexpected')"
    command=shlex.quote(sys.executable)+' -c '+shlex.quote(code)+' & wait'
    cancel=threading.Event();errors=[]
    def run():
        try:run_command(command,cancel_event=cancel,shell=True,executable='/bin/bash',text=True)
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=run);worker.start()
    deadline=time.monotonic()+2
    while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert ready.exists()
    started=time.monotonic();cancel.set();worker.join(1)
    assert not worker.is_alive() and isinstance(errors[0],TaskCancelled),errors
    elapsed=time.monotonic()-started;time.sleep(1.1)
    assert not late.exists(),'Child process survived stop and performed a later action'
    print(f'PASS: shell and child stopped in {elapsed:.3f}s; no later file write',flush=True)
