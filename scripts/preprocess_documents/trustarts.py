"""Cultural Trust: nine calendar pages and Featured Stories; direct details only."""
import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qs
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from city_pittsburgh import to_markdown
from cmu_signature_events import video_link
from downtown_events import atomic,write_json

ROOT=Path(__file__).resolve().parents[2]
BASE='https://trustarts.org'
ENTRIES={'concerts':BASE+'/calendar?organization_id=1', 'blog':BASE+'/pct_home/blog'}


def canonical(url):
    p=urlsplit(url)
    if p.scheme not in {'http','https'} or p.hostname not in {'www.trustarts.org','trustarts.org'}:return None
    return urlunsplit(('https','trustarts.org',p.path.rstrip('/') or '/',p.query,''))


def checked(raw):
    s=BeautifulSoup(raw,'html.parser')
    if s.title and re.search(r'just a moment|access denied|attention required|403 forbidden',s.title.get_text(),re.I):raise ValueError('Blocked HTML')
    root=s.select_one('main#maincontent,main')
    if root is None:raise ValueError('Main content container missing')
    return s,root


def clean(root):
    for n in list(root.select('script,style,nav,aside,footer,form,input,select,textarea,iframe,video,audio,img,picture,svg,.seat-availability-timer,.notification,.modal,.pagination,.pager,.filter-container,.visually-hidden,.social-share,.related-events,.related-posts,.pso-blog-post-bar,.email-capture,.modal-wrapper,.secondary,.secondary-navigation-wrapper,[role="complementary"]')):
        if n.parent:n.decompose()
    for a in list(root.select('a')):
        label=a.get_text(' ',strip=True)
        if video_link(a.get('href','')) or re.match(r'^(buy|get|book|reserve)\s+tickets|^donate now$|^subscribe( now| to email)?$|^back to stories$|^watch (video|now)|^add to (cart|calendar)',label,re.I):a.decompose()
        else:a.unwrap()
    for button in list(root.select('button')):
        if button.find_parent(['h2','h3','h4','h5','h6','summary']) or button.has_attr('aria-expanded'):button.unwrap()
        else:button.decompose()
    return to_markdown(root).strip()


def featured(raw):
    _,root=checked(raw)
    h=next((n for n in root.find_all(['h1','h2']) if n.get_text(' ',strip=True)=='Featured Stories'),None)
    section=h.find_parent('section') if h else None
    if section is None:raise ValueError('Featured Stories main section not found')
    return section


def parse(raw,kind):
    s,root=checked(raw)
    if kind=='blog':
        article=root.select_one('section.blog-post article.post')
        if article is not None:
            heading=root.select_one('h1.page-title') or root.find('h1')
            if heading is None:raise ValueError('Story title missing')
            title=heading.get_text(' ',strip=True)
            parts=['# '+title]
            for selector in ['.published-date','.authors']:
                n=root.select_one(selector)
                if n:parts.append(n.get_text(' ',strip=True))
            parts.append(clean(article));body='\n\n'.join(parts)
        else:
            root=featured(raw);title='Featured Stories';body='# Featured Stories\n\n'+clean(root)
    else:
        heading=next((h for h in root.find_all('h1') if not h.find_parent(class_=lambda c: c and ('modal' in c or 'email-capture' in c))),None)
        title='Cultural Trust Events Calendar' if root.select_one('article.event') else (heading.get_text(' ',strip=True) if heading else 'Cultural Trust Event')
        body=clean(root)
        if not body.startswith('# '):body='# '+title+'\n\n'+body
    if len(body)<100:raise ValueError('Too little main body content')
    return title,body,{}


def details(raw,kind):
    if kind=='blog':
        root=featured(raw)
        anchors=root.select('h3 a[href]')
    else:
        _,root=checked(raw);anchors=root.select('article.event .title a[href]')
    found=[]
    pattern=r'^/(?:production|event|exhibit)/[^/]+(?:/[^/]+)?$' if kind=='concerts' else r'^/blog/[^/]+$'
    for a in anchors:
        u=canonical(urljoin(BASE,a['href']))
        if u and re.match(pattern,urlsplit(u).path) and u not in found:found.append(u)
    if not found:raise ValueError('No '+kind+' detail links discovered')
    return found


def pagination(raw,kind):
    root=featured(raw) if kind=='blog' else checked(raw)[1]
    pages={1}
    for a in root.select('a[href]'):
        u=canonical(urljoin(BASE,a['href']))
        if not u:continue
        p=urlsplit(u)
        if p.path!=('/calendar' if kind=='concerts' else '/pct_home/blog'):continue
        number=parse_qs(p.query).get('page',['1'])[0]
        if number.isdigit():pages.add(int(number))
    return sorted(pages)


class Collector:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            rows=[r for r in csv.DictReader(f) if canonical(r['url'].strip())==BASE+'/']
        if len(rows)!=1:raise ValueError('Expected one Cultural Trust source row')
        self.source=rows[0];name=self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        manifest=self.out/'collection.json';config={'concert_pages':args.concert_pages,'entries':ENTRIES,'blog_scope':'Featured Stories only; sidebar excluded'}
        if manifest.exists() and (not args.resume or json.loads(manifest.read_text())!=config):raise ValueError('Use --resume with same configuration, or new --output-root')
        write_json(manifest,config)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.rule=None;self.last=0;self.manual={};self.errors=[];self.docs=[];self.selected={};self.stats={};self.browser_page=None
        if args.html_map:self.manual={canonical(u):(args.html_map.parent/Path(p)).resolve() for u,p in json.loads(args.html_map.read_text()).items()}

    def request(self,url):
        time.sleep(max(0,1-(time.monotonic()-self.last)));self.last=time.monotonic()
        return self.session.get(url,timeout=(10,30),allow_redirects=False,stream=True)

    def fetch(self,url):
        for _ in range(6):
            if not canonical(url):raise ValueError('External redirect rejected')
            if self.rule is None:
                with self.request(BASE+'/robots.txt') as r:
                    if r.status_code not in {200,404}:raise ValueError(f'robots HTTP {r.status_code}')
                    self.rule=RobotFileParser();self.rule.parse(r.text.splitlines() if r.status_code==200 else [])
            if not self.rule.can_fetch('ANLP-HW2-Research/1.0',url):raise ValueError('robots_disallowed')
            delay=self.rule.crawl_delay('ANLP-HW2-Research/1.0') or 0
            if delay>30:raise ValueError('robots delay too long')
            time.sleep(max(0,delay-(time.monotonic()-self.last)))
            if self.browser_page is not None and bool(re.match(r'^/(production|event|exhibit)/',urlsplit(url).path)):
                page=self.browser_page
                page.goto(url,wait_until='domcontentloaded',timeout=45000)
                try:
                    page.wait_for_selector('main#maincontent,main',timeout=8000)
                except Exception:
                    print('浏览器尚未显示活动正文。如果出现验证，请在打开的窗口中自行完成。',flush=True)
                    answer=input('完成并看到活动正文后按 Enter；输入 s 跳过本页：').strip().lower()
                    if answer=='s':raise ValueError('Browser page skipped by user')
                final=canonical(page.url)
                if final is None or not re.match(r'^/(production|event|exhibit)/',urlsplit(final).path):
                    raise ValueError('Browser is not on an event detail page')
                # Do not accept another event if the user navigated elsewhere.
                if urlsplit(url).path.rstrip('/')!=urlsplit(final).path.rstrip('/'):
                    raise ValueError('Browser event ID does not match requested event')
                raw=page.content().encode('utf-8')
                checked(raw)
                return raw
            with self.request(url) as r:
                if r.is_redirect:
                    url=urljoin(url,r.headers['Location']);continue
                if r.status_code>=400:
                    excerpt=next(r.iter_content(8192),b'').decode('utf-8','replace')
                    write_json(self.out/'diagnostics'/(hashlib.sha256(url.encode()).hexdigest()[:20]+'.json'),{'url':url,'status':r.status_code,'body_excerpt':excerpt})
                    r.raise_for_status()
                if 'html' not in r.headers.get('Content-Type','').lower():raise ValueError('Non-HTML response')
                chunks=[];size=0;start=time.monotonic()
                for chunk in r.iter_content(65536):
                    size+=len(chunk)
                    if size>20*1024*1024 or time.monotonic()-start>90:raise ValueError('Download limit')
                    chunks.append(chunk)
                return b''.join(chunks)
        raise ValueError('Redirect limit')

    def load(self,url,kind):
        path=self.out/kind/'raw'/(hashlib.sha256(url.encode()).hexdigest()[:24]+'.html')
        if url in self.manual:raw=self.manual[url].read_bytes()
        elif self.args.resume and path.exists():raw=path.read_bytes()
        else:raw=self.fetch(url)
        checked(raw);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        return raw,path

    def error(self,kind,url,exc):
        self.errors.append({'kind':kind,'url':url,'reason':str(exc)})
        print('  FAILED:',url,str(exc),flush=True)

    def save_doc(self,raw,url,kind,role,path):
        if role=='detail' and kind=='blog' and checked(raw)[1].select_one('section.blog-post article.post') is None:
            raise ValueError('Expected story body missing')
        title,body,extra=parse(raw,kind)
        identity=hashlib.sha256(url.encode()).hexdigest()[:24];folder=self.out/kind
        doc={'doc_id':identity,'title':title,'source_id':self.source['source id'].strip(),'category':self.source['category'].strip(),
             'source_url':url,'section':kind,'role':role,'document_type':'html','content_markdown':body,
             'content_hash':hashlib.sha256(body.encode()).hexdigest(),'raw_path':str(path.resolve()),
             'pipeline_version':'trustarts-sections-v1','processed_at':datetime.now(timezone.utc).isoformat(),**extra}
        atomic(folder/'markdown'/(identity+'.md'),body+'\n');write_json(folder/'records'/(identity+'.json'),doc)
        self.docs.append(doc)

    def run(self):
        for kind,entry in ENTRIES.items():
            policy=self.out/'concert_policy.json'
            if kind=='concerts' and policy.exists() and json.loads(policy.read_text()).get('mode')=='manual_listings_only':
                docs=[json.loads(line) for line in (self.out/'concerts/documents.jsonl').read_text().splitlines() if line.strip()]
                self.docs.extend(docs)
                self.stats[kind]={'mode':'manual_listings_only','documents':len(docs),'detail_required':False,'complete':True,'coverage':'saved listing cards only'}
                print('Events: reusing offline listing documents; no detail requests.',flush=True)
                continue
            print('\nSECTION '+kind,flush=True)
            wanted=[];pages=[1];seen=set();page_sets=set();listing_ok=0
            try:
                raw,path=self.load(entry,kind)
                if kind=='concerts':
                    detected=pagination(raw,kind)
                    if max(detected)!=self.args.concert_pages:raise ValueError(f'Expected {self.args.concert_pages} calendar pages; found {detected}')
                    pages=list(range(1,self.args.concert_pages+1))
                else:pages=pagination(raw,kind) if kind=='blog' else [1]
                while pages:
                    n=pages.pop(0)
                    if n in seen:continue
                    if len(seen)>=100:raise ValueError('Listing pagination safety limit')
                    seen.add(n);url=entry if n==1 else entry+('&' if '?' in entry else '?')+f'page={n}'
                    if n!=1:raw,path=self.load(url,kind)
                    links=details(raw,kind);signature=tuple(sorted(links))
                    if signature in page_sets:raise ValueError('Repeated listing page; pagination failed')
                    page_sets.add(signature)
                    for u in links:
                        if u not in wanted:wanted.append(u)
                    if kind=='blog':pages.extend(x for x in pagination(raw,kind) if x not in seen)
                    self.save_doc(raw,url,kind,'listing',path);listing_ok+=1
                    print(f'  Listing {n}: {len(links)} details',flush=True)
            except Exception as exc:self.error(kind,entry,exc)
            self.selected[kind]=wanted;success=0;blocked=0
            for i,url in enumerate(wanted,1):
                print(f'  Detail {i}/{len(wanted)}: {url}',flush=True)
                try:
                    raw,path=self.load(url,kind);self.save_doc(raw,url,kind,'detail',path);success+=1;blocked=0
                except Exception as exc:
                    self.error(kind,url,exc)
                    if isinstance(exc,requests.HTTPError) and exc.response.status_code in {403,429}:blocked+=1
                    if blocked>=3:
                        self.error(kind,url,ValueError('Stopped section after 3 consecutive access blocks; remaining URLs saved for resume/manual import'));break
            self.stats[kind]={'listing_pages':listing_ok,'details_discovered':len(wanted),'details_saved':success,
                              'complete':not any(e['kind']==kind for e in self.errors) and success==len(wanted)}
        write_json(self.out/'selected_details.json',self.selected)
        for kind in ENTRIES:
            docs=[d for d in self.docs if d['section']==kind]
            atomic(self.out/kind/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs))
        atomic(self.out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in self.docs))
        write_json(self.out/'summary.json',{'documents':len(self.docs),'complete':not self.errors,'sections':self.stats,'errors':self.errors})
        print(f'Saved {len(self.docs)} documents; complete={not self.errors}; {self.out}',flush=True)
        return not self.errors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--concert-pages',type=int,default=9)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--browser',action='store_true',help='Visible browser for concert details; user handles any verification manually')
    p.add_argument('--html-map',type=Path)
    args=p.parse_args()
    if args.concert_pages<1:p.error('--concert-pages must be positive')
    collector=Collector(args)
    if args.browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=False)
            try:
                context=browser.new_context()
                collector.browser_page=context.new_page()
                ok=collector.run()
            finally:browser.close()
    else:ok=collector.run()
    if not ok:raise SystemExit(2)


if __name__=='__main__':main()
