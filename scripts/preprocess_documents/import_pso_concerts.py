"""Offline import of manual/page1.html..page5.html; preserve other PSO sections."""
import argparse,csv,hashlib,json,shutil
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from downtown_events import atomic,write_json
from pittsburgh_symphony import BASE,canonical
ROOT=Path(__file__).resolve().parents[2]
def text(n):return n.get_text(' ',strip=True) if n else ''
def extract(path,page):
 s=BeautifulSoup(path.read_bytes(),'html.parser');current=s.select_one('.pagination [aria-current="page"]')
 if text(current)!=str(page):raise ValueError(f'{path}: wrong page number {text(current)}')
 cards=s.select('main article.event')
 if not cards:raise ValueError(f'{path}: no event cards')
 rows=[]
 for card in cards:
  a=card.select_one('.title a[href]');title=text(a)
  if not a or not title:raise ValueError('Missing event title')
  url=canonical(urljoin(BASE,a['href']))
  if not url or not any(route in url for route in ['/production/','/event/']):raise ValueError('Invalid event URL')
  fields={label:text(card.select_one(selector)) for label,selector in [('Dates','time'),('Venue','.venue'),('Organization','.organization')]}
  fields['Categories']=', '.join(text(n) for n in card.select('.category'))
  if not fields['Dates']:raise ValueError('Missing displayed dates')
  rows.append({'url':url,'title':title,'fields':fields,'page':page,'file':str(path.resolve())})
 return rows

def run(args):
 with (args.project/'config/source.csv').open(encoding='utf-8-sig',newline='') as f:
  source=next(r for r in csv.DictReader(f) if canonical(r['url'].strip())==BASE+'/')
 out=args.project/'data/preprocessed_documents'/source['source id'].strip()
 previous=json.loads((out/'summary.json').read_text())
 others=[];hashes={}
 for k in ['blog','musicians','faq']:
  p=out/k/'documents.jsonl';hashes[k]=hashlib.sha256(p.read_bytes()).hexdigest()
  others.extend(json.loads(line) for line in p.read_text().splitlines() if line.strip())
 rows=[];counts=[]
 for n in range(1,6):
  found=extract(args.manual_dir/f'page{n}.html',n);rows.extend(found);counts.append(len(found))
 groups={}
 for r in rows:groups.setdefault(r['url'],[]).append(r)
 docs=[];stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
 stage=out/('_concerts_import_'+stamp)
 for url,items in groups.items():
  identity=hashlib.sha256(url.encode()).hexdigest()[:24];body=['# '+items[0]['title'],''];seen=set()
  for item in items:
   signature=json.dumps(item['fields'],sort_keys=True)
   if signature in seen:continue
   seen.add(signature)
   if len(seen)>1:body+=['','## Additional listing information','']
   for key,value in item['fields'].items():
    if value:body.append('- '+key+': '+value)
  body='\n'.join(body)
  doc={'doc_id':identity,'title':items[0]['title'],'source_id':source['source id'].strip(),'category':source['category'].strip(),
       'section':'concerts','role':'listing_event','document_type':'event_listing','source_url':url,
       'listing_urls':[BASE+'/calendar'+(f'?page={i["page"]}' if i['page']!=1 else '') for i in items],
       'sources':[{'raw_path':i['file'],'page':i['page'],'acquisition_method':'manual_html'} for i in items],
       'content_markdown':body,'content_hash':hashlib.sha256(body.encode()).hexdigest(),
       'detail_included':False,'coverage':'provided_manual_listings_only','pipeline_version':'pso-manual-concerts-v1'}
  write_json(stage/'records'/(identity+'.json'),doc);atomic(stage/'markdown'/(identity+'.md'),body+'\n');docs.append(doc)
 for n in range(1,6):
  dest=stage/'raw'/f'page{n}.html';dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(args.manual_dir/f'page{n}.html',dest)
 atomic(stage/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs))
 archive=out/'_archive'/stamp;archive.mkdir(parents=True)
 for name in ['summary.json','documents.jsonl','selected_details.json']:
  if (out/name).exists():shutil.copy2(out/name,archive/name)
 (out/'concerts').rename(archive/'concerts');stage.rename(out/'concerts')
 atomic(out/'documents.jsonl',''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs+others))
 summary=previous.copy();summary['errors']=[e for e in previous.get('errors',[]) if e.get('kind')!='concerts']
 summary['sections']['concerts']={'mode':'manual_listings_only','listing_pages':5,'page_event_counts':counts,'listing_cards':len(rows),'unique_events':len(docs),'detail_required':False,'details_saved':0,'complete':True,'coverage':'all cards in provided files; cross-page filter consistency not verified'}
 summary['documents']=len(docs)+len(others);summary['complete']=not summary['errors'] and all(x.get('complete') for x in summary['sections'].values())
 summary['scope_note']='Concert completeness refers only to provided manual listing cards; full detail bodies intentionally excluded.'
 write_json(out/'summary.json',summary)
 write_json(out/'concert_policy.json',{'mode':'manual_listings_only','detail_required':False})
 for k,h in hashes.items():assert hashlib.sha256((out/k/'documents.jsonl').read_bytes()).hexdigest()==h
 print(json.dumps({'page_counts':counts,'unique_events':len(docs),'other_documents_unchanged':len(others),'total_documents':len(docs)+len(others),'archive':str(archive)},ensure_ascii=False,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,default=ROOT);p.add_argument('--manual-dir',type=Path)
 args=p.parse_args()
 if args.manual_dir is None:args.manual_dir=args.project/'data/manual'
 run(args)
