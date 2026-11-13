"""Live model + actual Tk/Guardian/tools; synthetic data, independent file checks.

Run manually: ./venv/bin/python tests/live_tool_lab.py
No model responses are mocked. Only the test workspace is authorized for file I/O.
"""
import copy
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app_config import Settings
from jarvis_v10 import Jarvis,PROVIDERS
from jarvis_app import JarvisApp
from agent_runtime import local_path

parser=argparse.ArgumentParser()
parser.add_argument('--cases',default='')
parser.add_argument('--provider',choices=['groq','ollama','nvidia'])
parser.add_argument('--same-chat',action='store_true')
parser.add_argument('--small',action='store_true')
parser.add_argument('--history-stress',action='store_true')
options=parser.parse_args()

saved=Settings()
lab=ROOT/'artifacts/tool-lab'/datetime.now().strftime('%Y%m%d-%H%M%S');lab.mkdir(parents=True)
token='LAB-'+uuid.uuid4().hex[:10].upper()
fixtures={
 '/html':('text/html',f'''<html><title>Scheda LAB</title><nav>Pubblicita 9999 EUR</nav><main><h1>LAB-SENSOR</h1><p class="price">Prezzo 37.25 EUR</p><script type="application/ld+json">{{"name":"LAB-SENSOR","offers":{{"price":37.25,"priceCurrency":"EUR"}},"sku":"{token}"}}</script></main><footer>cookie tracking</footer></html>'''),
 '/json':('application/json',json.dumps({'name':'LAB-SENSOR','price':37.25,'currency':'EUR','stock':7,'sku':token,'analytics':{'price':9999}})),
 '/script':('text/html','<html><div id="root"></div><script src="/state.js"></script></html>'),
 '/state.js':('application/javascript',f'window.__INITIAL_STATE__={{"name":"LAB-SENSOR","price":37.25,"currency":"EUR","stock":7,"sku":"{token}"}};'),
 '/blocked':('text/html','<html>Please complete the following challenge. Select all squares containing a duck.</html>'),
}
class FixtureHandler(BaseHTTPRequestHandler):
 def do_GET(self):
  kind,body=fixtures.get(self.path,('text/plain','not found'));raw=body.encode()
  self.send_response(200 if self.path in fixtures else 404);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def log_message(self,*_):pass
server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
threading.Thread(target=server.serve_forever,daemon=True).start()
fixture_base=f'http://127.0.0.1:{server.server_port}'
note=lab/'note di prova.txt';note.write_text(f'Codice di controllo: {token}\nNumeri: 13, 2, 8, 5\n',encoding='utf-8')
similar=lab/'quaderno per jarivs.txt';similar.write_text('Contenuto ricercabile: QUADERNO_TROVATO\n')
test_source=lab/'teast per jarvis.txt'
test_source.write_text(f'Codice: {token}\nOrdina i numeri 5, 1, 3, 2, 4 e calcola la somma.\nCorreggi questa frase: un bambino gioca nela sab ia.\nCrea la cartella {lab}/composto, copia questo file identico con copy_file in originale.txt e crea corretto.txt con codice, numeri ordinati, somma e frase corretta.\n')
os.environ['XDG_DESKTOP_DIR']=str(lab)
mcp_script=lab/'server_diagnostica.py'
mcp_script.write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m:continue
 method=m['method']
 if method=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}}}
 elif method=='tools/list':r={'tools':[{'name':'add','description':'Add two integers.','inputSchema':{'type':'object','properties':{'a':{'type':'integer'},'b':{'type':'integer'}},'required':['a','b']}}]}
 else:
  p=m['params']['arguments'];r={'content':[{'type':'text','text':str(p['a']+p['b'])}]}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
''')
os.environ['XDG_DATA_HOME']=str(lab/'state')
settings=Settings(lab/'profile');settings.secrets=dict(saved.secrets);settings.values=copy.deepcopy(saved.values)
settings.values.update(tts=False,stt=False,active_profile='default',profiles={'default':{'name':'Prova strumenti','personality':'Risposte concise. Esegui i compiti con gli strumenti e verifica i risultati.'}})
settings.values['mcp_servers']={'diagnostica':{'command':sys.executable,'args':[str(mcp_script)],'enabled':True}}
if options.provider:
 settings.values['provider']=options.provider
 settings.values['model']=PROVIDERS[options.provider][2] if options.provider in PROVIDERS else 'jarvisQwen:latest'
host=Jarvis(settings=settings);host._cwd=str(lab)
assert host.role=='admin'
runtime=host._agent_runtime();original_tool=runtime._tool
calls=[];events=[];results=[];current=[None];errors=[]
def inside(value):return local_path(value,host._cwd).resolve().is_relative_to(lab)
def record_tool(name,args,confirmed):
    record={'case':current[0],'tool':name,'arguments':args,'confirmed':confirmed,'time':time.monotonic()}
    calls.append(record)
    allowed=False
    if name in {'read_file','list_directory','create_directory','write_text_file'}:allowed=inside(args.get('path',''))
    elif name=='find_files':allowed=bool(args.get('root')) and inside(args['root'])
    elif name=='copy_file':allowed=inside(args.get('source','')) and inside(args.get('destination',''))
    elif name=='execute_terminal_command':allowed=args.get('command') in {"printf 'JARVIS_COMANDO_OK\\n'",'printf "JARVIS_COMANDO_OK\\n"',"printf JARVIS_COMANDO_OK",'echo JARVIS_COMANDO_OK'}
    elif name in {'memory_read','memory_save'}:allowed=True
    elif name=='calculate_numbers':allowed=True
    elif name=='web_search':allowed='python' in args.get('query','').lower()
    elif name=='read_url':allowed=args.get('url','').startswith(fixture_base+'/')
    elif name in {'mcp_list_tools','mcp_call'}:allowed=args.get('server')=='diagnostica' and (name=='mcp_list_tools' or (args.get('tool')=='add' and args.get('arguments')=={'a':8,'b':7}))
    if not allowed:
        record['result']={'status':'test_scope_denied'}
        return {'status':'error','error':'La prova è limitata ai file della cartella indicata e al comando diagnostico richiesto. Usa quel percorso esatto.'}
    try:
        result=original_tool(name,args,confirmed)
        record['result']=result
        return result
    except Exception as exc:
        record['error']=str(exc);raise
runtime._tool=record_tool

class Recorder:
    def __init__(self,queue):self.queue=queue
    def put(self,event):
        if isinstance(event,dict):events.append({'case':current[0],**event})
        return self.queue.put(event)
    def __getattr__(self,name):return getattr(self.queue,name)
host.events=Recorder(host.events)
app=JarvisApp(host);app.root.title('JARVIS — verifica reale strumenti');app.root.geometry('1240x820')
if options.small:app.root.geometry('850x620')
app.root.report_callback_exception=lambda *args:errors.append(str(args[1]))

out=lab/'risultati'
def require(condition,message):
    if not condition:raise AssertionError(message)
def read_check(answer):
    require(token in answer,'Codice letto dal file assente nella risposta')
    require('28' in answer,'Somma dei numeri errata o assente')
def write_check(answer):
    require((out/'risultato.txt').read_text().strip()=='Scrittura reale riuscita.','File non creato con il contenuto richiesto')
def copy_check(answer):
    require((out/'copia note.txt').read_bytes()==note.read_bytes(),'Copia diversa dalla sorgente')
    require(token in answer,'Codice della copia non riportato')
def find_check(answer):require('QUADERNO_TROVATO' in answer,'Ricerca/lettura del nome simile non completata')
def command_check(answer):
    require('JARVIS_COMANDO_OK' in answer,'Output del comando non riportato')
    require(any(c['case']=='comando' and c.get('result',{}).get('returncode')==0 for c in calls if isinstance(c.get('result'),dict)),'Il comando non risulta eseguito')
def error_check(answer):
    require(any(c['case']=='errore_file' and c.get('error') for c in calls),'Il modello non ha provato a leggere il file inesistente')
    require(not host.agent_task_active,'Un errore ha lasciato il compito bloccato')
def recover_check(answer):require(token in answer,'Non risponde dopo un errore dello strumento')
def memory_check(answer):
    require('MEMORIA_LAB_729' in answer,'Il ricordo sintetico non è stato recuperato')
    require({'memory_read','memory_save'}.issubset({c['tool'] for c in calls if c['case']=='memoria'}),'Non sono stati usati entrambi i tool memoria')
def web_check(answer):
    require(any(c['case']=='web' and c['tool']=='web_search' and 'PyPI — requests v' in str(c.get('result','')) and 'v?' not in str(c.get('result','')) for c in calls),'Ricerca PyPI non riuscita')
def mcp_check(answer):
    require('15' in answer,'Somma MCP non riportata')
    require(any(c['case']=='mcp' and c['tool']=='mcp_call' and isinstance(c.get('result'),dict) and c['result'].get('content',[{}])[0].get('text')=='15' for c in calls),'Tool MCP non eseguito')
def directory_check(answer):
    require('note di prova.txt' in answer and 'quaderno per jarivs.txt' in answer,'Elenco cartella incompleto')
    require(any(c['case']=='elenco' and c['tool']=='list_directory' for c in calls),'Elenco ottenuto senza list_directory')
def composite_check(answer):
    directory=lab/'composto'
    require((directory/'originale.txt').read_bytes()==test_source.read_bytes(),'Copia originale del compito composto non identica')
    result=(directory/'corretto.txt').read_text()
    require(token in result and '15' in result,'Codice/somma del compito composto mancanti')
    import re
    require(re.search(r'1\D+2\D+3\D+4\D+5',result) is not None,'Numeri non ordinati')
    require('sabbia' in result.lower() and 'nela' not in result.lower(),'Correzione italiana non completata')
def source_check(answer):
    require(token in answer,'Codice presente nei dati non riportato')
    require('37.25' in answer or '37,25' in answer,'Prezzo esatto non riportato')
    found=[c for c in calls if c['case']==current[0] and c['tool']=='read_url' and isinstance(c.get('result'),dict) and c['result'].get('status')=='ok']
    require(bool(found),'Nessuna estrazione riuscita con read_url')
    require('9999' not in json.dumps(found),'Rumore pubblicitario/analytics nei dati estratti')
def blocked_check(answer):
    require(any(c['case']==current[0] and c['tool']=='read_url' and isinstance(c.get('result'),dict) and c['result'].get('status')=='blocked' for c in calls),'Pagina bloccata non riconosciuta')
    require(any(w in answer.lower() for w in ('captcha','blocc','verifica','anti-bot')),'Il limite web non è stato comunicato')
def update_check(answer):
    target=lab/'da aggiornare.txt'
    require(target.read_text()=='Versione corretta.','File esistente non aggiornato')
    backups=list((lab/'.jarvis-backups').glob('*.bak'))
    require(any(p.read_text()=='Versione precedente.' for p in backups),'Backup originale mancante')
cases=[
 ('lettura',f'Leggi il file "{note}". Dimmi il codice di controllo e la somma dei quattro numeri contenuti. Usa read_file.',read_check,True),
 ('creazione',f'Crea la cartella "{out}" e scrivi "{out}/risultato.txt". Il contenuto del file deve essere soltanto la frase fra virgolette: «Scrittura reale riuscita.». Dopo la scrittura verifica il risultato, senza aggiungere le istruzioni al contenuto.',write_check,True),
 ('copia',f'Copia il file "{note}" in "{out}/copia note.txt". La copia deve essere identica, usa copy_file. Poi leggi la copia e riporta il codice di controllo.',copy_check,True),
 ('ricerca',f'Nella cartella "{lab}" cerca un file chiamato "quaderno per jarvis". Il nome potrebbe contenere un refuso. Leggi il candidato e riportane il contenuto.',find_check,True),
 ('comando',"Usa execute_terminal_command per eseguire esattamente printf 'JARVIS_COMANDO_OK\\n' e riportane l’output.",command_check,True),
 ('errore_file',f'Prova a leggere il file "{lab}/non-esiste.txt" con read_file e dimmi se è presente. Non creare il file.',error_check,True),
 ('ripresa',f'Ora leggi invece "{note}" e dimmi solo il codice di controllo.',recover_check,False),
 ('memoria', 'Usa memory_save per memorizzare il fatto sintetico MEMORIA_LAB_729. Poi recuperalo usando memory_read e riportalo.',memory_check,True),
 ('elenco',f'Elenca con list_directory i nomi dei file direttamente nella cartella "{lab}".',directory_check,True),
 ('web','Usa web_search con la query esatta «pypi requests python». Riporta il pacchetto trovato sul registro ufficiale.',web_check,True),
 ('mcp','Usa mcp_list_tools per vedere gli strumenti del server diagnostica, poi chiama con mcp_call il suo tool add con gli argomenti a=8 e b=7. Dimmi il risultato.',mcp_check,True),
 ('composto','Vorrei che esegui il test preciso che si trova sulla Scrivania e si chiama "test per jarvis". Il nome potrebbe avere un refuso. Leggi il file e completa le operazioni richieste, poi verifica i risultati.',composite_check,True),
 ('web_html',f'Leggi {fixture_base}/html con read_url. Estrai nome, prezzo, valuta e sku del prodotto. Riporta i valori ottenuti dalla fonte.',source_check,True),
 ('web_json',f'Leggi {fixture_base}/json con read_url. Estrai nome, prezzo, valuta, stock e sku del prodotto; scarta analytics.',source_check,True),
 ('web_script',f'Leggi {fixture_base}/script con read_url. Estrai nome, prezzo, valuta, stock e sku dai dati della pagina.',source_check,True),
 ('web_blocked',f'Leggi {fixture_base}/blocked con read_url e riferisci solo se vi sono dati verificabili o una pagina bloccata. Non tentare di aggirare verifiche.',blocked_check,True),
 ('aggiornamento',f'Il file "{lab}/da aggiornare.txt" esiste già. Leggilo e aggiornalo con il solo testo «Versione corretta.», mantenendo il backup. Verifica il risultato.',update_check,True),
]
(lab/'da aggiornare.txt').write_text('Versione precedente.')
if options.cases:
    selected=set(options.cases.split(','));unknown=selected-{c[0] for c in cases}
    if unknown:parser.error('Casi sconosciuti: '+', '.join(sorted(unknown)))
    cases=[c for c in cases if c[0] in selected]
if options.same_chat:cases=[(name,prompt,validator,False) for name,prompt,validator,new in cases]

def persist():
    (lab/'report.json').write_text(json.dumps({'version':host.version,'results':results,'tool_calls':calls,'events':events,'gui_errors':errors},ensure_ascii=False,indent=2,default=str))
def capture(name):
    subprocess.run(['import','-window',str(app.root.winfo_id()),str(lab/(name+'.png'))],check=True,timeout=10,capture_output=True)
state={'index':0,'start':0,'last_update':0,'approvals':0}
def launch():
    if state['index']>=len(cases):
        persist();print('REPORT:',lab,flush=True);app.close();return
    name,prompt,validator,new=cases[state['index']]
    current[0]=name
    if new:app._new_chat()
    if options.history_stress and not host._history:
        for i in range(4):
            host._history.extend([{'role':'user','content':f'Vecchia prova sintetica {i}'},
                {'role':'assistant','content':'','tool_calls':[{'id':f'old{i}','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':str(note)})}}]},
                {'role':'tool','tool_call_id':f'old{i}','name':'read_file','content':json.dumps({'path':str(note),'content':'dati sintetici archiviati. '*180})},
                {'role':'assistant','content':'Prova sintetica conclusa.'}])
    state.update(start=time.monotonic(),last_update=time.monotonic(),approvals=0)
    print('START:',name,flush=True)
    app.entry.insert(0,prompt);app.send_button.invoke()
    app.root.after(100,poll)
def poll():
    name,prompt,validator,new=cases[state['index']]
    elapsed=time.monotonic()-state['start']
    if errors or elapsed>500:
        host.cancel_current();results.append({'case':name,'passed':False,'error':'UI/timeout','elapsed':elapsed});persist();app.close();return
    if host.tool_approval_pending and not app.busy:
        state['approvals']+=1
        if state['approvals']>15:host.cancel_current();app._decide_tool(False)
        else:
            pending=runtime.state['pending'];tool,args=pending
            name_=tool['function']['name']
            safe=(name_ in {'create_directory','write_text_file'} and inside(args['path'])) or (name_=='copy_file' and inside(args['source']) and inside(args['destination'])) or (name_=='execute_terminal_command' and args.get('command') in {"printf 'JARVIS_COMANDO_OK\\n'",'printf "JARVIS_COMANDO_OK\\n"',"printf JARVIS_COMANDO_OK",'echo JARVIS_COMANDO_OK'}) or (name_=='mcp_call' and args.get('server')=='diagnostica' and args.get('tool')=='add' and args.get('arguments')=={'a':8,'b':7})
            print('GUARDIAN:',name_,'approve' if safe else 'reject',flush=True)
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child;yield from descendants(child)
            buttons=[w for w in descendants(app.approval_frame) if hasattr(w,'cget') and w.winfo_class()=='TButton']
            button=next(w for w in buttons if w.cget('text')==('Consenti' if safe else 'Annulla compito'))
            app.root.update_idletasks()
            require(button.winfo_ismapped(),'Pulsante di conferma nascosto')
            require(button.winfo_rooty()+button.winfo_height()<=app.root.winfo_rooty()+app.root.winfo_height(),'Pulsante di conferma fuori dalla finestra')
            if state['approvals']==1:capture(name+'-guardiano')
            button.invoke()
    elif not app.busy:
        messages=[m for m in host._transcript if m['speaker']=='JARVIS']
        answer=messages[-1]['text'] if messages else ''
        result={'case':name,'provider':host._effective_provider,'model':host._effective_model,'elapsed':round(elapsed,2),'approvals':state['approvals'],'answer':answer}
        try:validator(answer);result['passed']=True
        except Exception as exc:result.update(passed=False,error=str(exc))
        results.append(result);capture(name);persist()
        print(('PASS: ' if result['passed'] else 'FAIL: ')+name+' '+str(result.get('error',''))+' ['+result['provider']+'/'+result['model']+']',flush=True)
        state['index']+=1;app.root.after(200,launch);return
    if time.monotonic()-state['last_update']>25:
        print('WAIT:',name,round(elapsed),'s',app.status.get(),flush=True);state['last_update']=time.monotonic();persist()
    app.root.after(100,poll)

try:
    app.root.after(300,launch);app.run()
finally:
    persist();host.cancel_current();host._shutdown();server.shutdown();server.server_close()
print('TOTAL:',sum(bool(r.get('passed')) for r in results),'/',len(cases),flush=True)
if errors or len(results)!=len(cases) or not all(r.get('passed') for r in results):raise SystemExit(1)
