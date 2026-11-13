"""Lazy MCP stdio client. No subprocess starts until a user connects."""
from __future__ import annotations
import json
import os
import queue
import subprocess
import threading
from collections import deque

class MCPClient:
    def __init__(self, spec, timeout=20):
        self.spec, self.timeout = spec, timeout
        self.process = None
        self._counter = 0
        self._responses = queue.Queue()
        self._lock = threading.RLock()
        self.stderr = deque(maxlen=30)
        self.tools = []

    def connect(self):
        with self._lock:
            if self.process and self.process.poll() is None:
                return self.tools
            command = self.spec.get('command')
            args = self.spec.get('args', [])
            env = self.spec.get('env', {})
            if not isinstance(command, str) or not command:
                raise ValueError('Indica un eseguibile MCP stdio')
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise ValueError('Gli argomenti MCP devono essere una lista di stringhe')
            if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                raise ValueError('Le variabili MCP devono essere stringhe')
            self._responses = queue.Queue()
            self.process = subprocess.Popen([command, *args], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                env={**os.environ, **env}, bufsize=1)
            process, responses = self.process, self._responses
            threading.Thread(target=self._read, args=(process, responses), daemon=True).start()
            threading.Thread(target=self._read_errors, args=(process,), daemon=True).start()
            try:
                initialized = self.request('initialize', {'protocolVersion': '2024-11-05',
                    'capabilities': {}, 'clientInfo': {'name': 'jarvis', 'version': '10.1'}})
                if initialized.get('protocolVersion') != '2024-11-05':
                    raise RuntimeError('Versione MCP non supportata dal server')
                self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                self.tools = []
                params = {}
                while True:
                    page = self.request('tools/list', params)
                    self.tools.extend(page.get('tools', []))
                    if not page.get('nextCursor'):
                        break
                    params = {'cursor': page['nextCursor']}
                return self.tools
            except Exception:
                self.close()
                raise

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def _read(self, process, responses):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                    if 'id' in message:
                        responses.put(message)
                except ValueError:
                    continue
        finally:
            responses.put({'_closed': True})

    def _read_errors(self, process):
        for line in process.stderr:
            self.stderr.append(line.rstrip())

    def request(self, method, params):
        import time
        with self._lock:
            self._counter += 1
            identifier = self._counter
            self._send({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close()
                    raise TimeoutError('Timeout MCP: ' + method)
                try:
                    reply = self._responses.get(timeout=remaining)
                except queue.Empty:
                    self.close()
                    raise TimeoutError('Timeout MCP: ' + method) from None
                if reply.get('_closed'):
                    raise RuntimeError('Il server MCP si è chiuso')
                if 'method' in reply:  # Server requests (sampling/roots) are not supported.
                    self._send({'jsonrpc': '2.0', 'id': reply['id'],
                                'error': {'code': -32601, 'message': 'Method not supported'}})
                    continue
                if reply.get('id') != identifier:
                    continue
                if 'error' in reply:
                    raise RuntimeError(str(reply['error']))
                return reply.get('result', {})

    def call(self, name, arguments):
        self.connect()
        if not any(t['name'] == name for t in self.tools):
            raise ValueError('Strumento MCP sconosciuto: ' + name)
        return self.request('tools/call', {'name': name, 'arguments': arguments})

    def close(self):
        with self._lock:
            if self.process:
                process, self.process = self.process, None
                if process.poll() is None:
                    try:
                        process.stdin.close()
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        stream.close()
            self.tools = []
