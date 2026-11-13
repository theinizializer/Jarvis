"""KyGlove LAN client. Fixed destinations, no redirects, no credentials in tool results."""
import base64
import http.client
import json
import re
import secrets
import time
from urllib.parse import urlencode

from task_io import check_cancelled
from skills import kyglove_workshop as w


class Device:
    def __init__(self, address):
        if address not in {'kyglove.local', '192.168.4.1'}:
            raise ValueError('Usa kyglove.local oppure 192.168.4.1 sulla rete del guanto.')
        self.address = address
        config = (w.ROOT / w.SKETCH / 'GloveWiFiConfig.h').read_text()
        def setting(key):
            return re.search(r'\b' + key + r'\[\] = "([^"]+)";', config)[1]
        if 'KYGLOVE_LOCAL_PASSWORD' in config:
            local = (w.ROOT/w.SKETCH/'KyGloveLocal.h').read_text()
            password = re.search(r'#define KYGLOVE_LOCAL_PASSWORD "([^"]+)"',local)[1]
        else:
            password = setting('OTA_PASSWORD')
        credentials = setting('OTA_USER') + ':' + password
        self.auth = 'Basic ' + base64.b64encode(credentials.encode()).decode()

    def request(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.address, timeout=60 if path == '/update' else 5)
        try:
            conn.request('GET' if body is None else 'POST', path, body,
                         {'Authorization': self.auth, **(headers or {})})
            response = conn.getresponse()
            data = response.read(150000)
            if response.status != 200:
                raise ValueError(f'KyGlove HTTP {response.status} per {path}')
            return data.decode() if path == '/' else json.loads(data)
        finally:
            conn.close()

    def post(self, path, body=b'', headers=None):
        token = re.search(r"const token='([0-9a-f]{32})'", self.request('/'))
        if not token:
            raise ValueError('Token di sessione non disponibile.')
        return self.request(path, body, {'X-KyGlove-Token': token[1], **(headers or {})})

    def configure(self, changes, persist):
        current = self.request('/settings')
        form = {'t' + str(i): value for i, value in enumerate(current['tapG'])}
        for k in ('cooldown','window','filter','gain','deadzone','hysteresis'):
            form[k] = current[k]
        for k in ('invertX','invertY'):
            form[k] = int(current[k])
        if not changes or changes.keys() - form.keys():
            raise ValueError('Parametri sconosciuti o mancanti.')
        if any(type(v) not in {float,int,bool} for v in changes.values()):
            raise ValueError('Servono valori numerici.')
        form.update({k: int(v) if type(v) is bool else v for k,v in changes.items()})
        form['persist'] = int(persist)
        self.post('/settings', urlencode(form), {'Content-Type':'application/x-www-form-urlencoded'})
        return self.request('/settings')

    def change_image(self, action, revision, cancel):
        before = self.request('/status')
        if before['pending'] or before['protocol'] != 'KYGlove-OTA-v1:ESP32S3':
            raise ValueError('Versione in verifica o protocollo incompatibile.')
        if action == 'restore_base':
            rel = 'firmware_OTA/KYGlove_OTA5_GIROSCOPIO.bin'
            data = (w.ROOT / rel).read_bytes()
            if w.digest(data) != w.baseline()['sha256'][rel]:
                raise ValueError('Binario stabile alterato.')
            if before['version'] == w.BASE:
                return {'version':w.BASE,'confirmed':True}
            expected = w.BASE
        elif action == 'upload':
            proof, data = w.release(revision)
            parent = json.loads((w.directory(revision)/'modules.json').read_bytes())['parent']
            expected_parent = w.BASE if parent == w.BASE else 'jarvis-' + parent
            if before['version'] != expected_parent:
                raise ValueError('Versione di partenza o spazio OTA incompatibile.')
            expected = proof['version']
        if action in {'upload','restore_base'}:
            if len(data) > before['slotSize']:
                raise ValueError('Spazio OTA insufficiente.')
            boundary = secrets.token_hex(20)
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="firmware"; filename="app.bin"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
            self.post('/update', body, {'Content-Type':'multipart/form-data; boundary='+boundary,
                                        'X-KyGlove-Size':str(len(data))})
        else:
            if not before['canRollback']:
                raise ValueError('Slot precedente non disponibile.')
            self.post('/rollback')
            expected = None
        deadline = time.monotonic()+80
        while time.monotonic() < deadline:
            cancel.wait(2)
            check_cancelled(cancel)
            try:
                after = self.request('/status')
            except (OSError, http.client.HTTPException, ValueError):
                continue
            if after['version'] != before['version'] and not after['pending']:
                if expected and after['version'] != expected:
                    raise ValueError('Versione inattesa dopo il riavvio: '+after['version'])
                return {'version':after['version'], 'previous':before['version'], 'confirmed':True}
        raise ValueError('Riavvio non verificato. Leggi lo stato prima di qualsiasi nuovo invio.')
