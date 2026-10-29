"""Build an immutable, auditable Markdown knowledge snapshot; never edit inputs."""
import argparse,copy,hashlib,html,json,re,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
ROOT=Path(__file__).resolve().parents[1]
VERSION='knowledge-cleaning-v1'
EXCLUDE={'raw','records','markdown','manual','legacy','backup','backups','archive','archives','_archive','.git','__pycache__'}
ADDITIVE={'Single Pages','Wikipedia'}


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


def canonical(url):
    p=urlsplit(url.strip())
    if p.scheme not in {'http','https'} or not p.hostname:raise ValueError('Invalid HTTP source URL')
    host=p.hostname.lower()
    if ':' in host:host='['+host+']'
    if p.port and (p.scheme,p.port) not in {('http',80),('https',443)}:host+=':'+str(p.port)
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid','msclkid'}]
    return urlunsplit((p.scheme,host,p.path or '/',urlencode(query),p.fragment if p.fragment.startswith(('/','!')) else ''))


def discover(root):
    all_files=sorted(root.rglob('documents.jsonl'));selected=[];skipped=[]
    for p in all_files:
        parts=p.relative_to(root).parts
        if any(x in EXCLUDE or x.startswith('.') for x in parts[:-1]):
            skipped.append({'file':str(p),'reason':'archive_or_non_input_directory'});continue
        # Within each aggregate branch choose its topmost documents file.
        branch=p.parent
        additive=next((i for i,x in enumerate(parts[:-1]) if x in ADDITIVE),None)
        boundary=root.joinpath(*parts[:additive+1]) if additive is not None else root
        shadow=None
        parent=branch.parent
        while parent!=root.parent and (parent==boundary or boundary in parent.parents):
            if (parent/'documents.jsonl').exists():shadow=parent/'documents.jsonl';break
            if parent==boundary:break
            parent=parent.parent
        if shadow:skipped.append({'file':str(p),'reason':'covered_by_parent_aggregate','aggregate':str(shadow)})
        else:selected.append(p)
    return selected,skipped


def clean_markdown(text):
    changes=Counter();result=[];fence=None;blank=False
    for line in text.replace('\r\n','\n').replace('\r','\n').split('\n'):
        match=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if fence:
            result.append(line)
            if match and match[1][0]==fence[0] and len(match[1])>=len(fence):fence=None
            continue
        if match:fence=match[1];result.append(line);continue
        if line.startswith(('    ','\t')):result.append(line);continue  # indented code/list layout
        original=line
        line=unicodedata.normalize('NFC',line)
        line=re.sub('[\u200b\ufeff\u00ad]','',line).replace('\xa0',' ')
        # Only decode non-markup entities: never turn &lt; into an HTML element.
        line=re.sub(r'&(?:amp|quot|apos|nbsp);',lambda m:html.unescape(m[0]),line)
        line=re.sub(r'<!--.*?-->','',line)
        # Preserve Markdown hard-break spaces and all leading indentation.
        hard=line.endswith('  ')
        line=line.rstrip()+('  ' if hard and line.strip() else '')
        if line!=original:changes['normalized_lines']+=1
        # Exact standalone footer controls only, never contact/privacy paragraphs.
        label=re.sub(r'^\[([^]]+)\]\([^)]*\)$',r'\1',line.strip()).casefold()
        if label in {'privacy policy','cookie policy','cookie settings','manage cookies','back to top','skip to content','skip to main content'}:
            changes['standalone_ui_lines_removed']+=1;continue
        if not line.strip():
            if result and result[-1]=='':changes['extra_blank_lines_removed']+=1;continue
            result.append('')
        else:result.append(line)
    return '\n'.join(result).strip('\n'),dict(changes)


def flags(doc,body):
    result=[]
    if len(body)<150:result.append('short_body_review')
    if re.search(r'<(?:div|table|tr|td|script)\b',body,re.I):result.append('residual_html_review')
    if doc.get('detail_status')=='listing_only':result.append('missing_detail_body')
    if doc.get('requested_pages_complete') is False or doc.get('listing_complete') is False:result.append('incomplete_listing')
    if doc.get('detail_failures') or doc.get('details_not_fetched'):result.append('missing_event_details')
    q=doc.get('quality_flags',[])
    if q:result.append('source_quality_flags')
    # Similar paragraphs may represent responsive copies; review rather than delete globally.
    blocks=[b for b in body.split('\n\n') if len(b)>200 and not b.startswith(('#','|','```','~~~'))]
    if len(blocks)!=len(set(blocks)):result.append('repeated_long_block_review')
    return result


def blocked(body):
    # Conservative: only an error title plus a short body, not an article discussing HTTP errors.
    first=next((x.strip('# ').casefold() for x in body.splitlines() if x.strip()),'')
    return len(body)<1500 and first in {'403 forbidden','access denied','access restricted','just a moment...','attention required! | cloudflare','504 gateway time-out','504 gateway timeout'}


def build(input_root,output):
    files,skipped=discover(input_root)
    if not files:raise ValueError('No document inputs discovered')
    if output.exists():raise FileExistsError('Snapshot output already exists; choose a new path')
    docs=[];review=[];duplicates=[];manifest=[];quarantine=[];counts=Counter();changes=Counter();by_hash={};by_url=defaultdict(list)
    for p in files:
        manifest.append({'file':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        for line_no,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
            if not line.strip():continue
            counts['input_records']+=1
            ref={'input_file':str(p.resolve()),'line':line_no}
            try:
                d=json.loads(line)
                if not isinstance(d,dict):raise ValueError('Record is not an object')
                body=d.get('content_markdown')
                if not isinstance(body,str) or not body.strip():raise ValueError('Missing or empty content_markdown')
            except (ValueError,TypeError) as e:
                quarantine.append(dict(ref,reason=str(e),record=line));continue
            cleaned,edits=clean_markdown(body);changes.update(edits)
            if not cleaned or blocked(cleaned):
                quarantine.append(dict(ref,reason='empty_or_error_page',record=d));continue
            identity=digest(cleaned)
            url=d.get('source_url') or d.get('url') or ''
            issues=flags(d,cleaned)
            try:canon=canonical(url)
            except (ValueError,TypeError):canon=url;issues.append('invalid_or_missing_source_url')
            # Canonical declarations are evidence, not automatic cross-page merge authority.
            declared=d.get('declared_canonical_url')
            if declared:
                try:
                    if canonical(declared)!=canon:issues.append('canonical_url_mismatch_review')
                except ValueError:issues.append('invalid_declared_canonical')
            provenance=dict(ref,original_doc_id=d.get('doc_id'),source_url=url,canonical_source_url=canon,
                source_id=d.get('source_id'),original_content_hash=d.get('content_hash'),
                fetched_at=d.get('fetched_at'),processed_at=d.get('processed_at'),
                cleaning_changes=edits,quality_flags=d.get('quality_flags',[]),review_flags=issues,
                detail_status=d.get('detail_status'),content_scope=d.get('content_scope'),sources=d.get('sources',[]))
            if identity in by_hash:
                kept=by_hash[identity];kept['cleaning_provenance'].append(provenance)
                kept['sources'].append({'source_url':url,'source_id':d.get('source_id')})
                kept['review_flags']=sorted(set(kept['review_flags']+issues))
                duplicates.append(dict(ref,reason='identical_cleaned_markdown',kept_doc_id=kept['doc_id'],source_url=url))
                if canon and kept['doc_id'] not in by_url[canon]:by_url[canon].append(kept['doc_id'])
                continue
            out=copy.deepcopy(d);out.pop('blocks',None)
            out.update(doc_id=identity[:24],content_markdown=cleaned,content_hash=identity,
                canonical_source_url=canon,cleaning_version=VERSION,cleaning_provenance=[provenance],review_flags=issues)
            out['sources']=copy.deepcopy(d.get('sources') or [])
            out['sources'].append({'source_url':url,'source_id':d.get('source_id')})
            docs.append(out);by_hash[identity]=out
            if canon:by_url[canon].append(out['doc_id'])
    # A failed crawl may have produced no document at all; inspect adjacent run summaries too.
    run_warnings=[]
    for p in files:
        for name in ('summary.json','last_run.json'):
            status=p.parent/name
            if not status.exists():continue
            try:
                info=json.loads(status.read_text(encoding='utf-8'))
                if info.get('complete') is False or info.get('failures') or info.get('warnings'):
                    warning={'reason':'source_run_incomplete_review','status_file':str(status.resolve()),
                             'complete':info.get('complete'),'failures':info.get('failures'),'warnings':info.get('warnings')}
                    run_warnings.append(warning);review.append(warning)
            except (ValueError,TypeError) as e:
                review.append({'reason':'invalid_source_status_file','file':str(status),'error':str(e)})
    lookup={d['doc_id']:d for d in docs}
    for url,ids in by_url.items():
        if len(ids)>1:
            for id_ in ids:lookup[id_]['review_flags'].append('same_url_different_content_review')
            review.append({'reason':'same_url_different_content_review','url':url,'doc_ids':ids,'action':'retained_all_variants_no_automatic_longest_selection'})
    for d in docs:
        d['review_flags']=sorted(set(d['review_flags']))
        if d['review_flags']:review.append({'doc_id':d['doc_id'],'source_url':d.get('source_url'),'reasons':d['review_flags'],'action':'retained_pending_review'})
    output.mkdir(parents=True)
    def jsonl(name,rows):
        (output/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    jsonl('documents.jsonl',docs);jsonl('duplicates.jsonl',duplicates);jsonl('review_queue.jsonl',review);jsonl('quarantine.jsonl',quarantine)
    report=dict(version=VERSION,created_at=datetime.now(timezone.utc).isoformat(),input_records=counts['input_records'],output_documents=len(docs),
        exact_duplicates=len(duplicates),quarantined=len(quarantine),documents_needing_review=sum(bool(d['review_flags']) for d in docs),
        same_url_conflicts=sum(len(ids)>1 for ids in by_url.values()),source_run_warnings=len(run_warnings),changes=dict(changes),selected_files=manifest,skipped_files=skipped,
        policy='No fuzzy deduplication; differing URL variants retained for review. No dates/prices rewritten. No annotations or evaluation answers used.')
    (output/'cleaning_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'selected_files','skipped_files','policy'}},indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-root',type=Path,default=ROOT/'data/preprocessed_documents')
    p.add_argument('--output',type=Path,default=ROOT/'data/knowledge_base'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args=p.parse_args()
    if args.input_root.resolve()==args.output.resolve() or args.input_root.resolve() in args.output.resolve().parents:p.error('Output must be outside input tree')
    build(args.input_root,args.output)
    print('Snapshot: '+str(args.output.resolve()))

if __name__=='__main__':main()
