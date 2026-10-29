"""Collect City Paper's event listing pages and direct details into ONE document.
No date filter. Never follow ticket, website, promotion or detail sublinks.
Manual HTML map supports pages blocked by the server. No challenge bypass.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from city_pittsburgh import to_markdown

ROOT = Path(__file__).resolve().parents[2]
HOME = 'https://community.pghcitypaper.com/events'
HOST = 'community.pghcitypaper.com'
AGENT = 'ANLP-HW2-Research/1.0'


def save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8'); tmp.replace(path)


def dump(path, value):
    save(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def canonical(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), p.query, ''))


def text(node):
    return node.get_text(' ', strip=True)


def soup_checked(raw):
    s = BeautifulSoup(raw, 'html.parser')
    title = text(s.title).lower() if s.title else ''
    if any(x in title for x in ['attention required', 'just a moment', 'access denied']) or s.select_one('#cf-challenge-running, #challenge-form'):
        raise ValueError('Blocked/challenge HTML; provide saved rendered HTML with --html-map')
    return s


def listing(raw, expected_page, total_pages):
    s = soup_checked(raw)
    match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', text(s), re.I)
    if not match:
        raise ValueError('Missing Page X of Y marker; refusing unverified listing')
    current, total = map(int, match.groups())
    if current != expected_page or total != total_pages:
        raise ValueError(f'Page reports {current} of {total}; expected {expected_page} of {total_pages}. Check live pagination and --pages.')
    urls = {}
    for a in s.select('a[href]'):
        url = canonical(urljoin(HOME, a['href']))
        p = urlsplit(url)
        # Only the observed event detail route, never ticket/promote/category URLs.
        if p.hostname == HOST and re.fullmatch(r'/events/\d+/[^/]+', p.path):
            urls[url] = text(a)
    if not urls:
        raise ValueError('No event detail links found')
    return urls


def detail(raw):
    s = soup_checked(raw)
    root = s.find('main') or s.find('article') or s.body
    if root is None or root.find('h1') is None:
        raise ValueError('Missing event title/main content')
    title = text(root.find('h1'))
    if title.lower() == 'browse events':
        raise ValueError('Detail redirected to event listing')
    if not any(text(h).strip().lower() == 'about this event' for h in root.find_all(['h2','h3'])):
        raise ValueError('Missing About this event section; refusing unknown detail layout')
    # Remove the smallest self-contained promotion block containing its prompt.
    for node in list(root.find_all(string=re.compile(r'is this your event', re.I))):
        parent = node.parent
        while parent.parent is not None and parent.parent is not root:
            candidate = parent.parent
            if candidate.find('h1') or len(text(candidate)) > 600:
                break
            parent = candidate
        if parent is root or parent.find('h1'):
            node.extract()
        else:
            parent.decompose()
    for node in list(root.select('nav,footer,script,style,form,iframe,svg,img,button')):
        node.decompose()
    for a in list(root.select('a')):
        label = text(a).strip().lower()
        if re.match(r'^(get tickets|event website|promote it|is this your event|[←]?\s*back to events)\b', label):
            a.decompose()
        else:
            # Preserve descriptive anchor text, but no destination URL or recursion.
            a.unwrap()
    # Remove global header only if it is not the event's own title container.
    for header in list(root.find_all('header')):
        if not header.find('h1'):
            header.decompose()
    body = to_markdown(root).strip()
    flags = []
    if re.search(r'(?:…|\.\.\.)?\s*and\s+\d+\s+more', body, re.I):
        flags.append('schedule_truncated_by_source')
    return title, body, flags


class Collector:
    def __init__(self, args):
        self.args = args
        with args.sources.open(encoding='utf-8-sig', newline='') as f:
            rows = [r for r in csv.DictReader(f) if 'pghcitypaper.com' in r['url'] and 'event' in r['url'].lower()]
        if len(rows) != 1:
            raise ValueError('Expected exactly one City Paper events row in source.csv')
        self.source = rows[0]; name = self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.', '..'}:
            raise ValueError('Unsafe source folder')
        self.out = args.output_root / name
        manifest = self.out / 'collection.json'
        config = {'url': HOME, 'pages': args.pages, 'date_filter': None}
        if manifest.exists():
            if not args.resume:
                raise ValueError('Output exists: use --resume')
            if json.loads(manifest.read_text()) != config:
                raise ValueError('Configuration changed: use a new --output-root')
        dump(manifest, config)
        self.manual = {}
        if args.html_map:
            mapping = json.loads(args.html_map.read_text())
            self.manual = {canonical(url): (args.html_map.parent / path).resolve() for url, path in mapping.items()}
        self.session = requests.Session(); self.session.headers['User-Agent'] = AGENT
        self.last = 0; self.delay = 1; self.rules = None; self.errors = []

    def network(self, url):
        if self.args.offline:
            raise ValueError('offline: missing cached/manual HTML')
        for _ in range(6):
            if urlsplit(url).hostname != HOST:
                raise ValueError('External redirect rejected')
            if self.rules and not self.rules.can_fetch(AGENT, url):
                raise ValueError('robots_disallowed')
            time.sleep(max(0, self.delay - (time.monotonic() - self.last))); self.last = time.monotonic()
            with self.session.get(url, timeout=(10, 30), allow_redirects=False, stream=True) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers['Location']); continue
                if url.endswith('/robots.txt') and r.status_code == 404:
                    return b''
                r.raise_for_status(); chunks = []; size = 0; began = time.monotonic()
                for chunk in r.iter_content(65536):
                    chunks.append(chunk); size += len(chunk)
                    if size > 20*1024*1024 or time.monotonic()-began > 90:
                        raise ValueError('Download time/size limit')
                return b''.join(chunks)
        raise ValueError('Redirect limit')

    def load(self, url, path):
        if canonical(url) in self.manual:
            raw = self.manual[canonical(url)].read_bytes()
        elif self.args.resume and path.exists():
            raw = path.read_bytes()
        else:
            if self.rules is None:
                raw_rules = self.network('https://' + HOST + '/robots.txt')
                self.rules = RobotFileParser(); self.rules.parse(raw_rules.decode('utf-8', 'replace').splitlines())
                self.delay = max(1, self.rules.crawl_delay(AGENT) or 0)
                if self.delay > 30:
                    raise ValueError('Robots delay too long')
            raw = self.network(url)
        soup_checked(raw)
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return raw

    def error(self, stage, url, exc):
        self.errors.append({'stage': stage, 'url': url, 'reason': str(exc)})
        dump(self.out/'failures.json', self.errors)
        print(f'FAILED {stage}: {url}: {exc}', flush=True)

    def run(self):
        events = {}; pages_ok = 0; signatures = set()
        for n in range(1, self.args.pages + 1):
            url = HOME if n == 1 else HOME + f'?page={n}'
            print(f'Listing {n}/{self.args.pages}: {url}', flush=True)
            try:
                raw = self.load(url, self.out/'raw/listings'/f'page-{n:02d}.html')
                links = listing(raw, n, self.args.pages)
                signature = tuple(sorted(links))
                if signature in signatures:
                    raise ValueError('Duplicate listing page: pagination may not be working')
                signatures.add(signature); pages_ok += 1
                for link, label in links.items():
                    events.setdefault(link, {'title_hint': label, 'listing_urls': []})['listing_urls'].append(url)
            except Exception as exc:
                self.error('listing', url, exc)
                # A host block normally affects every page; do not repeat blocked requests.
                if isinstance(exc, requests.HTTPError) and exc.response.status_code in (403,429):
                    break
        dump(self.out/'discovered_events.json', events)
        records = []; partial_schedules = []
        for i, (url, provenance) in enumerate(events.items(), 1):
            print(f'Detail {i}/{len(events)}: {url}', flush=True)
            try:
                identity = hashlib.sha256(url.encode()).hexdigest()[:24]
                raw = self.load(url, self.out/'raw/details'/(identity+'.html'))
                title, body, flags = detail(raw)
                records.append({'source_url': url, 'title': title, 'content_markdown': body, 'listing_urls': provenance['listing_urls'], 'quality_flags': flags})
                if flags:
                    partial_schedules.append(url)
            except Exception as exc:
                self.error('detail', url, exc)
                if isinstance(exc, requests.HTTPError) and exc.response.status_code in (403,429):
                    break
        complete = pages_ok == self.args.pages and len(records) == len(events) and not self.errors
        summary = {'pages_expected': self.args.pages, 'pages_succeeded': pages_ok, 'unique_event_urls': len(events), 'details_succeeded': len(records), 'complete': complete, 'schedule_truncated_urls': partial_schedules, 'errors': self.errors, 'note': 'complete covers listing/detail extraction. Source-truncated recurrence schedules are separately flagged; no extra dates are invented.'}
        dump(self.out/'summary.json', summary)
        # Never replace a valid document with an empty blocked-page result.
        if records:
            sections = []
            for record in records:
                body = re.sub(r'^(#{1,5}) ', r'#\1 ', record['content_markdown'], flags=re.M)
                sections.append(body + '\n\nSource: ' + record['source_url'])
            body = '# Pittsburgh City Paper Events\n\n' + '\n\n'.join(sections)
            doc = {'doc_id': hashlib.sha256(HOME.encode()).hexdigest()[:24], 'source_id': self.source['source id'].strip(), 'category': self.source['category'].strip(), 'source_url': HOME, 'source_urls': [r['source_url'] for r in records], 'title': 'Pittsburgh City Paper Events', 'document_type': 'events_collection', 'event_count': len(records), 'content_markdown': body, 'complete': complete, 'quality_flags': ([] if complete else ['incomplete_collection']) + (['some_source_schedules_truncated'] if partial_schedules else []), 'content_hash': hashlib.sha256(body.encode()).hexdigest(), 'pipeline_version': 'citypaper-events-v1', 'processed_at': datetime.now(timezone.utc).isoformat()}
            dump(self.out/'document.json', doc)
            save(self.out/'article.md', body+'\n')
            save(self.out/'documents.jsonl', json.dumps(doc, ensure_ascii=False)+'\n')
        print(f'Done: {len(records)} event details; complete={complete}; output={self.out}', flush=True)
        return complete


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, default=ROOT/'config/source.csv')
    p.add_argument('--output-root', type=Path, default=ROOT/'data/preprocessed_documents')
    p.add_argument('--pages', type=int, default=8)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--html-map', type=Path, help='JSON mapping page URLs to saved HTML paths, relative to map file')
    p.add_argument('--offline', action='store_true')
    args = p.parse_args()
    if args.pages < 1: p.error('--pages must be positive')
    if args.offline: args.resume = True
    if not Collector(args).run(): raise SystemExit(2)


if __name__ == '__main__':
    main()
