"""City of Pittsburgh: bounded, resumable per-URL HTML/PDF collection.

No login, payment or request-submission actions. POST is used ONLY for the
observed read-only OpenCities listing pagination form. No external-site crawl.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup, Tag, Comment

ROOT = Path(__file__).resolve().parents[2]
BASE = 'https://www.pittsburghpa.gov'
SOURCE_URL = BASE + '/Home'
VERSION = 'pittsburgh-city-v1-markdown'
AGENT = 'ANLP-HW2-Research/1.0'
SEEDS = ['Home', 'Resident-Services', 'Recreation-Events', 'City-Government',
         'Safety', 'Business-Development', 'Resident-Services/Trash-Recycling',
         'Resident-Services/311', 'Recreation-Events/CitiParks/Park-Permits/Reserve-Park-Shelter',
         'Business-Development/Permits-Licenses-and-Inspections', 'Recreation-Events/CitiParks',
         'City-Meeting-Hub', 'Safety/Animal-Care-Control/Dog-Licenses',
         'Resident-Services/Trash-Recycling/Collection-Schedule-and-Newsletter',
         'City-Government/Finance-Budget', 'Business-Development/Procurement/Bid-Opportunities',
         'City-Government/The-Mayor/Press-Releases', 'Resident-Services/Road-Maintenance',
         'News-articles', 'Events-directory']
BRANCHES = ('/Resident-Services', '/Recreation-Events', '/City-Government', '/Safety',
            '/Business-Development', '/News-articles', '/Events-directory', '/City-Meeting-Hub')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in {'http', 'https'}:
        return None
    host = (p.hostname or '').lower()
    if host in {'pittsburghpa.gov', 'www.pittsburghpa.gov'}:
        host = 'www.pittsburghpa.gov'
    elif p.port and p.port not in {80, 443}:
        host += ':' + str(p.port)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit(('https' if host == 'www.pittsburghpa.gov' else p.scheme,
                       host, p.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def classify(url):
    p = urlsplit(url)
    if p.hostname != 'www.pittsburghpa.gov':
        return 'external'
    if any(k.lower() in {'lang', 'oc_lang', 'print', 'output'} for k, _ in parse_qsl(p.query)):
        return 'utility'
    path = p.path
    ext = Path(path).suffix.lower()
    if ext == '.pdf':
        return 'pdf'
    if ext in {'.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.css', '.js', '.zip', '.mp4', '.mp3', '.ico'}:
        return 'asset'
    if ext in {'.doc', '.docx', '.xls', '.xlsx', '.csv', '.ics', '.ppt', '.pptx'}:
        return 'attachment'
    if path in {'/', '/Home'} or any(path == b or path.startswith(b + '/') for b in BRANCHES):
        return 'html'
    # Internal public information linked from page content is allowed; utility routes are not.
    if re.search(r'/(?:Search|Login|Logout|Sign-In|Accessibility|Sitemap|Website-Disclaimers|api)(?:/|$)', path, re.I):
        return 'utility'
    if path.startswith('/files/'):
        return 'attachment'
    return 'html'


def text(node):
    value = re.sub(r'[\u200b\ufeff\u00ad]', '', node.get_text(' ', strip=True))
    value = re.sub(r'\s+', ' ', value).strip()
    return re.sub(r'\s+([,.;:!?%])', r'\1', value)


def render_table(table):
    trs = [tr for tr in table.find_all('tr') if tr.find_parent('table') is table]
    grid = {}
    for r, tr in enumerate(trs):
        c = 0
        for cell in tr.find_all(['th', 'td'], recursive=False):
            def span(key):
                raw = str(cell.get(key, 1))
                n = int(raw) if raw.isdigit() else 1
                if key == 'rowspan' and n == 0:
                    n = sum(t.parent is tr.parent for t in trs[r:])
                return min(1000, max(1, n))
            height, width = min(span('rowspan'), len(trs)-r), span('colspan')
            while any((r, c+j) in grid for j in range(width)):
                c += 1
            for i in range(height):
                for j in range(width):
                    grid[r+i, c+j] = (text(cell), cell.name == 'th')
            c += width
    if not grid:
        return ''
    width = max(c for r, c in grid)+1
    rows = [[grid.get((r,c), ('',False)) for c in range(width)] for r in range(len(trs))]
    n = 0
    for row in rows:
        if any(v for v,h in row) and all(h or not v for v,h in row):
            n += 1
        else:
            break
    headers = []
    for c in range(width):
        labels = []
        for row in rows[:n]:
            v = row[c][0]
            if v and (not labels or labels[-1] != v):
                labels.append(v)
        headers.append(' / '.join(labels) or f'Column {c+1}')
    def line(values):
        return '| ' + ' | '.join(v.replace('|', r'\|') for v in values) + ' |'
    caption = table.find('caption', recursive=False)
    parts = [text(caption)] if caption else []
    parts += [line(headers), line(['---']*width)]
    parts += [line([v for v,h in row]) for row in rows[n:] if any(v for v,h in row)]
    return '\n'.join(parts)


def to_markdown(root):
    """Walk DOM once, including accordion/tab bodies and free text in divs."""
    def inline(node):
        return text(node) if isinstance(node, Tag) else re.sub(r'\s+', ' ', str(node))
    def walk(node):
        parts, pending = [], []
        def flush():
            value = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
            if value:
                parts.append(value)
            pending.clear()
        for child in node.children:
            if not isinstance(child, Tag):
                pending.append(str(child))
                continue
            name = child.name
            if name in {'div','section','article','aside','header','details','p','h1','h2','h3','h4','h5','h6','ul','ol','dl','table','blockquote','pre','hr'}:
                flush()
                if re.fullmatch('h[1-6]', name):
                    value = '#' * int(name[1]) + ' ' + text(child)
                elif name == 'table':
                    value = render_table(child)
                elif name in {'ul','ol'}:
                    lines = []
                    start = int(child.get('start', 1)) if str(child.get('start',1)).isdigit() else 1
                    for i, li in enumerate(child.find_all('li', recursive=False)):
                        prefix = f'{start+i}. ' if name == 'ol' else '- '
                        lines0 = walk(li).splitlines()
                        if lines0:
                            lines.append(prefix+lines0[0])
                            lines.extend(' '*len(prefix)+l for l in lines0[1:])
                    value = '\n'.join(lines)
                elif name == 'dl':
                    value = '\n'.join('- '+text(x) for x in child.find_all(['dt','dd'],recursive=False))
                elif name == 'blockquote':
                    value = '\n'.join('> '+l for l in walk(child).splitlines())
                elif name == 'pre':
                    code = child.get_text()
                    fence = '`'*max(3, 1+max([len(x) for x in re.findall(r'`+',code)] or [0]))
                    value = fence+'\n'+code+'\n'+fence
                elif name == 'hr':
                    value = ''
                else:
                    value = walk(child)
                if value.strip():
                    parts.append(value.strip())
            elif name == 'br':
                pending.append('; ')
            elif child.find(['div','p','h2','h3','ul','table']):
                flush()
                parts.append(walk(child))
            else:
                pending.append(inline(child))
        flush()
        return '\n\n'.join(p for p in parts if p)
    return walk(root)


def parse_html(raw, url):
    soup = BeautifulSoup(raw, 'html.parser')
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    root = soup.select_one('#main-content') or soup.select_one('#main')
    if root is None:
        raise ValueError('Missing verified main-content/main container; possible error or JS shell')
    heading = root.find('h1')
    title = text(heading) if heading else text(soup.title) if soup.title else url
    links = []
    # Main text, local sidebar and all five branches in the global menu are discovery inputs.
    containers = [root] + soup.select('#sub-navigation-outter-container')
    for container in containers:
        for a in container.select('a[href]'):
            target = canonical(urljoin(url, a['href']))
            if target:
                links.append({'url':target, 'label':text(a), 'area':'content'})
    for a in soup.select('#nav a[href]'):
        target = canonical(urljoin(url,a['href']))
        if target and urlsplit(target).hostname == 'www.pittsburghpa.gov' and any(
            urlsplit(target).path == b or urlsplit(target).path.startswith(b+'/') for b in BRANCHES):
            links.append({'url':target,'label':text(a),'area':'navigation'})
    listing = bool(root.select_one('.seamless-pagination'))
    page_info = root.select_one('.seamless-pagination-info')
    match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', text(page_info) if page_info else '', re.I)
    page_number, total_pages = (int(match[1]), int(match[2])) if match else (1,1)
    listing_links = [canonical(urljoin(url,a['href'])) for a in root.select('article a[href], .list-item a[href]')]
    fingerprint = sha('\n'.join(sorted(set(filter(None,listing_links))))) if listing_links else sha(text(root))
    published = root.select_one('.published-on')
    dates = [text(t) for t in root.select('time, .published-on, .event-date, .next-date')]
    update = re.search(r'last updated:\s*([^\n]+)',root.get_text('\n'),re.I)
    declared = soup.select_one('link[rel=canonical]')
    # Preserve department contacts in the main body. Remove only actual template containers.
    for element in list(root.select('script,style,noscript,nav,footer,iframe,img,picture,svg,input,select,button,textarea,.seamless-pagination,.seamless-pagination-info,.visuallyhidden,#breadcrumbs-container,#sub-navigation-outter-container,.social-share,.share-this')):
        if element.parent is not None:
            element.decompose()
    body = to_markdown(root)
    if not body.startswith('# '):
        body = '# '+title+'\n\n'+body
    meaningful = re.sub(r'^#{1,6} .*$', '', body, flags=re.M).strip()
    return {'title':title,'content_markdown':body,'links':links,'listing':listing,
            'page':page_number,'total_pages':total_pages,'fingerprint':fingerprint,
            'thin':len(meaningful)<80,'published':text(published) if published and published.parent else None,
            'date_texts':dates,'last_updated':update[1].strip() if update else None,
            'declared_canonical_url':canonical(urljoin(url,declared['href'])) if declared and declared.get('href') else None}


def next_form(raw, url, events_from=None):
    soup = BeautifulSoup(raw, 'html.parser')
    button = soup.select_one('#main-content input[type=submit][value=Search]') if events_from else soup.select_one('.button-next input:not([disabled])')
    if not button or not button.get('name'):
        raise ValueError('Pagination has more pages but no supported Next form control')
    form = button.find_parent('form')
    if form is None:
        raise ValueError('Missing pagination form')
    data = []
    for node in form.select('input[name]'):
        kind = node.get('type','text').lower()
        if node.has_attr('disabled') or kind in {'submit','button','file','image','reset'}:
            continue
        if kind in {'checkbox','radio'} and not node.has_attr('checked'):
            continue
        data.append((node['name'],node.get('value','')))
    for node in form.select('select[name]'):
        options = node.select('option[selected]')
        if not options and not node.has_attr('multiple'):
            options = node.select('option')[:1]
        data.extend((node['name'],o.get('value',text(o))) for o in options)
    if events_from:
        date=datetime.strptime(events_from,'%Y-%m-%d')
        field=next((k for k,v in data if k.endswith('eventDateFrom$NoJsDatePicker')),None)
        if not field:raise ValueError('Missing observed event date filter control')
        data=[(k,date.strftime('%m/%d/%Y') if k==field else
               'False' if k.endswith('eventDateFrom$JavascriptEnabled') else
               events_from if k.endswith('eventDateFrom$JsDatePicker') else
               date.strftime('%m/%d/%Y') if k.endswith('eventDateFrom$JsDatePicker$dateInput') else v) for k,v in data]
    data.append((button['name'],button.get('value','Next')))
    action = canonical(urljoin(url,form.get('action') or url))
    if action != canonical(url):
        raise ValueError('Pagination action leaves its listing URL')
    return data


class Robots:
    """Wildcard-aware rules for this crawler and User-agent:* (longest match)."""
    def __init__(self, raw):
        groups, agents, rules = [], [], []
        for line in raw.splitlines()+['User-agent: END']:
            line = line.split('#',1)[0].strip()
            if ':' not in line:
                continue
            key,value = [v.strip() for v in line.split(':',1)]
            key=key.lower()
            if key=='user-agent':
                if rules:
                    groups.append((agents,rules));agents=[];rules=[]
                agents.append(value.lower())
            elif agents:
                rules.append((key,value))
        specific=[r for a,r in groups if any(x!='*' and x in AGENT.lower() for x in a)]
        chosen=specific or [r for a,r in groups if '*' in a]
        self.rules=[v for group in chosen for v in group]
        self.delay=max([float(v) for k,v in self.rules if k=='crawl-delay' and re.fullmatch(r'\d+(\.\d+)?',v)] or [0])
    def allowed(self,url):
        p=urlsplit(url);path=p.path+('?' + p.query if p.query else '')
        matches=[]
        for kind,value in self.rules:
            if kind not in {'allow','disallow'} or not value:
                continue
            pattern=re.escape(value).replace(r'\*','.*')
            if value.endswith('$'):
                pattern=pattern[:-2]+'$'
            if re.match(pattern,path):
                matches.append((len(value.replace('*','').rstrip('$')),kind=='allow'))
        return max(matches)[1] if matches else True


class Collector:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            all_sources=list(csv.DictReader(f))
            rows=[r for r in all_sources if canonical(r['url'].strip())==SOURCE_URL]
        self.other_sources={canonical(r['url'].strip()) for r in all_sources if canonical(r['url'].strip())!=SOURCE_URL}
        if len(rows)!=1:
            raise ValueError('Expected exactly one City of Pittsburgh Home source')
        self.source=rows[0];name=self.source['source id'].strip()
        if not name or name in {'.','..'} or '/' in name or '\\' in name:
            raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        policy_path=self.out/'filter_policy.json'
        self.filtered_pdfs=policy_path.exists() and json.loads(policy_path.read_text()).get('skip_pdfs',False)
        if self.filtered_pdfs:
            args.skip_pdfs=True
        if self.out.exists() and any(self.out.iterdir()) and not args.resume:
            raise FileExistsError('Output exists; use --resume or a new --output-root')
        self.out.mkdir(parents=True,exist_ok=True)
        for sub in ['raw','markdown','records']:
            (self.out/sub).mkdir(exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']=AGENT
        self.last=0;self.robots=None
        state=self.out/'state.json'
        if args.resume and state.exists():
            data=json.loads(state.read_text());self.queue=deque(data['queue']);self.seen=set(data['seen'])
            self.done=set(data['done']);self.failed=data['failed'];self.fingerprints=data.get('fingerprints',{})
        else:
            self.queue=deque();self.seen=set();self.done=set();self.failed=[];self.fingerprints={}
            for path in SEEDS:self.enqueue(BASE+'/'+path,0,SOURCE_URL)
        if args.retry_failed:
            for task in self.failed:self.queue.append(task)
            self.failed=[]
    def log(self,file,record):
        with (self.out/file).open('a',encoding='utf-8') as f:
            f.write(json.dumps(record,ensure_ascii=False)+'\n')
    def key(self,task):
        return task['url']+'|'+str(task.get('page',1))
    def enqueue(self,url,depth,parent,page=1,previous_raw=None):
        task={'url':url,'depth':depth,'parent_url':parent,'page':page,'previous_raw':previous_raw}
        key=self.key(task)
        if key not in self.seen:
            self.seen.add(key);self.queue.append(task)
    def save(self):
        data={'version':VERSION,'queue':list(self.queue),'seen':sorted(self.seen),'done':sorted(self.done),
              'failed':self.failed,'fingerprints':self.fingerprints}
        temp=self.out/'state.tmp';temp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');temp.replace(self.out/'state.json')
    def fetch(self,url,data=None):
        if self.robots and not self.robots.allowed(url):raise ValueError('robots_disallowed')
        delay=max(self.args.delay,self.robots.delay if self.robots else 0)
        if delay>self.args.max_wait:raise ValueError(f'crawl_delay_exceeds_max_wait:{delay}')
        time.sleep(max(0,delay-(time.monotonic()-self.last)))
        # Redirects are checked individually, including robots, before following.
        for redirect in range(6):
            self.last=time.monotonic()
            with self.session.request('POST' if data is not None else 'GET',url,data=data,
                                      timeout=(10,30),allow_redirects=False,stream=True) as response:
                if response.is_redirect:
                    target=canonical(urljoin(url,response.headers['Location']))
                    if not target or urlsplit(target).hostname!='www.pittsburghpa.gov':raise ValueError('external_redirect:'+str(target))
                    if self.robots and not self.robots.allowed(target):raise ValueError('robots_disallowed_redirect')
                    if response.status_code in {301,302,303}:data=None
                    url=target;time.sleep(delay);continue
                response.raise_for_status()
                chunks=[];size=0;start=time.monotonic()
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>self.args.max_mb*1024*1024:raise ValueError('download_size_limit')
                    if time.monotonic()-start>self.args.download_timeout:raise ValueError('download_time_limit')
                    chunks.append(chunk)
                return b''.join(chunks),url,response.headers.get('Content-Type','')
        raise ValueError('redirect_limit')
    def discover(self,parsed,task):
        for link in parsed['links']:
            target=link['url'];kind=classify(target)
            if self.filtered_pdfs and kind=='pdf':continue
            depth=task['depth']+1
            if link['area']=='navigation':depth=1
            decision='queued'
            if target in self.other_sources:decision='separate_source'
            elif kind not in {'html','pdf'}:decision=kind
            elif kind=='pdf' and self.args.skip_pdfs:decision='pdf_disabled'
            elif depth>self.args.max_depth:decision='depth_limit'
            elif target==SOURCE_URL or target==task['url']:decision='already_seen'
            elif target+'|1' in self.seen:decision='already_seen'
            if decision=='queued':self.enqueue(target,depth,task['url'])
            # Record each discovered target once per parent/decision, no credentials or form payloads.
            record={'parent_url':task['url'],'url':target,'label':link['label'],'area':link['area'],'decision':decision}
            signature=sha(json.dumps(record,sort_keys=True))
            if signature not in self.link_seen:
                self.link_seen.add(signature);self.log('discovered_urls.jsonl',record)
    def store_doc(self,url,title,body,raw_path,task,kind,extra):
        identity=sha(url)[:24]
        md=self.out/'markdown'/f'{identity}.md'
        doc={'doc_id':identity,'title':title,'source_id':self.source['source id'].strip(),
             'source_url':url,'category':self.source['category'].strip(),'document_type':kind,
             'content_markdown':body,'content_hash':sha(body),'pipeline_version':VERSION,
             'fetched_at':now(),'markdown_path':str(md.resolve()),'parent_url':task['parent_url'],
             'sources':[{'source_url':url,'requested_url':task['url'],'raw_path':str(raw_path.resolve()),
                         'source_id':self.source['source id'].strip(),'acquisition_method':'http'}],**extra}
        md.write_text(body+'\n',encoding='utf-8')
        record=self.out/'records'/f'{identity}.json'
        record.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    def process(self,task):
        url=task['url'];data=None
        if self.args.skip_pdfs and classify(url)=='pdf':return 'pdf_disabled'
        if task['previous_raw']:
            data=next_form(Path(task['previous_raw']).read_bytes(),url)
        raw,final,ctype=self.fetch(url,data)
        if urlsplit(url).path=='/Events-directory' and task['page']==1:
            form_data=next_form(raw,url,self.args.events_from)
            raw,final,ctype=self.fetch(url,form_data)
            verified=BeautifulSoup(raw,'html.parser').select_one('input[name$="eventDateFrom$NoJsDatePicker"]')
            expected=datetime.strptime(self.args.events_from,'%Y-%m-%d').strftime('%m/%d/%Y')
            if not verified or verified.get('value')!=expected:
                raise ValueError('Event start date filter was not retained by server')
        is_pdf='pdf' in ctype.lower() or raw.startswith(b'%PDF')
        if is_pdf and self.args.skip_pdfs:return 'pdf_disabled'
        identity=sha(url)[:24]+(f"_page_{task['page']:04d}" if task['page']>1 else '')
        path=self.out/'raw'/(identity+('.pdf' if is_pdf else '.html'))
        path.write_bytes(raw)
        if is_pdf:
            if self.args.skip_pdfs:return 'pdf_disabled'
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(raw));parts=[];empty=[]
            for i,page in enumerate(reader.pages,1):
                content=(page.extract_text(extraction_mode='layout') or '').strip()
                if content:parts.append(f'## Page {i}\n\n'+content)
                else:empty.append(i)
            if not parts:raise ValueError('pdf_no_text_requires_ocr')
            title=Path(urlsplit(final).path).name
            body='# '+title+'\n\n'+'\n\n'.join(parts)
            self.store_doc(final,title,body,path,task,'pdf',{'quality_flags':['pdf_layout_not_structured_table']+(['empty_pdf_pages'] if empty else []),'empty_pages':empty})
            return 'document_pdf'
        if 'html' not in ctype.lower():raise ValueError('unsupported_content_type:'+ctype)
        parsed=parse_html(raw,final)
        if parsed['listing']:
            if parsed['page']!=task['page']:raise ValueError(f"pagination_page_mismatch:expected{task['page']}:got{parsed['page']}")
            previous=self.fingerprints.setdefault(url,{})
            if any(value==parsed['fingerprint'] and page!=str(task['page']) for page,value in previous.items()):
                raise ValueError('pagination_repeated_content')
            previous[str(task['page'])]=parsed['fingerprint']
        self.discover(parsed,task)
        if parsed['listing'] and parsed['page']<parsed['total_pages']:
            if parsed['page']>=self.args.max_listing_pages:
                self.log('coverage_issues.jsonl',{'url':url,'reason':'listing_page_limit','page':parsed['page'],'total_pages':parsed['total_pages']})
            else:
                self.enqueue(url,task['depth'],task['parent_url'],task['page']+1,str(path.resolve()))
        if parsed['listing']:
            return 'listing_discovered'  # Avoid mixing hundreds of listing excerpts into a document.
        if parsed['thin']:
            self.log('coverage_issues.jsonl',{'url':url,'reason':'thin_page_check_browser_or_manual','raw_path':str(path)})
            return 'thin_page'
        kind='event' if '/Events-directory/' in final else 'news' if '/News-articles/' in final else 'meeting' if '/City-Meeting-Hub/' in final else 'city_information'
        self.store_doc(final,parsed['title'],parsed['content_markdown'],path,task,kind,
                       {k:parsed[k] for k in ['published','date_texts','last_updated','declared_canonical_url']})
        return 'document_html'
    def run(self):
        self.link_seen=set()
        self.save()
        raw,_,_=self.fetch(BASE+'/robots.txt');self.robots=Robots(raw.decode('utf-8','replace'))
        (self.out/'robots.txt').write_bytes(raw)
        count=0
        try:
            while self.queue and count<self.args.max_pages:
                task=self.queue.popleft();count+=1
                print(f"[{count}/{self.args.max_pages} max; {len(self.queue)} queued] page {task['page']} {task['url']}",flush=True)
                start=time.monotonic()
                try:
                    status=self.process(task);self.done.add(self.key(task))
                except KeyboardInterrupt:
                    self.queue.appendleft(task);raise
                except Exception as exc:
                    status='failed';self.failed.append(task)
                    self.log('failures.jsonl',{'url':task['url'],'page':task['page'],'reason':str(exc),'time':now()})
                    print('  failed:',str(exc),flush=True)
                self.log('crawl_log.jsonl',{'url':task['url'],'page':task['page'],'status':status,'elapsed_s':round(time.monotonic()-start,2),'time':now()})
                self.save()
                print(' ',status,flush=True)
        finally:
            self.save()
            records=sorted((self.out/'records').glob('*.json'))
            temp=self.out/'documents.tmp'
            with temp.open('w',encoding='utf-8') as f:
                for p in records:f.write(p.read_text(encoding='utf-8').strip()+'\n')
            temp.replace(self.out/'documents.jsonl')
            summary={'documents':len(records),'processed_this_run':count,'pending':len(self.queue),
                     'failed_tasks':len(self.failed),'queue_exhausted':not self.queue,
                     'coverage_note':'Queue exhaustion is not proof of complete website coverage. Inspect coverage_issues, discovered_urls and failures.',
                     'limits':vars(self.args),'time':now()}
            (self.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(f"Saved {len(records)} documents; {len(self.queue)} pending; {len(self.failed)} failed. {self.out}",flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--retry-failed',action='store_true')
    p.add_argument('--max-pages',type=int,default=3000,help='Request tasks per invocation, including listing pages; remaining queue is saved')
    p.add_argument('--max-depth',type=int,default=8)
    p.add_argument('--max-listing-pages',type=int,default=300)
    p.add_argument('--delay',type=float,default=1.0)
    p.add_argument('--max-wait',type=float,default=30)
    p.add_argument('--max-mb',type=int,default=30)
    p.add_argument('--download-timeout',type=float,default=120)
    p.add_argument('--skip-pdfs',action='store_true')
    p.add_argument('--events-from',default='2026-10-01',help='Start date for the public events listing, YYYY-MM-DD')
    args=p.parse_args()
    if min(args.max_pages,args.max_listing_pages,args.max_depth,args.max_mb,args.download_timeout)<=0 or args.delay<0:
        p.error('Limits must be positive and delay nonnegative')
    if args.retry_failed and not args.resume:p.error('--retry-failed requires --resume')
    try:datetime.strptime(args.events_from,'%Y-%m-%d')
    except ValueError:p.error('--events-from must be YYYY-MM-DD')
    Collector(args).run()


if __name__=='__main__':main()
