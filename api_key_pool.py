"""Named API credentials. Configuration contains references, never key values."""
from copy import deepcopy
from dataclasses import dataclass,field
import hashlib
import os
import re
import uuid

FIELDS={'groq':'GROQ_API_KEY','cerebras':'CEREBRAS_API_KEY','nvidia':'NVIDIA_API_KEY','openai':'OPENAI_API_KEY'}
MAX_KEYS=12


def is_pool_secret(name):
    return bool(re.fullmatch(r'JARVIS_(GROQ|CEREBRAS|NVIDIA|OPENAI)_API_KEY_[A-F0-9]{32}',name))


@dataclass(frozen=True)
class Credential:
    id: str
    name: str
    value: str=field(repr=False)

    @property
    def identity(self):return self.id+':'+hashlib.sha256(self.value.encode()).hexdigest()


def secret_ref(provider,key_id):
    if provider not in FIELDS or not re.fullmatch(r'[a-f0-9]{32}',key_id):raise ValueError('Riferimento API non valido.')
    return f'JARVIS_{provider.upper()}_API_KEY_{key_id.upper()}'


def entries(settings,provider):
    if provider not in FIELDS:raise ValueError('Provider API sconosciuto.')
    result=[]
    for item in settings.values.get('provider_keys',{}).get(provider,[]):
        if not isinstance(item,dict):continue
        try:secret_ref(provider,item.get('id',''))
        except (ValueError,TypeError):continue
        models=item.get('models',[])
        if not isinstance(models,list) or any(not isinstance(m,str) for m in models):continue
        result.append({'id':item['id'],'name':str(item.get('name') or 'Chiave aggiuntiva')[:60],
                       'enabled':bool(item.get('enabled',True)),'models':models,
                       'configured':bool(settings.secrets.get(secret_ref(provider,item['id']),''))})
        if len(result)>=MAX_KEYS:break
    return result


def credentials(settings,provider,model):
    result=[];seen=set()
    if settings.values.get('provider_primary_key_enabled',{}).get(provider,True):
        value=settings.secrets.get(FIELDS[provider],os.environ.get(FIELDS[provider],''))
        if isinstance(value,str) and value.strip():
            value=value.strip();result.append(Credential('default','Principale',value));seen.add(value)
    for item in entries(settings,provider):
        if not item['enabled'] or (item['models'] and model not in item['models']):continue
        value=settings.secrets.get(secret_ref(provider,item['id']),'').strip()
        if not value or value in seen:continue
        result.append(Credential(item['id'],item['name'],value));seen.add(value)
    return result


def commit(settings,values,secrets):
    """Restore files and in-memory/environment values if an atomic write fails."""
    from app_config import atomic_write
    previous_values=deepcopy(settings.values);previous_secrets=dict(settings.secrets)
    paths=(settings.path,settings.directory/'.env')
    files={p:p.read_text(encoding='utf-8') if p.exists() else None for p in paths}
    changed=set(previous_secrets)|set(secrets)
    old_environment={name:os.environ.get(name) for name in changed}
    try:
        settings.secrets=dict(secrets);settings.save_secrets({})
        settings.values.clear();settings.values.update(values);settings.save()
        for name in changed-set(secrets):os.environ.pop(name,None)
    except Exception:
        settings.values.clear();settings.values.update(previous_values);settings.secrets=previous_secrets
        for path,content in files.items():
            if content is None:
                if path.exists():path.unlink()
            else:atomic_write(path,content)
        for name,value in old_environment.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
        raise


def save_key(settings,provider,name,value='',models=(),enabled=True,key_id=None):
    if provider not in FIELDS:raise ValueError('Provider API sconosciuto.')
    name=name.strip()
    if not name or len(name)>60 or '\n' in name or '\r' in name:raise ValueError('Scegli un nome da 1 a 60 caratteri.')
    if not isinstance(value,str) or '\n' in value or '\r' in value:raise ValueError('La chiave API deve occupare una sola riga.')
    models=list(dict.fromkeys(m.strip() for m in models if m.strip()))
    if len(models)>12 or any(not re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}',m) for m in models):raise ValueError('Inserisci identificatori di modello validi, separati da virgole.')
    current=entries(settings,provider);existing=next((e for e in current if e['id']==key_id),None)
    if key_id is not None and existing is None:raise KeyError('Chiave non trovata.')
    if existing is None and len(current)>=MAX_KEYS:raise ValueError(f'Sono ammesse al massimo {MAX_KEYS} chiavi aggiuntive per provider.')
    key_id=key_id or uuid.uuid4().hex;ref=secret_ref(provider,key_id)
    value=value.strip() or settings.secrets.get(ref,'')
    if not value:raise ValueError('Inserisci la chiave API.')
    duplicates=[settings.secrets.get(FIELDS[provider],'').strip()]
    duplicates.extend(settings.secrets.get(secret_ref(provider,e['id']),'').strip() for e in current if e['id']!=key_id)
    if value in duplicates:raise ValueError('Questa chiave è già presente per il provider scelto.')
    record={'id':key_id,'name':name,'models':models,'enabled':bool(enabled)}
    records=[{k:v for k,v in e.items() if k!='configured'} for e in current]
    if existing is None:records.append(record)
    else:records[current.index(existing)]=record
    values=deepcopy(settings.values);values.setdefault('provider_keys',{})[provider]=records
    secrets=dict(settings.secrets);secrets[ref]=value;commit(settings,values,secrets)
    return key_id


def remove_key(settings,provider,key_id):
    current=entries(settings,provider)
    if not any(e['id']==key_id for e in current):raise KeyError('Chiave non trovata.')
    values=deepcopy(settings.values)
    values.setdefault('provider_keys',{})[provider]=[{k:v for k,v in e.items() if k!='configured'} for e in current if e['id']!=key_id]
    secrets=dict(settings.secrets);secrets.pop(secret_ref(provider,key_id),None);commit(settings,values,secrets)


def move_key(settings,provider,key_id,direction):
    current=entries(settings,provider);ids=[e['id'] for e in current]
    if key_id not in ids:raise KeyError('Chiave non trovata.')
    i=ids.index(key_id);j=max(0,min(len(current)-1,i+(-1 if direction<0 else 1)))
    current[i],current[j]=current[j],current[i]
    values=deepcopy(settings.values)
    values.setdefault('provider_keys',{})[provider]=[{k:v for k,v in e.items() if k!='configured'} for e in current]
    commit(settings,values,dict(settings.secrets))


def enable_primary(settings,provider,enabled):
    if provider not in FIELDS:raise ValueError('Provider API sconosciuto.')
    values=deepcopy(settings.values);values.setdefault('provider_primary_key_enabled',{})[provider]=bool(enabled)
    commit(settings,values,dict(settings.secrets))
