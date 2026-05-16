#!/usr/bin/env python3
"""
JARVIS — search_module.py
==========================
Modulo ricerca internet separato da jarvis_v6.py

Architettura:
─────────────────────────────────────────────────
  jarvis_v6.py  ←──importa──  search_module.py
─────────────────────────────────────────────────

Upgrade rispetto a v5.5:
  ok SearXNG locale (Docker) — nessuno scraping fragile
  ok Jina Reader API — legge pagine anche con JavaScript
  ok tenacity — retry intelligente con backoff esponenziale
  ok Intent detection via Ollama LLM — addio regex fragili
  ok Wikipedia API — mantenuta (stabile e gratuita)
  ok Open-Meteo — mantenuta (stabile e gratuita)
  ok ANSA RSS — mantenuta come fallback notizie
  ok Brave Search API — fallback se SearXNG non disponibile
  ok Cache TTL con invalidazione automatica
  ok Timeout e error handling robusti
  ok Link suggeriti dal modello LLM invece di siti fissi hardcoded

Come usarlo in jarvis_v6.py:
─────────────────────────────
  from search_module import SearchModule

  self.search = SearchModule(
      ollama_url="http://localhost:11434/api/chat",
      ollama_model="jarvisQwen",
      searxng_url="http://localhost:8080",
      brave_api_key="...",
      gnews_key="...",
  )

  web_ctx = self.search.get_context_sync(user_msg)

Installazione dipendenze:
─────────────────────────
  pip install tenacity requests
  # SearXNG (raccomandato):
  docker run -d -p 8080:8080 searxng/searxng
"""

import json
import re
import time
import threading
import subprocess
import sys
from urllib.parse import quote_plus
from typing import Optional

import requests

# ─── Auto-install tenacity ────────────────────────────────────────────────────
try:
    from tenacity import (
        retry, wait_exponential, stop_after_attempt,
        retry_if_exception_type,
    )
    TENACITY_OK = True
except ImportError:
    print("📦 Installo tenacity...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "tenacity", "-q",
         "--break-system-packages"],
        capture_output=True
    )
    try:
        from tenacity import (
            retry, wait_exponential, stop_after_attempt,
            retry_if_exception_type,
        )
        TENACITY_OK = True
    except ImportError:
        TENACITY_OK = False
        def retry(*args, **kwargs):
            def decorator(fn): return fn
            return decorator
        def wait_exponential(**kwargs): pass
        def stop_after_attempt(n): pass
        def retry_if_exception_type(t): pass


# ─── Costanti ─────────────────────────────────────────────────────────────────
OLLAMA_DEFAULT_URL   = "http://localhost:11434/api/chat"
OLLAMA_DEFAULT_MODEL = "jarvisQwen"
SEARXNG_DEFAULT_URL  = "http://localhost:8080"
JINA_BASE_URL        = "https://r.jina.ai/"
WIKIPEDIA_IT         = "https://it.wikipedia.org/w/api.php"
WIKIPEDIA_EN         = "https://en.wikipedia.org/w/api.php"
OPEN_METEO_URL       = "https://api.open-meteo.com/v1/forecast"
NOMINATIM_URL        = "https://nominatim.openstreetmap.org/search"
BRAVE_SEARCH_URL     = "https://api.search.brave.com/res/v1/web/search"
GNEWS_URL            = "https://gnews.io/api/v4/"
ANSA_RSS             = "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"
DDG_HTML             = "https://html.duckduckgo.com/html/"

CACHE_TTL   = 300
MAX_CHARS   = 1200
MAX_RESULTS = 4

WMO_CODES = {
    0: "☀️ Soleggiato",           1: "🌤️ Prevalentemente soleggiato",
    2: "⛅ Parzialmente nuvoloso", 3: "☁️ Coperto",
    45: "🌫️ Nebbia",              48: "🌫️ Nebbia gelata",
    51: "🌦️ Pioggerella leggera", 53: "🌦️ Pioggerella moderata",
    55: "🌧️ Pioggerella intensa", 61: "🌧️ Pioggia leggera",
    63: "🌧️ Pioggia moderata",   65: "🌧️ Pioggia forte",
    71: "🌨️ Neve leggera",       73: "🌨️ Neve moderata",  75: "❄️ Neve forte",
    80: "🌦️ Rovesci leggeri",    81: "🌧️ Rovesci moderati",
    82: "⛈️ Rovesci violenti",   95: "⛈️ Temporale",
    96: "⛈️ Temporale con grandine", 99: "⛈️ Temporale forte con grandine",
}


# ══════════════════════════════════════════════════════════════════════════════
# ─── CACHE ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class SearchCache:
    """Cache thread-safe con TTL automatico."""

    def __init__(self, ttl: int = CACHE_TTL):
        self._store: dict[str, tuple[float, str]] = {}
        self._lock  = threading.Lock()
        self._ttl   = ttl

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._store:
                ts, val = self._store[key]
                if time.time() - ts < self._ttl:
                    return val
                del self._store[key]
        return None

    def set(self, key: str, val: str):
        with self._lock:
            self._store[key] = (time.time(), val)

    def clear(self):
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


# ══════════════════════════════════════════════════════════════════════════════
# ─── HTTP CLIENT con RETRY ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class RobustHTTP:
    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent":      "JARVIS/6.0 (assistente personale; Python/requests)",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        })

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )),
        reraise=True,
    )
    def get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 20)
        return self._session.get(url, **kwargs)

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        )),
        reraise=True,
    )
    def post(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 20)
        return self._session.post(url, **kwargs)

    def get_text(self, url: str, **kwargs) -> str:
        try:
            r = self.get(url, **kwargs)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"   problem HTTP GET {url[:60]}: {e}", flush=True)
            return ""

    def get_json(self, url: str, **kwargs) -> dict:
        try:
            r = self.get(url, **kwargs)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"   problem HTTP JSON {url[:60]}: {e}", flush=True)
            return {}


# ══════════════════════════════════════════════════════════════════════════════
# ─── INTENT DETECTOR ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class IntentDetector:
    _RE_WEATHER = re.compile(
        r'\b(meteo|tempo|temperatura|piove|nevica|previsioni?|clima)\b',
        re.IGNORECASE
    )
    _RE_CITY_AFTER = re.compile(
        r'\b(?:meteo|tempo|temperatura)\s+(?:a|in|di|su)\s+'
        r'([A-ZÀ-Ùa-zà-ù][a-zà-ù]{2,}(?:\s[A-ZÀ-Ùa-zà-ù][a-zà-ù]{2,})?)',
        re.IGNORECASE
    )
    _RE_NEWS = re.compile(
        r'\b(notizie|news|ultime\s+notizie|aggiornamenti?|cosa\s+è\s+successo|'
        r'eventi\s+recenti|oggi\s+cosa|ieri\s+cosa|breaking)\b',
        re.IGNORECASE
    )
    _RE_WIKI = re.compile(
        r'\b(chi\s+è|chi\s+era|cos\'è|cosa\s+è|definizione|storia\s+di|'
        r'wikipedia|wiki|inventore|fondatore|nato|morto|capitale|'
        r'scoperto|creato|quando\s+è\s+nato|dove\s+si\s+trova)\b',
        re.IGNORECASE
    )
    _RE_WEB = re.compile(
        r'\b('
        # Italiano — verbi di ricerca + categorie
        r'cerc\w+|ricerc\w+|trov\w+|hotel|albergo|volo|voli|aereo|aerei|'
        r'prezzo|costo|quanto\s+costa|offerta|ristorante|farmacia|orari|'
        r'recensione|migliore|consiglio|dove\s+comprare|acquistare|shop|'
        r'bigliett\w+|prenot\w+|disponibil\w+|economico|economici|basso\s+prezzo|'
        # Francese
        r'trouv\w+|cherch\w+|hôtel|hotel|vol\s+|billet|prix|tarif|coût|'
        r'moins\s+cher|pas\s+cher|low\s+cost|bon\s+marché|offre|'
        r'restaurant|pharmacie|horaires|avis|meilleur|conseil|acheter|'
        r'réserv\w+|réservation|disponible|'
        # Inglese
        r'search|find|look\s+for|flight|flights|price|cost|cheap|affordable|'
        r'best|recommend|buy|shop|store|review|hours|available|book\w*|ticket'
        r')\b',
        re.IGNORECASE
    )

    def __init__(self, ollama_url: str, ollama_model: str):
        self._url   = ollama_url
        self._model = ollama_model
        self._http  = RobustHTTP()

    # Messaggi che NON richiedono mai ricerca — filtrati immediatamente (0ms)
    _RE_NO_SEARCH = re.compile(
        r'^(ciao|salve|hey|hi|hello|buongiorno|buonasera|buonanotte|'
        r'bonjour|bonsoir|bonne\s+nuit|salut|'                           # FR saluti
        r'ok|okay|grazie|prego|perfetto|capito|esatto|bene|benissimo|'
        r'merci|super|parfait|compris|d\x27accord|'                     # FR ack
        r'thanks|thank\s+you|great|got\s+it|sure|'                     # EN ack
        r'si|no|forse|non\s+so|non\s+capisco|ripeti|rileggi|'
        r'jarvis|svegliati|sei\s+l[ìi]|come\s+stai|come\s+va|'
        r'comment\s+vas|comment\s+tu\s+vas|how\s+are\s+you|'
        r'dimmi|ascolta|aspetta|fermati|stop|basta|fine|esci|'
        r'cosa\s+puoi\s+fare|aiuto|help|comandi|cosa\s+sai\s+fare'
        r')[\s\w?!.]*$',
        re.IGNORECASE
    )


    # Comandi operativi → non serve mai cercare su internet
    # Copre forma diretta ("elimina") e indiretta ("puoi eliminare", "vorrei che elimini")
    _RE_OPERATIONAL = re.compile(
        r'\b('
        r'elimin\w*|cancell\w*|rimuov\w*|rimoss\w*|rinomina\w*|'
        r'instal\w*|disinstall\w*|aggiorn\w*|scaric\w*|compil\w*|'
        r'avvi\w*|ferma\w*|riavvi\w*|abilit\w*|disabilit\w*|'
        r'apri\w*|chiudi\w*|esegu\w*|lanci\w*|'
        r'configur\w*|impost\w*|modific\w*|cambi\w*|'
        r'pulisci\w*|svuota\w*|format\w*|backup\w*|ripristin\w*|'
        r'spegn\w*|blocc\w*|sblocc\w*|copi\w*|spost\w*|cre[ai]\w*|'
        r'delete|remove|install|uninstall|update|upgrade|'
        r'start|stop|restart|enable|disable|close|run|'
        r'create|copy|move|rename|upload|'
        r'clean|clear|restore'
        r')\b',
        re.IGNORECASE
    )

    # Saluti da rimuovere all'inizio del messaggio prima di analizzarlo
    _RE_STRIP_GREETING = re.compile(
        r'^(bonjour|bonsoir|salut|ciao|salve|hello|hi|hey|jarvis)[,\s!]*'
        r'(jarvis|peut[- ]tu|peux[- ]tu|pourrais[- ]tu|puoi|potresti|can you|could you)?[,\s!]*',
        re.IGNORECASE
    )

    def detect(self, message: str) -> tuple[str, str]:
        """
        Classificazione SOLO con regex e filtri — zero chiamate a Ollama.
        Ollama deve essere usato esclusivamente dal modello principale.
        """
        msg = message.strip()

        # 1. Rimuovi saluto iniziale prima di analizzare
        #    "bonjour jarvis peut tu trouver un hotel" → "peut tu trouver un hotel"
        msg_clean = self._RE_STRIP_GREETING.sub("", msg).strip()
        # Se dopo aver rimosso il saluto rimane poco → solo saluto
        if len(msg_clean) < 6:
            return ("none", "")

        # 2. Messaggi corti o saluti puri → none immediato
        if self._RE_NO_SEARCH.match(msg_clean):
            return ("none", "")

        # 3. Controlla PRIMA se è una ricerca web — ha priorità sui comandi operativi
        #    "trovami un volo" è web, non un comando operativo
        if self._RE_WEB.search(msg_clean):
            return self._detect_regex(msg_clean)

        # 4. Comandi operativi → none immediato
        if self._RE_OPERATIONAL.search(msg_clean):
            return ("none", "")

        # 5. Regex sul messaggio pulito → risultato immediato
        return self._detect_regex(msg_clean)

    def _detect_llm(self, message: str) -> Optional[tuple[str, str]]:
        prompt = f"""Classifica questa richiesta dell'utente.

Rispondi SOLO con un JSON valido, nessun altro testo:
{{
  "intent": "<weather|news|wikipedia|web|none>",
  "query": "<query ottimizzata per la ricerca, o stringa vuota se intent=none>",
  "city": "<nome città solo per intent=weather, altrimenti stringa vuota>"
}}

Regole:
- weather: domande su meteo, temperatura, previsioni per una città
- news: richiesta di notizie recenti o aggiornamenti
- wikipedia: chi è, cos'è, storia di, definizione, biografie, fatti storici
- web: prezzi, hotel, voli, prodotti, ristoranti, tutto ciò che richiede ricerca pratica
- none: saluti, domande generiche che non richiedono ricerca su internet

Richiesta utente: "{message}"

JSON:"""

        try:
            resp = self._http.post(
                self._url,
                json={
                    "model":    self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":   False,
                    "options":  {
                        "num_predict": 120,
                        "temperature": 0.1,
                        "num_ctx":     512,
                    }
                },
                timeout=12,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "").strip()

            json_match = re.search(r'\{.*?\}', content, re.DOTALL)
            if not json_match:
                return None

            data   = json.loads(json_match.group(0))
            intent = data.get("intent", "none").lower().strip()
            query  = data.get("query", "").strip()
            city   = data.get("city", "").strip()

            if intent not in ("weather", "news", "wikipedia", "web", "none"):
                return None

            if intent == "weather":
                return ("weather", city or query or message)
            if intent == "none":
                return ("none", "")
            return (intent, query or message)

        except Exception as e:
            print(f"   problem IntentDetector LLM: {e}", flush=True)
            return None

    def _detect_regex(self, message: str) -> tuple[str, str]:
        msg = message.strip()

        if self._RE_WEATHER.search(msg):
            m    = self._RE_CITY_AFTER.search(msg)
            city = m.group(1).strip() if m else ""
            return ("weather", city or msg)

        if self._RE_NEWS.search(msg):
            topic_m = re.search(
                r'notizie\s+(?:su|di|sul|sulla|sui|sulle)\s+(.+)',
                msg, re.IGNORECASE
            )
            return ("news", topic_m.group(1).strip() if topic_m else "")

        if self._RE_WIKI.search(msg):
            query = re.sub(
                r'^(chi\s+è|chi\s+era|cos\'è|cosa\s+è|storia\s+di|wikipedia\s+|wiki\s+)',
                '', msg, flags=re.IGNORECASE
            ).strip()
            return ("wikipedia", query or msg)

        if self._RE_WEB.search(msg):
            # Rimuovi frasi introduttive e lascia solo le parole chiave
            query = msg
            # Rimuovi prefissi comuni IT/FR/EN
            query = re.sub(
                r'^(vorrei\s+che\s+mi\s+|potresti\s+|puoi\s+|peux[- ]tu\s+|peut[- ]tu\s+|'
                r'pourrais[- ]tu\s+|can\s+you\s+|could\s+you\s+|please\s+|'
                r'cerca|ricerca|trova|trouv\w+|cherch\w+|find|search|look\s+for)\s*',
                '', query, flags=re.IGNORECASE
            ).strip()
            # Rimuovi "mi/me/moi/moi" iniziale
            query = re.sub(r'^(mi|me|moi)\s+', '', query, flags=re.IGNORECASE).strip()
            # Rimuovi verbi residui dopo il primo strip
            query = re.sub(r'^(trov\w+|trouv\w+|cherch\w+|find|search|get)\s+', '', query, flags=re.IGNORECASE).strip()
            # Rimuovi "un/una/des/les/a/an" iniziale
            query = re.sub(r'^(un|una|des|les|a|an)\s+', '', query, flags=re.IGNORECASE).strip()
            return ("web", query or msg)

        return ("none", "")


# ══════════════════════════════════════════════════════════════════════════════
# ─── PAGE READER ─────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class PageReader:
    def __init__(self):
        self._http = RobustHTTP()

    def read(self, url: str, max_chars: int = MAX_CHARS) -> str:
        text = self._jina_read(url, max_chars)
        if text:
            return text
        return self._raw_read(url, max_chars)

    def _jina_read(self, url: str, max_chars: int) -> str:
        try:
            jina_url = JINA_BASE_URL + url
            headers  = {"Accept": "text/plain", "X-Return-Format": "text"}
            resp     = self._http.get(jina_url, headers=headers, timeout=6)
            resp.raise_for_status()
            text = resp.text.strip()

            if not text or len(text) < 80:
                return ""

            text = re.sub(r'^Title:.*?\n',        '', text, flags=re.MULTILINE)
            text = re.sub(r'^URL Source:.*?\n',    '', text, flags=re.MULTILINE)
            text = re.sub(r'^Markdown Content:\n', '', text, flags=re.MULTILINE)
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            return self._truncate(text, max_chars)

        except Exception as e:
            print(f"   problem Jina Reader {url[:50]}: {e}", flush=True)
            return ""

    def _raw_read(self, url: str, max_chars: int) -> str:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
            resp = self._http.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            html = resp.text

            for tag in ('script', 'style', 'nav', 'footer', 'header', 'aside'):
                html = re.sub(
                    rf'<{tag}[^>]*>.*?</{tag}>', '', html,
                    flags=re.DOTALL | re.IGNORECASE
                )
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()

            if len(text) < 50:
                return ""
            return self._truncate(text, max_chars)

        except Exception as e:
            print(f"   problem Raw read {url[:50]}: {e}", flush=True)
            return ""

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = ""
        for s in sentences:
            if len(result) + len(s) > max_chars:
                break
            result += s + " "
        return (result.strip() + " [...]") if result else text[:max_chars] + " [...]"


# ══════════════════════════════════════════════════════════════════════════════
# ─── SEARCH BACKENDS ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class SearXNGBackend:
    def __init__(self, base_url: str = SEARXNG_DEFAULT_URL):
        self._url  = base_url.rstrip('/')
        self._http = RobustHTTP()
        self._ok   = self._check()

    def _check(self) -> bool:
        try:
            r = self._http.get(f"{self._url}/healthz", timeout=3)
            return r.status_code == 200
        except Exception:
            try:
                r = self._http.get(self._url, timeout=3)
                return r.status_code == 200
            except Exception:
                return False

    @property
    def available(self) -> bool:
        return self._ok

    def search(self, query: str, num: int = MAX_RESULTS) -> list[dict]:
        if not self._ok:
            return []
        try:
            resp = self._http.get(
                f"{self._url}/search",
                params={
                    "q":        query,
                    "format":   "json",
                    "language": "fr-FR,it-IT,en-US",
                    "engines":  "google,bing,duckduckgo",
                },
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])[:num]
            return [
                {"title": r.get("title",""), "url": r.get("url",""), "content": r.get("content","")}
                for r in results if r.get("url")
            ]
        except Exception as e:
            print(f"   problem SearXNG: {e}", flush=True)
            self._ok = False
            return []


class BraveBackend:
    def __init__(self, api_key: str):
        self._key  = api_key
        self._http = RobustHTTP()

    @property
    def available(self) -> bool:
        return bool(self._key)

    def search(self, query: str, num: int = MAX_RESULTS) -> list[dict]:
        if not self._key:
            return []
        try:
            resp = self._http.get(
                BRAVE_SEARCH_URL,
                headers={
                    "Accept":               "application/json",
                    "Accept-Encoding":      "gzip",
                    "X-Subscription-Token": self._key,
                },
                params={
                    "q": query, "count": num,
                    "search_lang": "it", "country": "IT",
                    "text_decorations": False,
                },
                timeout=20,
            )
            resp.raise_for_status()
            results = resp.json().get("web", {}).get("results", [])[:num]
            return [
                {"title": r.get("title",""), "url": r.get("url",""), "content": r.get("description","")}
                for r in results if r.get("url")
            ]
        except Exception as e:
            print(f"   problem Brave Search: {e}", flush=True)
            return []


class DDGFallbackBackend:
    """
    Usa duckduckgo_search se disponibile, altrimenti HTML scraping come fallback.
    Installa con: pip install duckduckgo_search
    """
    def __init__(self):
        self._http = RobustHTTP()
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS
            self._DDGS = DDGS
            self._use_lib = True
        except ImportError:
            self._DDGS = None
            self._use_lib = False

    @property
    def available(self) -> bool:
        return True

    def search(self, query: str, num: int = MAX_RESULTS) -> list[dict]:
        # Metodo 1: libreria duckduckgo_search v8+ (context manager)
        if self._use_lib:
            try:
                results = []
                # region: fr-fr per francese, it-it per italiano, wt-wt per neutro
                region = "fr-fr" if any(w in query.lower() for w in ("luxembourg","france","paris","belgique","suisse","pour","avec","dans","les","des")) else "it-it" if any(c in query for c in "àèìòù") else "wt-wt"
                with self._DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=num, region=region):
                        results.append({
                            "title":   r.get("title", ""),
                            "url":     r.get("href", ""),
                            "content": r.get("body", ""),
                        })
                if results:
                    return results
            except Exception as e:
                print(f"   problem DDGS lib: {e}", flush=True)

        # Metodo 2: HTML scraping (fallback se libreria non disponibile o fallisce)
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,it;q=0.7",
            }
            # kl: it-it / fr-fr / en-us — rileva dalla query o usa en-us come default
            kl = "it-it" if any(c in query for c in "àèìòùéç") else "fr-fr" if any(w in query.lower() for w in ("le","la","les","un","une","des","pour","avec","dans")) else "en-us"
            resp = self._http.post(
                DDG_HTML, data={"q": query, "kl": kl},
                headers=headers, timeout=25
            )
            html = resp.text
            results = []

            # Prova parsing strutturato
            blocks = re.findall(
                r'<div class="result(?:__body)?[^"]*">(.*?)</div>\s*(?:</div>|<div class="result)',
                html, re.DOTALL
            )
            for block in blocks[:num]:
                title_m   = re.search(r'class="result__a[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
                snippet_m = re.search(r'class="result__snippet[^"]*"[^>]*>(.*?)</(?:a|span)>', block, re.DOTALL)
                url_m     = re.search(r'href="(https?://(?!duckduckgo)[^"]+)"', block)
                title   = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()   if title_m   else ""
                snippet = re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip() if snippet_m else ""
                url     = url_m.group(1)                                       if url_m     else ""
                if url and title:
                    results.append({"title": title, "url": url, "content": snippet})

            # Fallback generico: prendi tutti i link con testo
            if not results:
                links = re.findall(
                    r'href="(https?://(?!duckduckgo\.com|duck\.co)[^"]{10,200})"[^>]*>([^<]{15,100})</a>',
                    html
                )
                for url, title in links[:num]:
                    results.append({"title": title.strip(), "url": url, "content": ""})

            return results[:num]

        except Exception as e:
            print(f"   problem DDG scraping: {e}", flush=True)
            return []


# ══════════════════════════════════════════════════════════════════════════════
# ─── SPECIALIZZATI: Wikipedia, Meteo, Notizie ────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class WikipediaSource:
    def __init__(self):
        self._http = RobustHTTP()

    def search(self, query: str, lang: str = "fr") -> str:
        # Rileva lingua dalla query: parole francesi → fr, italiano → it, default en
        if lang == "fr" or any(w in query.lower() for w in ("le ","la ","les ","pour ","avec ","dans ")):
            api_url = "https://fr.wikipedia.org/w/api.php"
        elif lang == "it" or any(c in query for c in "àèìòù"):
            api_url = WIKIPEDIA_IT
        else:
            api_url = WIKIPEDIA_EN
        cached_lang = lang

        try:
            data    = self._http.get_json(api_url, params={
                "action": "query", "list": "search",
                "srsearch": query, "srlimit": 3,
                "format": "json", "utf8": 1,
            })
            results = data.get("query", {}).get("search", [])
            if not results:
                if lang == "it":
                    return self.search(query, lang="en")
                return ""
            title = results[0]["title"]
        except Exception as e:
            return f"problem Wikipedia ricerca: {e}"

        try:
            data    = self._http.get_json(api_url, params={
                "action": "query", "prop": "extracts",
                "exintro": True, "explaintext": True,
                "titles": title, "format": "json", "utf8": 1,
            })
            pages   = data.get("query", {}).get("pages", {})
            page    = next(iter(pages.values()))
            extract = page.get("extract", "").strip()

            if not extract:
                if lang == "it":
                    return self.search(query, lang="en")
                return ""

            if len(extract) > 800:
                sentences = re.split(r'(?<=[.!?])\s+', extract)
                trimmed   = ""
                for s in sentences:
                    if len(trimmed) + len(s) > 800:
                        break
                    trimmed += s + " "
                extract = trimmed.strip() + " [...]"

            url = (
                f"https://{'it' if cached_lang=='it' else 'en'}.wikipedia.org"
                f"/wiki/{quote_plus(title.replace(' ', '_'))}"
            )
            return f"📖 Wikipedia — {title}\n{extract}\n🔗 {url}"

        except Exception as e:
            return f"problem Wikipedia estratto: {e}"


class MeteoSource:
    def __init__(self):
        self._http = RobustHTTP()

    def get(self, city: str) -> str:
        try:
            geo = self._http.get_json(
                NOMINATIM_URL,
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "JARVIS/6.0"},
            )
            if not geo:
                return f"problem Città non trovata: {city}"
            lat  = geo[0]["lat"]
            lon  = geo[0]["lon"]
            nome = geo[0].get("display_name", city).split(",")[0]
        except Exception as e:
            return f"problem Geocoding: {e}"

        try:
            w     = self._http.get_json(OPEN_METEO_URL, params={
                "latitude": lat, "longitude": lon,
                "current":  "temperature_2m,relative_humidity_2m,wind_speed_10m,weathercode",
                "daily":    "temperature_2m_max,temperature_2m_min,weathercode",
                "timezone": "auto", "forecast_days": 3,
            })
            curr  = w.get("current", {})
            temp  = curr.get("temperature_2m", "?")
            umid  = curr.get("relative_humidity_2m", "?")
            vento = curr.get("wind_speed_10m", "?")
            cond  = WMO_CODES.get(curr.get("weathercode", 0), "❓")

            daily    = w.get("daily", {})
            max_t    = daily.get("temperature_2m_max", [])
            min_t    = daily.get("temperature_2m_min", [])
            day_code = daily.get("weathercode", [])
            dates    = daily.get("time", [])

            lines = [
                f"🌍 Meteo — {nome}",
                f"🌡️  Temperatura: {temp}°C",
                f"💧 Umidità: {umid}%",
                f"💨 Vento: {vento} km/h",
                f"☁️  Condizioni: {cond}",
                "",
                "📅 Previsioni 3 giorni:",
            ]
            for i, (d, mx, mn, dc) in enumerate(zip(dates[:3], max_t[:3], min_t[:3], day_code[:3])):
                label    = ["Oggi", "Domani", "Dopodomani"][i] if i < 3 else d
                cond_day = WMO_CODES.get(dc, "?")
                lines.append(f"  {label}: {mn}°↓ {mx}°↑  {cond_day}")

            return "\n".join(lines)
        except Exception as e:
            return f"problem Open-Meteo: {e}"


class NewsSource:
    def __init__(self, gnews_key: str = ""):
        self._key  = gnews_key
        self._http = RobustHTTP()

    def get(self, query: str = "", categoria: str = "general") -> str:
        if self._key:
            result = self._gnews(query, categoria)
            if result:
                return result
        return self._ansa_rss(query)

    def _gnews(self, query: str, categoria: str) -> str:
        try:
            params = {"token": self._key, "lang": "it", "max": 5}
            if query:
                params["q"] = query
                endpoint    = "search"
            else:
                params["topic"] = categoria
                endpoint        = "top-headlines"

            data     = self._http.get_json(f"{GNEWS_URL}{endpoint}", params=params)
            articles = data.get("articles", [])
            if not articles:
                return ""

            parts = [f"📰 Notizie GNews{' — ' + query if query else ''}"]
            for a in articles[:5]:
                parts.append(
                    f"\n• {a.get('title','')}\n"
                    f"  {a.get('description','')[:150]}\n"
                    f"  🔗 {a.get('url','')}"
                )
            return "\n".join(parts)
        except Exception:
            return ""

    def _ansa_rss(self, query: str) -> str:
        try:
            text = self._http.get_text(ANSA_RSS, timeout=15)
            if not text:
                return ""

            items = re.findall(
                r'<item>.*?<title><!\[CDATA\[(.*?)\]\]></title>'
                r'.*?<description><!\[CDATA\[(.*?)\]\]></description>'
                r'.*?<link>(.*?)</link>.*?</item>',
                text, re.DOTALL
            )[:5]

            if not items:
                return ""

            if query:
                q_lower    = query.lower()
                items_filt = [i for i in items if q_lower in i[0].lower() or q_lower in i[1].lower()]
                if items_filt:
                    items = items_filt

            parts = ["📰 Notizie ANSA"]
            for title, desc, link in items:
                clean_desc = re.sub(r'<[^>]+>', '', desc).strip()[:150]
                parts.append(f"\n• {title.strip()}\n  {clean_desc}\n  🔗 {link.strip()}")
            return "\n".join(parts)
        except Exception as e:
            return f"problem ANSA RSS: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# ─── SEARCH MODULE PRINCIPALE ────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

TAVILY_URL = "https://api.tavily.com/search"

class TavilyBackend:
    """Tavily Search API — risultati veri, fatto apposta per AI."""

    def __init__(self, api_key: str = ""):
        self._key  = api_key
        self._http = RobustHTTP()

    @property
    def available(self) -> bool:
        return bool(self._key)

    def search(self, query: str, num: int = MAX_RESULTS) -> list[dict]:
        if not self._key:
            return []
        try:
            r = self._http.post(
                TAVILY_URL,
                json={
                    "api_key":             self._key,
                    "query":               query,
                    "max_results":         num,
                    "include_answer":      True,
                    "include_raw_content": False,
                    "search_depth":        "basic",
                },
                timeout=15,
            )
            data    = r.json()
            results = []
            if data.get("answer"):
                results.append({
                    "title":   "Risposta",
                    "url":     "",
                    "content": data["answer"],
                })
            for item in data.get("results", [])[:num]:
                results.append({
                    "title":   item.get("title", ""),
                    "url":     item.get("url", ""),
                    "content": item.get("content", "") or item.get("snippet", ""),
                })
            return results
        except Exception as e:
            print(f"   problem Tavily: {e}", flush=True)
            return []


class SearchModule:
    """
    Modulo principale di ricerca per JARVIS v6.0.

    Uso da jarvis_v6.py:
    ────────────────────
        from search_module import SearchModule

        self.search = SearchModule(
            ollama_url=OLLAMA_URL,
            ollama_model=self.model,
            searxng_url="http://localhost:8080",
            brave_api_key="",
            gnews_key="",
        )

        web_ctx = self.search.get_context_sync(user_msg)
    """

    def __init__(
        self,
        ollama_url:    str = OLLAMA_DEFAULT_URL,
        ollama_model:  str = OLLAMA_DEFAULT_MODEL,
        searxng_url:   str = SEARXNG_DEFAULT_URL,
        brave_api_key: str = "",
        gnews_key:     str = "",
        tavily_key:    str = "",
    ):
        self._cache   = SearchCache()
        self._reader  = PageReader()
        self._intent  = IntentDetector(ollama_url, ollama_model)
        self._wiki    = WikipediaSource()
        self._meteo   = MeteoSource()
        self._news    = NewsSource(gnews_key)
        self._stats   = {"searches": 0, "cache_hits": 0, "errors": 0}

        self._searxng = SearXNGBackend(searxng_url)
        self._brave   = BraveBackend(brave_api_key)
        self._ddg     = DDGFallbackBackend()
        self._tavily  = TavilyBackend(tavily_key)

        self._print_status()

    # ── Status ────────────────────────────────────────────────────────────────
    def _print_status(self):
        searxng_ok = "ok" if self._searxng.available else "❌ (avvia: docker run -d -p 8080:8080 searxng/searxng)"
        brave_ok   = "ok" if self._brave.available   else "⚠️  (nessuna API key)"
        news_ok    = "ok GNews" if self._news._key   else "ok ANSA RSS"
        print(f"🔍 SearchModule v6.0:")
        print(f"   SearXNG:    {searxng_ok}")
        print(f"   Brave:      {brave_ok}")
        print(f"   DDG:        ok (fallback)")
        tavily_ok = "ok ✅" if self._tavily.available else "❌ (nessuna API key)"
        print(f"   Tavily:     {tavily_ok}")
        print(f"   Jina:       ok (lettura pagine)")
        print(f"   Wikipedia:  ok")
        print(f"   Open-Meteo: ok")
        print(f"   Notizie:    {news_ok}")
        print(f"   Link LLM:   ok (suggeriti dal modello)")

    def status(self) -> str:
        lines = ["🔍 SearchModule v6.0 — stato:"]
        lines.append(f"  SearXNG:    {'ok online'    if self._searxng.available else '❌ offline'}")
        lines.append(f"  Brave:      {'ok configurato' if self._brave.available else '⚠️  non configurato'}")
        lines.append(f"  DDG:        ok fallback")
        lines.append(f"  Jina Reader:ok lettura pagine")
        lines.append(f"  Wikipedia:  ok")
        lines.append(f"  Open-Meteo: ok")
        lines.append(f"  Notizie:    {'GNews ok' if self._news._key else 'ANSA RSS ok'}")
        lines.append(f"  Link LLM:   ok suggeriti dal modello")
        lines.append(f"  Cache:      {self._cache.size()} elementi")
        s = self._stats
        lines.append(f"  Ricerche:   {s['searches']} | Cache hit: {s['cache_hits']} | Errori: {s['errors']}")
        return "\n".join(lines)

    # ── Entry point principale ────────────────────────────────────────────────
    def get_context_sync(self, user_msg: str) -> str:
        """
        Analizza il messaggio, esegue la ricerca appropriata,
        ritorna il contesto da iniettare nel prompt di JARVIS.
        Ritorna stringa vuota se non serve ricerca.
        """
        cached = self._cache.get(f"ctx:{user_msg}")
        if cached:
            self._stats["cache_hits"] += 1
            return cached

        intent, query = self._intent.detect(user_msg)

        if intent == "none":
            return ""

        # Stampa solo se sta effettivamente cercando qualcosa
        self._stats["searches"] += 1
        print(f"   🔍 [{intent}]: {query or user_msg}", flush=True)

        result = ""
        try:
            if intent == "weather":
                result = self._meteo.get(query or user_msg)
            elif intent == "news":
                result = self._news.get(query)
            elif intent == "wikipedia":
                result = self._wiki.search(query or user_msg)
            else:
                result = self._smart_search(query or user_msg)
        except Exception as e:
            self._stats["errors"] += 1
            result = f"problem Ricerca fallita: {e}"

        if result:
            self._cache.set(f"ctx:{user_msg}", result)

        return result

    # ── Link suggeriti dal modello LLM ────────────────────────────────────────
    def _get_booking_links(self, query: str, q_lower: str) -> str:
        """
        Chiede al modello Ollama di suggerire i siti migliori
        per la query specifica invece di usare siti fissi hardcoded.
        Il modello conosce i siti più adatti per ogni tipo di ricerca
        e può suggerire alternative diverse in base al contesto.
        """
        # Mappa categorie → keyword che le attivano
        categorie = {
            "prenotare hotel o alloggi": any(w in q_lower for w in (
                'hotel', 'albergo', 'ostello', 'b&b', 'affittacamere', 'appartamento vacanze'
            )),
            "cercare e acquistare voli aerei": any(w in q_lower for w in (
                'volo', 'aereo', 'biglietto aereo', 'low cost', 'compagnia aerea'
            )),
            "trovare ristoranti o locali": any(w in q_lower for w in (
                'ristorante', 'dove mangiare', 'pizzeria', 'trattoria', 'locale', 'bar', 'pub'
            )),
            "acquistare prodotti online": any(w in q_lower for w in (
                'acquistare', 'comprare', 'shop', 'offerta', 'prezzo migliore',
                'dove comprare', 'online shop'
            )),
            "cercare auto o moto usate": any(w in q_lower for w in (
                'auto usata', 'moto usata', 'macchina usata', 'autosalone'
            )),
            "trovare lavoro": any(w in q_lower for w in (
                'lavoro', 'offerta lavoro', 'assunzione', 'posizione aperta'
            )),
            "prenotare treni o bus": any(w in q_lower for w in (
                'treno', 'trenitalia', 'italo', 'bus', 'pullman', 'biglietto treno'
            )),
        }

        categoria = next((cat for cat, match in categorie.items() if match), None)
        if not categoria:
            return ""  # nessun link extra necessario per questa query

        prompt = f"""L'utente cerca: "{query}"
Categoria: {categoria}

Suggerisci 2-3 siti web REALI e AFFIDABILI più adatti per questa ricerca specifica.
Preferisci siti italiani o europei quando disponibili.
Rispondi SOLO con un JSON valido, nessun altro testo:
{{
  "emoji": "<emoji appropriata alla categoria>",
  "label": "<etichetta breve, es: 'Prenota su' oppure 'Cerca su' oppure 'Acquista su'>",
  "sites": [
    {{"name": "NomeSito", "url": "https://www.esempio.it/search?q={query}"}},
    {{"name": "NomeSito2", "url": "https://www.esempio2.it/cerca/{query}"}}
  ]
}}

Usa URL diretti alla pagina di ricerca quando possibile, non solo la homepage.
Varia i suggerimenti — non sempre gli stessi siti."""

        try:
            resp = self._intent._http.post(
                self._intent._url,
                json={
                    "model":    self._intent._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":   False,
                    "options":  {
                        "num_predict": 250,
                        "temperature": 0.3,
                        "num_ctx":     512,
                    }
                },
                timeout=10,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "").strip()

            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                return ""

            data  = json.loads(json_match.group(0))
            emoji = data.get("emoji", "🔗")
            label = data.get("label", "Vedi anche")
            sites = data.get("sites", [])

            if not sites:
                return ""

            lines = [f"\n\n{emoji} {label}:"]
            for s in sites:
                name = s.get("name", "").strip()
                url  = s.get("url",  "").strip()
                if name and url and url.startswith("http"):
                    lines.append(f"  • {name}: {url}")

            return "\n".join(lines) if len(lines) > 1 else ""

        except Exception as e:
            print(f"   problem Link LLM: {e}", flush=True)
            return ""

    # ── Ricerca web generale ──────────────────────────────────────────────────
    def _ask_model_for_urls(self, query: str) -> list[str]:
        """
        Chiede al modello di suggerire gli URL diretti migliori per questa query.
        Il modello sa già dove cercare voli, hotel, prezzi, notizie, ecc.
        """
        prompt = (
            f"Per questa ricerca: '{query}'\n"
            "Rispondi SOLO con una lista JSON di 3 URL diretti e specifici.\n"
            "Usa siti affidabili (skyscanner.it, booking.com, meteo.it, ecc).\n"
            "Costruisci URL con parametri se possibile.\n"
            "Rispondi SOLO con JSON array: [\"url1\", \"url2\", \"url3\"]"
        )
        try:
            r = requests.post(
                self._intent._url,
                json={
                    "model":   self._intent._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":  False,
                    "options": {"temperature": 0.1, "num_predict": 200},
                },
                timeout=30
            )
            content = r.json().get("message", {}).get("content", "")
            # Estrai array JSON
            m = re.search(r'\[.*?\]', content, re.DOTALL)
            if m:
                urls = json.loads(m.group())
                return [u for u in urls if u.startswith("http")]
        except Exception as e:
            print(f"   ⚠️ URL model error: {e}", flush=True)
        return []

    # Siti affidabili per categoria — il modello va direttamente dove sa trovare info vere
    _SITE_ROUTES = {
        "voli":    ["https://www.skyscanner.it/trasporti/voli/{q}/",
                    "https://www.google.com/travel/flights?q={q}"],
        "hotel":   ["https://www.booking.com/searchresults.it.html?ss={q}",
                    "https://www.tripadvisor.it/Search?q={q}"],
        "meteo":   ["https://wttr.in/{q}?format=3",
                    "https://www.ilmeteo.it/meteo/{q}"],
        "notizie": ["https://news.google.com/search?q={q}&hl=it",
                    "https://www.ansa.it/ricerca/ansait/search.shtml?queryStr={q}"],
        "prezzi":  ["https://www.google.com/search?q={q}+prezzo+2025",
                    "https://www.amazon.it/s?k={q}"],
        "ristoranti": ["https://www.tripadvisor.it/Search?q={q}",
                       "https://www.thefork.it/ricerca?q={q}"],
        "eventi":  ["https://www.eventbrite.it/d/{q}/",
                    "https://www.google.com/search?q={q}+eventi+2025"],
    }

    def _smart_search(self, query: str) -> str:
        """Cerca con Tavily (se disponibile) oppure DDG."""
        # Tavily — API vera, risultati affidabili
        if self._tavily.available:
            print(f"   🔍 Tavily: {query[:60]}", flush=True)
            raw = self._tavily.search(query)
            if raw:
                parts = []
                for r in raw[:5]:
                    title   = r.get("title", "")
                    snippet = r.get("content", "")
                    url     = r.get("url", "")
                    if snippet:
                        line = f"- {title}\n  {snippet[:400]}"
                        if url:
                            line += f"\n  {url}"
                        parts.append(line)
                if parts:
                    return "\n\n".join(parts)

        # Fallback DDG
        print(f"   🔍 DDG: {query[:60]}", flush=True)
        raw = self._ddg.search(query)
        if not raw:
            simplified = self._simplify_query(query)
            raw = self._ddg.search(simplified)
        if not raw:
            return ""

        parts = []
        for r in raw[:5]:
            title   = r.get("title", "")
            snippet = r.get("content", "")
            url     = r.get("url", "")
            if snippet and url:
                parts.append(f"- {title}\n  {snippet[:300]}\n  {url}")

        return "\n\n".join(parts) if parts else ""


    def _web_search(self, query: str) -> str:
        """
        Cerca con il miglior backend disponibile,
        legge il contenuto reale delle pagine trovate,
        poi chiede al modello di suggerire link utili.
        """
        # Scegli backend in ordine di priorità
        if self._searxng.available:
            backend_name = "SearXNG"
            raw_results  = self._searxng.search(query)
        elif self._brave.available:
            backend_name = "Brave"
            raw_results  = self._brave.search(query)
        else:
            backend_name = "DDG"
            raw_results  = self._ddg.search(query)

        # Se DDG non trova niente, riprova con query semplificata
        if not raw_results and backend_name == "DDG":
            simplified = self._simplify_query(query)
            if simplified != query:
                print(f"   🔄 Riprovo con query semplificata: {simplified}", flush=True)
                raw_results = self._ddg.search(simplified)

        if not raw_results:
            print(f"   problem {backend_name}: nessun risultato", flush=True)
            if backend_name != "DDG":
                raw_results = self._ddg.search(query)
            if not raw_results:
                return f"problem Nessun risultato trovato per: '{query}'"

        print(f"   📋 {backend_name}: {len(raw_results)} URL trovati", flush=True)

        # Leggi il contenuto reale delle pagine
        parts = []
        for i, r in enumerate(raw_results[:MAX_RESULTS]):
            url     = r.get("url", "")
            title   = r.get("title", "")
            snippet = r.get("content", "")

            if not url:
                continue

            print(f"   📄 [{i+1}] Leggo: {url[:60]}...", flush=True)
            content = self._reader.read(url)

            if content and len(content) > 80:
                domain = re.search(r'https?://(?:www\.)?([^/]+)', url)
                site   = domain.group(1) if domain else url
                parts.append(
                    f"📌 {title or site}\n"
                    f"   {content}\n"
                    f"   🔗 {url}"
                )
                print(f"   ok [{i+1}] {site} — {len(content)} caratteri", flush=True)
            elif snippet and len(snippet) > 30:
                parts.append(
                    f"📌 {title}\n"
                    f"   {snippet[:400]}\n"
                    f"   🔗 {url}"
                )
                print(f"   ⚠️ [{i+1}] Solo snippet da {backend_name}", flush=True)
            else:
                print(f"   ⚠️ [{i+1}] Nessun contenuto utile", flush=True)

        if not parts:
            links = "\n".join(f"  🔗 {r['url']}" for r in raw_results[:4] if r.get("url"))
            return f"problem Trovati {len(raw_results)} link ma contenuto non leggibile:\n{links}"

        # Link suggeriti dal modello — non più hardcoded
        extra = self._get_booking_links(query, query.lower())

        return (
            f"🌐 Risultati web — '{query}'\n\n"
            + "\n\n".join(parts)
            + extra
        )

    # ── Metodi diretti (per comandi espliciti in JARVIS) ──────────────────────
    def search(self, query: str) -> str:
        """Ricerca web diretta — per il comando 'cerca [query]'."""
        return self._web_search(query)

    def meteo(self, city: str) -> str:
        """Meteo diretto — per il comando 'meteo [città]'."""
        return self._meteo.get(city)

    def wikipedia(self, query: str) -> str:
        """Wikipedia diretto — per il comando 'wiki [argomento]'."""
        return self._wiki.search(query)

    def notizie(self, query: str = "") -> str:
        """Notizie dirette — per il comando 'notizie'."""
        return self._news.get(query)

    def clear_cache(self):
        """Svuota la cache."""
        self._cache.clear()
        return "🗑️ Cache ricerche svuotata"


# ══════════════════════════════════════════════════════════════════════════════
# ─── TEST STANDALONE ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _test():
    """
    Test rapido del modulo:
      python search_module.py
    """
    print("\n" + "=" * 52)
    print("🧪 TEST search_module.py")
    print("=" * 52)

    sm = SearchModule(
        ollama_url=OLLAMA_DEFAULT_URL,
        ollama_model=OLLAMA_DEFAULT_MODEL,
        searxng_url=SEARXNG_DEFAULT_URL,
    )

    test_cases = [
        ("meteo a Milano",            "weather"),
        ("chi è Leonardo da Vinci",   "wikipedia"),
        ("ultime notizie",            "news"),
        ("hotel economici a Roma",    "web"),
        ("ciao come stai",            "none"),
        ("quanto costa un iPhone 15", "web"),
        ("ristoranti a Firenze",      "web"),
        ("voli low cost per Parigi",  "web"),
    ]

    print(f"\n{'─'*52}")
    for msg, expected in test_cases:
        intent, query = sm._intent.detect(msg)
        ok = "✅" if intent == expected else "❌"
        print(f"{ok} '{msg}'")
        print(f"   Intento: {intent} (atteso: {expected}) | Query: '{query}'")

    print(f"\n{'─'*52}")
    print("Test ricerca reale (meteo Roma):")
    result = sm.get_context_sync("che tempo fa a Roma oggi")
    print(result[:300] + "..." if len(result) > 300 else result)

    print(f"\n{'─'*52}")
    print("Test link LLM (hotel Roma):")
    links = sm._get_booking_links("hotel economici a Roma", "hotel economici a roma")
    print(links if links else "  (nessun link generato)")

    print(f"\n{'─'*52}")
    print(sm.status())
    print("=" * 52)


if __name__ == "__main__":
    _test()