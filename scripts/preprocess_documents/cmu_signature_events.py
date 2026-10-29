"""Signature Events section + its direct event pages. No detail link recursion."""
import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup, Comment
from cmu_about import Collector as CMUTransport, normalize
from city_pittsburgh import to_markdown
from downtown_events import atomic, write_json

ROOT=Path(__file__).resolve().parents[2]
HOME='https://www.cmu.edu/engage/events'
LEGACY='https://www.cmu.edu/engage/alumni/events/campus/index.html'


def video_link(url):
    p=urlsplit(url);host=(p.hostname or '').lower()
    return (any(host==h or host.endswith('.'+h) for h in ['youtube.com','youtu.be','vimeo.com','kaltura.com','panopto.com'])
            or bool(re.search(r'\.(mp4|webm|mov|m3u8)(?:$|[?#])',url,re.I)))


def checked(raw):
    s=BeautifulSoup(raw,'html.parser')
    if s.title and re.search(r'just a moment|access denied|attention required',s.title.get_text(),re.I):
        raise ValueError('Blocked page, not event content')
    return s


def signature_section(s):
    h=next((n for n in s.find_all(['h2','h3']) if n.get_text(' ',strip=True)=='Signature Events'),None)
    section=h.find_parent('section') if h else None
    if section is None:raise ValueError('Signature Events section not found')
    return section


def discover(raw):
    section=signature_section(checked(raw));urls=[]
    # Includes six cards and the event link inside CMU125 accordion.
    for a in section.select('a[href]'):
        url=urljoin(HOME,a['href'])
        if video_link(url):continue
        url=normalize(url)
        if not url:raise ValueError('Unexpected non-CMU Signature Event link')
        if urlsplit(url).path.lower().endswith('.pdf'):continue
        if url!=normalize(HOME) and url not in urls:urls.append(url)
    if not urls:raise ValueError('No Signature Event details discovered')
    return urls


def clean(root):
    for n in list(root.find_all(string=lambda x:isinstance(x,Comment))):n.extract()
    for n in list(root.select('header,footer,nav,aside,.Header,.Footer,#footer,#header,.Breadcrumbs,.breadcrumbs,script,style,noscript,iframe,video,audio,source,track,form,input,select,textarea,img,picture,svg,.visually-hidden,.skip-link,.topsearch,[role="complementary"],.sidebar')):
        if n.parent:n.decompose()
    for n in list(root.select('[class*="paragraph--type--video"],.Video,.VideoEmbed,[data-video-url]')):
        if n.parent:n.decompose()
    for a in list(root.select('a')):
        if video_link(a.get('href','')) or re.search(r'^(watch|play)\s+(the\s+)?(video|recording)',a.get_text(' ',strip=True),re.I):
            a.decompose()
        else:a.unwrap()  # Preserve anchor wording, save no child-link destinations.
    for button in list(root.select('button')):
        if button.find_parent(['h2','h3','h4','h5','h6']):button.unwrap() # accordion headings
        else:button.decompose()
    return to_markdown(root).strip()


def parse(raw,overview=False):
    s=checked(raw)
    if overview:
        root=signature_section(s);title='CMU Signature Events'
    else:
        root=s.select_one('main') or s.select_one('.layout-content')
        if root is None:raise ValueError('No identifiable CMU main body')
        h=root.find('h1') or s.find('h1')
        title=h.get_text(' ',strip=True) if h else (s.title.get_text(' ',strip=True) if s.title else 'CMU Event')
    body=clean(root)
    if not body.startswith('# '):body='# '+title+'\n\n'+body
    if len(body)<100:raise ValueError('Empty/short event body')
    return title,body


class Collector(CMUTransport):
    # Reuse bounded downloads, CMU-only redirects, robots handling and manual HTML.
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            rows=[r for r in csv.DictReader(f) if normalize(r['url'].strip()) in {normalize(HOME),normalize(LEGACY)}]
        if len(rows)!=1:raise ValueError('Expected one CMU campus events source row (new or legacy URL)')
        self.source=rows[0];name=self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unsafe source folder name')
        self.out=args.output_root/name
        if self.out.exists() and any(self.out.iterdir()) and not args.resume:raise ValueError('Use --resume or new --output-root')
        for d in ['raw','records','markdown']:(self.out/d).mkdir(parents=True,exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.last=0;self.rules={};self.manual={}
        if args.html_map:
            self.manual={normalize(u):(args.html_map.parent/Path(p)).resolve() for u,p in json.loads(args.html_map.read_text()).items()}
        self.errors=[]

    def load(self,url):
        path=self.out/'raw'/(hashlib.sha256(url.encode()).hexdigest()[:24]+'.html')
        if self.args.resume and path.exists() and url not in self.manual:
            return path.read_bytes(),url,'cache',path
        raw,final,method=self.fetch(url);checked(raw);path.write_bytes(raw)
        return raw,final,method,path

    def run(self):
        home=normalize(HOME)
        raw,final,method,path=self.load(home)
        urls=[home]+discover(raw)
        write_json(self.out/'selected_urls.json',urls)
        docs=[]
        for i,url in enumerate(urls,1):
            print(f'[{i}/{len(urls)}] {url}',flush=True)
            try:
                if url!=home:raw,final,method,path=self.load(url)
                title,body=parse(raw,overview=url==home)
                identity=hashlib.sha256(url.encode()).hexdigest()[:24]
                mdpath=self.out/'markdown'/(identity+'.md')
                doc={'doc_id':identity,'title':title,'source_id':self.source['source id'].strip(),'category':self.source['category'].strip(),
                     'source_url':url,'final_url':final,'depth':0 if url==home else 1,'document_type':'html',
                     'content_markdown':body,'content_hash':hashlib.sha256(body.encode()).hexdigest(),
                     'pipeline_version':'cmu-signature-events-v1','processed_at':datetime.now(timezone.utc).isoformat(),
                     'sources':[{'source_url':url,'raw_path':str(path.resolve()),'acquisition_method':method}],
                     'markdown_path':str(mdpath.resolve())}
                atomic(mdpath,body+'\n');write_json(self.out/'records'/(identity+'.json'),doc);docs.append(doc)
            except Exception as exc:
                self.errors.append({'url':url,'reason':str(exc)});print('  FAILED:',exc,flush=True)
        # Only current selected pages enter the aggregate, never stale records.
        if docs:atomic(self.out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs))
        write_json(self.out/'summary.json',{'selected_urls':len(urls),'documents':len(docs),'complete':len(docs)==len(urls),
                   'errors':self.errors,'scope':'Signature Events section and direct details only; no videos or detail sublinks'})
        print(f'Saved {len(docs)}/{len(urls)} documents: {self.out}',flush=True)
        return not self.errors


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--html-map',type=Path)
    args=p.parse_args()
    if not Collector(args).run():raise SystemExit(2)


if __name__=='__main__':main()
