"""Bounded provider failover. Tools, identity and results belong to the host,
not to a provider, so a model change never restarts the user's task.
"""
from copy import deepcopy
import os
import json
import shutil
import subprocess
import time
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from app_config import data_dir
from task_io import TaskCancelled,check_cancelled


class ProviderFailure(RuntimeError):
    pass


class ContextBudgetExceeded(RuntimeError):
    """A smaller model cannot repair an oversized active task without losing evidence."""


class KeysUnavailable(ProviderFailure):
    """Individual key cooldowns are already recorded; do not disable the whole provider."""
    def __init__(self,message,*,quota=False):
        super().__init__(message);self.quota=quota


def quota_details(error):
    """Keep the HTTP body readable, expose only numeric quota facts, never account IDs."""
    raw=getattr(error,'_jarvis_quota_body',None)
    if raw is None:
        raw=error.read(16384);error._jarvis_quota_body=raw
        try:error.fp.seek(0)
        except (OSError,AttributeError):pass
    text=raw.decode('utf-8',errors='replace')
    def number(label):
        match=re.search(r'\b'+label+r'\s*[:=]?\s*(\d+(?:\.\d+)?)',text,re.I)
        return float(match.group(1)) if match else None
    result={k:v for k in ('limit','used','requested') if (v:=number(k)) is not None}
    for unit in ('TPM','TPD','RPM','RPD'):
        if re.search(r'\b'+unit+r'\b',text,re.I):result['unit']=unit;break
    try:result['retry_seconds']=float((error.headers or {}).get('retry-after',''))
    except (TypeError,ValueError):
        match=re.search(r'try again in\s+(\d+(?:\.\d+)?)s',text,re.I)
        if match:result['retry_seconds']=float(match.group(1))
    return result


def provider_messages(messages, provider):
    copied = deepcopy(messages)
    for message in copied:
        source = message.pop('_provider', None)
        if source != provider or provider=='groq':
            message.pop('reasoning', None)
            message.pop('reasoning_content', None)
            message.pop('thinking', None)
    return copied


class ProviderRouter:
    def __init__(self, host):
        self.host = host
        self.failures = {}
        self.turn = None
        self.pinned = None
        self.pinned_model=None
        self.groq_reserve_until=0
        self.model_failures={}
        self.key_failures={}
        self.pinned_keys={}

    def reset(self):
        self.failures.clear()
        self.turn = self.pinned = None
        self.pinned_model=None;self.groq_reserve_until=0
        self.model_failures.clear()
        self.key_failures.clear();self.pinned_keys.clear()

    def route(self):
        primary = self.host._cfg.get('provider', 'ollama')
        if not self.host._cfg.get('auto_fallback', True) or primary == 'ollama':
            return [primary]
        chain = [primary, 'groq', 'cerebras', 'ollama']
        return list(dict.fromkeys(chain))

    def complete(self, messages, tools=None):
        host = self.host
        state = host._agent.state if host._agent else None
        turn = state.get('task_id') if state else None
        if turn != self.turn or turn is None:
            self.turn, self.pinned = turn, None
            self.pinned_model=None
            self.pinned_keys.clear()
        route = self.route()
        if self.pinned in route:
            route = route[route.index(self.pinned):]
        errors = []
        for provider in route:
            if host._agent_cancel.is_set():
                raise TaskCancelled('Compito interrotto su richiesta.')
            cooldown = self.failures.get(provider)
            if cooldown and cooldown[0] > time.monotonic():
                errors.append(cooldown[1])
                continue
            preferred_model=host.model_for(provider)
            reserve=provider=='groq' and preferred_model=='openai/gpt-oss-120b' and host._cfg.get('auto_fallback',True) and host._cfg.get('groq_small_fallback',False)
            models=[preferred_model]
            if reserve:models.append('openai/gpt-oss-20b')
            # A smaller Groq model is temporary; the preferred model returns after its quota reset.
            if self.pinned==provider and provider!='groq' and self.pinned_model in models:models=models[models.index(self.pinned_model):]
            for model in models:
                check_cancelled(host._agent_cancel)
                quota_waited=0
                cooldown=self.model_failures.get((provider,model))
                if cooldown and cooldown[0]>time.monotonic():
                    remaining=cooldown[0]-time.monotonic()+1
                    limit=max(0,min(120,float(host._cfg.get('groq_retry_wait',120))))
                    if provider=='groq' and model==preferred_model and remaining<=limit:
                        host.events.put({'type':'provider_wait','provider':provider,'model':model,'until':time.monotonic()+remaining,'seconds':remaining})
                        if host._agent_cancel.wait(remaining):raise TaskCancelled('Attesa Groq interrotta su richiesta.')
                        quota_waited=remaining
                    else:errors.append(cooldown[1]);continue
                host.events.put({'type':'provider_attempt','provider':provider,'model':model})
                try:
                    if provider=='ollama':model=host._prepare_local_model(model,bool(tools))
                    result=self._completion(provider,model,provider_messages(messages,provider),tools,waited=quota_waited)
                    check_cancelled(host._agent_cancel)
                except (HTTPError,URLError,TimeoutError,OSError,json.JSONDecodeError,ProviderFailure) as error:
                    status=error.code if isinstance(error,HTTPError) else None
                    delay=300 if status in {401,402,403,404,422} else 30
                    if status==429:
                        try:delay=max(1,min(3600,quota_details(error).get('retry_seconds',30)))
                        except (ValueError,TypeError):pass
                    reason=host._provider_problem(provider,error)
                    host.provider_diagnostic('failure',provider=provider,model=model,status=status)
                    if not isinstance(error,KeysUnavailable):
                        if status==429:self.model_failures[(provider,model)]=(time.monotonic()+delay,reason)
                        else:self.failures[provider]=(time.monotonic()+delay,reason)
                    errors.append(reason)
                    host.events.put({'type':'provider_unavailable','provider':provider,'model':model,'reason':reason})
                    if reserve and model=='openai/gpt-oss-120b' and (status==429 or getattr(error,'quota',False)):
                        self.groq_reserve_until=time.monotonic()+delay
                        continue
                    break
                self.failures.pop(provider,None)
                self.model_failures.pop((provider,model),None)
                self.pinned=provider if turn else None
                self.pinned_model=model if turn else None
                host._effective_provider,host._effective_model=provider,model
                host._last_completion_provider=provider
                if provider=='ollama':host._effective_api_key_name=None
                host.provider_diagnostic('selected',provider=provider,model=model,fallback=provider!=host._cfg.get('provider') or model!=preferred_model)
                host.events.put({'type':'provider_used','provider':provider,'model':model,
                    'preferred':host._cfg.get('provider'),'key_name':host._effective_api_key_name,
                    'fallback':provider!=host._cfg.get('provider') or model!=preferred_model})
                return result
        host.events.put({'type': 'provider_failed', 'reason': 'Nessun provider della catena è disponibile.'})
        raise ProviderFailure('Nessun cervello disponibile. ' + ' → '.join(errors))

    def _completion(self,provider,model,messages,tools,*,waited=0):
        host=self.host
        if provider=='ollama':return self._completion_one(provider,model,messages,tools,waited=waited)
        slots=host.api_credentials(provider,model)
        configured=provider in host._cfg.get('provider_keys',{}) or provider in host._cfg.get('provider_primary_key_enabled',{})
        if not configured or (len(slots)==1 and slots[0].id=='default'):
            host._effective_api_key_name='Principale'
            return self._completion_one(provider,model,messages,tools,waited=waited)
        if not slots:raise KeysUnavailable(f'{provider}: nessuna chiave abilitata per {model}. Controlla API multiple in Servizi.')
        pinned=self.pinned_keys.get(provider)
        if pinned and self.turn:
            slots.sort(key=lambda slot:slot.identity!=pinned)
        reasons=[];waiting=[];oversized=None
        for slot in slots:
            check_cancelled(host._agent_cancel)
            active=[c for scope in ('*',model) if (c:=self.key_failures.get((provider,slot.identity,scope))) and c[0]>time.monotonic()]
            cooldown=max(active,key=lambda c:c[0]) if active else None
            if cooldown:
                reasons.append(cooldown[1])
                if cooldown[2]==429:waiting.append((cooldown[0]-time.monotonic()+1,slot))
                continue
            host.events.put({'type':'api_key_attempt','provider':provider,'model':model,'key_name':slot.name})
            try:
                # Try another available credential before waiting on an exhausted key.
                result=self._completion_one(provider,model,messages,tools,waited=waited,slot=slot,wait_on_quota=False)
                check_cancelled(host._agent_cancel)
            except TaskCancelled:raise
            except ContextBudgetExceeded as error:
                oversized=error;reasons.append(str(error));continue
            except (HTTPError,URLError,TimeoutError,OSError,json.JSONDecodeError,ProviderFailure) as error:
                check_cancelled(host._agent_cancel)
                status=error.code if isinstance(error,HTTPError) else None
                # Invalid input is common to all credentials; do not spend calls repeating it.
                if status is not None and status<500 and status not in {401,402,403,404,422,429}:raise
                details=quota_details(error) if status==429 else {}
                delay=300 if status in {401,402,403,404,422} else 30
                if status==429:delay=max(1,min(86400,details.get('retry_seconds',30)))
                reason=host._provider_problem(provider,error)
                scope='*' if status in {401,402,403} else model
                self.key_failures[(provider,slot.identity,scope)]=(time.monotonic()+delay,reason,status)
                reasons.append(slot.name+': '+reason)
                if status==429:waiting.append((delay+1,slot))
                host.provider_diagnostic('key_unavailable',provider=provider,model=model,key_id=slot.id,status=status)
                host.events.put({'type':'api_key_unavailable','provider':provider,'model':model,'key_name':slot.name,'reason':reason})
                continue
            self.pinned_keys[provider]=slot.identity
            self.key_failures.pop((provider,slot.identity,model),None);self.key_failures.pop((provider,slot.identity,'*'),None)
            host._effective_api_key_name=slot.name
            host.provider_diagnostic('key_selected',provider=provider,model=model,key_id=slot.id)
            host.events.put({'type':'api_key_used','provider':provider,'model':model,'key_name':slot.name})
            return result
        if oversized:raise oversized
        limit=max(0,min(120,float(host._cfg.get('groq_retry_wait',120))))
        if provider=='groq' and waiting:
            delay,slot=min(waiting,key=lambda pair:pair[0])
            if 1<delay<=limit-waited:
                host.events.put({'type':'provider_wait','provider':provider,'model':model,'key_name':slot.name,'until':time.monotonic()+delay,'seconds':delay})
                if host._agent_cancel.wait(delay):raise TaskCancelled('Attesa API interrotta su richiesta.')
                try:
                    result=self._completion_one(provider,model,messages,tools,waited=waited+delay,slot=slot)
                    check_cancelled(host._agent_cancel)
                except TaskCancelled:raise
                except (HTTPError,URLError,TimeoutError,OSError,json.JSONDecodeError,ProviderFailure) as error:
                    check_cancelled(host._agent_cancel)
                    status=error.code if isinstance(error,HTTPError) else None
                    details=quota_details(error) if status==429 else {}
                    delay=max(1,min(86400,details.get('retry_seconds',30))) if status==429 else (300 if status in {401,402,403,404,422} else 30)
                    reason=host._provider_problem(provider,error)
                    self.key_failures[(provider,slot.identity,'*' if status in {401,402,403} else model)]=(time.monotonic()+delay,reason,status)
                    reasons.append(reason)
                else:
                    self.key_failures.pop((provider,slot.identity,model),None)
                    self.pinned_keys[provider]=slot.identity;host._effective_api_key_name=slot.name
                    host.events.put({'type':'api_key_used','provider':provider,'model':model,'key_name':slot.name})
                    return result
        raise KeysUnavailable(f'{provider}: nessuna API disponibile. '+' → '.join(dict.fromkeys(reasons)),quota=bool(waiting))

    def _completion_one(self,provider,model,messages,tools,*,waited=0,slot=None,wait_on_quota=True):
        host=self.host
        def send():
            kwargs={'provider':provider,'model':model}
            if slot is not None:kwargs['api_key']=slot.value
            return host._complete_once(messages,tools,**kwargs)
        for attempt in range(3):
            try:return send()
            except HTTPError as error:
                if provider!='groq' or error.code!=429:raise
                details=quota_details(error)
                host.provider_diagnostic('quota',provider=provider,model=model,**details)
                if details.get('requested',0)>details.get('limit',float('inf')):
                    # Waiting cannot fix a single request larger than the account's TPM cap.
                    host.events.put({'type':'provider_retry','provider':provider,'message':'La richiesta supera la quota del singolo invio; riduco i turni precedenti mantenendo integro il compito attivo.'})
                    current=getattr(host,'_groq_input_budget',host._cfg.get('groq_input_budget',5000))
                    host._groq_input_budget=max(800,min(current-500,int(details['limit']*.55)))
                    if attempt==0:error.close();continue
                    error.close()
                    raise ContextBudgetExceeded('Groq: il compito attivo supera il limite di token per richiesta. I risultati restano conservati; occorre una richiesta più piccola o una quota maggiore.') from error
                delay=details.get('retry_seconds',0)+1
                if not wait_on_quota:raise
                limit=max(0,min(120,float(host._cfg.get('groq_retry_wait',120))))
                if attempt>=2 or not (1<delay<=limit-waited):raise
                error.close();waited+=delay
                host.events.put({'type':'provider_wait','provider':provider,'model':model,
                                 'until':time.monotonic()+delay,'seconds':delay})
                if host._agent_cancel.wait(delay):raise TaskCancelled('Attesa Groq interrotta su richiesta.')


class LocalOllama:
    def __init__(self, host, request):
        self.host, self.request = host, request
        self.process = None
        self.selected = None

    def _tags(self):
        base = self.host._cfg['ollama_host'].rstrip('/')
        return self.request(base + '/api/tags', timeout=2)

    def _start(self):
        host = self.host
        url = urlparse(host._cfg['ollama_host'])
        if not host._cfg.get('ollama_autostart', True):
            raise ProviderFailure('Ollama non è avviato; avvio automatico disattivato.')
        if url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ProviderFailure('Il server Ollama configurato non è locale; non lo posso avviare da questo PC.')
        host.require_admin()
        binary = shutil.which('ollama')
        if not binary:
            raise ProviderFailure('Ollama non è installato. Installa un modello locale prima di usarlo come fallback.')
        if self.process is None or self.process.poll() is not None:
            environment = dict(os.environ)
            for key in list(environment):
                if any(part in key.upper() for part in ('API_KEY', 'PASSWORD', 'SECRET', 'TOKEN')):
                    environment.pop(key, None)
            environment['OLLAMA_HOST'] = url.netloc
            directory = data_dir(); directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(directory / 'ollama-service.log', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                self.process = subprocess.Popen([binary, 'serve'], stdin=subprocess.DEVNULL, stdout=fd,
                                                stderr=subprocess.STDOUT, env=environment, start_new_session=True)
            finally:
                os.close(fd)
            host.events.put({'type': 'local_service', 'message': 'Avvio del servizio Ollama locale…'})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            check_cancelled(host._agent_cancel)
            try:
                return self._tags()
            except (URLError, OSError):
                if self.process and self.process.poll() is not None:
                    break
                if host._agent_cancel.wait(.2):raise TaskCancelled('Avvio locale interrotto.')
        raise ProviderFailure('Ollama non si è avviato. Controlla ollama-service.log nelle impostazioni locali.')

    def model(self, preferred, needs_tools):
        try:
            result = self._tags()
        except (URLError, OSError) as error:
            # HTTP errors indicate an accessible server with a different problem.
            if isinstance(error, HTTPError):
                raise
            result = self._start()
        models = [m['name'] for m in result.get('models', [])]
        if not models:
            raise ProviderFailure('Ollama è avviato ma non contiene modelli. Nessun download viene eseguito automaticamente.')
        candidates = list(dict.fromkeys([preferred, self.selected,
            *sorted(models, key=lambda name: (not name.lower().startswith('jarvis'), 'qwen' not in name.lower(), name))]))
        base = self.host._cfg['ollama_host'].rstrip('/')
        for model in candidates:
            if not model or model not in models:
                continue
            if needs_tools:
                try:
                    info = self.request(base + '/api/show', {'model': model}, timeout=5)
                    capabilities = info.get('capabilities')
                    if isinstance(capabilities, list) and 'tools' not in capabilities:
                        continue
                except HTTPError as error:
                    error.close()
                    continue
            self.selected = model
            return model
        raise ProviderFailure('I modelli Ollama installati non dichiarano supporto agli strumenti. Scegli un modello locale con tool calling.')
