"""Collect the 12 homepage month cards and all Load More event occurrences.

No other homepage sections are crawled. Output is grouped by actual YYYY-MM.
Playwright loads monthly lists; requests fetches same-site event detail HTML.
"""
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
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from city_pittsburgh import Robots

ROOT=Path(__file__).resolve().parents[2]
HOME='https://pittsburgh.events/'
MONTHS={name.lower():i for i,name in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}


def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def txt(n):return n.get_text(' ',strip=True) if n else ''
def dump(p,d):
    p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp')
    t.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');t.replace(p)


def month_links(raw):
    s=BeautifulSoup(raw,'html.parser')
    h=next((h for h in s.find_all(['h2','h3']) if txt(h)=='Pittsburgh Events by Month'),None)
    if h is None:raise ValueError('Month section not found')
    cards=h.parent.select('a.city-months-block__card[href]')
    result=[]
    for a in cards:
        label=txt(a.select_one('.city-categories-block__card-title'));number=MONTHS.get(label.lower())
        line=txt(a.select_one('.city-months-block__card-line'))
        year=re.search(r'\b(20\d{2})\b',line)
        url=urljoin(HOME,a['href'])
        if not number or not year or urlsplit(url).hostname!='pittsburgh.events':raise ValueError('Invalid month card: '+txt(a))
        match=re.search(r'([\d,]+) Upcoming Events',txt(a))
        result.append({'url':url,'month':number,'year':int(year[1]),'year_month':f'{year[1]}-{number:02d}',
                       'home_count':int(match[1].replace(',','')) if match else None})
    if len(result)!=12 or len({r['month'] for r in result})!=12:raise ValueError('Expected exactly 12 unique months')
    return result


def event_rows(raw,source_url):
    s=BeautifulSoup(raw,'html.parser');events=[];errors=[]
    count=s.select_one('.gsb-event-num');match=re.search(r'Upcoming Events:\s*([\d,]+)',txt(count))
    expected=int(match[1].replace(',','')) if match else None
    for i,row in enumerate(s.select('ul.gsb-events-list > li.gsb-event-row')):
        try:
            ticket=row.select_one('a.gsb-tickets-button[href]')
            name_node=row.select_one('.gsb-venue-desc.is-mobile div')
            name=txt(name_node)
            label=ticket.get('aria-label','') if ticket else ''
            date=re.search(r'\bon (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)',label)
            if not name:
                name=re.sub(r'^View Tickets for | on \d{4}-.*$','',label)
            if date:
                start=date[1];dt=datetime.fromisoformat(start)
            else:
                # Use displayed month/year/day; never infer the year from today's date.
                month=txt(row.select_one('.d-month'));day=txt(row.select_one('.d-day'));year=txt(row.select_one('.d-year'))
                dt=datetime.strptime(f'{month} {day} {year}','%b %d %Y');start=dt.date().isoformat()
            venue_node=row.select_one('.gsb-date-desc.is-desktop a')
            venue=txt(venue_node) or txt(row.select_one('.gsb-date-desc.is-mobile'))
            time_node=row.select_one('.gsb-time')
            if time_node:
                for child in list(time_node.find_all(recursive=False)):child.decompose()
            display_time=txt(time_node)
            url=urljoin(source_url,ticket['href']) if ticket else source_url
            if not name:raise ValueError('Missing event title')
            identity=sha('|'.join([url,name,start,display_time,venue]))[:24]
            events.append({'doc_id':identity,'title':name,'start_date':start,'year_month':dt.strftime('%Y-%m'),
                           'time_display':display_time,'venue':venue,'venue_url':urljoin(source_url,venue_node['href']) if venue_node else None,
                           'address':txt(row.select_one('.gsb-location.is-desktop')),
                           'category':txt(row.select_one('.gsb-category-pill:not(.gsb-category-pill--highDemand)')),
                           'ticket_information':txt(row.select_one('.gsb-from-price')),
                           'source_url':url,'listing_url':source_url,'raw_card_html':str(row)})
        except Exception as exc:errors.append({'row':i,'reason':str(exc),'text':txt(row)})
    return events,expected,errors


def detail_text(raw):
    """Use explicit event structured data, not ticket widgets/footer/other events."""
    s=BeautifulSoup(raw,'html.parser');details=[]
    def visit(value):
        if isinstance(value,list):
            for x in value:visit(x)
        elif isinstance(value,dict):
            kind=value.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if any(str(k).endswith('Event') for k in kind):
                fields={k:value[k] for k in ['name','startDate','endDate','description','eventStatus','location','organizer','performer'] if k in value}
                if fields:details.append(fields)
            if '@graph' in value:visit(value['@graph'])
    for node in s.select('script[type="application/ld+json"]'):
        try:visit(json.loads(node.get_text()))
        except (ValueError,TypeError):pass
    return details


class Collector:
    def __init__(self,args):
        self.args=args
        with args.sources.open(encoding='utf-8-sig',newline='') as f:
            rows=[r for r in csv.DictReader(f) if r['url'].strip().rstrip('/')==HOME.rstrip('/')]
        if len(rows)!=1:raise ValueError('Expected Pittsburgh events calendar in source.csv')
        self.source=rows[0];name=self.source['source id'].strip()
        if not name or '/' in name or '\\' in name or name in {'.','..'}:raise ValueError('Unsafe source name')
        self.out=args.output_root/name
        if self.out.exists() and any(self.out.iterdir()) and not args.resume:raise FileExistsError('Use --resume or a new --output-root')
        self.out.mkdir(parents=True,exist_ok=True)
        for d in ['raw/months','raw/details','months']:(self.out/d).mkdir(parents=True,exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']='ANLP-HW2-Research/1.0'
        self.rules=None;self.last=0
    def log(self,name,value):
        with (self.out/name).open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
    def fetch(self,url):
        for _ in range(6):
            if urlsplit(url).hostname!='pittsburgh.events':raise ValueError('External redirect rejected')
            if self.rules and not self.rules.allowed(url):raise ValueError('robots_disallowed')
            delay=max(1,self.rules.delay if self.rules else 0)
            if delay>30:raise ValueError('robots delay too long')
            time.sleep(max(0,delay-(time.monotonic()-self.last)));self.last=time.monotonic()
            with self.session.get(url,timeout=(10,30),stream=True,allow_redirects=False) as r:
                if r.is_redirect:url=urljoin(url,r.headers['Location']);continue
                if url.endswith('/robots.txt') and r.status_code==404:return b''
                r.raise_for_status();parts=[];size=0;begin=time.monotonic()
                for part in r.iter_content(65536):
                    size+=len(part)
                    if size>20*1024*1024 or time.monotonic()-begin>120:raise ValueError('download_limit')
                    parts.append(part)
                return b''.join(parts)
        raise ValueError('redirect_limit')
    def expand(self,page,month):
        url=month['url']
        if not self.rules.allowed(url):raise ValueError('robots_disallowed')
        r=page.goto(url,wait_until='domcontentloaded',timeout=45000)
        if r and r.status>=400:raise ValueError(f'HTTP {r.status}')
        page.wait_for_selector('.gsb-event-num',timeout=30000)
        clicks=0;failure=None
        while True:
            button=page.locator('.gsb-show-more').filter(has_text='Load More Events')
            if not button.count() or not button.first.is_visible():break
            if clicks>=self.args.max_load_more:
                failure='load_more_limit';break
            old=page.locator('ul.gsb-events-list > li.gsb-event-row').count()
            try:
                page.wait_for_timeout(int(max(1,self.rules.delay)*1000));button.first.click(timeout=15000)
                page.wait_for_function('(n)=>document.querySelectorAll("ul.gsb-events-list > li.gsb-event-row").length>n',arg=old,timeout=30000)
                clicks+=1
                print(f"  {month['year_month']}: Load More {clicks}, {page.locator('li.gsb-event-row').count()} rows",flush=True)
            except Exception as exc:failure='load_more_failed:'+str(exc);break
        html=page.content()
        events,expected,errors=event_rows(html,url)
        unique={e['doc_id']:e for e in events}
        complete=failure is None and not errors and expected is not None and len(events)==expected and len(unique)==len(events)
        report={**month,'declared_count':expected,'parsed_rows':len(events),'unique_occurrences':len(unique),
                'load_more_clicks':clicks,'complete':complete,'failure':failure,'parse_errors':errors,
                'home_count_matches':month['home_count']==expected,'captured_at':datetime.now(timezone.utc).isoformat()}
        (self.out/'raw/months'/(month['year_month']+'.html')).write_text(html,encoding='utf-8')
        dump(self.out/'raw/months'/(month['year_month']+'.events.json'),list(unique.values()))
        dump(self.out/'raw/months'/(month['year_month']+'.status.json'),report)
        return list(unique.values()),report
    def save_event(self,event,report):
        folder=self.out/'raw/event_cache';folder.mkdir(parents=True,exist_ok=True)
        identity=event['doc_id'];record=folder/(identity+'.json')
        if record.exists() and not self.args.retry_details:
            old=json.loads(record.read_text())
            if ('month_listing_incomplete' in old.get('quality_flags',[])) == (not report['complete']):return
        url=event['source_url'];details=[];detail_status='no_event_detail_url';detail_raw=None
        if url!=event['listing_url']:
            detail_raw=self.out/'raw/details'/(sha(url)[:24]+'.html')
            try:
                raw=detail_raw.read_bytes() if detail_raw.exists() else self.fetch(url)
                detail_raw.write_bytes(raw);details=detail_text(raw)
                detail_status='structured_event_data' if details else 'ticket_page_no_editorial_event_data'
            except Exception as exc:
                detail_status='detail_failed';self.log('failures.jsonl',{'url':url,'stage':'detail','reason':str(exc)})
        flags=[]
        if not report['complete']:flags.append('month_listing_incomplete')
        if detail_status=='detail_failed':flags.append('detail_fetch_failed')
        item={k:v for k,v in event.items() if k!='raw_card_html'}
        item.update(quality_flags=flags,detail_status=detail_status,detail_data=details,
                    fetched_at=report['captured_at'],
                    sources=[{'source_url':event['listing_url'],'raw_path':str((self.out/'raw/months'/(report['year_month']+'.html')).resolve())}])
        if detail_raw and detail_raw.exists():item['sources'].append({'source_url':url,'raw_path':str(detail_raw.resolve())})
        dump(record,item)
    def migrate_old(self):
        # Keep structured occurrence facts as extraction cache, not RAG documents.
        cache=self.out/'raw/event_cache';cache.mkdir(parents=True,exist_ok=True)
        legacy=[]
        for path in sorted((self.out/'months').glob('*/records/*.json')):
            old=json.loads(path.read_text())
            if old.get('pipeline_version')!='pittsburgh-events-months-v1':continue
            item={k:v for k,v in old.items() if k not in {'content_markdown','content_hash','markdown_path','pipeline_version','document_type'}}
            if 'event_category' in old:item['category']=old['event_category']
            target=cache/(old['doc_id']+'.json')
            if not target.exists():dump(target,item)
            legacy.append((path,path.parent.parent/'markdown'/(old['doc_id']+'.md')))
        return legacy
    def assemble(self):
        legacy=self.migrate_old()
        reports=[json.loads(p.read_text()) for p in sorted((self.out/'raw/months').glob('*.status.json'))]
        groups={r['year_month']:{} for r in reports}
        # Include every discovered occurrence, even if detail fetching was interrupted.
        for path in sorted((self.out/'raw/months').glob('*.events.json')):
            for event in json.loads(path.read_text()):
                event={k:v for k,v in event.items() if k!='raw_card_html'}
                event.setdefault('detail_status','not_fetched')
                event.setdefault('detail_data',[])
                groups.setdefault(event['year_month'],{})[event['doc_id']]=event
        for path in sorted((self.out/'raw/event_cache').glob('*.json')):
            event=json.loads(path.read_text());group=groups.setdefault(event['year_month'],{})
            # Listing fields are authoritative when a partial listing was refreshed.
            if event['doc_id'] in group:
                current=group[event['doc_id']]
                for key in ['detail_status','detail_data','sources','fetched_at']:
                    if key in event:current[key]=event[key]
            else:group[event['doc_id']]=event
        docs=[]
        for ym,by_id in sorted(groups.items()):
            events=sorted(by_id.values(),key=lambda e:(e['start_date'],e.get('time_display',''),e['title'],e['doc_id']))
            report=next((r for r in reports if r['year_month']==ym),None)
            complete=bool(report and report['complete'])
            folder=self.out/'months'/ym;folder.mkdir(parents=True,exist_ok=True)
            title=f'Pittsburgh Events — {ym}'
            lines=['# '+title,'',f'Event occurrences: {len(events)}','']
            source_urls=set();sources=[]
            if report:source_urls.add(report['url'])
            for event in events:
                lines += [f"## {event['start_date']} — {event['title']}",'']
                for key,label in [('start_date','Date/time'),('time_display','Displayed time'),('venue','Venue'),('address','Address'),('category','Category'),('ticket_information','Ticket information at collection')]:
                    if event.get(key):lines.append('- '+label+': '+event[key])
                for key,label in [('source_url','Event page'),('listing_url','Month listing')]:
                    if event.get(key):
                        lines.append(f"- {label}: {event[key]}");source_urls.add(event[key])
                for detail in event.get('detail_data',[]):
                    lines += ['', '### Event detail data']
                    for key,value in detail.items():
                        rendered=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else BeautifulSoup(str(value),'html.parser').get_text(' ',strip=True)
                        lines.append('- '+key+': '+rendered)
                sources.extend(event.get('sources',[]));lines.append('')
            if report:sources.append({'source_url':report['url'],'raw_path':str((self.out/'raw/months'/(ym+'.html')).resolve())})
            body='\n'.join(lines).strip();md=folder/'article.md'
            detail_failed=sum(e.get('detail_status')=='detail_failed' for e in events)
            not_fetched=sum(e.get('detail_status')=='not_fetched' for e in events)
            flags=[]
            if not complete:flags.append('month_listing_incomplete')
            if detail_failed:flags.append('some_details_failed')
            if not_fetched:flags.append('some_details_not_fetched')
            doc={'doc_id':sha('pittsburgh-events-month:'+ym)[:24],'title':title,'year_month':ym,
                 'source_id':self.source['source id'].strip(),'category':self.source['category'].strip(),
                 'source_url':report['url'] if report else (events[0]['listing_url'] if events else HOME),
                 'source_urls':sorted(source_urls),'sources':list({json.dumps(x,sort_keys=True):x for x in sources}.values()),
                 'document_type':'monthly_events','event_count':len(events),'content_markdown':body,'content_hash':sha(body),
                 'quality_flags':flags,'listing_complete':complete,'detail_failures':detail_failed,'details_not_fetched':not_fetched,
                 'pipeline_version':'pittsburgh-events-months-v2','markdown_path':str(md.resolve()),
                 'fetched_at':report.get('captured_at') if report else None,
                 'processed_at':datetime.now(timezone.utc).isoformat()}
            md.write_text(body+'\n',encoding='utf-8');dump(folder/'document.json',doc)
            (folder/'documents.jsonl').write_text(json.dumps(doc,ensure_ascii=False)+'\n',encoding='utf-8');docs.append(doc)
        target=self.out/'documents.tmp'
        target.write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs),encoding='utf-8');target.replace(self.out/'documents.jsonl')
        dump(self.out/'summary.json',{'documents':len(docs),'event_occurrences':sum(d['event_count'] for d in docs),
             'months':reports,'all_12_listings_complete':len(reports)==12 and all(r['complete'] for r in reports),
             'detail_failures':sum(d['detail_failures'] for d in docs),'details_not_fetched':sum(d['details_not_fetched'] for d in docs),
             'note':'One RAG document per YYYY-MM. Ingest root documents.jsonl OR monthly files, not both.'})
        # Remove only known v1 generated documents after replacement files are written.
        for record,md in legacy:
            record.unlink()
            if md.exists():md.unlink()
        for directory in (self.out/'months').glob('*/*'):
            if directory.is_dir() and directory.name in {'records','markdown'} and not any(directory.iterdir()):directory.rmdir()
    def run(self):
        self.migrate_old()
        if self.args.assemble_only:
            self.assemble();print('Monthly documents rebuilt from local files; no network requests.');return
        self.rules=Robots(self.fetch(HOME+'robots.txt').decode('utf-8','replace'))
        if self.rules.delay>30:raise ValueError('robots delay too long')
        manifest=self.out/'selected_months.json'
        if self.args.resume and manifest.exists():months=json.loads(manifest.read_text())
        else:
            raw=self.fetch(HOME);(self.out/'raw/home.html').write_bytes(raw);months=month_links(raw);dump(manifest,months)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=not self.args.headed)
            context=browser.new_context(timezone_id='America/New_York')
            # Keep JS/data needed for Load More; block media/ads and external navigations.
            def route(r):
                request=r.request
                if request.resource_type in {'image','media','font'}:return r.abort()
                if request.is_navigation_request():
                    if urlsplit(request.url).hostname!='pittsburgh.events' or not self.rules.allowed(request.url):return r.abort()
                return r.continue_()
            context.route('**/*',route);page=context.new_page()
            try:
                for index,month in enumerate(months,1):
                    print(f"[{index}/12] {month['year_month']} {month['url']}",flush=True)
                    try:
                        status=self.out/'raw/months'/(month['year_month']+'.status.json')
                        if self.args.resume and status.exists() and json.loads(status.read_text())['complete']:
                            report=json.loads(status.read_text());events=json.loads((self.out/'raw/months'/(month['year_month']+'.events.json')).read_text())
                        else:events,report=self.expand(page,month)
                        for i,event in enumerate(events,1):
                            print(f"  event {i}/{len(events)}: {event['title']}",flush=True);self.save_event(event,report)
                    except Exception as exc:self.log('failures.jsonl',{'url':month['url'],'stage':'month','reason':str(exc)});print('  FAILED:',str(exc),flush=True)
                    self.assemble()
            finally:browser.close();self.assemble()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--assemble-only',action='store_true',help='Convert existing local occurrence records into monthly documents without network')
    p.add_argument('--retry-details',action='store_true')
    p.add_argument('--headed',action='store_true')
    p.add_argument('--max-load-more',type=int,default=300)
    args=p.parse_args()
    if args.assemble_only:args.resume=True
    if args.max_load_more<1:p.error('--max-load-more must be positive')
    Collector(args).run()


if __name__=='__main__':main()
