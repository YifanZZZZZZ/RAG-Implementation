"""Visit Pittsburgh: homepage plus three selected modules, depth <= 3.

Only HTTPS www.visitpittsburgh.com is requested. No external redirects or PDFs.
Optional --html-map JSON maps in-scope URLs to browser-saved HTML snapshots.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup, Comment
from city_pittsburgh import to_markdown, Robots

ROOT=Path(__file__).resolve().parents[2]
HOME='https://www.visitpittsburgh.com/'
HOST='www.visitpittsburgh.com'
VERSION='visit-pittsburgh-v1-markdown'
MODULES={"what's coming up",'more fall inspiration!','plan your trip'}
RELATED=re.compile(r'^(related\b|suggested content\b|recommended\b|you (?:may|might) also|more like this)',re.I)


def normalize(url):
    p=urlsplit(url)
    if p.scheme not in {'http','https'} or p.hostname!=HOST or p.port not in {None,443}:
        return None
    if p.username or p.password:return None
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
           if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid'}]
    return urlunsplit(('https',HOST,p.path or '/',urlencode(sorted(query)),''))


def permitted(url):
    p=urlsplit(url)
    ext=Path(p.path).suffix.lower()
    if ext and ext not in {'.html','.htm'}:return False
    if re.search(r'/(?:search|login|logout|cart|feed|api)(?:/|$)',p.path,re.I):return False
    if any('.pdf' in v.lower() for k,v in parse_qsl(p.query)):return False
    return True


def module(node,main):
    for parent in node.parents:
        if parent is main:break
        classes=parent.get('class',[])
        if any(x in classes for x in ['block','spotlights','mashup']):return parent
    # Fail closed on an unrecognized recommendation wrapper: remove its top-level section.
    parent=node
    while parent.parent is not main and parent.parent is not None:parent=parent.parent
    return parent if parent is not main else node


def parse(raw,url,is_home=False):
    soup=BeautifulSoup(raw,'html.parser')
    main=soup.select_one('main#main') or soup.select_one('main')
    if main is None:raise ValueError('Missing main body: blocked/error page or changed template')
    for comment in main.find_all(string=lambda s:isinstance(s,Comment)):comment.extract()
    removed=[]
    for heading in list(main.find_all(['h2','h3','h4'])):
        if heading.parent is not None and RELATED.match(heading.get_text(' ',strip=True)):
            block=module(heading,main);removed.append(heading.get_text(' ',strip=True));block.decompose()
    for node in list(main.select('script,style,noscript,nav,footer,form,button,input,select,textarea,iframe,img,picture,svg,.ds,.blog-author,.breadcrumbs,.breadcrumb,.social-share,.share-tools,[class*="related-"],[class*="recommended-"]')):
        if node.parent is not None:node.decompose()
    dynamic=[]
    for listing in main.select('.listings'):
        if 'is-loading' in listing.get('class',[]) or not listing.select('a[href]'):
            dynamic.append({'id':listing.get('id'),'reason':'dynamic_listing_requires_rendered_html'})
    containers=[];found=[]
    if is_home:
        for h in main.find_all(['h2','h3']):
            name=h.get_text(' ',strip=True).replace('’',"'").casefold()
            if name in MODULES:
                containers.append((module(h,main),h.get_text(' ',strip=True)));found.append(name)
        if set(found)!=MODULES:raise ValueError('Homepage module headings missing: '+str(sorted(MODULES-set(found))))
    else:containers=[(main,None)]
    links=[]
    for container,label in containers:
        for a in container.select('a[href]'):
            href=a['href'].strip()
            if not href or href.startswith(('#','mailto:','tel:','javascript:')):continue
            absolute=urljoin(url,href);target=normalize(absolute)
            decision='candidate' if target and permitted(target) else 'external_or_non_html'
            links.append({'url':target or absolute,'label':a.get_text(' ',strip=True),'module':label,'decision':decision})
    h1=main.find('h1')
    title=h1.get_text(' ',strip=True) if h1 else soup.title.get_text(' ',strip=True) if soup.title else url
    content=to_markdown(main)
    if not content.startswith('# '):content='# '+title+'\n\n'+content
    body=re.sub(r'^#{1,6} .*$', '',content,flags=re.M).strip()
    if len(body)<80:raise ValueError('Insufficient body content; manual review required')
    return {'title':title,'content_markdown':content,'links':links,'dynamic':dynamic,'removed_sections':removed,'modules':found}


class Crawler:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            matches=[r for r in csv.DictReader(f) if r['url'].strip()==HOME]
        if len(matches)!=1:raise ValueError('Expected exactly one Visit Pittsburgh homepage source')
        self.source=matches[0];name=self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        if self.out.exists() and any(self.out.iterdir()) and not args.resume:raise FileExistsError('Output exists: use --resume or another --output-root')
        self.out.mkdir(parents=True,exist_ok=True)
        for part in ['raw','markdown','records']:(self.out/part).mkdir(exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.last=0;self.robots=None
        self.manual={}
        if args.html_map:
            for url,path in json.loads(args.html_map.read_text()).items():
                target=normalize(url)
                if not target or not permitted(target):raise ValueError('Out-of-scope manual URL: '+url)
                p=Path(path);self.manual[target]=p if p.is_absolute() else args.html_map.parent/p
        state=self.out/'state.json'
        if args.resume and state.exists():
            old=json.loads(state.read_text());self.queue=deque(old['queue']);self.seen=set(old['seen']);self.failures=old['failures']
        else:
            self.queue=deque([{'url':HOME,'depth':0,'parent_url':None,'origin_module':None}]);self.seen={HOME};self.failures=[]
        if args.retry_failed:
            self.queue.extend(self.failures);self.failures=[]
        if args.resume and self.manual:
            pending={task['url'] for task in self.queue}
            for record in (self.out/'records').glob('*.json'):
                doc=json.loads(record.read_text())
                if doc['source_url'] in self.manual and doc['source_url'] not in pending:
                    self.queue.append({'url':doc['source_url'],'depth':doc['depth'],
                                       'parent_url':doc['parent_url'],'origin_module':doc['origin_module']})
                    pending.add(doc['source_url'])
    def log(self,name,value):
        with (self.out/name).open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
    def save(self):
        tmp=self.out/'state.tmp';tmp.write_text(json.dumps({'version':VERSION,'queue':list(self.queue),'seen':sorted(self.seen),'failures':self.failures},ensure_ascii=False));tmp.replace(self.out/'state.json')
    def fetch(self,url,robots=False):
        if not robots and url in self.manual:return self.manual[url].read_bytes(),url,'manual_html'
        for _ in range(6):
            # Check before every request, including every redirect target.
            if normalize(url)!=url or (not robots and not permitted(url)):raise ValueError('Out-of-scope request rejected: '+url)
            if self.robots and not self.robots.allowed(url):raise ValueError('robots_disallowed')
            delay=max(self.args.delay,self.robots.delay if self.robots else 0)
            if delay>30:raise ValueError('robots_delay_exceeds_30s')
            time.sleep(max(0,delay-(time.monotonic()-self.last)));self.last=time.monotonic()
            with self.session.get(url,timeout=(10,30),allow_redirects=False,stream=True) as response:
                if response.is_redirect:
                    target=normalize(urljoin(url,response.headers['Location']))
                    if not target or not permitted(target):raise ValueError('external_or_non_html_redirect_blocked')
                    url=target;continue
                if robots and response.status_code==404:return b'',url,'http'
                response.raise_for_status()
                content_type=response.headers.get('Content-Type','').lower()
                if not robots and 'html' not in content_type:raise ValueError('non_html_response_not_saved:'+content_type)
                chunks=[];size=0;start=time.monotonic()
                for chunk in response.iter_content(65536):
                    if not chunks and chunk.startswith(b'%PDF'):raise ValueError('pdf_response_not_saved')
                    size+=len(chunk)
                    if size>15*1024*1024 or time.monotonic()-start>120:raise ValueError('download_limit')
                    chunks.append(chunk)
                return b''.join(chunks),url,'http'
        raise ValueError('redirect_limit')
    def process(self,task):
        raw,url,method=self.fetch(task['url'])
        if raw.lstrip().startswith(b'%PDF'):raise ValueError('pdf_not_saved')
        identity=hashlib.sha256(url.encode()).hexdigest()[:24]
        raw_path=self.out/'raw'/(identity+'.html');raw_path.write_bytes(raw)
        page=parse(raw,url,task['depth']==0)
        for link in page['links']:
            decision=link['decision'];depth=task['depth']+1
            if decision=='candidate':
                if depth>3:decision='depth_limit'
                elif link['url'] in self.seen:decision='already_seen'
                else:
                    self.seen.add(link['url']);decision='queued'
                    self.queue.append({'url':link['url'],'depth':depth,'parent_url':url,'origin_module':link['module'] or task['origin_module']})
            self.log('discovered_urls.jsonl',{'parent_url':url,'depth':depth,**link,'decision':decision})
        if page['dynamic']:self.log('coverage_issues.jsonl',{'url':url,'issues':page['dynamic']})
        body=page['content_markdown'];md=self.out/'markdown'/(identity+'.md')
        doc={'doc_id':identity,'title':page['title'],'source_id':self.source['source id'].strip(),
             'source_url':url,'category':self.source['category'].strip(),'document_type':'html',
             'content_markdown':body,'content_hash':hashlib.sha256(body.encode()).hexdigest(),
             'depth':task['depth'],'parent_url':task['parent_url'],'origin_module':task['origin_module'],
             'pipeline_version':VERSION,'processed_at':datetime.now(timezone.utc).isoformat(),
             'fetched_at':datetime.now(timezone.utc).isoformat() if method=='http' else None,
             'quality_flags':['dynamic_listing_incomplete'] if page['dynamic'] else [],
             'removed_sections':page['removed_sections'],'markdown_path':str(md.resolve()),
             'sources':[{'source_url':url,'requested_url':task['url'],'raw_path':str(raw_path.resolve()),'acquisition_method':method}]}
        md.write_text(body+'\n',encoding='utf-8')
        (self.out/'records'/(identity+'.json')).write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    def run(self):
        self.save()
        raw,_,_=self.fetch(HOME+'robots.txt',robots=True);self.robots=Robots(raw.decode('utf-8','replace'))
        (self.out/'robots.txt').write_bytes(raw)
        count=0
        try:
            while self.queue and count<self.args.max_pages:
                task=self.queue.popleft();count+=1
                print(f"[{count}/{self.args.max_pages} max; pending {len(self.queue)}] depth={task['depth']} {task['url']}",flush=True)
                try:
                    self.process(task);status='saved'
                except KeyboardInterrupt:
                    self.queue.appendleft(task);raise
                except Exception as exc:
                    status='failed';self.failures.append(task)
                    self.log('failures.jsonl',{'url':task['url'],'reason':str(exc)})
                    print('  failed:',str(exc),flush=True)
                self.log('crawl_log.jsonl',{**task,'status':status});self.save()
        finally:
            self.save();records=sorted((self.out/'records').glob('*.json'))
            temp=self.out/'documents.tmp'
            with temp.open('w',encoding='utf-8') as f:
                for record in records:f.write(record.read_text(encoding='utf-8').strip()+'\n')
            temp.replace(self.out/'documents.jsonl')
            summary={'documents':len(records),'pending':len(self.queue),'failed':len(self.failures),'max_depth':3,
                     'scope':'Homepage body; child links only from the three selected homepage modules. No related modules, PDF downloads or other hosts.',
                     'note':'Review quality_flags and coverage_issues: JS-loaded listings may be incomplete.'}
            (self.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--max-pages',type=int,default=1000)
    p.add_argument('--delay',type=float,default=1)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--retry-failed',action='store_true')
    p.add_argument('--html-map',type=Path)
    args=p.parse_args()
    if args.max_pages<1 or args.delay<0:p.error('Invalid limit/delay')
    if args.retry_failed and not args.resume:p.error('--retry-failed requires --resume')
    Crawler(args).run()


if __name__=='__main__':main()
