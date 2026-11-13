"""Real curl + HTTP + tool payload. Local synthetic server only; no cloud/model inference."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis,PROVIDERS
from task_io import TaskCancelled
from web_module import WebModule

counts={};observed_tools=[];slow_started=threading.Event();slow_stop=threading.Event()
raw='inizio-'+'x'*12000+'-price: 42.35 EUR-fine'
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        counts[self.path]=counts.get(self.path,0)+1
        if self.path=='/redirect':
            self.send_response(302);self.send_header('Location','/json');self.end_headers();return
        if self.path=='/slow':
            self.send_response(200);self.send_header('Content-Type','text/plain');self.end_headers();slow_started.set()
            while not slow_stop.wait(.1):
                try:self.wfile.write(b'x');self.wfile.flush()
                except OSError:break
            return
        status=403 if self.path=='/blocked' else 200
        kind='application/octet-stream' if self.path=='/binary' else ('application/json' if self.path=='/json' else 'text/plain; charset=utf-8')
        content={'/json':json.dumps({'name':'CURL-LAB','price':42.35,'currency':'EUR'}),'/raw':raw,'/large':'x'*(2*1024*1024+100)+'-price:42.35 EUR','/unicode':'Caffè, qualità e € 42,35','/binary':'binary','/blocked':'Please complete the following challenge. Select all squares containing a duck.'}.get(self.path,'not found')
        data=content.encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        observed_tools.extend(t['function']['name'] for t in data.get('tools',[]))
        if data['messages'][-1]['role']=='tool':
            result=json.loads(data['messages'][-1]['content']);assert result['http_status']==200 and result['transport']=='curl'
            message={'content':'HTTP 200: la risposta contiene price 42.35.'}
        else:
            message={'tool_calls':[{'id':'curl-proof','type':'function','function':{'name':'curl_fetch','arguments':json.dumps({'url':base+'/json','find':'price'})}}]}
        body=json.dumps({'choices':[{'message':message}]}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*_):pass

server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}';web=WebModule();checks=[]
try:
    first=web.curl_fetch(base+'/raw',max_chars=256);second=web.curl_fetch(base+'/raw',offset=256,max_chars=256)
    assert first['next_offset']==256 and second['offset']==256 and counts['/raw']==1
    assert not first['cache_hit'] and second['cache_hit']
    assert first['http_status']==200 and first['curl_exit_code']==0;checks.append('real curl, HTTP metadata, pagination and cached body')
    match=web.curl_fetch(base+'/raw',find='price')
    assert match['match_count']==1 and match['matches'][0]['offset']==raw.index('price');checks.append('literal find beyond the first 12000 characters')
    fresh=web.curl_fetch(base+'/raw',find='price',refresh=True);assert not fresh['cache_hit'] and counts['/raw']==2;checks.append('explicit refresh bypasses pagination cache')
    large=web.curl_fetch(base+'/large',find='price');assert large['status']=='ok' and large['matches'][0]['offset']>2*1024*1024;checks.append('download and find beyond 2 MiB')
    assert web.curl_fetch(base+'/unicode')['body_excerpt']=='Caffè, qualità e € 42,35';checks.append('UTF-8')
    redirected=web.curl_fetch(base+'/redirect');assert redirected['effective_url']==base+'/json' and redirected['http_status']==200;checks.append('redirect effective URL')
    blocked=web.curl_fetch(base+'/blocked');assert blocked['status']=='blocked' and blocked['http_status']==403;checks.append('HTTP 403 challenge is not a successful extraction')
    binary=web.curl_fetch(base+'/binary');assert binary['status']=='error' and 'body_excerpt' not in binary;checks.append('non-text rejected')
    cancel=threading.Event();cancel_result=[]
    def slow():
        try:web.curl_fetch(base+'/slow',cancel_event=cancel)
        except TaskCancelled:cancel_result.append(True)
    worker=threading.Thread(target=slow);worker.start();assert slow_started.wait(3);start=time.monotonic();cancel.set();worker.join(2);slow_stop.set()
    assert not worker.is_alive() and cancel_result;checks.append('curl cancellation in '+str(round(time.monotonic()-start,3))+' s')
    with tempfile.TemporaryDirectory(prefix='jarvis-curl-tool-') as directory:
        home=Path(directory)
        with patch('app_config.ROOT',home),patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'data')}),patch.dict(PROVIDERS,{'groq':(base+'/chat','GROQ_API_KEY','openai/gpt-oss-120b')}):
            settings=Settings(home/'config');settings.secrets={'GROQ_API_KEY':'synthetic-only'};settings.values.update(provider='groq',model='openai/gpt-oss-120b',auto_fallback=False)
            host=Jarvis(settings=settings);host.role='admin'
            answer=host.process('Prova a leggere '+base+'/json con curl.')
            assert 'HTTP 200' in answer and 'curl_fetch' in observed_tools
            result=[m for m in host._history if m.get('name')=='curl_fetch'];assert len(result)==1
            checks.append('curl_fetch exposed in API tools and executed by the real agent runtime')
            host._shutdown()
    output=ROOT/'artifacts/curl-fetch-smoke.json';output.write_text(json.dumps({'checks':checks,'transport':'real curl','model':'simulated local API','cloud_api_calls':0},indent=2))
    print('PASS: '+'; '.join(checks))
finally:slow_stop.set();server.shutdown();server.server_close()
