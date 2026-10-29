"""All tags / All Events -> one Markdown document, including each detail page.

Uses the public REST endpoint used by the site's calendar. Does not crawl links
inside event details. Default inclusive date window: 2026-10-28..2027-01-01.
"""
import argparse
import csv
import hashlib
import html as html_module
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from city_pittsburgh import to_markdown

ROOT = Path(__file__).resolve().parents[2]
HOME = 'https://downtownpittsburgh.com/events/'
BASE = 'https://downtownpittsburgh.com'
API = BASE + '/wp-json/vibemap/v1/events-data'
AGENT = 'ANLP-HW2-Research/1.0'


def write_json(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def markdown(html):
    if '<' not in (html or ''):
        return html_module.unescape(html or '').strip()
    soup = BeautifulSoup(html or '', 'html.parser')
    for node in soup.select('script,style,iframe,nav,footer,form'):
        node.decompose()
    # Keep important links as text; never follow them.
    for a in soup.select('a[href]'):
        href = a['href']
        if href.startswith(('https://', 'http://')):
            a.replace_with(f"{a.get_text(' ', strip=True)} ({href})")
    return to_markdown(soup).strip()


def scalar(value):
    return value[0] if isinstance(value, list) and value else value


def date_value(value):
    value = scalar(value)
    return date.fromisoformat(str(value)[:10]) if value else None


def in_window(event, start, end):
    meta = event.get('meta', {})
    a = date_value(meta.get('vibemap_event_start_date'))
    b = date_value(meta.get('vibemap_event_end_date')) or a
    if a is None or b < a:
        raise ValueError('Missing or invalid date range')
    return a <= end and b >= start  # Includes multi-day events overlapping window.


def group_events(events):
    """Merge only records sharing an explicit series identity and content/venue facts.

    Different descriptions/prices/statuses remain separate variants. Titles alone
    never establish identity. Keep every listed date, without expanding RRULEs.
    """
    groups = {}
    for e in events:
        m = e.get('meta', {})
        identity = scalar(m.get('vibemap_event_id')) or ('post:' + str(e['id']))
        facts = {k: scalar(m.get('vibemap_event_' + k)) for k in
                 ['place_id', 'hotspots_place', 'address', 'organizer', 'price',
                  'event_status', 'url', 'recurrence', 'recurrence_display']}
        key = json.dumps([identity, e['title'], markdown(e.get('content', '')), facts],
                         sort_keys=True, ensure_ascii=False)
        groups.setdefault(key, []).append(e)
    return list(groups.values())


def parse_detail(raw, event_id):
    soup = BeautifulSoup(raw, 'html.parser')
    for script in soup.select('script.vibemap-preload-data[type="application/json"]'):
        data = json.loads(script.get_text())
        for key, response in data.items():
            if key.split('?')[0] == f'/wp/v2/vibemap_event/{event_id}':
                body = response.get('body', {})
                if str(body.get('id')) != str(event_id):
                    continue
                if not isinstance(body.get('content'), dict):
                    continue
                return body
    raise ValueError('Matching full event payload absent; refusing skeleton/related events')


class Collector:
    def __init__(self, args):
        self.args = args
        with args.sources.open(encoding='utf-8-sig', newline='') as f:
            rows = [r for r in csv.DictReader(f) if r['url'].strip().rstrip('/') == HOME.rstrip('/')]
        if len(rows) != 1:
            raise ValueError('Expected one Downtown calendar row in source.csv')
        self.source = rows[0]
        name = self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.', '..'}:
            raise ValueError('Unsafe source name')
        self.out = args.output_root / name
        self.out.mkdir(parents=True, exist_ok=True)
        config = {'start': args.start.isoformat(), 'end': args.end.isoformat(), 'tags': 'all', 'per_page': 100}
        manifest = self.out / 'collection.json'
        if manifest.exists():
            if not args.resume:
                raise ValueError('Output exists; use --resume or a new --output-root')
            previous = json.loads(manifest.read_text())
            if previous != config:
                if not (previous['start'] <= config['start'] <= config['end'] <= previous['end']):
                    raise ValueError('Cache does not cover requested dates; use a new --output-root')
                # Preserve the original pagination query: never mix narrower pages into it.
                config = previous
        write_json(manifest, config)
        self.query_config = config
        write_json(self.out / 'selection.json', {'start': str(args.start), 'end': str(args.end), 'grouping': 'series-and-content-v2'})
        self.session = requests.Session()
        self.session.headers['User-Agent'] = AGENT
        self.last = 0
        self.delay = 1.0
        self.robots = None
        self.errors = []

    def get(self, url):
        if self.args.offline:
            raise ValueError('offline: required raw file not cached')
        for _ in range(6):
            if urlsplit(url).hostname != 'downtownpittsburgh.com':
                raise ValueError('Off-site URL/redirect rejected')
            if self.robots and not self.robots.can_fetch(AGENT, url):
                raise ValueError('robots_disallowed')
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            with self.session.get(url, timeout=(10, 45), allow_redirects=False, stream=True) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers['Location'])
                    continue
                if url.endswith('/robots.txt') and r.status_code == 404:
                    return b''
                r.raise_for_status()
                chunks, size, started = [], 0, time.monotonic()
                for chunk in r.iter_content(65536):
                    chunks.append(chunk); size += len(chunk)
                    if size > 50 * 1024 * 1024 or time.monotonic() - started > 120:
                        raise ValueError('Download size/time limit')
                return b''.join(chunks)
        raise ValueError('Too many redirects')

    def error(self, stage, item, exc):
        self.errors.append({'stage': stage, 'item': str(item), 'reason': str(exc)})
        print(f'  WARNING {stage}: {item}: {exc}', flush=True)

    def run(self):
        if not self.args.offline:
            robots = self.get(BASE + '/robots.txt').decode('utf-8', 'replace')
            self.robots = RobotFileParser(); self.robots.parse(robots.splitlines())
            self.delay = max(1, self.robots.crawl_delay(AGENT) or 0)
            if self.delay > 30:
                raise ValueError('Robots crawl delay exceeds 30s; stopping rather than ignoring it')
        all_events = {}; totals = set(); page = 1; listing_complete = False
        try:
            while page <= self.args.max_pages:
                params = {'page': page, 'per_page': 100, 'start_date': self.query_config['start'], 'end_date': self.query_config['end'] + 'T23:59:59'}
                path = self.out / 'raw/api' / f'page-{page:04d}.json'
                if self.args.resume and path.exists():
                    data = json.loads(path.read_text())
                else:
                    data = json.loads(self.get(API + '?' + urlencode(params)))
                    if 'events' not in data or 'pagination' not in data:
                        raise ValueError('Unexpected calendar API schema')
                    write_json(path, data)
                pagination = data['pagination']; totals.add(int(pagination['total']))
                if int(pagination['current_page']) != page:
                    raise ValueError('API returned wrong page')
                before = len(all_events)
                for event in data['events']:
                    all_events[str(event['id'])] = event
                print(f'Listing page {page}/{pagination["total_pages"]}: {len(all_events)} unique events', flush=True)
                if not pagination['has_more']:
                    listing_complete = len(totals) == 1 and len(all_events) == next(iter(totals))
                    if not listing_complete:
                        raise ValueError('Unique event count differs from API total or total changed')
                    break
                if len(all_events) == before:
                    raise ValueError('Pagination made no progress')
                page += 1
            else:
                raise ValueError('max-pages reached; listing incomplete')
        except Exception as exc:
            self.error('listing', page, exc)
        chosen = []
        for event in all_events.values():
            try:
                if in_window(event, self.args.start, self.args.end):
                    chosen.append(event)
            except Exception as exc:
                self.error('date', event['id'], exc)
        chosen.sort(key=lambda e: (str(scalar(e['meta'].get('vibemap_event_start_date', ''))), str(e['title']), str(e['id'])))
        groups = group_events(chosen)
        print(f'Selected {len(chosen)} records -> {len(groups)} series/content variants', flush=True)
        sections = []; detail_ok = 0
        for i, members in enumerate(groups, 1):
            # A cached member can supply the shared body; prefer it to a fresh download.
            event = next((e for e in members if (self.out/'raw/details'/(str(e['id'])+'.html')).exists()), members[0])
            print(f'Detail group {i}/{len(groups)} ({len(members)} records): {event["title"]}', flush=True)
            url = event['permalink']; meta = event.get('meta', {}).copy()
            content = event.get('content', '')
            try:
                path = self.out / 'raw/details' / (str(event['id']) + '.html')
                if self.args.resume and path.exists():
                    raw = path.read_bytes()
                else:
                    raw = self.get(url)
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                detail = parse_detail(raw, event['id'])
                content = detail['content']['rendered']
                meta.update(detail.get('meta', {})); detail_ok += 1
            except Exception as exc:
                self.error('detail', url, exc)  # Keep full listing description as explicit fallback.
            lines = ['## ' + markdown(event['title']), '', '- Source: ' + url]
            for key, label in [('hotspots_place','Venue'),('address','Address'),('organizer','Organizer'),('price','Price'),('event_status','Status'),('recurrence_display','Recurrence'),('recurrence','Recurrence rule'),('url','Official event link')]:
                value = scalar(meta.get('vibemap_event_' + key))
                if value not in (None, '', []):
                    lines.append(f'- {label}: {markdown(str(value))}')
            tags = [t['name'] for t in event.get('tags', []) if not t.get('hide_from_frontend')]
            if tags:
                lines.append('- Tags: ' + ', '.join(tags))
            lines += ['', '### Listed dates and times', '']
            schedules = {}
            for member in members:
                mm = member.get('meta', {})
                pair = (str(scalar(mm.get('vibemap_event_start_date', ''))),
                        str(scalar(mm.get('vibemap_event_end_date', ''))))
                schedules.setdefault(pair, []).append(member['permalink'])
            for (a, b), urls in sorted(schedules.items()):
                lines.append(f'- {a} → {b or a}')
                # One displayed source per identical schedule; all URLs remain in JSON metadata.
                lines.append('  Source: ' + urls[0])
            lines += ['', '### Description', '', markdown(content)]
            sections.append('\n'.join(lines))
        body = '# Downtown Pittsburgh — All Events\n\n' + f'Date window: {self.args.start} through {self.args.end} (inclusive). All tags.\n\n' + '\n\n'.join(sections)
        complete = listing_complete and not self.errors
        doc = {'doc_id': hashlib.sha256((HOME + str(self.args.start) + str(self.args.end)).encode()).hexdigest()[:24], 'source_id': self.source['source id'].strip(), 'category': self.source['category'].strip(), 'source_url': HOME, 'source_urls': [e['permalink'] for e in chosen], 'title': 'Downtown Pittsburgh — All Events', 'document_type': 'events_collection', 'date_start': str(self.args.start), 'date_end': str(self.args.end), 'event_count': len(groups), 'occurrence_records': len(chosen), 'content_markdown': body, 'content_hash': hashlib.sha256(body.encode()).hexdigest(), 'complete': complete, 'quality_flags': [] if complete else ['incomplete_collection_see_summary'], 'pipeline_version': 'downtown-events-v2', 'processed_at': datetime.now(timezone.utc).isoformat()}
        atomic(self.out/'article.md', body+'\n')
        write_json(self.out/'document.json', doc)
        atomic(self.out/'documents.jsonl', json.dumps(doc, ensure_ascii=False)+'\n')
        write_json(self.out/'summary.json', {'documents': 1, 'api_unique_events': len(all_events), 'selected_occurrence_records': len(chosen), 'event_groups': len(groups), 'representative_details_succeeded': detail_ok, 'offline': self.args.offline, 'listing_complete': listing_complete, 'complete': complete, 'errors': self.errors, 'note': 'Only events currently published by the calendar; future coverage is not guaranteed. Dates are preserved as supplied; no recurrence is invented.'})
        print(f'Saved ONE document, {len(groups)} event groups / {len(chosen)} records; complete={complete}: {self.out}', flush=True)
        return complete


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, default=ROOT/'config/source.csv')
    p.add_argument('--output-root', type=Path, default=ROOT/'data/preprocessed_documents')
    p.add_argument('--start', type=date.fromisoformat, default=date(2026, 10, 28))
    p.add_argument('--end', type=date.fromisoformat, default=date(2027, 1, 1))
    p.add_argument('--resume', action='store_true')
    p.add_argument('--offline', action='store_true', help='Reuse cached lists/details only; missing details use listing descriptions and are flagged')
    p.add_argument('--max-pages', type=int, default=1000)
    args = p.parse_args()
    if args.offline:
        args.resume = True
    if args.start > args.end or args.max_pages < 1:
        p.error('Invalid date range or max-pages')
    if not Collector(args).run():
        raise SystemExit(2)


if __name__ == '__main__':
    main()
