"""Convert 13 explicitly selected public PDFs into per-URL Markdown documents.

Downloads only the selected files; no recursive crawl. Existing city PDF exclusion
policy is unaffected: these documents go to separate budget/tax source folders.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from pypdf import PdfReader
import pypdf

ROOT=Path(__file__).resolve().parents[2]
BASE='https://www.pittsburghpa.gov/files/assets/city/v/'
TAX=BASE+'1/finance/documents/tax-forms/'
BUDGET=BASE+'4/omb/documents/operating-budgets/2025-operating-budget.pdf'
ITEMS=[
 ('budget','2025 Operating Budget',BUDGET,2025),
 ('tax','Amusement Tax — 2026',TAX+'at-2026-fillable.pdf',2026),
 ('tax','Institution & Service Privilege Tax — 2026',TAX+'isp-2026-fillable_1.pdf',2026),
 ('tax','Local Services Tax — 2026',TAX+'ls-2026-fillable.pdf',2026),
 ('tax','Parking Tax — 2026',TAX+'2026-pt-tax-form.pdf',2026),
 ('tax','Payroll Expense Tax — 2026',TAX+'2026-et-1-tax-form.pdf',2026),
 ('tax',"Taxpayers' Bill of Rights — revised December 2025",TAX+'taxpayers-bill-of-rights-rev.-12.2025.pdf',2025),
 ('tax','Tax Rate by Tax Type — 2022',TAX+'16958_2022_tax_rate_by_tax_type.pdf',2022),
 ('tax','Tax Due Date Calendar — 2022',TAX+'16957_2022_tax_due_date_calendar_.pdf',2022),
 ('tax','Fire Sale Form',TAX+'firesale.pdf',None),
 ('tax','Payroll Expense Tax Allocation Schedule for Professional Organization — Form Instructions 2019',TAX+'6825_payroll_expense_tax_allocation_schedule_for_professional_organization_form_instructions8.15.19.pdf',2019),
 ('tax','Local Service Tax Allocation Schedule for Professional Employer Organization — Form Instructions 2019',TAX+'6822_local_service_tax_allocation_schedule_for_professional_employer_organization_form_instructions8.15.19.pdf',2019),
 ('tax','General Contractor Detail Report Instructions',TAX+'7065_general_contractor_detail_report_instructions.pdf',None),
]


def now():return datetime.now(timezone.utc).isoformat()
def digest(data):return hashlib.sha256(data).hexdigest()
def write_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)


def extract(raw,title):
    if not raw.lstrip().startswith(b'%PDF'):raise ValueError('Not a PDF: possible HTML error response')
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted and not reader.decrypt(''):raise ValueError('Encrypted PDF requires a password')
    page_info=[];parts=['# '+title];flags=[]
    for number,page in enumerate(reader.pages,1):
        if number==1 or number%25==0:print(f'  extracting page {number}/{len(reader.pages)}',flush=True)
        mode='layout';error=None
        try:body=page.extract_text(extraction_mode='layout',layout_mode_space_vertically=False) or ''
        except Exception as exc:
            mode='plain_fallback';error=str(exc)
            try:body=page.extract_text() or ''
            except Exception as fallback:
                body='';mode='failed';error += '; '+str(fallback)
        # Preserve horizontal spacing and Unicode; remove only invisible/control noise.
        body=body.replace('\r\n','\n').replace('\r','\n')
        body=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\u200b\ufeff]','',body)
        body='\n'.join(line.rstrip() for line in body.splitlines()).strip('\n')
        status='ok' if body.strip() else 'no_extractable_text'
        if mode=='failed':status='extraction_failed'
        if not body.strip():flags.append(f'{status}:page_{number}')
        elif len(body.strip())<40:flags.append(f'sparse_text:page_{number}')
        if mode=='plain_fallback':flags.append(f'layout_fallback:page_{number}')
        label=str(getattr(page,'page_label',number))
        heading=f'## PDF page {number}' + (f' (printed label: {label})' if label!=str(number) else '')
        parts.append(heading)
        if body.strip():
            fence='`'*max(3,1+max([len(x) for x in re.findall(r'`+',body)] or [0]))
            parts.append(fence+'text\n'+body+'\n'+fence)
        page_info.append({'page_number':number,'page_label':label,'characters':len(body),'status':status,'extraction_mode':mode,'error':error})
    if not any(p['status']=='ok' for p in page_info):raise ValueError('No extractable text; OCR or manual processing required')
    fields=[]
    try:
        # Do not inject internal field names or blank widgets into retrieval text.
        for name,field in (reader.get_fields() or {}).items():
            fields.append({'name':name,'label':str(field.get('/TU','')),'type':str(field.get('/FT','')),
                           'value':str(field.get('/V',''))})
    except Exception as exc:flags.append('form_field_inspection_failed:'+str(exc))
    return '\n\n'.join(parts),page_info,fields,flags


def download(session,url,max_mb):
    for _ in range(6):
        with session.get(url,timeout=(15,45),stream=True,allow_redirects=False) as response:
            if response.is_redirect:
                target=urljoin(url,response.headers['Location']);p=urlsplit(target)
                if p.scheme!='https' or p.hostname not in {'pittsburghpa.gov','www.pittsburghpa.gov'}:
                    raise ValueError('Unexpected external redirect')
                url=target;time.sleep(1);continue
            response.raise_for_status()
            chunks=[];size=0;start=time.monotonic();last=start
            for chunk in response.iter_content(262144):
                size+=len(chunk)
                if size>max_mb*1024*1024:raise ValueError('PDF exceeds download size limit')
                if time.monotonic()-start>300:raise ValueError('PDF download exceeds 300 seconds')
                chunks.append(chunk)
                if time.monotonic()-last>5:
                    print(f'  downloaded {size/1024/1024:.1f} MiB',flush=True);last=time.monotonic()
            raw=b''.join(chunks)
            if not raw.lstrip().startswith(b'%PDF'):raise ValueError('Server returned non-PDF content')
            return raw,url
    raise ValueError('Too many redirects')


def source_names(path):
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    result={}
    for group,url in [('budget',BUDGET),('tax','https://pittsburghpa.gov/finance/tax-forms')]:
        matches=[r for r in rows if r['url'].strip()==url]
        if len(matches)!=1:raise ValueError('Missing/ambiguous source.csv entry: '+url)
        name=matches[0]['source id'].strip()
        if not name or name in {'.','..'} or '/' in name or '\\' in name:raise ValueError('Unsafe source name')
        result[group]=matches[0]
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    p.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--only',choices=['all','budget','tax'],default='all')
    p.add_argument('--pdf-dir',type=Path,help='Use already downloaded PDFs with their original URL filenames')
    p.add_argument('--offline',action='store_true',help='Never download; fail visibly if a local/cache PDF is missing')
    p.add_argument('--reprocess',action='store_true',help='Re-extract completed records from local/cache PDF')
    p.add_argument('--max-mb',type=int,default=150)
    args=p.parse_args()
    if args.max_mb<=0:p.error('--max-mb must be positive')
    sources=source_names(args.sources)
    items=[i for i in ITEMS if args.only=='all' or i[0]==args.only]
    session=requests.Session();session.headers['User-Agent']='ANLP-HW2-Research/1.0 (selected public PDFs)'
    failures=0;folders={}
    for index,(group,title,url,year) in enumerate(items,1):
        source=sources[group];folder=args.output_root/source['source id'].strip();folders[group]=folder
        for sub in ['raw','markdown','records']:(folder/sub).mkdir(parents=True,exist_ok=True)
        identity=digest(url.encode())[:24];record=folder/'records'/(identity+'.json')
        pdf=folder/'raw'/Path(urlsplit(url).path).name;md=folder/'markdown'/(identity+'.md')
        print(f'[{index}/{len(items)}] {title}',flush=True)
        if record.exists() and not args.reprocess:
            print('  already converted; skipped',flush=True);continue
        entry={'url':url,'time':now()}
        try:
            supplied=args.pdf_dir/pdf.name if args.pdf_dir else None
            previous=json.loads(record.read_text()) if record.exists() else {}
            if supplied and supplied.exists():
                raw=supplied.read_bytes();final=url;method='local_pdf';fetched=None
            elif pdf.exists():
                raw=pdf.read_bytes();final=previous.get('final_url',url);method='cached_pdf';fetched=previous.get('fetched_at')
            else:
                if args.offline:raise FileNotFoundError('No local/cache PDF: '+pdf.name)
                raw,final=download(session,url,args.max_mb);method='http';fetched=now();time.sleep(1)
            if not raw.lstrip().startswith(b'%PDF'):raise ValueError('Invalid PDF signature')
            pdf.write_bytes(raw)
            body,pages,fields,flags=extract(raw,title)
            doc={'doc_id':identity,'title':title,'source_id':source['source id'].strip(),'source_url':url,
                 'final_url':final,'category':source['category'].strip(),'document_type':'pdf',
                 'document_year':year,'year_basis':'filename/title supplied by user; not inferred current applicability',
                 'content_markdown':body,'content_hash':digest(body.encode()),'raw_hash':digest(raw),
                 'page_count':len(pages),'pages':pages,'form_fields':fields,'quality_flags':flags,
                 'pipeline_version':'selected-pdfs-v1','pypdf_version':pypdf.__version__,
                 'fetched_at':fetched,'processed_at':now(),'markdown_path':str(md.resolve()),
                 'sources':[{'source_url':url,'final_url':final,'source_id':source['source id'].strip(),
                             'raw_path':str(pdf.resolve()),'acquisition_method':method}],
                 'extraction_note':'Layout-preserving text, not reconstructed tables. Blank form widgets are metadata only; images/charts require separate review.'}
            md.write_text(body+'\n',encoding='utf-8');write_json(record,doc)
            entry.update(status='converted',pages=len(pages),quality_flags=flags)
            print(f'  saved {len(pages)} pages; {len(flags)} quality flags',flush=True)
        except Exception as exc:
            failures+=1;entry.update(status='failed',error=str(exc));print('  FAILED:',str(exc),flush=True)
        with (folder/'conversion_log.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
        # Incremental assembly ensures completed documents remain usable on interruption.
        assemble(folder,group)
    for group,folder in folders.items():assemble(folder,group)
    print(f'Finished; {failures} failures this run. Rerun the same command to retry unfinished PDFs.')
    if failures:raise SystemExit(1)


def assemble(folder,group):
    expected={digest(url.encode())[:24] for g,title,url,year in ITEMS if g==group}
    records=[]
    for identity in sorted(expected):
        path=folder/'records'/(identity+'.json')
        if path.exists():records.append(json.loads(path.read_text()))
    tmp=folder/'documents.tmp'
    with tmp.open('w',encoding='utf-8') as f:
        for doc in records:f.write(json.dumps(doc,ensure_ascii=False)+'\n')
    tmp.replace(folder/'documents.jsonl')
    write_json(folder/'summary.json',{'expected_pdfs':len(expected),'converted_pdfs':len(records),
               'missing_document_ids':sorted(expected-{d['doc_id'] for d in records}),
               'pages':sum(d['page_count'] for d in records),
               'documents_with_quality_flags':sum(bool(d['quality_flags']) for d in records)})


if __name__=='__main__':main()
