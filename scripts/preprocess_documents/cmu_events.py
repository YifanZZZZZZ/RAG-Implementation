"""CMU main calendar: inclusive 2026-10-26..2027-01-01, New York time.
One document per month, organized by day. Fetch direct details only.
"""
import argparse
import calendar
import csv
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from city_pittsburgh import to_markdown
from downtown_events import atomic, write_json

ROOT = Path(__file__).resolve().parents[2]
BASE = 'https://events.cmu.edu/'
TZ = ZoneInfo('America/New_York')
CAP = 1000
SYNTAX = '<widget type="events_calendar">' + ''.join(
    f'<arg id="{k}">{v}</arg>' for k,v in {
        'hide_repeats':'false', 'show_groups':'true', 'show_locations':'true',
        'show_tags':'true', 'month_view_day_limit':CAP, 'max':CAP,
        'search_all_events_only':'true', 'use_modular_templates':'true',
        'exclude_category':'exclude from main calendar', 'exclude_group':'Admin'
    }.items()) + '</widget>'
FIELDS = 'id,title,time,location,href,summary,until,repeats,is_multi_day,is_first_multi_day,multi_day_span,is_canceled,is_online'


def md(value):
    soup = BeautifulSoup(str(value or ''), 'html.parser')
    for n in list(soup.select('script,style,nav,aside,footer,form,iframe,button,img,svg')):
        n.decompose()
    for a in soup.select('a'):
        a.unwrap()  # Keep meaningful anchor text; never visit sublinks.
    return to_markdown(soup).strip()


def api_url(view, target, fields=FIELDS):
    return BASE + f'live/calendar/view/{view}/' + ('slug/' if view=='event' else 'date/') + target + '?' + urlencode({'user_tz':'America/New_York','template_vars':fields,'syntax':SYNTAX})


def month_days(data, start, end):
    if not isinstance(data,dict) or not isinstance(data.get('days'),list):
        raise ValueError('Unexpected month API schema')
    days = {}; errors = []
    if sum(len(d.get('events', [])) for d in data['days']) >= CAP:
        errors.append('Month response reaches safety cap; verify possible month-level truncation')
    for item in data['days']:
        d = datetime.strptime(item['date'],'%Y%m%d').date()
        if not start <= d <= end:
            continue
        rows = item.get('events',[])
        if not isinstance(rows,list):
            raise ValueError('Invalid events array')
        count = item.get('event_count')
        if count is None or int(count)!=len(rows) or len(rows)>=CAP:
            errors.append(f'{d}: count={count}, received={len(rows)}; possible truncation')
        unique = {}
        for e in rows:
            if not e.get('id') or not e.get('title') or not e.get('href'):
                raise ValueError(f'{d}: missing event identity/title/href')
            key = (str(e['id']),str(e.get('ts_start')),str(e.get('ts_end')))
            unique[key] = e
        days[d.isoformat()] = list(unique.values())
    expected = (end-start).days+1
    if len(days)!=expected:
        errors.append(f'Missing dates: expected {expected}, received {len(days)}')
    return days,errors


def internal_body(data, slug):
    e = data.get('event') if isinstance(data,dict) else None
    if not isinstance(e,dict) or str(e.get('id'))!=slug.split('-')[0]:
        raise ValueError('Missing/mismatched event detail ID')
    parts = []
    # Whitelist only main-body fields. Sidebar contacts, related_content,
    # social/share/map/registration controls are deliberately not extracted.
    for key in ['summary','description']:
        value = md(e.get(key))
        if value and value not in parts:
            parts.append(value)
    cost = md(e.get('cost'))
    if cost:
        parts.append('Cost: '+md(e.get('cost_type'))+' '+cost)
    if not parts:
        raise ValueError('No detail body returned')
    return '\n\n'.join(parts)


def external_body(raw):
    s = BeautifulSoup(raw,'html.parser')
    if s.title and re.search(r'just a moment|attention required|access denied',s.title.get_text(),re.I):
        raise ValueError('Blocked external detail')
    for n in list(s.select('nav,aside,footer,header,script,style,form,iframe,[role="complementary"],.sidebar,#sidebar,.Header,.Footer,.related-content,.related-events')):
        n.decompose()
    root = s.select_one('main article,main,.layout-content,article')
    if root is None:
        raise ValueError('No identifiable external main body; not falling back to full page')
    body = md(str(root))
    if len(body)<80:
        raise ValueError('External body too short or dynamic')
    return body


class Collector:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            rows=[r for r in csv.DictReader(f) if r['url'].strip().rstrip('/')==BASE.rstrip('/')]
        if len(rows)!=1:raise ValueError('Expected CMU events calendar row')
        self.source=rows[0];name=self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        config={'start':str(args.start),'end':str(args.end),'timezone':str(TZ),'scope':'main-calendar','cap':CAP}
        path=self.out/'collection.json'
        if path.exists() and (not args.resume or json.loads(path.read_text())!=config):
            raise ValueError('Use --resume with the same range or a new --output-root')
        write_json(path,config)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.rules={};self.last={};self.errors=[];self.details={}
        self.browser_context=None; self.blocked_origins=set()

    def request(self,url,robots=False):
        if urlsplit(url).scheme not in {'https','http'}:raise ValueError('Not an HTTP URL')
        for _ in range(6):
            p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
            if origin in self.blocked_origins:
                raise ValueError('Host previously returned 403/429; stopping repeated requests')
            if not robots and origin not in self.rules:
                raw=self.request(origin+'/robots.txt',robots=True)
                rule=RobotFileParser();rule.parse(raw.decode('utf-8','replace').splitlines());self.rules[origin]=rule
            rule=self.rules.get(origin)
            if not robots and not rule.can_fetch('ANLP-HW2-Research/1.0',url):raise ValueError('robots_disallowed')
            delay=max(1,rule.crawl_delay('ANLP-HW2-Research/1.0') or 0) if rule else 1
            if delay>30:raise ValueError('robots delay too long')
            time.sleep(max(0,delay-(time.monotonic()-self.last.get(origin,0))));self.last[origin]=time.monotonic()
            if self.browser_context is not None:
                response=self.browser_context.request.get(url, timeout=30000, max_redirects=0)
                raw=response.body();status=response.status
                self.check_response(url,status,dict(response.headers),raw)
                if 300<=status<400 and response.headers.get('location'):
                    url=urljoin(url,response.headers['location']);continue
                if robots and status==404:return b''
                if status>=400:raise ValueError(f'HTTP {status}: {url}')
                if len(raw)>30*1024*1024:raise ValueError('Download size limit')
                return raw
            with self.session.get(url,timeout=(10,30),stream=True,allow_redirects=False) as r:
                if r.is_redirect:
                    target=urljoin(url,r.headers['Location'])
                    if urlsplit(target).scheme not in {'https','http'}:raise ValueError('Invalid redirect')
                    url=target;continue
                if robots and r.status_code==404:return b''
                if r.status_code>=400:
                    excerpt=next(r.iter_content(16384),b'')
                    self.check_response(url,r.status_code,dict(r.headers),excerpt)
                r.raise_for_status();parts=[];size=0;began=time.monotonic()
                for part in r.iter_content(65536):
                    size+=len(part)
                    if size>30*1024*1024 or time.monotonic()-began>90:raise ValueError('Download limit')
                    parts.append(part)
                return b''.join(parts)
        raise ValueError('Redirect limit')

    def check_response(self,url,status,headers,raw):
        if status<400:return
        # No cookies/authentication headers are persisted in diagnostics.
        digest=hashlib.sha256(url.encode()).hexdigest()[:20]
        folder=self.out/'diagnostics'
        write_json(folder/(digest+'.json'),{'url':url,'status':status,
            'headers':{k:v for k,v in headers.items() if k.lower() in {'server','content-type','x-cache','cf-ray','retry-after'}},
            'response_excerpt':raw[:16384].decode('utf-8','replace')})
        if status in (403,429):
            p=urlsplit(url);self.blocked_origins.add(f'{p.scheme}://{p.netloc}')
            raise ValueError(f'HTTP {status}; stopped requests to this host. Error saved in {folder}')

    def cached(self,url,path):
        if self.args.resume and path.exists():return path.read_bytes()
        raw=self.request(url);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        return raw

    def detail(self,url):
        if url in self.details:return self.details[url]
        digest=hashlib.sha256(url.encode()).hexdigest()[:24]
        try:
            p=urlsplit(url)
            if p.hostname=='events.cmu.edu' and re.match(r'/event/\d+',p.path):
                slug=p.path.split('/event/',1)[1].strip('/')
                endpoint=api_url('event',slug,'id,title,summary,description,cost,cost_type')
                raw=self.cached(endpoint,self.out/'raw/details'/(digest+'.json'))
                body=internal_body(json.loads(raw),slug)
            else:
                # Calendar href itself may be an external detail. Fetch only that page.
                raw=self.cached(url,self.out/'raw/details'/(digest+'.html'))
                body=external_body(raw)
            self.details[url]=(body,None)
        except Exception as exc:
            self.errors.append({'stage':'detail','url':url,'reason':str(exc)})
            self.details[url]=('',str(exc))
        return self.details[url]

    def run(self):
        months=[];cursor=self.args.start.replace(day=1)
        while cursor<=self.args.end:
            months.append(cursor)
            cursor=(cursor.replace(day=28)+timedelta(days=4)).replace(day=1)
        docs=[];coverage=[]
        for index,month in enumerate(months,1):
            start=max(month,self.args.start);end=min(month.replace(day=calendar.monthrange(month.year,month.month)[1]),self.args.end)
            ym=month.strftime('%Y-%m');url=api_url('month',month.strftime('%Y%m%d'))
            print(f'Month {index}/{len(months)}: {ym} ({start}..{end})',flush=True)
            try:
                raw=self.cached(url,self.out/'raw/months'/(ym+'.json'))
                days,problems=month_days(json.loads(raw),start,end)
            except Exception as exc:
                self.errors.append({'stage':'month','month':ym,'reason':str(exc)});continue
            for problem in problems:self.errors.append({'stage':'coverage','month':ym,'reason':problem})
            lines=[f'# CMU Events — {ym}','',f'Timezone: {TZ}. Included dates: {start} through {end}.','']
            failed=set();sources=set();count=0
            for d,events in sorted(days.items()):
                print(f'  {d}: {len(events)} events',flush=True)
                lines += ['## '+d,'']
                if not events:lines += ['No events returned by the main calendar for this date.','']
                for e in events:
                    count+=1;detail_url=urljoin(BASE,e['href']);sources.add(detail_url)
                    lines += ['### '+md(e['title']),'']
                    if e.get('is_all_day'):lines.append('- Time: All day')
                    elif e.get('ts_start'):
                        lines.append('- Start: '+datetime.fromtimestamp(int(e['ts_start']),TZ).isoformat())
                    if e.get('ts_end'):lines.append('- End: '+datetime.fromtimestamp(int(e['ts_end']),TZ).isoformat())
                    for key,label in [('location','Location'),('repeats','Repeats'),('until','Until'),('is_canceled','Canceled')]:
                        if e.get(key):lines.append('- '+label+': '+md(e[key]))
                    lines += ['- Source: '+detail_url,'']
                    body,error=self.detail(detail_url)
                    if error:
                        failed.add(detail_url);body=md(e.get('summary'))
                    # Prevent embedded source headings from escaping the event section.
                    body=re.sub(r'^#{1,6}\s+', '#### ',body,flags=re.M)
                    lines += [body,'']
            body='\n'.join(lines).strip();complete=not problems and not failed
            folder=self.out/'months'/ym
            doc={'doc_id':hashlib.sha256((BASE+str(start)+str(end)).encode()).hexdigest()[:24],
                 'source_id':self.source['source id'].strip(),'category':self.source['category'].strip(),
                 'title':f'CMU Events — {ym}','source_url':BASE+'month','source_urls':sorted(sources),
                 'document_type':'monthly_events','year_month':ym,'date_start':str(start),'date_end':str(end),
                 'timezone':str(TZ),'days_covered':len(days),'event_occurrences':count,'complete':complete,
                 'quality_flags':([] if not problems else ['listing_incomplete'])+([] if not failed else ['detail_failures']),
                 'content_markdown':body,'content_hash':hashlib.sha256(body.encode()).hexdigest(),
                 'pipeline_version':'cmu-calendar-v1','processed_at':datetime.now(timezone.utc).isoformat()}
            atomic(folder/'article.md',body+'\n');write_json(folder/'document.json',doc)
            atomic(folder/'documents.jsonl',json.dumps(doc,ensure_ascii=False)+'\n');docs.append(doc)
            coverage.append({'month':ym,'days':len(days),'event_occurrences':count,'detail_failures':len(failed),'complete':complete})
        complete=len(docs)==len(months) and not self.errors
        if docs:atomic(self.out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs))
        write_json(self.out/'summary.json',{'documents':len(docs),'complete':complete,'months':coverage,'unique_detail_urls':len(self.details),'errors':self.errors,'note':'Main calendar snapshot only; unannounced events are unavailable. Empty dates are included. Detail sublinks and sidebars are excluded.'})
        print(f'Done: {len(docs)} monthly documents; complete={complete}; {self.out}',flush=True)
        return complete


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start',type=date.fromisoformat,default=date(2026,10,26))
    p.add_argument('--end',type=date.fromisoformat,default=date(2027,1,1))
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--browser',action='store_true',help='Use a normal visible Chromium session and its HTTP context; no challenge bypass')
    args=p.parse_args()
    if args.start>args.end:p.error('start must not exceed end')
    collector=Collector(args)
    if args.browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=False)
            try:
                context=browser.new_context(timezone_id='America/New_York')
                page=context.new_page()
                response=page.goto(BASE+'month',wait_until='domcontentloaded',timeout=45000)
                if response and response.status>=400:
                    collector.check_response(BASE+'month',response.status,dict(response.headers),response.body())
                    raise ValueError('Calendar page is not accessible in browser')
                collector.browser_context=context
                ok=collector.run()
            finally:
                browser.close()
    else:
        ok=collector.run()
    if not ok:raise SystemExit(2)


if __name__=='__main__':main()
