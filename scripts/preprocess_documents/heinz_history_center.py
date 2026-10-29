"""Scoped Heinz History Center collector: museum/campaign/exhibit/research pages;
exactly two event LIST pages, never event detail requests.
"""
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from carnegie_museums import Fetcher, canonical, key, soup, markdown
from downtown_events import atomic, write_json

ROOT=Path(__file__).resolve().parents[2]
HOME='https://www.heinzhistorycenter.org/'
VERSION='heinz-history-center-v1'


def link(base,href):
    u=canonical(urljoin(base,href))
    p=urlsplit(u)
    if p.hostname not in {'www.heinzhistorycenter.org','heinzhistorycenter.org'}:return None
    if re.search(r'\.(pdf|jpg|jpeg|png|mp4|zip|ics)/?$',p.path,re.I):return None
    if '/event/' in p.path:return None  # event details are out of scope everywhere
    return u


def main_node(raw):
    root=soup(raw).select_one('main.page,main')
    if root is None:raise ValueError('Expected main page container missing')
    return root


def content(raw):
    root=main_node(raw)
    h=root.find('h1')
    title=h.get_text(' ',strip=True) if h else ''
    for n in list(root.select('.breadcrumb,.breadcrumb_nav,.sub_nav,.sub_nav_wrap,.skip_link,.blog_related,.event_related,.pagination,.gallery,.image_gallery,.media_gallery,.newsletter,.related_posts,.related_content')):
        if n.parent:n.decompose()
    # Some themes put meaningful titles in header elements; preserve their text.
    for n in root.select('header'):n.name='div'
    body=markdown(root)
    # Remove only identical adjacent blocks (responsive duplicates), not repeated facts globally.
    blocks=[]
    for block in body.split('\n\n'):
        if block.strip() and (not blocks or block!=blocks[-1]):blocks.append(block)
    body='\n\n'.join(blocks)
    if title and not body.startswith('# '):body='# '+title+'\n\n'+body
    if len(body)<100:raise ValueError('Insufficient main body; manual HTML/template review needed')
    return title,body


def homepage(raw):
    root=main_node(raw)
    family=[]
    for a in root.select('.family a[href]'):
        u=link(HOME,a['href'])
        if u and u not in family:family.append(u)
    if len(family)!=4:raise ValueError(f'Expected four family-of-museums links; got {len(family)}')
    def label(text):
        a=next((a for a in root.select('a[href]') if a.get_text(' ',strip=True).casefold()==text.casefold()),None)
        if a is None:raise ValueError('Homepage link not found: '+text)
        u=link(HOME,a['href'])
        if not u:raise ValueError('Unsupported homepage URL: '+a['href'])
        return u
    heading=next((h for h in root.find_all(['h2','h3']) if 'Building the Next Chapter' in h.get_text()),None)
    container=heading.find_parent(class_='cta_visual_inner') if heading else None
    a=container.select_one('a.cta_visual_link[href]') if container else None
    if not a:raise ValueError('Capital campaign link not found')
    return family,link(HOME,a['href']),label('View All Events'),label('Explore Exhibits'),label('Research & Explore')


def research_links(raw,base):
    root=main_node(raw);found=[]
    for a in root.select('a.sub_nav_link[href]'):
        u=link(base,a['href'])
        if u and u!=base and urlsplit(u).path.startswith('/research/detre-library-archives/') and u not in found:found.append(u)
    if not found:raise ValueError('Research sub-navigation missing')
    return found


def exhibit_categories(raw,base):
    root=main_node(raw);found=[]
    for a in root.select('a[href]'):
        if a.get_text(' ',strip=True).casefold()!='browse exhibits':continue
        u=link(base,a['href'])
        if u and u not in found:found.append(u)
    if not found:raise ValueError('Browse Exhibits category links missing')
    return found


def exhibit_links(raw,base):
    root=main_node(raw)
    for n in list(root.select('nav,aside,.sub_nav,.blog_related,.event_related,.related_content')):
        if n.parent:n.decompose()
    found=[]
    for a in root.select('h2 a[href],h3 a[href],a.card_title_link[href]'):
        u=link(base,a['href'])
        if u and u!=base and u not in found and '/exhibit' in urlsplit(u).path:found.append(u)
    if not found:raise ValueError('No exhibit detail cards found; review saved directory HTML')
    nxt=root.select_one('a.pagination_arrow_right:not(.pagination_arrow_disabled)[href]')
    return found,link(base,nxt['href']) if nxt else None


def event_cards(raw):
    root=main_node(raw);cards=root.select('li.events_list_item')
    if not cards:raise ValueError('No event summary cards found')
    result=[]
    for card in cards:
        a=card.select_one('a.card_title_link[href]')
        if not a:raise ValueError('Event card missing title')
        def txt(selector):
            n=card.select_one(selector)
            return n.get_text(' ',strip=True) if n else ''
        result.append(dict(url=canonical(urljoin(HOME,a['href'])),title=a.get_text(' ',strip=True),
            date=txt('.card_date'),location=txt('.card_location'),time=txt('.card_time'),
            description=txt('.card_description'),ticket=txt('.card_ticket_label')))
    current=root.select_one('select[name="current_page"] option[selected]')
    nxt=root.select_one('a.pagination_arrow_right:not(.pagination_arrow_disabled)[href]')
    return result,int(current['value']) if current else None,link(HOME,nxt['href']) if nxt else None


def run(args):
    with (ROOT/'config/source.csv').open(encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    source=next(r for r in rows if r['url'].strip().rstrip('/')==HOME.rstrip('/'))
    out=args.output or ROOT/'data/preprocessed_documents'/source['source id'].strip()
    fetch=Fetcher(out,args);errors=[];docs={};queue={};event_pages=[]
    def get(url,stage):
        print(stage+': '+url,flush=True)
        try:return fetch.get(url)[0]
        except Exception as e:
            errors.append(dict(url=url,stage=stage,error=str(e)));print('FAILED: '+str(e),flush=True);return None
    def failure(url,stage,e):errors.append(dict(url=url,stage=stage,error=str(e)))
    def save(url,title,body,section,**extra):
        d=dict(doc_id=key(url),source_id=source['source id'].strip(),category=source['category'].strip(),
          source_url=url,title=title,section=section,document_type='html',content_markdown=body,
          content_hash=hashlib.sha256(body.encode()).hexdigest(),pipeline_version=VERSION,
          processed_at=datetime.now(timezone.utc).isoformat(),raw_path=str(out/'raw'/(key(url)+'.html')),**extra)
        docs[url]=d
        write_json(out/'records'/(key(url)+'.json'),d)
        atomic(out/'markdown'/(key(url)+'.md'),body+'\n')
    def parse_save(url,raw,section):
        try:
            title,body=content(raw);save(url,title,body,section)
        except Exception as e:failure(url,section,e)
    raw=get(HOME,'homepage')
    if raw is None:raise RuntimeError('Homepage unavailable. Use --html-map with saved homepage HTML.')
    family,campaign,events,exhibits,research=homepage(raw)
    for u in family:queue[u]='family'
    queue[campaign]='campaign'
    # Two listing pages only; URLs in the cards are metadata, never queued.
    url=events;seen_events=set();parts=['# Heinz History Center — Event summaries']
    for page in (1,2):
        raw=get(url,f'events page {page}')
        if raw is None:break
        try:
            cards,actual,nxt=event_cards(raw)
            if actual is not None and actual!=page:raise ValueError(f'Expected page {page}, received {actual}')
            page_rows=[]
            for card in cards:
                signature=json.dumps(card,sort_keys=True)
                if signature in seen_events:continue
                seen_events.add(signature);page_rows.append(card)
                parts.append('## '+card['title'])
                for field in ('date','time','location','ticket'):
                    if card[field]:parts.append(field.title()+': '+card[field])
                if card['description']:parts.append(card['description'])
            if not page_rows:raise ValueError('Page contains no new event summaries')
            event_pages.append(dict(url=url,page=page,events=page_rows))
            if page==1:
                if not nxt:raise ValueError('Second event page link missing')
                url=nxt
        except Exception as e:failure(url,'events',e);break
    if event_pages:
        save(events,'Event summaries','\n\n'.join(parts),'events',content_scope='listing_only_by_request',
             listing_pages=[p['url'] for p in event_pages],events_count=len(seen_events),requested_pages_complete=len(event_pages)==2)
    write_json(out/'event_listings.json',event_pages)
    raw=get(research,'research index')
    if raw:
        parse_save(research,raw,'research')
        try:
            for u in research_links(raw,research):queue[u]='research_detail'
        except Exception as e:failure(research,'research discovery',e)
    raw=get(exhibits,'exhibits index')
    if raw:
        parse_save(exhibits,raw,'exhibits_index')
        try:categories=exhibit_categories(raw,exhibits)
        except Exception as e:failure(exhibits,'exhibit discovery',e);categories=[]
        for category in categories:
            url=category;visited=set()
            while url:
                if url in visited:failure(url,'exhibit directory','Pagination loop');break
                visited.add(url)
                raw=get(url,'exhibit directory')
                if raw is None:break
                try:
                    children,nxt=exhibit_links(raw,url)
                    for u in children:queue[u]='past_exhibit' if 'past-exhibits' in category else 'exhibit'
                    url=nxt
                except Exception as e:failure(url,'exhibit discovery',e);break
    write_json(out/'discovered.json',queue)
    for i,(u,section) in enumerate(queue.items(),1):
        raw=get(u,f'detail {i}/{len(queue)} [{section}]')
        if raw:parse_save(u,raw,section)
    atomic(out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs.values()))
    write_json(out/'failures.json',errors)
    write_json(out/'manual_html_map.example.json',{e['url']:f'saved-{i+1}.html' for i,e in enumerate(errors)})
    write_json(out/'summary.json',dict(documents=len(docs),events=len(seen_events),event_pages=len(event_pages),
        sections={s:sum(d['section']==s for d in docs.values()) for s in sorted({d['section'] for d in docs.values()})},
        failures=len(errors),complete=not errors and len(event_pages)==2,pipeline_version=VERSION))
    print(f'Saved {len(docs)} documents; {len(errors)} failures; output: {out}',flush=True)
    return 2 if errors else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path)
    p.add_argument('--resume',action='store_true',help='Reuse cached successful HTML')
    p.add_argument('--html-map',help='JSON mapping URLs to locally saved HTML files')
    p.add_argument('--offline',action='store_true')
    p.add_argument('--delay',type=float,default=1)
    args=p.parse_args()
    if args.delay<0:p.error('delay must be nonnegative')
    raise SystemExit(run(args))

if __name__=='__main__':main()
