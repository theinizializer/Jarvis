#!/usr/bin/env python3
"""
site_analyzer.py — Analizzatore automatico di siti per web_module
Testa ogni sito e produce una scheda: tipo risposta, variabili utili, qualità dato.

Uso:
    python site_analyzer.py                    # testa tutti i siti
    python site_analyzer.py prezzi             # solo categoria prezzi
    python site_analyzer.py voli               # solo categoria voli
    python site_analyzer.py --url https://...  # testa un URL specifico
"""

import re
import json
import subprocess
import sys
import time
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
    BS4 = True
except ImportError:
    BS4 = False

# ══════════════════════════════════════════════════════════════════════════════
# SITI DA TESTARE
# ══════════════════════════════════════════════════════════════════════════════

SITES = {

    "prezzi": [
        # Amazon multi-paese
        {"name": "Amazon IT",  "url": "https://www.amazon.it/s?k=thinkpad+e16",        "expect": "price"},
        {"name": "Amazon DE",  "url": "https://www.amazon.de/s?k=thinkpad+e16",        "expect": "price"},
        {"name": "Amazon FR",  "url": "https://www.amazon.fr/s?k=thinkpad+e16",        "expect": "price"},
        {"name": "Amazon COM", "url": "https://www.amazon.com/s?k=thinkpad+e16",       "expect": "price"},
        {"name": "Amazon ES",  "url": "https://www.amazon.es/s?k=thinkpad+e16",        "expect": "price"},
        # Aggregatori prezzi
        {"name": "Trovaprezzi","url": "https://www.trovaprezzi.it/cerca.aspx?search=thinkpad+e16", "expect": "price"},
        {"name": "Idealo IT",  "url": "https://www.idealo.it/prezzi/thinkpad-e16.html","expect": "price"},
        {"name": "Idealo DE",  "url": "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=thinkpad+e16", "expect": "price"},
        # Shop elettronica
        {"name": "MediaWorld", "url": "https://www.mediaworld.it/it/search.html?searchTerm=thinkpad+e16", "expect": "price"},
        {"name": "Unieuro",    "url": "https://www.unieuro.it/online/search?q=thinkpad+e16", "expect": "price"},
        {"name": "eBay IT",    "url": "https://www.ebay.it/sch/i.html?_nkw=thinkpad+e16", "expect": "price"},
        {"name": "eBay DE",    "url": "https://www.ebay.de/sch/i.html?_nkw=thinkpad+e16", "expect": "price"},
        {"name": "eBay COM",   "url": "https://www.ebay.com/sch/i.html?_nkw=thinkpad+e16", "expect": "price"},
        # Asia / componenti
        {"name": "AliExpress", "url": "https://www.aliexpress.com/wholesale?SearchText=arduino+nano", "expect": "price"},
        {"name": "Adafruit",   "url": "https://www.adafruit.com/search?q=bno085",      "expect": "price"},
        {"name": "LCSC",       "url": "https://www.lcsc.com/search?q=bno085",          "expect": "price"},
        {"name": "Mouser",     "url": "https://www.mouser.it/Search/Refine?Keyword=bno085", "expect": "price"},
    ],

    "voli": [
        # Low cost — API JSON
        {"name": "Ryanair API farfnd", "url": "https://www.ryanair.com/api/farfnd/3/oneWayFares?departureAirportIataCode=CRL&arrivalAirportIataCode=CIA&outboundDepartureDateFrom=2026-10-25&outboundDepartureDateTo=2026-10-27&currency=EUR&adultPaxCount=1", "expect": "flight_json"},
        {"name": "Ryanair API timtbl", "url": "https://www.ryanair.com/api/timtbl/3/schedules/CRL/CIA/years/2026/months/10", "expect": "flight_json"},
        {"name": "Wizz Air API",       "url": "https://be.wizzair.com/15.6.0/Api/search/timetable", "expect": "flight_json"},
        {"name": "EasyJet HTML",       "url": "https://www.easyjet.com/it/voli-economici/bru-fco?outboundDate=2026-10-25", "expect": "flight"},
        # Aggregatori voli
        {"name": "Kayak IT",           "url": "https://www.kayak.it/flights/BRU-FCO/2026-10-25?sort=price_a", "expect": "flight"},
        {"name": "eDreams IT",         "url": "https://www.edreams.it/voli/bruxelles-roma/BRU/FCO/2026-10-25/", "expect": "flight"},
        {"name": "Omio IT",            "url": "https://www.omio.it/voli/bruxelles/roma-fiumicino/2026-10-25", "expect": "flight"},
        {"name": "Skyscanner IT HTML", "url": "https://www.skyscanner.it/rotte/bru/fco/bruxelles-per-roma-fiumicino.html", "expect": "flight"},
        {"name": "FlightConnections",  "url": "https://www.flightconnections.com/flights-from-bru-to-fco", "expect": "flight"},
        {"name": "FlightsFrom",        "url": "https://www.flightsfrom.com/BRU/to-FCO", "expect": "flight"},
    ],

    "tech": [
        # GitHub
        {"name": "GitHub README raw",  "url": "https://raw.githubusercontent.com/DisplayLink/evdi/master/README.md", "expect": "markdown"},
        {"name": "GitHub API repo",    "url": "https://api.github.com/repos/DisplayLink/evdi", "expect": "crypto_json"},
        {"name": "GitHub API releases","url": "https://api.github.com/repos/koalaman/shellcheck/releases/latest", "expect": "crypto_json"},
        # AUR
        {"name": "AUR HTML",           "url": "https://aur.archlinux.org/packages/evdi-dkms", "expect": "text"},
        {"name": "AUR API",            "url": "https://aur.archlinux.org/rpc/v5/info?arg=evdi-dkms", "expect": "crypto_json"},
        # Wiki
        {"name": "ArchWiki",           "url": "https://wiki.archlinux.org/title/DisplayLink", "expect": "text"},
        {"name": "Wikipedia API",      "url": "https://it.wikipedia.org/api/rest_v1/page/summary/Linux", "expect": "crypto_json"},
        {"name": "Wikipedia HTML",     "url": "https://it.wikipedia.org/wiki/Linux", "expect": "text"},
        # Package managers
        {"name": "PyPI API",           "url": "https://pypi.org/pypi/beautifulsoup4/json", "expect": "crypto_json"},
        {"name": "PyPI HTML",          "url": "https://pypi.org/project/beautifulsoup4/", "expect": "text"},
        {"name": "NPM API",            "url": "https://registry.npmjs.org/axios/latest", "expect": "crypto_json"},
        {"name": "Repology",           "url": "https://repology.org/api/v1/project/evdi", "expect": "crypto_json"},
        # Distro specifici
        {"name": "Debian packages",    "url": "https://packages.debian.org/search?keywords=evdi", "expect": "text"},
        {"name": "Ubuntu packages",    "url": "https://packages.ubuntu.com/search?keywords=evdi", "expect": "text"},
        {"name": "Gentoo packages",    "url": "https://packages.gentoo.org/packages/x11-drivers/xf86-video-dummy", "expect": "text"},
        # Stack Overflow / docs
        {"name": "Stack Overflow",     "url": "https://stackoverflow.com/questions/tagged/linux+kernel", "expect": "text"},
        {"name": "MDN Web Docs",       "url": "https://developer.mozilla.org/it/docs/Web/JavaScript", "expect": "text"},
        {"name": "ReadTheDocs",        "url": "https://docs.readthedocs.io/en/stable/", "expect": "text"},
    ],

    "crypto": [
        {"name": "CoinGecko simple",   "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=eur,usd", "expect": "crypto_json"},
        {"name": "CoinGecko BTC full", "url": "https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false", "expect": "crypto_json"},
        {"name": "Binance BTCEUR",     "url": "https://api.binance.com/api/v3/ticker/price?symbol=BTCEUR", "expect": "crypto_json"},
        {"name": "Binance ETHEUR",     "url": "https://api.binance.com/api/v3/ticker/price?symbol=ETHEUR", "expect": "crypto_json"},
        {"name": "Kraken XBTEUR",      "url": "https://api.kraken.com/0/public/Ticker?pair=XBTEUR", "expect": "crypto_json"},
        {"name": "Kraken ETHEUR",      "url": "https://api.kraken.com/0/public/Ticker?pair=ETHEUR", "expect": "crypto_json"},
        {"name": "CoinCap API",        "url": "https://api.coincap.io/v2/assets/bitcoin", "expect": "crypto_json"},
    ],

    "food": [
        {"name": "Uber Eats IT",       "url": "https://www.ubereats.com/it/search?q=pizza&pl=JTdCJTIyYWRkcmVzcyUyMiUzQSUyMk1pbGFubyUyMiUyQyUyMnJlZmVyZW5jZSUyMiUzQSUyMkNoSUpzYVpzaXdoeEpod1JheWt2V2k0QzR3USUyMiUyQyUyMnJlZmVyZW5jZVR5cGUlMjIlM0ElMjJnb29nbGVfcGxhY2VzJTIyJTJDJTIybGF0aXR1ZGUlMjIlM0E0NS40NjQyMDM1JTJDJTIybG9uZ2l0dWRlJTIyJTNBOS4xODk5ODIxJTdE", "expect": "price"},
        {"name": "Deliveroo IT",       "url": "https://deliveroo.it/it/restaurants/milan/milan-city-centre?utm_source=google", "expect": "price"},
        {"name": "JustEat IT",         "url": "https://www.justeat.it/area/milano", "expect": "price"},
        {"name": "TheFork IT",         "url": "https://www.thefork.it/ristoranti/milano-c543871", "expect": "price"},
    ],

}

# ══════════════════════════════════════════════════════════════════════════════
# FETCH
# ══════════════════════════════════════════════════════════════════════════════

HEADERS = [
    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "-H", "Accept: text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "-H", "Accept-Language: it-IT,it;q=0.9,en;q=0.8",
    "-H", "Accept-Encoding: identity",
    "--max-time", "10",
    "--silent",
    "-L",
]

def fetch(url: str) -> tuple[str, int, str]:
    """Ritorna (body, status_code, content_type)"""
    cmd = ["curl"] + HEADERS + ["-w", "\n###STATUS:%{http_code}###CT:%{content_type}###", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = r.stdout
        # Estrai status e content-type dal footer
        m = re.search(r'###STATUS:(\d+)###CT:([^#]*)###', out)
        if m:
            body = out[:m.start()]
            status = int(m.group(1))
            ct = m.group(2).strip()
        else:
            body, status, ct = out, 0, "unknown"
        return body, status, ct
    except Exception as e:
        return "", 0, f"error:{e}"

# ══════════════════════════════════════════════════════════════════════════════
# ANALIZZATORI
# ══════════════════════════════════════════════════════════════════════════════

def detect_response_type(body: str, ct: str) -> str:
    """Rileva tipo di risposta."""
    if not body:
        return "empty"
    b = body.strip()
    if "application/json" in ct or (b.startswith("{") or b.startswith("[")):
        return "json"
    if b.startswith("<!DOCTYPE") or b.startswith("<html") or "<html" in b[:200]:
        return "html"
    if b.startswith("#") or "```" in b[:500]:
        return "markdown"
    if "<" not in b:
        return "text"
    return "html"


def find_price_variables(body: str, resp_type: str) -> dict:
    """Cerca variabili prezzo nel body."""
    found = {}

    if resp_type == "json":
        try:
            data = json.loads(body)
            # Cerca ricorsivamente chiavi con "price"
            def find_keys(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        new_path = f"{path}.{k}" if path else k
                        if any(x in k.lower() for x in ["price","prezzo","cost","amount","value","fare"]):
                            found[new_path] = v
                        find_keys(v, new_path)
                elif isinstance(obj, list) and obj:
                    find_keys(obj[0], f"{path}[0]")
            find_keys(data)
        except Exception:
            pass
        return found

    if resp_type == "html" and BS4:
        soup = BeautifulSoup(body, "html.parser")

        # 1. Attributi data-* con "price"
        for tag in soup.find_all(attrs=True):
            for attr, val in tag.attrs.items():
                if "price" in attr.lower() or "prezzo" in attr.lower():
                    found[f"attr:{attr}"] = str(val)[:80]

        # 2. Classi CSS con "price"
        for tag in soup.find_all(class_=re.compile(r'price|prezzo|cost|amount', re.I)):
            cls = " ".join(tag.get("class", []))
            text = tag.get_text(strip=True)[:60]
            if re.search(r'[\d]+[.,][\d]+', text):
                found[f"class:{cls}"] = text

        # 3. a-offscreen (Amazon)
        for tag in soup.find_all("span", class_="a-offscreen"):
            t = tag.get_text(strip=True)
            if re.search(r'[\d]+[.,][\d]+\s*[€$£]', t):
                found["amazon:a-offscreen"] = t
                break

        # 4. Meta tags prezzo (schema.org)
        for tag in soup.find_all("meta", attrs={"itemprop": re.compile(r'price', re.I)}):
            found[f"meta:itemprop:{tag.get('itemprop')}"] = tag.get("content","")

        # 5. JSON-LD embedded
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(tag.string or "")
                if isinstance(ld, dict) and "offers" in ld:
                    offers = ld["offers"]
                    if isinstance(offers, dict):
                        found["jsonld:price"] = offers.get("price","")
                        found["jsonld:currency"] = offers.get("priceCurrency","")
            except Exception:
                pass

        # 6. Pattern €/$/£ nel testo
        prices_raw = re.findall(r'[\d]+[.,][\d]+\s*[€$£]|[€$£]\s*[\d]+[.,][\d]+', body)
        if prices_raw:
            found["raw_prices"] = prices_raw[:5]

    return found


def find_flight_variables(body: str, resp_type: str) -> dict:
    """Cerca variabili volo nel body."""
    found = {}

    if resp_type == "json":
        try:
            data = json.loads(body)
            def find_keys(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        new_path = f"{path}.{k}" if path else k
                        if any(x in k.lower() for x in
                               ["depart","arriv","flight","price","fare","time","iata","carrier"]):
                            found[new_path] = str(v)[:80]
                        find_keys(v, new_path)
                elif isinstance(obj, list) and obj:
                    find_keys(obj[0], f"{path}[0]")
            find_keys(data)
        except Exception:
            pass
        return found

    if resp_type == "html":
        # Cerca pattern orari
        times = re.findall(r'\b\d{1,2}:\d{2}\b', body)
        if times:
            found["times"] = times[:6]

        # Cerca numeri volo
        flights = re.findall(r'\b[A-Z]{2}\d{3,4}\b', body)
        if flights:
            found["flight_numbers"] = list(set(flights))[:6]

        # Cerca prezzi
        prices = re.findall(r'[\d]+[.,][\d]+\s*€', body)
        if prices:
            found["prices"] = prices[:5]

        # data-e2e Ryanair
        e2e = re.findall(r'data-e2e="([^"]+)"', body)
        if e2e:
            found["data-e2e"] = list(set(e2e))[:10]

    return found


def score_result(found: dict, expect: str, status: int, resp_type: str) -> int:
    """Punteggio qualità 0-10."""
    if status == 0 or not found:
        return 0
    if status in [403, 429, 503]:
        return 0
    if status != 200:
        return 1

    score = 3  # base per risposta 200

    if expect in ["price", "crypto_json"]:
        if "amazon:a-offscreen" in found or "jsonld:price" in found:
            score += 4
        elif any("class:" in k for k in found):
            score += 3
        elif "raw_prices" in found:
            score += 2
        if resp_type == "json" and found:
            score += 2

    elif expect in ["flight", "flight_json"]:
        if resp_type == "json" and found:
            score += 4
        if "flight_numbers" in found:
            score += 2
        if "times" in found and "prices" in found:
            score += 2
        elif "times" in found:
            score += 1

    elif expect == "markdown":
        if resp_type in ["markdown", "text"] and len(found) > 0:
            score += 4

    elif expect == "text":
        if resp_type in ["html", "text"]:
            score += 2

    return min(score, 10)


# ══════════════════════════════════════════════════════════════════════════════
# ANALYZER PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

def analyze_site(site: dict) -> dict:
    """Analizza un sito e ritorna la scheda completa."""
    name   = site["name"]
    url    = site["url"]
    expect = site.get("expect", "text")

    print(f"  Testing {name}...", end=" ", flush=True)
    body, status, ct = fetch(url)
    resp_type = detect_response_type(body, ct)
    from web_scraper import extract,blocked_reason
    query={'price':'prezzo disponibilita','crypto_json':'prezzo','flight':'voli orari prezzi','flight_json':'voli orari prezzi'}.get(expect,'')
    extraction=extract(body,url,query)

    # Trova variabili in base al tipo atteso
    if expect in ["price", "crypto_json"]:
        variables = find_price_variables(body, resp_type)
    elif expect in ["flight", "flight_json"]:
        variables = find_flight_variables(body, resp_type)
    else:
        variables = {"length": len(body), "type": resp_type}

    quality = score_result(variables, expect, status, resp_type)

    # Blocco?
    blocked = False
    if status in [403, 429, 503, 0]:
        blocked = True
    elif blocked_reason(body):
        blocked = True

    result = {
        "name":      name,
        "url":       url,
        "status":    status,
        "ct":        ct[:40],
        "resp_type": resp_type,
        "blocked":   blocked,
        "variables": variables,
        "quality":   quality,
        "body_len":  len(body),
        "expect":    expect,
        "extraction": extraction,
    }

    bar = "█" * quality + "░" * (10 - quality)
    status_str = "🔴 BLOCKED" if blocked else f"🟢 {status}"
    print(f"{status_str}  [{bar}] {quality}/10  ({resp_type}, {len(body)} bytes)")

    return result


def print_report(results: list):
    """Stampa report finale con schede per sito."""
    print("\n" + "═"*70)
    print("📋  REPORT FINALE")
    print("═"*70)

    working   = [r for r in results if not r["blocked"] and r["quality"] >= 5]
    partial   = [r for r in results if not r["blocked"] and 1 <= r["quality"] < 5]
    blocked_l = [r for r in results if r["blocked"]]

    print(f"\n✅ FUNZIONANO BENE ({len(working)}):")
    for r in sorted(working, key=lambda x: -x["quality"]):
        print(f"  {r['quality']:2}/10  {r['name']:25}  {r['resp_type']:8}  "
              f"status:{r['status']}")
        for k, v in list(r["variables"].items())[:3]:
            print(f"         → {k}: {str(v)[:60]}")

    print(f"\n⚠️  PARZIALI ({len(partial)}):")
    for r in sorted(partial, key=lambda x: -x["quality"]):
        print(f"  {r['quality']:2}/10  {r['name']:25}  {r['resp_type']:8}  "
              f"status:{r['status']}")

    print(f"\n🔴 BLOCCATI/ERRORE ({len(blocked_l)}):")
    for r in blocked_l:
        print(f"   0/10  {r['name']:25}  status:{r['status']}  {r['ct'][:30]}")

    # Salva JSON completo
    out_path = "/tmp/site_analysis.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n💾 Report completo salvato in: {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    # Modalità URL singolo
    if "--url" in args:
        idx = args.index("--url")
        url = args[idx+1]
        cat = args[idx+2] if idx+2 < len(args) else "price"
        result = analyze_site({"name": url, "url": url, "expect": cat})
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    # Filtra categorie
    categories = [a for a in args if a in SITES]
    if not categories:
        categories = list(SITES.keys())

    all_results = []
    for cat in categories:
        print(f"\n{'─'*70}")
        print(f"🔍 Categoria: {cat.upper()}")
        print(f"{'─'*70}")
        for site in SITES[cat]:
            result = analyze_site(site)
            result["category"] = cat
            all_results.append(result)
            time.sleep(0.4)

    print_report(all_results)
