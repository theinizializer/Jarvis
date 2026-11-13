"""
web_module.py — JARVIS v10
Modulo web unificato. Sostituisce completamente search_module.py.
Unica interfaccia pubblica: WebModule.search(query) → str

Struttura interna:
  _fetch()            — HTTP robusto con retry
  _detect_intent()    — classifica la query (meteo, crypto, voli, wiki, pkg…)
  _handle_*()         — handler specifici per categoria
  WebModule           — classe pubblica con .search() e .fetch_url()

JARVIS chiama solo WebModule — mai i simboli _privati direttamente.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse
from contextvars import ContextVar

_CANCEL_EVENT=ContextVar('jarvis_web_cancel',default=None)

# ---------------------------------------------------------------------------
# HTTP robusto (curl — nessuna dipendenza da requests a livello modulo)
# ---------------------------------------------------------------------------

_CURL_TIMEOUT = 12
_MAX_RETRIES  = 2
_MAX_DOWNLOAD = 8 * 1024 * 1024


def _fetch_response(url: str, *, headers: dict | None = None, timeout: int = _CURL_TIMEOUT) -> dict:
    """Fixed curl GET; retain HTTP metadata and error bodies for diagnostics."""
    parsed=urlparse(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Sono ammessi soltanto URL HTTP/HTTPS senza credenziali.')
    cmd = ["curl", "-sSL", "--max-time", str(timeout), "--compressed",
           '--proto','=http,https','--proto-redir','=http,https','--max-redirs','4',
           '--max-filesize',str(_MAX_DOWNLOAD),'-A','Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36']
    for k, v in {'Accept':'text/html,application/json,text/plain,*/*;q=0.8',**(headers or {})}.items():
        cmd += ["-H", f"{k}: {v}"]
    marker='\n__JARVIS_HTTP_METADATA__'
    cmd += ['--write-out',marker+'%{json}','--url',url]
    result={'url':url,'effective_url':url,'http_status':0,'content_type':'','body':'','curl_exit_code':None,'status':'error'}
    for attempt in range(_MAX_RETRIES + 1):
        try:
            cancel=_CANCEL_EVENT.get()
            if cancel is None:r = subprocess.run(cmd, capture_output=True, text=True,errors='replace', timeout=timeout + 5)
            else:
                from task_io import run_command
                r=run_command(cmd,cancel_event=cancel,text=True,errors='replace',timeout=timeout+5)
            body,separator,raw_meta=r.stdout.rpartition(marker)
            try:meta=json.loads(raw_meta) if separator else {}
            except ValueError:meta={}
            status=int(meta.get('http_code') or meta.get('response_code') or 0)
            result.update(body=body if separator else r.stdout,http_status=status,
                          effective_url=meta.get('url_effective') or url,content_type=meta.get('content_type') or '',curl_exit_code=r.returncode)
            if r.returncode==0 and 200<=status<300:
                result['status']='ok';return result
            if status and status<500:return result
            if r.returncode in {23,63}:return result
        except subprocess.TimeoutExpired:
            pass
        if attempt < _MAX_RETRIES:
            cancel=_CANCEL_EVENT.get()
            if cancel is None:time.sleep(1.2*(attempt+1))
            elif cancel.wait(1.2*(attempt+1)):
                from task_io import TaskCancelled
                raise TaskCancelled('Ricerca web interrotta.')
    return result


def _fetch(url: str, *, headers: dict | None = None, timeout: int = _CURL_TIMEOUT) -> str:
    response=_fetch_response(url,headers=headers,timeout=timeout)
    return response['body'] if response['status']=='ok' else ''


def _fetch_json(url: str, *, headers: dict | None = None) -> dict | list | None:
    """Fetch + parse JSON. Ritorna None se fallisce."""
    raw = _fetch(url, headers=headers)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _html_to_text(html: str, max_chars: int = 2000) -> str:
    """Estrae testo leggibile dall'HTML (rimuove tag, collassa spazi)."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>",  "", text,  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;",  "&", text)
    text = re.sub(r"&lt;",   "<", text)
    text = re.sub(r"&gt;",   ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def _url_wikipedia(q: str, lang: str = "it") -> str:
    return f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote_plus(q)}&format=json&utf8=1&srlimit=3"

def _url_archwiki(q: str) -> str:
    return f"https://wiki.archlinux.org/index.php?search={quote_plus(q)}"

def _url_pypi(pkg: str) -> str:
    return f"https://pypi.org/pypi/{quote_plus(pkg)}/json"

def _url_npm(pkg: str) -> str:
    return f"https://registry.npmjs.org/{quote_plus(pkg)}/latest"

def _url_aur_search(pkg: str) -> str:
    return f"https://aur.archlinux.org/rpc/?v=5&type=search&arg={quote_plus(pkg)}"

def _url_aur_info(pkg: str) -> str:
    return f"https://aur.archlinux.org/rpc/?v=5&type=info&arg[]={quote_plus(pkg)}"

def _url_debian_pkg(pkg: str) -> str:
    return f"https://packages.debian.org/search?keywords={quote_plus(pkg)}&searchon=names&suite=stable&section=all"

def _url_ubuntu_pkg(pkg: str) -> str:
    return f"https://packages.ubuntu.com/search?keywords={quote_plus(pkg)}&searchon=names&suite=noble&section=all"

def _url_github_raw(owner: str, repo: str, path: str = "README.md") -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}"

def _url_binance(symbol: str) -> str:
    return f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}USDT"

def _url_coingecko(coin: str) -> str:
    return f"https://api.coingecko.com/api/v3/simple/price?ids={quote_plus(coin.lower())}&vs_currencies=usd,eur"

def _url_kraken(pair: str) -> str:
    return f"https://api.kraken.com/0/public/Ticker?pair={pair.upper()}"

def _url_amazon(q: str) -> str:
    return f"https://www.amazon.it/s?k={quote_plus(q)}"

def _url_adafruit(q: str) -> str:
    return f"https://www.adafruit.com/search?q={quote_plus(q)}"

def _url_justeat(q: str, city: str = "Roma") -> str:
    return f"https://www.justeat.it/area/{quote_plus(city.lower())}"

def _url_ryanair_airports() -> str:
    return "https://www.ryanair.com/api/views/locate/5/airports/it/active"

def _url_ryanair_farfnd(origin: str, dest: str, out: str, ret: str) -> str:
    return (
        f"https://www.ryanair.com/api/farfnd/3/oneWayFares"
        f"?departureAirportIataCode={origin}&arrivalAirportIataCode={dest}"
        f"&outboundDepartureDateFrom={out}&outboundDepartureDateTo={ret}"
    )


# ---------------------------------------------------------------------------
# Intent detection — classifica la query senza LLM
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("meteo",   [r"\bmeteo\b", r"\btemperatura\b", r"\bpioggia\b", r"\bnuvol", r"\bprevisioni\b", r"\bweather\b"]),
    ("crypto",  [r"\bbitcoin\b", r"\bbtc\b", r"\bethereum\b", r"\beth\b", r"\bcrypto\b", r"\bcoin\b", r"\bprezzo\b.*\b(crypto|btc|eth)\b"]),
    ("voli",    [r"\bvolo\b", r"\baereo\b", r"\bryanair\b", r"\bflight\b", r"\bpartenza\b.*\barrivo\b"]),
    ("wiki",    [r"\bwikipedia\b", r"\bcos[''è]\b", r"\bchi è\b", r"\bstoria di\b", r"\bspiega\b"]),
    ("archwiki",[r"\barch\b.*\bwiki\b", r"\bpacman\b", r"\baur\b", r"\barchlinux\b"]),
    ("pypi",    [r"\bpypi\b", r"\bpip3?\s+install\b", r"\bpacchetto python\b"]),
    ("npm",     [r"\bnpm\b", r"\bnodejs\b", r"\bnode\.js\b", r"\bjavascript package\b"]),
    ("aur",     [r"\baur\b", r"\byay\b", r"\bparu\b"]),
    ("github",  [r"\bgithub\.com/\S+", r"\brepo\b.*\bgithub\b"]),
    ("amazon",  [r"\bamazon\b", r"\bcomprare\b", r"\bprezzo\b.*\bprodotto\b"]),
    ("adafruit",[r"\badafruit\b", r"\bcomponente\b.*\belettronico\b"]),
    ("pkg",     [r"\binstalla\b", r"\bapt\b", r"\bdnf\b", r"\bpacchetto\b"]),
    ("cibo",    [r"\bricetta\b", r"\bcucina\b", r"\bingredient", r"\bpiatto\b"]),
    ("generico",[r".*"]),
]


def _detect_intent(query: str) -> str:
    q = query.lower()
    for intent, patterns in _INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, q):
                return intent
    return "generico"


# ---------------------------------------------------------------------------
# Handler per categoria
# ---------------------------------------------------------------------------

def _handle_wiki(query: str, lang: str = "it") -> str:
    data = _fetch_json(_url_wikipedia(query, lang))
    if not data:
        return ""
    results = data.get("query", {}).get("search", [])
    if not results:
        return "Nessun risultato su Wikipedia."
    top = results[0]
    snippet = re.sub(r"<[^>]+>", "", top.get("snippet", ""))
    return f"Wikipedia — {top['title']}: {snippet}"


def _handle_archwiki(query: str) -> str:
    html = _fetch(_url_archwiki(query))
    if not html:
        return ""
    text = _html_to_text(html, max_chars=1200)
    return f"Arch Wiki: {text}" if text else ""


def _handle_crypto(query: str) -> str:
    # Cerca il simbolo nella query
    coins = {
        "bitcoin": ("bitcoin", "BTCUSDT"),
        "btc":     ("bitcoin", "BTCUSDT"),
        "ethereum":("ethereum","ETHUSDT"),
        "eth":     ("ethereum","ETHUSDT"),
        "solana":  ("solana",  "SOLUSDT"),
        "sol":     ("solana",  "SOLUSDT"),
    }
    q = query.lower()
    coin_id, binance_pair = "bitcoin", "BTCUSDT"
    for keyword, (cid, bp) in coins.items():
        if keyword in q:
            coin_id, binance_pair = cid, bp
            break

    # Prova Binance prima
    data = _fetch_json(_url_binance(binance_pair.replace("USDT", "")))
    if data and "price" in data:
        price = float(data["price"])
        return f"{coin_id.capitalize()} — ${price:,.2f} USD (Binance)"

    # Fallback CoinGecko
    data = _fetch_json(_url_coingecko(coin_id))
    if data and coin_id in data:
        usd = data[coin_id].get("usd", "N/D")
        eur = data[coin_id].get("eur", "N/D")
        return f"{coin_id.capitalize()} — ${usd} USD / €{eur} EUR"

    return "Impossibile recuperare il prezzo crypto."


def _parse_pypi(pkg: str) -> str:
    data = _fetch_json(_url_pypi(pkg))
    if not isinstance(data,dict):
        return ""
    info = data.get("info", {})
    if not isinstance(info,dict) or not info.get('name') or not info.get('version'):return ''
    name    = info.get("name", pkg)
    version = info.get("version", "?")
    summary = info.get("summary", "")
    return f"PyPI — {name} v{version}: {summary}"


def _registry_package(query,registry):
    """Extract an explicit registry identifier; never use the whole sentence as a slug."""
    if registry=='pypi':
        identifier=r'[A-Za-z0-9][A-Za-z0-9._-]*'
        patterns=[rf'(?:pypi|pip3?\s+install|pacchetto\s+python)\s+({identifier})(?:\s+python)?',
                  rf'({identifier})\s+(?:su|on)\s+pypi',rf'https?://pypi\.org/project/({identifier})/?']
    else:
        identifier=r'(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*'
        patterns=[rf'(?:npm|npm\s+install)\s+({identifier})',rf'({identifier})\s+(?:su|on)\s+npm']
    for pattern in patterns:
        match=re.fullmatch(pattern,query.strip(),re.I)
        if match:return match.group(1)
    return None


def _handle_registry(query,registry):
    package=_registry_package(query,registry)
    if package is None:return _handle_generico(query)
    result=_parse_pypi(package) if registry=='pypi' else _parse_npm(package)
    return result or f'Nessun risultato verificabile su {registry.upper()} per {package}.'


def _parse_npm(pkg: str) -> str:
    data = _fetch_json(_url_npm(pkg))
    if not isinstance(data,dict) or not data.get('name') or not data.get('version'):
        return ""
    name    = data.get("name", pkg)
    version = data.get("version", "?")
    desc    = data.get("description", "")
    return f"npm — {name}@{version}: {desc}"


def _parse_aur(pkg: str) -> str:
    data = _fetch_json(_url_aur_search(pkg))
    if not data or not data.get("results"):
        return ""
    top = data["results"][0]
    return (
        f"AUR — {top['Name']} v{top.get('Version','?')}: "
        f"{top.get('Description','(nessuna descrizione)')}"
    )


def _handle_pkg(query: str) -> str:
    """Identifica il nome pacchetto e cerca su PyPI/AUR/npm."""
    # Estrai il nome del pacchetto dalla query
    pkg = re.sub(
        r"(installa|install|cerca|search|pacchetto|package|pip|npm|aur)\s*",
        "", query, flags=re.I
    ).strip().split()[0] if query.strip() else query

    results = []
    r = _parse_pypi(pkg)
    if r:
        results.append(r)
    r = _parse_aur(pkg)
    if r:
        results.append(r)
    r = _parse_npm(pkg)
    if r:
        results.append(r)
    return "\n".join(results) if results else f"Nessun pacchetto trovato per '{pkg}'."


def _handle_github(query: str) -> str:
    """Cerca un repo GitHub ed estrae il README."""
    match = re.search(r"github\.com/([^/\s]+)/([^/\s]+)", query)
    if match:
        owner, repo = match.group(1), match.group(2)
        text = _fetch(_url_github_raw(owner, repo))
        return _html_to_text(text, 1500) if text else f"README di {owner}/{repo} non trovato."
    return ""


def _handle_cibo(query: str) -> str:
    """Cerca una ricetta su Giallozafferano."""
    url = f"https://www.giallozafferano.it/ricerca-ricette/{quote_plus(query)}/"
    html = _fetch(url)
    return _html_to_text(html, 1200) if html else ""


def _handle_generico(query: str) -> str:
    """Discover source URLs, then extract their relevant data, never a CAPTCHA."""
    from web_scraper import search_links,format_document,blocked_reason,organic_url
    scope_match=re.search(r'\bsite:([a-z0-9.-]+)',query,re.I)
    scope=scope_match.group(1).lower().removeprefix('www.') if scope_match else ''
    def allowed(link):
        target=organic_url(link['url'],'https://www.bing.com');host=(urlparse(target).hostname or '').removeprefix('www.')
        return bool(target and (not scope or host==scope or host.endswith('.'+scope)))
    url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}&kl=it-it"
    html = _fetch(url, headers={"Accept-Language": "it-IT,it;q=0.9"})
    links=[link for link in search_links(html,url) if allowed(link)] if html else []
    # Public RSS endpoint, no API key or Tavily dependency.
    if not links:
        from xml.etree import ElementTree
        raw=_fetch(f'https://www.bing.com/search?format=rss&q={quote_plus(query)}')
        if raw and not blocked_reason(raw):
            try:
                tree=ElementTree.fromstring(raw)
                links=[link for item in tree.findall('.//item') if allowed(link:={'url':item.findtext('link',''),'title':item.findtext('title','')})][:5]
            except ElementTree.ParseError:pass
    outputs=[];failures=[]
    for link in links[:3]:
        if urlparse(link['url']).scheme not in {'http','https'}:continue
        document=WebModule().read_url(link['url'],query)
        if document['status']=='ok':outputs.append(format_document(document)[:2800])
        else:failures.append(document.get('error','Download non riuscito'))
    if outputs:return '\n\n'.join(outputs)
    reason=blocked_reason(html) if html else ''
    return '[Web error] Nessuna fonte verificabile. '+(reason or (failures[0] if failures else 'Motori di ricerca non disponibili. Puoi indicare un URL da leggere direttamente.'))


def _handle_amazon(query):
    from web_scraper import format_document
    match=re.search(r'amazon\.(it|de|fr|es|com|co\.uk)\b',query,re.I)
    domain='amazon.'+(match.group(1).lower() if match else 'it')
    keywords=re.sub(r'\bsite:\S+','',query,flags=re.I)
    keywords=re.sub(r'\b(?:amazon(?:\.[a-z.]+)?|price|prezzo|prezzi|costo|costa|su|per|delle|del|le|il|quanto|cerca|trova)\b',' ',keywords,flags=re.I)
    keywords=' '.join(keywords.split())
    url=f'https://www.{domain}/s?k={quote_plus(keywords)}'
    document=WebModule().read_url(url,keywords+' prezzo')
    if document['status']=='ok':return format_document(document)
    return _handle_generico(re.sub(r'\bsite:\S+','',query)+f' site:{domain}')


# ---------------------------------------------------------------------------
# Analizzatore siti (ex site_analyzer.py — ora interno)
# ---------------------------------------------------------------------------

def _analyze_site(url: str) -> dict:
    """
    Testa un URL e restituisce un dizionario con:
      status, latency_ms, title, description
    Usato internamente — non esposto come API pubblica.
    """
    start = time.time()
    html  = _fetch(url, timeout=10)
    ms    = int((time.time() - start) * 1000)

    if not html:
        return {"url": url, "status": "unreachable", "latency_ms": ms}

    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    desc_m  = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)

    return {
        "url":         url,
        "status":      "ok",
        "latency_ms":  ms,
        "title":       _html_to_text(title_m.group(1), 80) if title_m else "",
        "description": _html_to_text(desc_m.group(1), 200) if desc_m else "",
    }


# ---------------------------------------------------------------------------
# WebModule — unica interfaccia pubblica per JARVIS
# ---------------------------------------------------------------------------

class WebModule:
    """
    Interfaccia web unificata per JARVIS v10.
    Sostituisce completamente SearchModule di search_module.py.

    Uso da jarvis_v10.py:
        web = WebModule(lang="it")
        risposta = web.search("prezzo Bitcoin")
        testo    = web.fetch_url("https://example.com")
    """

    def __init__(self, lang: str = "it"):
        self.lang = lang
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 300.0  # secondi
        self._raw_cache={}

    # ------------------------------------------------------------------
    def _cached(self, key: str) -> str | None:
        if key in self._cache:
            result, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return result
            del self._cache[key]
        return None

    def _store(self, key: str, value: str) -> None:
        self._cache[key] = (value, time.time())

    # ------------------------------------------------------------------
    def search(self, query: str, *, cancel_event=None) -> str:
        token=_CANCEL_EVENT.set(cancel_event)
        try:return self._search(query)
        finally:_CANCEL_EVENT.reset(token)

    def _search(self, query: str) -> str:
        """
        Punto di ingresso principale. Riceve una query in linguaggio naturale,
        rileva l'intento e chiama l'handler appropriato.
        Ritorna una stringa leggibile da JARVIS.
        """
        query = query.strip()
        if not query:
            return ""

        # A user-supplied source goes directly to curl + extractor, not to a search engine.
        direct=re.search(r'https?://[^\s<>"\u00ab\u00bb]+',query)
        if direct:
            from web_scraper import format_document
            url=direct.group().rstrip('.,;)')
            return format_document(self.read_url(url,query.replace(direct.group(),'')))

        cached = self._cached(query)
        if cached:
            return cached

        intent = _detect_intent(query)

        handlers = {
            "wiki":     lambda q: _handle_wiki(q, self.lang),
            "archwiki": _handle_archwiki,
            "crypto":   _handle_crypto,
            "pkg":      _handle_pkg,
            "github":   _handle_github,
            "cibo":     _handle_cibo,
            "aur":      lambda q: _parse_aur(q),
            "pypi":     lambda q: _handle_registry(q,'pypi'),
            "npm":      lambda q: _handle_registry(q,'npm'),
            "generico": _handle_generico,
            # "meteo" e "voli" richiedono API key o gestione dedicata
            "meteo":    lambda q: _handle_generico(f"meteo {q}"),
            "voli":     lambda q: _handle_generico(f"voli {q}"),
            "amazon":   _handle_amazon,
            "adafruit": lambda q: _handle_generico(f"adafruit {q}"),
        }

        handler = handlers.get(intent, _handle_generico)
        try:
            result = handler(query)
        except Exception as e:
            from task_io import TaskCancelled
            if isinstance(e,TaskCancelled):raise
            result = f"[WebModule] Errore durante la ricerca: {e}"

        if result and not result.startswith(('Nessun risultato','Ricerca non disponibile','[WebModule] Errore','[Web ')):
            self._store(query, result)
        return result or "Nessun risultato trovato."

    # ------------------------------------------------------------------
    def read_url(self,url: str,query: str='',*,cancel_event=None) -> dict:
        from web_scraper import extract
        token=_CANCEL_EVENT.set(cancel_event if cancel_event is not None else _CANCEL_EVENT.get())
        try:
            result=extract(_fetch(url),url,query);result['transport']='curl'
            queue=list(result.get('data_sources',[]));seen={url}
            # Read at most three static same-site data sources. Never execute JS or bypass a CAPTCHA.
            if result['status']=='no_data':
                for _ in range(3):
                    if not queue:break
                    source=queue.pop(0)
                    if source in seen:continue
                    seen.add(source);document=extract(_fetch(source),source,query)
                    if document['status']=='ok':return {**document,'discovered_from':url}
                    queue.extend(s for s in document.get('data_sources',[]) if s not in seen)
            return result
        finally:_CANCEL_EVENT.reset(token)

    def curl_fetch(self,url: str,*,find: str='',offset: int=0,max_chars: int=5000,refresh: bool=False,cancel_event=None) -> dict:
        """Bounded raw HTTP evidence: no custom flags, shell, credentials, files or JS execution."""
        from web_scraper import blocked_reason
        if not isinstance(find,str) or len(find)>160:raise ValueError('find deve contenere al massimo 160 caratteri.')
        if isinstance(offset,bool) or not isinstance(offset,int) or not 0<=offset<=_MAX_DOWNLOAD:raise ValueError('Offset non valido.')
        if isinstance(max_chars,bool) or not isinstance(max_chars,int) or not 256<=max_chars<=10000:raise ValueError('max_chars deve essere tra 256 e 10000.')
        if not isinstance(refresh,bool):raise ValueError('refresh deve essere booleano.')
        token=_CANCEL_EVENT.set(cancel_event if cancel_event is not None else _CANCEL_EVENT.get())
        try:
            if _CANCEL_EVENT.get() is not None:
                from task_io import check_cancelled
                check_cancelled(_CANCEL_EVENT.get())
            cached=self._raw_cache.get(url)
            cache_hit=bool(cached and time.monotonic()-cached[0]<60 and not refresh)
            if cache_hit:response=cached[1]
            else:
                response=_fetch_response(url)
                if response['status']=='ok' and not blocked_reason(response['body']):
                    if len(self._raw_cache)>=4:self._raw_cache.pop(next(iter(self._raw_cache)))
                    self._raw_cache[url]=(time.monotonic(),response)
            body=response['body'];blocked=blocked_reason(body)
            result={k:v for k,v in response.items() if k!='body'}
            result.update(transport='curl',source='untrusted_web_content',body_chars=len(body),javascript_executed=False,cache_hit=cache_hit)
            if blocked:result.update(status='blocked',error=blocked)
            elif response['status']!='ok':result['error']=f"Download non riuscito: HTTP {response['http_status']}, curl {response['curl_exit_code']}."
            content_type=response['content_type'].lower()
            if content_type and not any(t in content_type for t in ('text/','json','javascript','xml','html')):
                return {**result,'status':'error','error':'Contenuto non testuale; nessuna anteprima.'}
            if find:
                from itertools import islice
                matches=list(islice(re.finditer(re.escape(find),body[offset:],re.I),1001))
                result.update(find=find,match_count=min(1000,len(matches)),match_count_is_lower_bound=len(matches)>1000,
                              matches=[{'offset':offset+m.start(),'text':body[max(0,offset+m.start()-350):offset+m.end()+650]} for m in matches[:5]],truncated=len(matches)>5)
            else:
                excerpt=body[offset:offset+max_chars];next_offset=offset+len(excerpt)
                result.update(offset=offset,body_excerpt=excerpt,truncated=next_offset<len(body),next_offset=next_offset if next_offset<len(body) else None)
            return result
        finally:_CANCEL_EVENT.reset(token)

    def search_result(self,query: str,*,cancel_event=None) -> dict:
        result=self.search(query,cancel_event=cancel_event)
        error=result.startswith(('[Web ','[WebModule] Errore','Nessun risultato','Ricerca non disponibile'))
        return {'status':'error' if error else 'ok','output':result,'transport':'curl','javascript_executed':False}

    def fetch_url(self, url: str, *, analyze: bool = False,cancel_event=None) -> str:
        """
        Scarica e restituisce il testo di un URL.
        Se analyze=True, ritorna anche metadati (titolo, latenza).
        """
        from web_scraper import format_document
        document=self.read_url(url,cancel_event=cancel_event)
        output=format_document(document)
        return ('Stato: '+document['status']+'\n'+output) if analyze else output

    # ------------------------------------------------------------------
    def status(self) -> dict:
        """Ritorna info sullo stato del modulo (cache, ultima ricerca)."""
        return {
            "cache_entries": len(self._cache),
            "cache_ttl_s":   self._cache_ttl,
            "lang":          self.lang,
            "transport":     "curl",
            "extractor":     "html + json + embedded JavaScript data",
            "javascript_executed": False,
        }

    def clear_cache(self) -> None:
        """Svuota la cache delle ricerche."""
        self._cache.clear()
        self._raw_cache.clear()
