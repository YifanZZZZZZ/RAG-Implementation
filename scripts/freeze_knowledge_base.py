"""Freeze a cleaned corpus with the user's explicit keep-all review decisions."""
import argparse,hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
def freeze(source,output):
    if output.exists():raise FileExistsError(output)
    data=(source/'documents.jsonl').read_bytes()
    docs=[json.loads(x) for x in data.decode().splitlines() if x.strip()]
    if not docs or len({d['doc_id'] for d in docs})!=len(docs):raise ValueError('Empty corpus or duplicate document IDs')
    decisions=[]
    for line in (source/'review_queue.jsonl').read_text().splitlines():
        if not line.strip():continue
        item=json.loads(line)
        reason=item.get('reason')
        decision='ignored_source_run_warning' if reason=='source_run_incomplete_review' else 'accepted_keep_all_url_variants' if reason=='same_url_different_content_review' else 'accepted_keep_document_with_flags'
        decisions.append({'review_item':item,'decision':decision,'basis':'User explicitly requested these dispositions before freezing; no claim of repaired missing data.'})
    output.mkdir(parents=True)
    (output/'documents.jsonl').write_bytes(data)
    for name in ['cleaning_report.json','review_queue.jsonl','duplicates.jsonl','quarantine.jsonl']:
        if (source/name).exists():shutil.copyfile(source/name,output/name)
    (output/'review_decisions.jsonl').write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in decisions))
    manifest={'sha256':hashlib.sha256(data).hexdigest(),'documents':len(docs),'source':str(source.resolve()),
        'frozen_at':datetime.now(timezone.utc).isoformat(),'review_decisions':len(decisions),
        'policy':'All retained documents and URL variants accepted as-is. Source-run reminders ignored. Original quality flags preserved.',
        'review_decisions_sha256':hashlib.sha256((output/'review_decisions.jsonl').read_bytes()).hexdigest()}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();freeze(a.source,a.output)
if __name__=='__main__':main()
