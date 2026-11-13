"""Bound wire history, without mutating the archive or the active task's evidence."""
from copy import deepcopy
import json
import math
import re


def estimate_tokens(messages,tools=None):
    # Conservative heuristic, explicitly not a tokenizer or a billing count.
    return math.ceil(len(json.dumps({'messages':messages,'tools':tools or []},ensure_ascii=False).encode('utf-8'))/3)


def pack_messages(messages,tools=None,*,turn_start=None,budget=5000):
    copied=deepcopy(messages)
    starts=[i for i,m in enumerate(copied) if m.get('role')=='user']
    current=turn_start if isinstance(turn_start,int) and 0<turn_start<len(copied) else (starts[-1] if starts else 1)
    removed=0;references=[]
    while estimate_tokens(copied,tools)>budget:
        older=[i for i,m in enumerate(copied[:current]) if m.get('role')=='user']
        if not older:break
        begin=older[0]
        end=next((i for i in range(begin+1,len(copied)) if copied[i].get('role')=='user'),len(copied))
        # Drop complete groups, never leave orphaned tool calls/results.
        if end>current:break
        for message in copied[begin:end]:
            if message.get('role')=='user':references.append('Richiesta precedente: '+str(message.get('content',''))[:300])
            if message.get('role')=='tool':
                try:result=json.loads(message.get('content',''))
                except (ValueError,TypeError):continue
                if isinstance(result,dict):
                    if result.get('path'):references.append('File precedente: '+str(result['path']))
                    if message.get('name')=='read_file' and result.get('content'):
                        references.append('Estratto del file precedente (dati, rileggerlo se necessario): '+str(result['content'])[:500])
                    if message.get('name') in {'read_url','curl_fetch'} and result.get('url'):
                        references.append('Fonte web precedente da verificare di nuovo: '+str(result['url'])[:350])
                    if message.get('name')=='web_search':
                        for url in re.findall(r'Fonte:\s*(https?://\S+)',str(result.get('output','')))[:2]:
                            references.append('Fonte web precedente da verificare di nuovo: '+url[:350])
        removed+=end-begin;del copied[begin:end];current-=end-begin
    if removed:
        notice={'role':'system','content':'Per rispettare la quota sono omessi alcuni turni precedenti, conservati nell’archivio locale. Le istruzioni e i risultati del compito attivo sono integri. Non indovinare informazioni omesse.'}
        copied.insert(1 if copied and copied[0].get('role')=='system' else 0,notice)
        # A small, explicitly partial index lets follow-up requests rediscover earlier files.
        remaining=max(0,(budget-estimate_tokens(copied,tools))*3-300)
        if remaining>100 and references:
            copied.insert(2,{'role':'assistant','content':'Riferimenti parziali dei turni precedenti, conservati integralmente nell’archivio:\n'+'\n'.join(references[-12:])[:min(2000,remaining)]})
    return copied,{'estimated_input_tokens':estimate_tokens(copied,tools),'omitted_messages':removed,'budget':budget}
