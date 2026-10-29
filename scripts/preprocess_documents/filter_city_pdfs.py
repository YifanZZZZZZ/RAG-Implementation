"""Remove PDF documents and artifacts from a city crawl. Preview by default.

Stop the collector before running this script. --apply permanently removes files;
no copies of the removed document content are retained. HTML content is unchanged.
"""
import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def rows(path):
    if path.exists():
        with path.open(encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def pdf_url(value):
    return isinstance(value,str) and urlsplit(value.split('|',1)[0]).path.lower().endswith('.pdf')


def run(folder, apply=False):
    folder=folder.resolve()
    if not (folder/'documents.jsonl').is_file() or not (folder/'records').is_dir():
        raise ValueError('Expected a city crawl folder with documents.jsonl and records/')
    ids,urls,deleted_files=set(),set(),set()
    retained_files=set()
    counts=Counter(documents_before=0, pdf_documents_removed=0)

    def local_artifact(value):
        if not value:return None
        p=Path(value)
        # Support moving the source folder while retaining old absolute metadata.
        if not p.is_absolute():p=folder/p
        try:p.resolve().relative_to(folder)
        except ValueError:
            parts=p.parts
            for name in ('raw','markdown','records'):
                if name in parts:
                    index=len(parts)-1-list(reversed(parts)).index(name)
                    p=folder.joinpath(*parts[index:]);break
            else:raise ValueError('Refusing artifact outside source folder: '+value)
        resolved=p.resolve()
        resolved.relative_to(folder)  # also rejects symlinks escaping the folder
        if resolved==folder:raise ValueError('Artifact points at root')
        return resolved

    def examine(doc,record=None):
        is_pdf=doc.get('document_type')=='pdf'
        paths=[]
        if record:paths.append(record.resolve())
        if doc.get('markdown_path'):paths.append(local_artifact(doc['markdown_path']))
        if doc.get('raw_path'):paths.append(local_artifact(doc['raw_path']))
        for source in doc.get('sources',[]):
            if source.get('raw_path'):paths.append(local_artifact(source['raw_path']))
        if is_pdf:
            if doc.get('doc_id'):ids.add(doc['doc_id'])
            for key in ('source_url','requested_url','final_url','declared_canonical_url'):
                if doc.get(key):urls.add(doc[key])
            for source in doc.get('sources',[]):
                for key in ('source_url','requested_url','final_url'):
                    if source.get(key):urls.add(source[key])
            deleted_files.update(paths)
        else:retained_files.update(paths)

    for doc in rows(folder/'documents.jsonl'):
        counts['documents_before']+=1
        if doc.get('document_type')=='pdf':counts['pdf_documents_removed']+=1
        examine(doc)
    for record in (folder/'records').glob('*.json'):
        examine(json.loads(record.read_text(encoding='utf-8')),record)
    for identity in ids:
        if not identity or Path(identity).name!=identity:raise ValueError('Invalid document id')
        deleted_files.update([folder/'records'/(identity+'.json'),folder/'markdown'/(identity+'.md')])
    # Raw filenames use requested URL hashes, which can differ from final URL ids.
    for url in urls:
        stem=hashlib.sha256(url.encode()).hexdigest()[:24]
        deleted_files.update((folder/'raw').glob(stem+'.*'))
    # Also delete orphan/failed PDF downloads, including non-.pdf URLs served as PDF.
    for raw in (folder/'raw').iterdir():
        if raw.is_file():
            with raw.open('rb') as f:magic=f.read(5)
            if raw.suffix.lower()=='.pdf' or magic==b'%PDF':deleted_files.add(raw.resolve())
    deleted_files={p.resolve() for p in deleted_files if p.exists()}
    if deleted_files & retained_files:
        raise ValueError('A PDF artifact is shared with a retained document; refusing ambiguous deletion')

    def removed(record):
        if record.get('document_type')=='pdf' or record.get('doc_id') in ids:return True
        for key in ('url','source_url','requested_url','final_url','parent_url'):
            value=record.get(key)
            if value and (value in urls or pdf_url(value)):return True
        return False

    # Stage all rewritten files before mutating originals. No PDF backup is kept.
    staged=[]
    def stage_json(name,value):
        if apply:
            temp=folder/(name+'.filter-tmp')
            temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            staged.append((temp,folder/name))
    try:
        for name in ['documents.jsonl','crawl_log.jsonl','discovered_urls.jsonl','failures.jsonl','coverage_issues.jsonl']:
            path=folder/name
            if not path.exists():continue
            temp=folder/(name+'.filter-tmp')
            out=temp.open('w',encoding='utf-8') if apply else None
            if apply:staged.append((temp,path))
            try:
                for record in rows(path):
                    if removed(record):counts[name+'_rows_removed']+=1
                    elif out:out.write(json.dumps(record,ensure_ascii=False)+'\n')
            finally:
                if out:out.close()
        state_path=folder/'state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        for key in ('queue','failed'):
            before=state.get(key,[])
            state[key]=[t for t in before if not removed(t)]
            counts['state_'+key+'_removed']=len(before)-len(state[key])
        for key in ('seen','done'):
            before=state.get(key,[])
            state[key]=[v for v in before if v.split('|',1)[0] not in urls and not pdf_url(v)]
        state['fingerprints']={k:v for k,v in state.get('fingerprints',{}).items() if k not in urls and not pdf_url(k)}
        if state_path.exists():stage_json('state.json',state)
        counts['files_deleted']=len(deleted_files)
        counts['bytes_deleted']=sum(p.stat().st_size for p in deleted_files)
        counts['documents_after']=counts['documents_before']-counts['documents.jsonl_rows_removed']
        for sub in ('records','raw','markdown'):
            counts[sub+'_files_deleted']=sum(p.parent==folder/sub for p in deleted_files)
        timestamp=datetime.now(timezone.utc).isoformat()
        summary_path=folder/'summary.json'
        if summary_path.exists():
            summary=json.loads(summary_path.read_text())
            summary.update(documents=counts['documents_after'],pending=len(state.get('queue',[])),
                           failed_tasks=len(state.get('failed',[])),queue_exhausted=not state.get('queue'),
                           filtered_at=timestamp)
            summary.setdefault('limits',{})['skip_pdfs']=True
            stage_json('summary.json',summary)
        stage_json('filter_policy.json',{'exclude_document_types':['pdf'],'skip_pdfs':True,'updated_at':timestamp})
        report={'mode':'apply' if apply else 'preview','folder':str(folder),'counts':dict(counts),
                'note':'Non-PDF document body text, including links mentioning PDFs, is unchanged.'}
        if apply:
            # Policy first prevents accidental reintroduction on a subsequent resume.
            staged.sort(key=lambda pair:pair[1].name!='filter_policy.json')
            for temp,path in staged:os.replace(temp,path)
            for path in sorted(deleted_files):path.unlink()
            (folder/'filter_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        return report
    finally:
        for temp,path in staged:
            if temp.exists():temp.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,default=ROOT/'data/preprocessed_documents/City of Pittsburgh webpage')
    p.add_argument('--apply',action='store_true',help='Permanently remove matched files and rewrite manifests')
    args=p.parse_args()
    print(json.dumps(run(args.directory,args.apply),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
