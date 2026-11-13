"""Real browser coordinates and independent input on a local deterministic page."""
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api_key_pool import Credential
from app_config import Settings
from jarvis_v10 import Jarvis

HTML = b'''<!doctype html><html><body style="margin:0;background:#101720;color:white">
<button style="position:absolute;left:160px;top:120px;width:120px;height:48px"
onclick="document.getElementById('result').textContent='clicked'">Test</button>
<input style="position:absolute;left:160px;top:220px;width:120px;height:48px">
<p id="result" style="position:absolute;left:160px;top:300px"></p></body></html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(HTML)
    def log_message(self, *_):
        pass

server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_DATA_HOME': tmp}):
        settings = Settings(Path(tmp)/'cfg')
        settings.values['vision_headless'] = True
        host = Jarvis(settings=settings)
        runtime = host.vision()
        try:
            runtime.open(f'http://127.0.0.1:{server.server_port}')
            def located(x,y):
                answer={'found':True,'x':x,'y':y,'bbox':[160,y-20,280,y+20],'confidence':.99,'description':'test control'}
                with patch('api_key_pool.credentials', return_value=[Credential('test','test','not-a-real-key')]), patch(
                    'jarvis_v10.request_json', return_value={'choices':[{'message':{'content':json.dumps(answer)}}]}):
                    return runtime.locate('fixture control')
            observed = located(220,144)
            runtime.act(observed['source_id'],'click')
            assert runtime.page.locator('#result').inner_text() == 'clicked'
            try:
                runtime.act(observed['source_id'],'click')
                raise AssertionError('A reused observation was accepted')
            except ValueError:
                pass
            observed = located(220,244)
            runtime.act(observed['source_id'],'type',text='JARVIS independent keyboard')
            assert runtime.page.locator('input').input_value() == 'JARVIS independent keyboard'
            observed = located(220,144)
            runtime.page.locator('#result').evaluate("el => el.textContent = 'changed after capture'")
            try:
                runtime.act(observed['source_id'],'click')
                raise AssertionError('A changed page was accepted')
            except ValueError:
                pass
            print('Browser reale: click, tastiera indipendente, rettangoli, osservazioni monouso e pagina modificata OK')
        finally:
            host._shutdown()
finally:
    server.shutdown()
    server.server_close()
