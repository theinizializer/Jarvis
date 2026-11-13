"""Cancellable task I/O. Abandoned responses never run callbacks or tools."""
import http.client
import io
import json
import os
import queue
import signal
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPHandler, HTTPSHandler, build_opener


class TaskCancelled(Exception):
    pass


def check_cancelled(event):
    if event.is_set():raise TaskCancelled('Compito interrotto su richiesta.')


class Exchange:
    def __init__(self,event,timeout):
        self.event,self.timeout=event,timeout
        self.sockets=[];self.lock=threading.Lock()

    def attach(self,sock):
        with self.lock:
            self.sockets.append(sock)
            if self.event.is_set():
                self._shutdown(sock)
                raise TaskCancelled('Compito interrotto su richiesta.')
            sock.settimeout(self.timeout)

    @staticmethod
    def _shutdown(sock):
        try:sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass

    def abort(self):
        with self.lock:
            for sock in self.sockets:self._shutdown(sock)


def cancellable_json(request,timeout,event):
    check_cancelled(event)
    exchange=Exchange(event,timeout);results=queue.Queue(maxsize=1)

    class HTTP(http.client.HTTPConnection):
        def connect(self):
            super().connect();exchange.attach(self.sock)

    class HTTPS(http.client.HTTPSConnection):
        def connect(self):
            super().connect();exchange.attach(self.sock)

    class Plain(HTTPHandler):
        def http_open(self,req):return self.do_open(HTTP,req)

    class Secure(HTTPSHandler):
        def https_open(self,req):return self.do_open(HTTPS,req,context=self._context)

    def work():
        try:
            check_cancelled(event)
            try:
                with build_opener(Plain(),Secure()).open(request,timeout=min(timeout,10)) as response:
                    value=json.load(response)
            except HTTPError as error:
                try:body=error.read(16384)
                finally:error.close()
                raise HTTPError(error.url,error.code,error.msg,error.headers,io.BytesIO(body)) from None
            results.put((value,None))
        except Exception as error:results.put((None,error))

    threading.Thread(target=work,daemon=True,name='jarvis-http').start()
    while True:
        if event.wait(.04):
            exchange.abort()
            raise TaskCancelled('Compito interrotto su richiesta.')
        try:value,error=results.get_nowait()
        except queue.Empty:continue
        check_cancelled(event)
        if error:raise error
        return value


def run_command(command,*,cancel_event,timeout=30,**kwargs):
    check_cancelled(cancel_event)
    process=subprocess.Popen(command,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
    deadline=time.monotonic()+timeout
    def stop_tree():
        for sig in (signal.SIGTERM,signal.SIGKILL):
            try:os.killpg(process.pid,sig)
            except ProcessLookupError:pass
            if sig==signal.SIGTERM:
                try:process.communicate(timeout=.15)
                except subprocess.TimeoutExpired:pass
        try:process.communicate(timeout=.5)
        except subprocess.TimeoutExpired:
            process.stdout.close();process.stderr.close()
            process.wait(timeout=.5)
    try:
        while True:
            check_cancelled(cancel_event)
            if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(command,timeout)
            try:
                out,err=process.communicate(timeout=min(.1,max(.001,deadline-time.monotonic())))
                check_cancelled(cancel_event)
                return subprocess.CompletedProcess(command,process.returncode,out,err)
            except subprocess.TimeoutExpired:continue
    except BaseException:
        stop_tree();raise
