"""CMU About body and direct menu links in five groups; no recursive discovery."""
import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Comment
from city_pittsburgh import to_markdown, Robots

ROOT=Path(__file__).resolve().parents[2]
HOME='https://www.cmu.edu/about/'
GROUPS={'About','Academics','Admission','Campus Life','Research'}


def normalize(url):
    p=urlsplit(url)
    host=(p.hostname or '').lower()
    if p.scheme not in {'http','https'} or not (host=='cmu.edu' or host.endswith('.cmu.edu')):return None
    if p.username or p.password or p.port not in {None,80,443}:return None
    return urlunsplit(('https',host,p.path.rstrip('/') or '/',p.query,''))


def seeds(raw):
    s=BeautifulSoup(raw,'html.parser');nav=s.select_one('#block-cmu-base-main-menu')
    if nav is None:raise ValueError('Expected CMU main navigation missing')
    result={normalize(HOME):{'url':normalize(HOME),'depth':0,'menu_groups':[],'menu_labels':[]}}
    found=set()
    for item in nav.select(':scope > ul > li'):
        button=item.find('button',recursive=False)
        group=button.get_text(' ',strip=True) if button else ''
        if group not in GROUPS:continue
        found.add(group)
        for a in item.select('.Header__group a[href]'):
            target=normalize(urljoin(HOME,a['href']))
            if not target:raise ValueError('Unexpected non-CMU menu destination: '+a['href'])
            entry=result.setdefault(target,{'url':target,'depth':1,'menu_groups':[],'menu_labels':[]})
            if group not in entry['menu_groups']:entry['menu_groups'].append(group)
            label=a.get_text(' ',strip=True)
            if label not in entry['menu_labels']:entry['menu_labels'].append(label)
    if found!=GROUPS:raise ValueError('Missing navigation groups: '+str(GROUPS-found))
    return list(result.values())


def parse(raw,url):
    s=BeautifulSoup(raw,'html.parser')
    for node in s.find_all(string=lambda x:isinstance(x,Comment)):node.extract()
    # Remove real site furniture before selecting a body. Do not delete paragraphs
    # merely because they mention "Academics", which is also valid page content.
    for node in list(s.select('header,footer,nav,aside,.Footer,.Header,#footer,#header,#nav,.Breadcrumbs,.breadcrumbs,.breadcrumb,script,style,noscript,iframe,form,button,input,select,textarea,img,picture,svg,.visually-hidden,.skip-link,.topsearch')):
        if node.parent is not None:node.decompose()
    root=None
    for selector in ['main#main-content','main','.layout-content','#content','div#main-content']:
        root=s.select_one(selector)
        if root is not None:break
    if root is None:raise ValueError('No supported CMU body container; manual review needed')
    heading=root.find('h1') or s.find('h1')
    title=heading.get_text(' ',strip=True) if heading else s.title.get_text(' ',strip=True) if s.title else url
    body=to_markdown(root)
    if not body.startswith('# '):body='# '+title+'\n\n'+body
    if len(body)<120:raise ValueError('Body too short; may be a dynamic shell or error page')
    return title,body


class Collector:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            sources=[r for r in csv.DictReader(f) if normalize(r['url'].strip())==normalize(HOME)]
        if len(sources)!=1:raise ValueError('Expected one CMU About entry in source.csv')
        self.source=sources[0];name=self.source['source id'].strip()
        if not name or name in {'.','..'} or '/' in name or '\\' in name:raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        if self.out.exists() and any(self.out.iterdir()) and not args.resume:raise FileExistsError('Use --resume or a new --output-root')
        for sub in ['raw','records','markdown']:(self.out/sub).mkdir(parents=True,exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.last=0;self.rules={}
        self.manual={}
        if args.html_map:
            for u,path in json.loads(args.html_map.read_text()).items():
                u=normalize(u)
                if not u:raise ValueError('Non-CMU manual URL')
                path=Path(path);self.manual[u]=path if path.is_absolute() else args.html_map.parent/path
    def request(self,url):
        time.sleep(max(0,1-(time.monotonic()-self.last)));self.last=time.monotonic()
        r=self.session.get(url,timeout=(10,30),allow_redirects=False,stream=True)
        return r
    def fetch(self,url):
        url=normalize(url)
        if url in self.manual:return self.manual[url].read_bytes(),url,'manual_html'
        for _ in range(6):
            host=urlsplit(url).hostname
            if host not in self.rules:
                with self.request('https://'+host+'/robots.txt') as r:
                    if r.status_code==404:self.rules[host]=Robots('')
                    elif r.status_code==200:self.rules[host]=Robots(r.text)
                    else:raise ValueError(f'robots check failed: {r.status_code} on {host}')
            rules=self.rules[host]
            if not rules.allowed(url):raise ValueError('robots_disallowed')
            if rules.delay>30:raise ValueError('robots crawl delay exceeds 30 seconds')
            time.sleep(max(0,rules.delay-(time.monotonic()-self.last)))
            with self.request(url) as r:
                if r.is_redirect:
                    target=normalize(urljoin(url,r.headers['Location']))
                    if not target:raise ValueError('Redirect outside CMU rejected')
                    url=target;continue
                r.raise_for_status()
                if 'html' not in r.headers.get('Content-Type','').lower():raise ValueError('Non-HTML destination is not saved')
                chunks=[];size=0;start=time.monotonic()
                for chunk in r.iter_content(65536):
                    size+=len(chunk)
                    if size>15*1024*1024 or time.monotonic()-start>120:raise ValueError('Download limit')
                    chunks.append(chunk)
                return b''.join(chunks),url,'http'
        raise ValueError('Redirect limit')
    def assemble(self,failures):
        paths=sorted((self.out/'records').glob('*.json'))
        tmp=self.out/'documents.tmp'
        with tmp.open('w',encoding='utf-8') as f:
            for p in paths:f.write(p.read_text(encoding='utf-8').strip()+'\n')
        tmp.replace(self.out/'documents.jsonl')
        (self.out/'summary.json').write_text(json.dumps({'documents':len(paths),'failed_this_run':failures,'depth_limit':1,
             'scope':'About body + direct menu links only; no child link discovery; template footers excluded.'},indent=2))
    def run(self):
        manifest=self.out/'selected_urls.json'
        if self.args.resume and manifest.exists():items=json.loads(manifest.read_text())
        else:
            raw,_,_=self.fetch(HOME);items=seeds(raw)
            (self.out/'raw'/'navigation-source.html').write_bytes(raw)
            manifest.write_text(json.dumps(items,ensure_ascii=False,indent=2))
        failures=0
        try:
            for i,item in enumerate(items,1):
                identity=hashlib.sha256(item['url'].encode()).hexdigest()[:24]
                record=self.out/'records'/(identity+'.json')
                if record.exists() and not self.args.reprocess and item['url'] not in self.manual:continue
                print(f"[{i}/{len(items)}] depth={item['depth']} {item['url']}",flush=True)
                try:
                    raw,final,method=self.fetch(item['url'])
                    rp=self.out/'raw'/(identity+'.html');rp.write_bytes(raw)
                    title,body=parse(raw,final)
                    md=self.out/'markdown'/(identity+'.md');md.write_text(body+'\n',encoding='utf-8')
                    stamp=datetime.now(timezone.utc).isoformat()
                    doc={'doc_id':identity,'title':title,'source_id':self.source['source id'].strip(),'category':self.source['category'].strip(),
                         'source_url':item['url'],'final_url':final,'document_type':'html','content_markdown':body,
                         'content_hash':hashlib.sha256(body.encode()).hexdigest(),'pipeline_version':'cmu-about-v1',
                         'depth':item['depth'],'menu_groups':item['menu_groups'],'menu_labels':item['menu_labels'],
                         'fetched_at':stamp if method=='http' else None,'processed_at':stamp,'markdown_path':str(md.resolve()),
                         'sources':[{'source_url':item['url'],'final_url':final,'raw_path':str(rp.resolve()),'acquisition_method':method}]}
                    record.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
                    status={'url':item['url'],'status':'saved'}
                except Exception as exc:
                    failures+=1;status={'url':item['url'],'status':'failed','error':str(exc)};print('  FAILED:',str(exc),flush=True)
                with (self.out/'crawl_log.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(status,ensure_ascii=False)+'\n')
                self.assemble(failures)
        finally:self.assemble(failures)
        print(f'Finished: {len(items)} selected URLs, {failures} failures this run. {self.out}')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--reprocess',action='store_true')
    p.add_argument('--html-map',type=Path)
    args=p.parse_args();Collector(args).run()


if __name__=='__main__':main()
