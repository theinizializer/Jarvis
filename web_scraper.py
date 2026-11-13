"""Query-focused extraction from downloaded HTML/JSON/JavaScript data.

No browser, model, paid search service, or JavaScript execution is involved.
Every selected value retains its document path; website text is untrusted data.
"""
from __future__ import annotations

import ast
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import urljoin, urlparse, parse_qs,urldefrag

MAX_PARSE=8*1024*1024


def words(text):
    text=unicodedata.normalize('NFKD',str(text).lower())
    text=''.join(c for c in text if not unicodedata.combining(c))
    text=re.sub(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])',' ',text)
    return set(re.findall(r'[a-z][a-z0-9]+|\d+',text))


STOP=words('a al alla alle con da del della delle di e il in la le lo nel nella nelle o per su un una che come quale quali quanto cerca trova informazioni informazione sito pagina dammi dimmi mostra estrai prodotto prodotti modello risultati dai dati about and for from the this with please search information product products show extract find get data')
GROUPS=[words(s) for s in (
    'prezzo prezzi price pricing cost costo amount currency valuta euro eur usd offers offerte fare tariffa',
    'volo voli flight departure arrival airport flightnumber partenza arrivo orario time date data',
    'versione version release aggiornamento latest',
    'disponibile disponibilita availability stock inventory instock',
    'autore author publisher editore',
)]
NOISE=re.compile(r'cookie|consent|tracking|analytics|advert|telemetry|password|secret|csrf|token|session|nonce|translation|localization|i18n|webpack|manifest|bundles?|chunks?|refinement|facet|price[-_]?filter',re.I)
COMMON=words('name title nome titolo description descrizione price currency priceCurrency offers version author availability flightNumber departureDate arrivalDate')
PRICE_WORDS=words('price prezzo prezzi pricing cost costo costa amount fare tariffa')
PRICE_NOISE=re.compile(r'translation|filter|facet|refinement|slider|shipping|delivery|discount|coupon|saving|percent|installment|a-text-price|(?:low|high|min|max|old|was|list|reference)[-_]?price',re.I)
MONEY=re.compile(r'(?:[€$£]|\b(?:EUR|USD|GBP)\b)\s*\d(?:[\d.,\u00a0 ]*\d)?|\d(?:[\d.,\u00a0 ]*\d)?\s*(?:[€$£]|EUR\b|USD\b|GBP\b)',re.I)
ACCESSORY=re.compile(r'\b(?:custodia|cavo|cable|cover|adapter|adattator\w*|case|carrying|portaocchiali|naselli|nose\s+pads|pellicola|mount|dock|panno|replacement|paraluce|parasole|copertura|sound\s+tube|tubo\s+sonoro|silicone|light\s+shield|ear\s+pads)\b',re.I)


def accessory_mismatch(name,entities):
    compatibility=re.search(r'\b(?:per|for|compatibil\w*\s+con)\b',str(name),re.I)
    digits={e for e in entities if e.isdigit()}
    if compatibility and digits and not digits<=words(str(name)[:compatibility.start()]):return True
    accessory=ACCESSORY.search(str(name)[:160])
    if not accessory or any(ACCESSORY.search(e) for e in entities):return False
    main=re.search(r'\b(?:occhiali|(?:AR|XR)?\s*glasses|headset|visore)\b',str(name),re.I)
    return main is None or accessory.start()<main.start()


def price_requested(query):return bool(words(query)&PRICE_WORDS)


def junk_field(key,value=None):
    if NOISE.search(str(key)) or re.search(r'\.(?:js|css|map)(?:\?|$)',str(key),re.I):return True
    return isinstance(value,str) and bool(re.match(r'https?://',value) and re.search(r'\.(?:js|css|map)(?:\?|$)',value,re.I))


def identity_matches(text,entities):
    actual=words(text);digits={e for e in entities if e.isdigit()}
    if digits and not digits<=actual:return False
    variants=entities&words('air pro max plus ultra mini lite')
    if variants and not variants<=actual:return False
    names={e for e in entities if len(e)>=4 and not e.isdigit()}
    return not names or any(e in actual or e in re.sub(r'\W','',str(text).lower()) for e in names)


def terms(query):
    wanted=words(query)-STOP
    for group in GROUPS:
        if wanted & group:wanted |= group
    return wanted


def blocked_reason(body):
    sample=body[:100000].lower()
    patterns=('please complete the following challenge','select all squares containing',
              'verify you are human','verify that you are human','access denied',
              'unusual traffic from your computer','enable javascript and cookies to continue')
    if any(p in sample for p in patterns):return 'Il sito richiede una verifica anti-bot; nessun dato verificabile estratto.'
    if '<title>just a moment' in sample or 'id="challenge-form"' in sample:
        return 'Il sito richiede una verifica anti-bot; nessun dato verificabile estratto.'
    return ''


class Page(HTMLParser):
    HIDDEN={'style','noscript','svg','iframe','template','nav','footer','header','aside','form','button'}
    BLOCKS={'p','li','h1','h2','h3','h4','td','tr','pre','div','article','section','main'}
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.blocks=[];self.facts=[];self.scripts=[];self.links=[];self.data_sources=[];self.products=[];self.primary_prices=[];self.title=''

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);parent=self.stack[-1] if self.stack else None
        hidden=(parent and parent['hidden']) or tag in self.HIDDEN
        descriptor=' '.join([tag,attrs.get('id',''),attrs.get('class',''),attrs.get('itemprop','')])
        if NOISE.search(descriptor) or attrs.get('aria-hidden')=='true':hidden=True
        if re.search(r'\b(?:sponsored|advertisement|ad|ads|breadcrumb|refinement|price-filter)\b|navFooter|nav-main|nav-belt|nav-global|footer',descriptor,re.I):hidden=True
        node={'tag':tag,'attrs':attrs,'hidden':bool(hidden),'parts':[],'descriptor':descriptor,'titles':[],'prices':[],'price_seen':False}
        if tag=='script' and attrs.get('src') and not NOISE.search(attrs['src']):self.data_sources.append(attrs['src'])
        if tag=='meta':
            key=attrs.get('itemprop') or attrs.get('property') or attrs.get('name','')
            if key and attrs.get('content') and not NOISE.search(key):
                self.facts.append((f'meta:{key}',attrs['content']))
        if not hidden:
            for key,value in attrs.items():
                if key.startswith('data-') and not NOISE.search(key) and value:
                    self.facts.append((f'{descriptor}@{key}',value[:400]))
            if tag=='img' and attrs.get('alt'):
                for ancestor in self.stack:
                    if not ancestor['hidden']:ancestor['parts'].append(attrs['alt'])
        if tag in self.VOID:return
        self.stack.append(node)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)

    def handle_data(self,data):
        if not self.stack:return
        if self.stack[-1]['tag']=='script':
            self.stack[-1]['parts'].append(data);return
        if self.stack[-1]['hidden']:return
        for node in self.stack:
            if node['tag']!='script' and sum(map(len,node['parts']))<30000:node['parts'].append(data)

    def handle_endtag(self,tag):
        indices=[i for i,n in enumerate(self.stack) if n['tag']==tag]
        if not indices:return
        i=indices[-1];closing=self.stack[i:];self.stack=self.stack[:i]
        for node in reversed(closing):self.finish(node)

    def finish(self,node):
        value=' '.join(' '.join(node['parts']).split())
        tag=node['tag'];attrs=node['attrs']
        if tag=='script':
            if not NOISE.search(attrs.get('id','')):self.scripts.append((attrs.get('id') or attrs.get('type') or 'inline', ''.join(node['parts'])))
        elif not node['hidden']:
            if tag=='a' and attrs.get('href'):
                self.links.append({'url':attrs['href'],'title':(value or attrs.get('aria-label') or attrs.get('title',''))[:240],'class':attrs.get('class','')})
            if not value:return
            if tag=='title':self.title=value[:200]
            price_class=attrs.get('class','')
            is_price=bool(re.search(r'(?:^|\s)(?:a-offscreen|a-price|price)(?:\s|$)',price_class) or attrs.get('itemprop')=='price')
            if is_price and not node['price_seen'] and not re.search(r'a-text-price|list-price|old-price|shipping|delivery',node['descriptor'],re.I):
                primary=bool(re.search(r'corePrice|priceToPay|priceblock',node['descriptor'],re.I));owner=False;rejected=False
                for parent in reversed(self.stack):
                    if PRICE_NOISE.search(parent['descriptor']):rejected=True;break
                    if re.search(r'corePrice|priceToPay|priceblock',parent['descriptor'],re.I):primary=True
                    if re.search(r'(?:^|\s)a-price(?:\s|$)',parent['attrs'].get('class','')):parent['price_seen']=True
                    if parent['attrs'].get('data-asin') or 'schema.org/Product' in parent['attrs'].get('itemtype',''):
                        parent['prices'].extend(m.group().strip() for m in MONEY.finditer(value));owner=True;break
                if primary and not owner and not rejected:self.primary_prices.extend(m.group().strip() for m in MONEY.finditer(value))
            if tag in {'h1','h2','h3'}:
                for parent in reversed(self.stack):
                    if parent['attrs'].get('data-asin') or 'schema.org/Product' in parent['attrs'].get('itemtype',''):
                        parent['titles'].append(value);break
            if attrs.get('data-asin') or 'schema.org/Product' in attrs.get('itemtype',''):
                self.products.append({'id':attrs.get('data-asin',''),'name':' '.join(node['titles']) or value[:250],'text':value[:2500],'prices':list(dict.fromkeys(node['prices']))})
            if tag in self.BLOCKS:self.blocks.append((node['descriptor'],value[:5000]))
            if attrs.get('itemprop') or re.search(r'price|prezzo|amount|flight|version',node['descriptor'],re.I):
                self.facts.append((node['descriptor'],value[:500]))

    def close(self):
        super().close()
        for node in reversed(self.stack):self.finish(node)
        self.stack=[]


def object_literal(text):
    """Parse a static JS object/array literal; identifiers as values/code are refused."""
    token=re.compile(r'''\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|[A-Za-z_$][\w$]*|[{}\[\]:,]''')
    out=[];position=0;depth=0
    while position<len(text) and position<400000:
        match=token.match(text,position)
        if not match:return None
        raw=match.group();position=match.end()
        if raw.isspace() or raw.startswith(('//','/*')):continue
        if raw in ('{','['):depth+=1
        if raw in ('}',']'):
            depth-=1
            if out and out[-1]==',':out.pop()
        if raw.startswith("'"):
            try:raw=json.dumps(ast.literal_eval(raw))
            except (ValueError,SyntaxError):return None
        elif re.fullmatch(r'[A-Za-z_$][\w$]*',raw):
            if text[position:].lstrip().startswith(':'):raw=json.dumps(raw)
            elif raw not in {'true','false','null'}:return None
        out.append(raw)
        if depth==0:
            try:return json.loads(''.join(out))
            except (ValueError,RecursionError):return None
    return None


def script_data(script):
    """Decode data, never eval JavaScript. Supports JSON, assignments and JSON.parse literals."""
    decoder=json.JSONDecoder();text=script.strip()
    try:
        value=json.loads(text)
        if isinstance(value,(dict,list)):yield value;return
    except (ValueError,RecursionError):pass
    for match in list(re.finditer(r'(?:=|\breturn)\s*([\[{])',text))[:20]:
        try:
            value,_=decoder.raw_decode(text[match.start(1):])
            if isinstance(value,(dict,list)):yield value
        except (ValueError,RecursionError):
            value=object_literal(text[match.start(1):])
            if isinstance(value,(dict,list)):yield value
    for match in list(re.finditer(r'JSON\.parse\(\s*((?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'))\s*\)',text))[:10]:
        try:
            literal=match.group(1)
            raw=json.loads(literal) if literal.startswith('"') else ast.literal_eval(literal)
            value=json.loads(raw)
            if isinstance(value,(dict,list)):yield value
        except (ValueError,SyntaxError,RecursionError):continue
    variables={}
    for match in list(re.finditer(r'''\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|-?\d+(?:\.\d+)?|true|false|null)\s*(?:;|$)''',text))[:60]:
        if NOISE.search(match.group(1)):continue
        raw=match.group(2)
        try:variables[match.group(1)]=ast.literal_eval(raw) if raw.startswith("'") else json.loads(raw)
        except (ValueError,SyntaxError,RecursionError):continue
    if variables:yield variables


def json_facts(data,wanted,source,entities=None,pricing=False):
    """Bounded traversal of all records, preserving a path rather than only array[0]."""
    found=[];visited=0
    entities=set(entities or set())
    # A requested field (e.g. sku, requires_python) is not a product/entity name.
    pending=[data];scanned=0;keys=set()
    while pending and scanned<5000:
        obj=pending.pop();scanned+=1
        if isinstance(obj,dict):
            for k,v in obj.items():
                keys |= words(k)
                if isinstance(v,(dict,list)) and not junk_field(k):pending.append(v)
        elif isinstance(obj,list):pending.extend(obj[:200])
    entities-={key for key in keys if not key.isdigit()}
    text_wanted={word for word in wanted if not word.isdigit()}
    def walk(obj,path,depth=0,inherited=False):
        nonlocal visited
        visited+=1
        if visited>5000 or depth>24:return
        if isinstance(obj,dict):
            scalars=[(k,v) for k,v in obj.items() if not isinstance(v,(dict,list)) and v is not None and not junk_field(k,v)]
            context=words(' '.join(str(v) for _,v in scalars))
            identity=words(' '.join(str(v) for k,v in scalars if words(k)&words('name title nome titolo model modello')))
            if pricing and accessory_mismatch(' '.join(str(v) for k,v in scalars if words(k)&words('name title nome titolo model modello')),entities):return
            if entities and identity and not identity_matches(' '.join(str(v) for k,v in scalars if words(k)&words('name title nome titolo model modello')),entities):return
            entity_hit=bool((context|words(path))&entities)
            if entities and identity and not identity&entities and not inherited:return
            price_parent=pricing and bool(re.search(r'price|prezzo|cost|amount|fare',path,re.I))
            relevant=(not wanted or price_parent or bool(context&text_wanted) or any(words(k)&text_wanted for k,_ in scalars))
            if relevant:
                for k,v in scalars:
                    key=words(k)
                    if pricing and (PRICE_NOISE.search(path+'.'+k) or not (re.search(r'price|prezzo|cost|amount|fare|name|title|nome|model|brand|asin|sku|stock|availab|currency',k,re.I) or price_parent and k.lower() in {'value','text','display','formatted'})):continue
                    # Include identity/currency beside a matching price or other requested field.
                    if not wanted or price_parent or key&text_wanted or (key&COMMON) or (words(v)&text_wanted):
                        value=str(v)[:450]
                        if value:found.append({'field':f'{path}.{k}','value':v if isinstance(v,(int,float,bool)) else value,'source':source})
            for k,v in obj.items():
                if isinstance(v,(dict,list)) and not junk_field(k):walk(v,f'{path}.{k}',depth+1,inherited or entity_hit)
        elif isinstance(obj,list):
            for i,v in enumerate(obj[:200]):walk(v,f'{path}[{i}]',depth+1,inherited)
    walk(data,'$')
    return found[:100]


def extract(body,url,query='',max_chars=6500):
    """Return a compact source-attributed document, or a truthful blocked/empty status."""
    base={'url':url,'source':'downloaded_web_data','javascript_executed':False}
    if not body.strip():return {**base,'status':'error','error':'Pagina vuota o download non riuscito.'}
    blocked=blocked_reason(body)
    if blocked:return {**base,'status':'blocked','error':blocked}
    wanted=terms(query);entities=(words(re.sub(r'\bsite:\S+','',query))-STOP)-COMMON-set().union(*GROUPS)-words('amazon ebay shop store official ufficiale site it com de fr net org')
    pricing=price_requested(query)
    facts=[];passages=[];title='';links=[];kind='text';data_sources=[]
    try:data=json.loads(body)
    except (ValueError,RecursionError):data=None
    if isinstance(data,(dict,list)):
        kind='json';facts=json_facts(data,wanted,'json',entities,pricing)
    elif re.search(r'<(?:!doctype|html|head|body|script|main|article|div|p)\b',body[:10000],re.I):
        kind='html';page=Page();page.feed(body[:MAX_PARSE]);page.close();title=page.title
        for source,script in page.scripts:
            for data in script_data(script):facts.extend(json_facts(data,wanted,'script:'+source,entities,pricing))
            data_sources.extend(re.findall(r'''(?:fetch|axios\.get)\(\s*["']([^"']+)["']''',script))
        data_sources.extend(page.data_sources)
        for field,value in page.facts:
            if not wanted or words(field+' '+str(value))&wanted:
                facts.append({'field':field,'value':value,'source':'html'})
        ranked=sorted(page.blocks,key=lambda b: (len(words(' '.join(b))&wanted),b[0].startswith(('main','article')),min(len(b[1]),1500)),reverse=True)
        seen=set()
        for descriptor,value in ranked:
            if wanted and not words(descriptor+' '+value)&wanted:continue
            value=value[:1200]
            if value in seen or any(value in old for old in seen):continue
            seen.add(value);passages.append(value)
            if len(passages)>=4:break
        if pricing and page.products:
            products=[p for p in page.products if identity_matches(p['name'],entities) and not accessory_mismatch(p['name'],entities)]
            direct=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)',url)
            if direct:
                products=[p for p in products if p['id']==direct.group(1)]
                if page.primary_prices and identity_matches(page.title,entities):
                    products.insert(0,{'id':direct.group(1),'name':page.title,'text':page.title+' '+' '.join(page.primary_prices),'prices':list(dict.fromkeys(page.primary_prices))})
            passages=[p['text'] for p in products[:4]]
            facts=[f for f in facts if f['source']!='html']
            for product in products[:8]:
                amounts=product['prices']
                if not amounts:
                    amounts=[m.group().strip() for m in MONEY.finditer(product['text']) if not re.search(r'consegna|spedizione|shipping|delivery',product['text'][max(0,m.start()-65):m.start()],re.I)]
                for amount in amounts:
                    fact={'field':f"product[{product['id']}].price",'value':amount,'source':'html_product','product':product['name']}
                    if (urlparse(url).hostname or '').endswith(('amazon.it','amazon.de','amazon.fr','amazon.es','amazon.com','amazon.co.uk')) and re.fullmatch(r'[A-Z0-9]{10}',product['id']):
                        fact['product_url']=urljoin(url,'/dp/'+product['id'])
                    facts.append(fact)
        elif pricing and page.title and any(e.isdigit() for e in entities) and any(e.isdigit() for e in words(page.title)) and not identity_matches(page.title,entities):
            facts=[];passages=[]
        for link in page.links:
            candidate=urljoin(url,link['url'])
            if urlparse(candidate).scheme in {'http','https'} and not NOISE.search(link['title']):
                if organic_url(candidate,url):links.append({'url':candidate,'title':link['title']})
    else:
        for data in script_data(body[:MAX_PARSE]):facts.extend(json_facts(data,wanted,'javascript_data',entities,pricing))
        data_sources.extend(re.findall(r'''(?:fetch|axios\.get)\(\s*["']([^"']+)["']''',body[:MAX_PARSE]))
        if facts:kind='javascript'
        elif not urlparse(url).path.endswith(('.js','.mjs')):
            blocks=re.split(r'\n\s*\n|\n',body)
            passages=sorted((b.strip()[:1200] for b in blocks if b.strip() and (not wanted or words(b)&wanted)),key=lambda b:len(words(b)&wanted),reverse=True)[:4]
    if pricing:
        facts=[f for f in facts if not PRICE_NOISE.search(f['field'])]
        facts.sort(key=lambda f:not bool(re.search(r'price|prezzo|cost|amount|fare',f['field'],re.I)))
        passages=[p for p in passages if MONEY.search(p) and not re.search(r'\b(?:filter|filtra|slider|intervallo)\b',p,re.I)]
    unique=[];seen=set();budget=0
    for fact in facts:
        key=(fact['field'],str(fact['value']),fact['source'])
        if key in seen:continue
        size=len(json.dumps(fact,ensure_ascii=False))
        if budget+size>max_chars-1400:continue
        unique.append(fact);seen.add(key);budget+=size
        if len(unique)>=35:break
    remaining=max(0,max_chars-budget-800)
    passages=[p[:min(remaining//max(1,len(passages)),1200)] for p in passages if remaining>0]
    sources=[]
    for candidate in data_sources:
        candidate=urljoin(url,candidate);p=urlparse(candidate)
        if p.scheme in {'https','http'} and p.netloc==urlparse(url).netloc and not NOISE.search(candidate) and candidate not in sources:sources.append(candidate)
    evidence=[f for f in unique if re.search(r'price|prezzo|cost|amount|fare',f['field'],re.I) and (isinstance(f['value'],(float,int)) and not isinstance(f['value'],bool) or MONEY.search(str(f['value'])) or re.fullmatch(r'\d+(?:[.,]\d+)?',str(f['value']).strip()))]
    found=bool(evidence) if pricing else bool(unique or passages)
    links.sort(key=lambda link:('/dp/' not in link['url'] and '/gp/product/' not in link['url'],-len(words(link['title'])&wanted)))
    result={**base,'status':'ok' if found else 'no_data','content_type':kind,'title':title,'facts':unique,'passages':passages,'links':links[:8],'data_sources':sources[:4]}
    if pricing:result['price_evidence']=evidence
    if result['status']=='no_data':result['error']=('Nessun prezzo numerico pertinente trovato. ' if pricing else 'Nessun dato pertinente nel contenuto scaricato. ')+'La pagina potrebbe caricare i dati tramite JavaScript; curl non esegue gli script.'
    return result


def format_document(document):
    if document['status']!='ok':
        links='\n'.join(f"Link da verificare: {link['url']}" for link in document.get('links',[])[:3])
        return f"[Web {document['status']}] {document.get('error','Nessun dato')}\nFonte: {document['url']}"+ ('\n'+links if links else '')
    lines=[f"Fonte: {document['url']}"]
    if document.get('title'):lines.append('Titolo: '+document['title'])
    for f in document.get('facts',[]):
        product=('Prodotto: '+f['product'][:220]+' · ') if f.get('product') else ''
        lines.append(f"- {product}{f['field']}: {f['value']} ({f['source']})")
        if f.get('product_url'):lines.append('Scheda prodotto: '+f['product_url'])
    lines.extend(document.get('passages',[]))
    return '\n'.join(lines)


def organic_url(href,base):
    target=urljoin(base,href);parsed=urlparse(target)
    if parsed.hostname and ('duckduckgo.com'==parsed.hostname or parsed.hostname.endswith('.duckduckgo.com')) and parsed.path in {'/l/','/l'}:
        target=parse_qs(parsed.query).get('uddg',[''])[0];parsed=urlparse(target)
    host=(parsed.hostname or '').lower()
    if parsed.scheme not in {'http','https'} or not host or parsed.username or parsed.password:return ''
    if any(host==domain or host.endswith('.'+domain) for domain in ('duckduckgo.com','bing.com','google.com')):return ''
    if re.search(r'(?:[?&](?:ad_|click_metadata|adurl)|/aclick|/y\.js)',target,re.I):return ''
    if re.search(r'\.(?:js|css|png|jpg|svg|gif)(?:\?|$)',target,re.I):return ''
    return urldefrag(target)[0]


def search_links(body,url):
    if blocked_reason(body):return []
    page=Page();page.feed(body);page.close();found=[]
    for link in page.links:
        if not any(s in link['class'] for s in ('result-link','result__a')):continue
        target=organic_url(link['url'],url)
        if not target:continue
        if target not in [r['url'] for r in found]:found.append({'url':target,'title':link['title']})
    return found[:5]
