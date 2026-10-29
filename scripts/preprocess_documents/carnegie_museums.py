"""Carnegie Museums homepage selections + complete forward events pagination.
No links are discovered from detail pages. Run --help for manual/resume options.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
import requests
from bs4 import BeautifulSoup, Comment
from city_pittsburgh import to_markdown
from downtown_events import atomic, write_json

ROOT = Path(__file__).resolve().parents[2]
HOME = 'https://carnegiemuseums.org/'
VERSION = 'carnegie-museums-v2'


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('Not an HTTP URL: ' + url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', urlencode([
        (k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ('fbclid','gclid')]), ''))


def key(url):
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def soup(raw):
    s = BeautifulSoup(raw, 'html.parser')
    title = s.title.get_text(' ', strip=True) if s.title else ''
    if re.search(r'just a moment|access denied|access restricted|403 forbidden|attention required', title, re.I):
        raise ValueError('Access-block page, not article HTML')
    return s


def markdown(node):
    node = BeautifulSoup(str(node), 'html.parser')
    for comment in node.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for n in list(node.select('script,style,nav,aside,footer,form,iframe,video,audio,img,picture,svg,figcaption,.image-caption,.single-top-bar,.ct-font-resizer,.sharedaddy,.related,.related-posts,.related-events,.tribe-events-related-events,.tribe-events-back,.tribe-events-sub-nav,.tribe-events-cal-links,.sidebar,[role="complementary"],.cookie-banner,.newsletter')):
        if n.parent: n.decompose()
    for a in list(node.select('a')):
        if re.match(r'^(buy|get|book|reserve) tickets|^donate( now)?$|^watch video|^subscribe$|^add to calendar$', a.get_text(' ',strip=True), re.I):
            a.decompose()
        else: a.unwrap()
    for n in list(node.select('button')):
        if n.has_attr('aria-expanded') or n.find_parent(['h2','h3','h4','summary']): n.unwrap()
        else: n.decompose()
    return to_markdown(node).strip()


def home_sections(raw):
    s = soup(raw)
    groups = {}
    for name, selector in [('exhibitions','.exhibit-element'), ('stories','.article-element')]:
        cards = s.select(selector)
        if not cards: raise ValueError('Homepage section missing: ' + name)
        groups[name] = []
        for card in cards:
            a = card.select_one('.exhibit-title a, .article-title a')
            if a:
                groups[name].append((canonical(urljoin(HOME,a['href'])), a.get_text(' ',strip=True), markdown(card)))
    entry = next((a for a in s.select('a[href]') if a.get_text(' ',strip=True).lower() == 'view all events'), None)
    if not entry: raise ValueError('View all Events link missing')
    return canonical(urljoin(HOME,entry['href'])), groups


def listing(raw, url):
    s = soup(raw)
    cards = s.select('article.tribe-events-calendar-list__event')
    if not cards and not s.select_one('.tribe-events-view'):
        raise ValueError('Expected events calendar markup missing')
    rows = []
    for card in cards:
        a = card.select_one('a.tribe-events-calendar-list__event-title-link')
        if not a: raise ValueError('Event card title link missing')
        rows.append((canonical(urljoin(url,a['href'])), a.get_text(' ',strip=True), markdown(card)))
    nxt = s.select_one('a.tribe-events-c-nav__next[href]')
    target = canonical(urljoin(url,nxt['href'])) if nxt else None
    if target and (urlsplit(target).hostname != 'carnegiemuseums.org' or not urlsplit(target).path.startswith('/events')):
        raise ValueError('Unexpected pagination URL: '+target)
    return rows, target


def detail(raw):
    s = soup(raw)
    if s.select_one('body.single-tribe_event_series'):
        main = s.select_one('main#main')
        cards = main.select('article.tribe-events-pro-summary__event') if main else []
        if not cards: raise ValueError('Event series has no static schedule cards')
        title = s.title.get_text(' ',strip=True).split(' - The Andy Warhol Museum')[0]
        lines = ['# '+title]
        for card in cards:
            heading = card.select_one('.tribe-events-pro-summary__event-title')
            when = card.select_one('time[datetime]')
            if heading is None or when is None: raise ValueError('Series card missing title/date')
            lines.append('## '+when['datetime'])
            lines.append(heading.get_text(' ',strip=True))
            lines.append(when.get_text(' ',strip=True))
        return '\n\n'.join(lines)
    # Prefer the actual Elementor post, excluding header/footer/popup templates.
    elementor = s.select_one('[data-elementor-type="wp-post"]')
    if elementor is not None:
        for n in list(elementor.select('.elementor-hidden-desktop.elementor-hidden-tablet.elementor-hidden-mobile')):
            if n.parent: n.decompose()
        body = markdown(elementor)
        body = re.sub(r'\[swaddtocart\b[^\]]*\]', '', body)
        # Responsive copies repeat entire widgets; remove only identical text blocks.
        blocks, seen = [], set()
        for block in body.split('\n\n'):
            normalized = ' '.join(block.split())
            if normalized and normalized not in seen:
                blocks.append(block); seen.add(normalized)
        body = '\n\n'.join(blocks)
        if len(body) < 100: raise ValueError('Elementor post body too short')
        return body
    # WordPress articles first: title/byline outside entry-content must be retained.
    root = s.select_one('#tribe-events-content')
    if root is None:
        entry = s.select_one('.entry-content')
        root = entry.find_parent('article') if entry else None
        if root is None: root = entry
    if root is None:
        # CSS selector lists use document order, not selector priority.
        for selector in ('main#main', '#main-content', '[role="main"]', 'main:not(.subnavwrap)', '#main'):
            root = s.select_one(selector)
            if root is not None: break
    if root is None: raise ValueError('No verified main body container; manual HTML/template review needed')
    h = root.find('h1') or s.find('h1')
    title = h.get_text(' ',strip=True) if h else ''
    body = markdown(root)
    if len(body) < 100: raise ValueError('Main body too short; may be blocked or dynamic')
    if title and not body.startswith('# '): body = '# '+title+'\n\n'+body
    return body


class Fetcher:
    def __init__(self, out, args):
        self.out, self.args = out, args
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'ANLP-course-research/1.0 (public museum information)'
        self.mapping = {}
        if args.html_map:
            path = Path(args.html_map).resolve()
            for u,p in json.loads(path.read_text()).items():
                self.mapping[canonical(u)] = (path.parent / p).resolve()

    def get(self, url):
        path = self.out/'raw'/(key(url)+'.html')
        if url in self.mapping:
            raw = self.mapping[url].read_text(encoding='utf-8', errors='replace')
            origin = str(self.mapping[url])
        elif self.args.resume and path.exists():
            return path.read_text(encoding='utf-8'), str(path)
        elif self.args.offline:
            raise ValueError('Offline: no saved HTML for '+url)
        else:
            time.sleep(self.args.delay)
            for attempt in range(3):
                try:
                    r = self.session.get(url, timeout=(10,45))
                    if r.status_code not in {500,502,503,504} or attempt == 2:
                        break
                    reason = f'HTTP {r.status_code}'
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if attempt == 2: raise
                    reason = str(exc)
                wait = 2 ** (attempt + 1)
                print(f'  Retry {attempt+1}/2 in {wait}s: {reason}', flush=True)
                time.sleep(wait)
            if not r.ok:
                atomic(self.out/'errors'/(key(url)+'.html'),r.text)
                r.raise_for_status()
            if 'html' not in r.headers.get('Content-Type','').lower():
                raise ValueError('Response is not HTML')
            raw, origin = r.text, r.url
        soup(raw)  # do not cache recognized access-block pages as successful HTML
        atomic(path,raw)
        return raw, origin


def run(args):
    with (ROOT/'config/source.csv').open(encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    row = next((r for r in rows if any(v.strip().rstrip('/') == HOME.rstrip('/') for v in r)), None)
    if row is None: raise ValueError('Carnegie Museums source missing in config/source.csv')
    source = row[0].strip()
    out = Path(args.output) if args.output else ROOT/'data/preprocessed_documents'/source
    fetch = Fetcher(out,args)
    failures, pending, docs, pages, seen_pages = [], {}, [], [], set()
    complete = False
    def add(section, rows, parent):
        for url,title,text in rows:
            item = pending.setdefault(url, {'title':title,'sections':[], 'listing_context':[], 'discovered_from':[]})
            for field,value in [('sections',section),('listing_context',text),('discovered_from',parent)]:
                if value not in item[field]: item[field].append(value)
    raw,_ = fetch.get(HOME)
    entry, groups = home_sections(raw)
    for section,rows in groups.items(): add(section,rows,HOME)
    url = entry
    while url:
        if url in seen_pages:
            failures.append({'url':url,'stage':'pagination','error':'Pagination loop; incomplete'})
            break
        if args.max_pages and len(pages) >= args.max_pages:
            failures.append({'url':url,'stage':'pagination','error':'Explicit max-pages limit reached; incomplete'})
            break
        seen_pages.add(url)
        print(f'Listing {len(pages)+1}: {url}',flush=True)
        try:
            raw,_ = fetch.get(url)
            rows,nxt = listing(raw,url)
            if not rows and nxt: raise ValueError('Empty page has a next link')
            fingerprint = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
            if fingerprint in {p['fingerprint'] for p in pages}: raise ValueError('Server returned repeated listing page; incomplete')
            add('events',rows,url)
            pages.append({'url':url,'cards':len(rows),'fingerprint':fingerprint})
            url = nxt
            if not url: complete = True
        except Exception as e:
            failures.append({'url':url,'stage':'listing','error':str(e)})
            break
    write_json(out/'discovered.json',pending)
    # One document per direct URL, retaining every distinct listing date/location.
    for i,(url,item) in enumerate(pending.items(),1):
        print(f'Detail {i}/{len(pending)}: {url}',flush=True)
        status, body, origin = 'complete', '', None
        scope = 'detail_body'
        try:
            raw, origin = fetch.get(url)
            body = detail(raw)
            parsed = soup(raw)
            if parsed.select_one('body.single-tribe_event_series'):
                scope = 'event_series_schedule'
                body = '## Event series schedule\n\n' + body
        except Exception as e:
            status = 'listing_only'
            failures.append({'url':url,'stage':'detail','error':str(e)})
            print('  FAILED (listing retained): '+str(e),flush=True)
        context = '\n\n'.join(item['listing_context'])
        content = '# '+item['title']+'\n\n## Listing information\n\n'+context
        if body: content += '\n\n## Detail page\n\n'+body
        doc = dict(doc_id=key(url),source_id=source,category=row[1].strip(),source_url=url,
                   title=item['title'],sections=item['sections'],discovered_from=item['discovered_from'],
                   document_type='html',content_markdown=content,content_hash=hashlib.sha256(content.encode()).hexdigest(),
                   detail_status=status,content_scope=scope if body else 'listing_only',raw_path=str(out/'raw'/(key(url)+'.html')) if body else None,
                   input_origin=origin,pipeline_version=VERSION,processed_at=datetime.now(timezone.utc).isoformat())
        write_json(out/'records'/(key(url)+'.json'),doc)
        atomic(out/'markdown'/(key(url)+'.md'),content+'\n')
        docs.append(doc)
    atomic(out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs))
    write_json(out/'failures.json',failures)
    write_json(out/'manual_html_map.example.json',{e['url']: 'saved-page.html' for e in failures})
    write_json(out/'summary.json',dict(source_id=source,pipeline_version=VERSION,listing_pages=pages,
        pagination_complete=complete,documents=len(docs),complete_details=sum(d['detail_status']=='complete' for d in docs),
        listing_only=sum(d['detail_status']=='listing_only' for d in docs),
        event_series_schedules=sum(d['content_scope']=='event_series_schedule' for d in docs),failures=len(failures),
        complete=complete and not failures))
    print(f'Saved {len(docs)} documents to {out}; failures={len(failures)}; pagination_complete={complete}',flush=True)
    return 0 if complete and not failures else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output')
    p.add_argument('--resume',action='store_true',help='Reuse successfully cached HTML')
    p.add_argument('--html-map',help='JSON mapping original URL to saved HTML path (relative to mapping file)')
    p.add_argument('--offline',action='store_true',help='No network; combine with --resume/--html-map')
    p.add_argument('--delay',type=float,default=1.0)
    p.add_argument('--max-pages',type=int,default=0,help='0 = follow Next Events to the end; a positive cap marks output incomplete')
    args=p.parse_args()
    if args.delay < 0 or args.max_pages < 0: p.error('delay and max-pages must be nonnegative')
    raise SystemExit(run(args))

if __name__=='__main__': main()
