"""Extract only 21 explicitly requested food/sports/travel pages. No crawling/pagination."""
import argparse,csv,hashlib,json,re
from pathlib import Path
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from carnegie_museums import Fetcher,key,soup,markdown
from wikipedia_cultural_institutions import parse_article
from downtown_events import atomic,write_json
from visit_pittsburgh import module,RELATED
ROOT=Path(__file__).resolve().parents[2]
# slug, requested URL, CSV source id, parser, optional Wikipedia title
TARGETS=[
('picklesburgh-news','https://www.picklesburgh.com/news/','Pickleburgh','news',None),
('free-things','https://www.visitpittsburgh.com/things-to-do/free-things-to-do/','Visit Pittsburgh webpage','visit',None),
('top-things','https://www.visitpittsburgh.com/blog/top-things-to-do/','Visit Pittsburgh webpage','visit',None),
('family-fun','https://www.visitpittsburgh.com/things-to-do/family-fun/?hitsPerPage=96','Visit Pittsburgh webpage','visit',None),
('restaurants-culinary','https://www.visitpittsburgh.com/restaurants-culinary/?hitsPerPage=96','Visit Pittsburgh webpage','visit',None),
('food-festivals','https://www.visitpittsburgh.com/events-festivals/food-festivals/','Food Festivals','visit',None),
('picklesburgh-wikipedia','https://en.wikipedia.org/wiki/Picklesburgh','Pickleburgh','wiki','Picklesburgh'),
('taco-about','https://www.pghtacofest.com/about','Pittsburgh Taco Fest','taco',None),
('taco-faqs','https://www.pghtacofest.com/faqs','Pittsburgh Taco Fest','taco',None),
('restaurant-week-summer-2026','https://pittsburghrestaurantweek.com/restaurants/summer-2026-restaurants/','Pittsburgh Restaurant Week','wordpress',None),
('little-italy','https://littleitalydays.com/','Little Italy Days','wordpress',None),
('little-italy-schedule','https://littleitalydays.com/entertainment-schedule/','Little Italy Days','wordpress',None),
('banana-activities','https://bananasplitfest.com/activities/','Banana Split Fest','wordpress',None),
('banana-events','https://bananasplitfest.com/events/','Banana Split Fest','wordpress',None),
('sports-teams','https://www.visitpittsburgh.com/things-to-do/pittsburgh-sports-teams/','Visit Pittsburgh Sports','visit',None),
('pirates','https://www.mlb.com/pirates','Pittsburgh Pirates','sports',None),
('pirates-wikipedia','https://en.wikipedia.org/wiki/Pittsburgh_Pirates','Pittsburgh Pirates','wiki','Pittsburgh Pirates'),
('steelers','https://www.steelers.com/','Pittsburgh Steelers','sports',None),
('steelers-wikipedia','https://en.wikipedia.org/wiki/Pittsburgh_Steelers','Pittsburgh Steelers','wiki','Pittsburgh Steelers'),
('penguins-wikipedia','https://en.wikipedia.org/wiki/Pittsburgh_Penguins','Pittsburgh Penguins','wiki','Pittsburgh Penguins'),
('penguins-news','https://www.nhl.com/penguins/news/','Pittsburgh Penguins','sports',None)]


def extract(raw,target):
    slug,url,source,kind,expected=target
    if kind=='wiki':
        title,body,canonical,revision=parse_article(raw,expected,url)
        return title,body,{'revision_url':revision,'declared_canonical_url':canonical}
    s=soup(raw)
    title=s.title.get_text(' ',strip=True) if s.title else slug
    if kind=='taco':
        root=BeautifulSoup('<div></div>','html.parser').div
        for n in s.select('body > .header-section,body > .speaker,body > .speakers-container'):
            root.append(n.extract())
    else:
        selectors=['main#main','main'] if kind=='visit' else ['main#main-content','main'] if kind=='sports' else ['main#main','main#main-content','main','#primary','#content'] if kind=='news' else ['.entry-content','[data-elementor-type="wp-post"]','main#main','main#main-content','main','article']
        root=None
        for selector in selectors:
            root=s.select_one(selector)
            if root is not None:break
    if root is None:raise ValueError('No known body container; save rendered HTML or review this template')
    dynamic_missing=[]
    if kind=='visit':
        for listing in root.select('.listings'):
            if not listing.select('a[href]'):
                dynamic_missing.append('dynamic_listing_missing')
                listing.decompose()
        for h in list(root.find_all(['h2','h3','h4'])):
            label=h.get_text(' ',strip=True)
            if h.parent and (RELATED.match(label) or label.casefold() in {'in this section','authentic experiences'}):
                module(h,root).decompose()
    for n in list(root.select('nav,aside,footer,[role="contentinfo"],.site-footer,#footer,.footer,.credits,.venue,.navbar,.menu,.subnav,.breadcrumb,.breadcrumbs,.cookie-banner,.newsletter,.social-share,.share-tools,.pagination,.tribe-events-c-subscribe-dropdown,[class*="related-"],[class*="recommended-"],.elementor-location-footer,.elementor-location-header,.elementor-location-popup')):
        if n.parent:n.decompose()
    # Do not lose titles or dates contained in an article's semantic header.
    for n in root.select('header'):n.name='div'
    for n in root.select('time'):
        n.name='span'  # retain visible date text
    h=root.find('h1')
    if h:title=h.get_text(' ',strip=True)
    body=markdown(root)
    if not body.startswith('# '):body='# '+title+'\n\n'+body
    if len(re.sub(r'^#+.*$', '',body,flags=re.M).strip())<80:
        raise ValueError('Too little body text; dynamic/blocked page needs saved rendered HTML')
    return title,body,{'content_scope':'current_page_static_text_only','quality_flags':sorted(set(dynamic_missing + (['no_dynamic_loading_performed'] if kind=='sports' else [])))}


def main(default=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--only',choices=[t[0] for t in TARGETS],default=default)
    p.add_argument('--visit-extras',action='store_true',help='Only the five additionally requested Visit Pittsburgh URLs')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--html-map',help='JSON URL -> HTML path; paths relative to mapping JSON')
    p.add_argument('--html',type=Path,help='Single saved HTML; requires --only or an individual script')
    p.add_argument('--offline',action='store_true')
    p.add_argument('--delay',type=float,default=1)
    args=p.parse_args()
    if args.visit_extras and args.only:p.error('--visit-extras cannot be combined with --only')
    if args.html and not args.only:p.error('--html requires a single selected page')
    if args.delay<0:p.error('delay must be nonnegative')
    with (ROOT/'config/source.csv').open(encoding='utf-8-sig') as f:
        sources={r['source id'].strip():r for r in csv.DictReader(f)}
    targets=[t for t in TARGETS if not args.only or t[0]==args.only]
    if args.visit_extras:targets=[t for t in targets if t[0] in {'free-things','top-things','sports-teams','family-fun','restaurants-culinary'}]
    groups={};errors=[];warnings=[]
    for index,t in enumerate(targets,1):
        slug,url,name,kind,_=t
        out=args.output_root/name/'Single Pages'
        groups.setdefault(out,[])
        print(f'[{index}/{len(targets)}] {url}',flush=True)
        try:
            source=sources[name]
            fetch=Fetcher(out,args)
            if args.html:fetch.mapping[url]=args.html.resolve()
            raw,origin=fetch.get(url)
            title,body,extra=extract(raw,t)
            d=dict(doc_id=key(url),source_id=name,source_url=url,category=source['category'].strip(),
                title=title,document_type='encyclopedia_article' if kind=='wiki' else 'html',
                content_markdown=body,content_hash=hashlib.sha256(body.encode()).hexdigest(),
                raw_path=str(out/'raw'/(key(url)+'.html')),input_origin=origin,
                processed_at=datetime.now(timezone.utc).isoformat(),pipeline_version='food-sports-single-pages-v1',**extra)
            write_json(out/'records'/(slug+'.json'),d)
            atomic(out/'markdown'/(slug+'.md'),body+'\n')
            print(f'  SAVED {len(body)} characters',flush=True)
            if 'dynamic_listing_missing' in extra.get('quality_flags',[]):
                warnings.append(dict(url=url,slug=slug,reason='dynamic_listing_missing'))
                print('  INCOMPLETE: dynamic listing absent; import browser-rendered HTML with --html/--html-map',flush=True)
        except Exception as e:
            error=dict(url=url,error=str(e),slug=slug);errors.append(error);groups[out].append(error)
            print('  FAILED: '+str(e),flush=True)
    for out,failures in groups.items():
        # Preserve other successful pages in this source when --only is used.
        records=[]
        for t in TARGETS:
            if out.parent.name!=t[2]:continue
            record=out/'records'/(t[0]+'.json')
            if record.exists():records.append(json.loads(record.read_text()))
        atomic(out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in records))
        write_json(out/'last_run.json',dict(documents_available=len(records),failures=failures,
            warnings=[w for w in warnings if any(t[0]==w['slug'] and t[2]==out.parent.name for t in TARGETS)],
            note='Prior successful records are retained if a refresh fails; inspect processed_at. Status covers selected pages in this run.'))
        write_json(out/'manual_html_map.example.json',{e['url']:e['slug']+'.html' for e in failures})
    print(f'Finished {len(targets)} requested pages; {len(errors)} failures. No child URLs requested.')
    raise SystemExit(2 if errors or warnings else 0)

if __name__=='__main__':main()
