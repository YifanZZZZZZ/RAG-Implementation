"""Offline reconstruction of the researched supplement; does not run chunking or models."""
import importlib.util,json,re,string,unicodedata,hashlib,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
import argparse, tempfile
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description="Rebuild traceable day3 prose supplements from cached official records and saved local listings.")
p.add_argument('--output',type=Path,required=True)
p.add_argument('--source',type=Path,default=ROOT/'data/supplements/day3_web_verified_v1/provided_original.json')
p.add_argument('--cache',type=Path,default=ROOT/'data/supplements/day3_web_verified_v1/raw/cmoa')
p.add_argument('--base',type=Path,default=ROOT/'data/snapshots/cleaned_20261028T162326827998Z/documents.jsonl')
p.add_argument('--questions',type=Path,default=ROOT/'data/evaluation/test_set_day_3.json')
a=p.parse_args()
OUT=a.output; SOURCE=a.source; BASE=a.base; QUESTIONS=a.questions; CACHE=a.cache
if OUT.exists():raise FileExistsError(OUT)

def normalize(t):
    return ' '.join(unicodedata.normalize('NFKC',t).casefold().replace('’',"'").replace('–','-').split())
def qnorm(t):
    return ' '.join(re.sub(r'\b(a|an|the)\b',' ',t.lower().translate(str.maketrans('','',string.punctuation))).split())
def write(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
original=json.loads(SOURCE.read_text());questions=json.loads(QUESTIONS.read_text())
initial={q['id']:[d['id'] for d in original if qnorm(q['question']) in qnorm(d['content'])] for q in questions}
# Use the already-reviewed saved-listing extraction rules; no web calls or model execution.
spec=importlib.util.spec_from_file_location('prepare_day3',ROOT/'scripts/prepare_day3_supplement.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
tmp=Path(tempfile.mkdtemp(prefix='day3-saved-listings-'))/'derived'
sys.argv=['prepare_day3_supplement','--base',str(BASE),'--supplement',str(SOURCE),'--questions',str(QUESTIONS),'--output',str(tmp)]
module.main()
local=json.loads((tmp/'added_documents.json').read_text())
local_by_id={d['target_question_id'].split('_')[-1].lstrip('0'):d for d in local}
web=[];by_q={};ambiguous={};missing_field={}
for path in sorted(CACHE.glob('*.json'),key=lambda p:int(p.stem)):
    x=json.loads(path.read_text());q=x['question'];title=x['searched_title']
    records=[h['_source'] for h in x['response']['hits']['hits'] if normalize(h['_source']['title'])==normalize(title)]
    if 'painting' in q['question'].lower():records=[r for r in records if r.get('medium')=='paintings']
    if len(records)!=1:
        ambiguous[q['id']]=[{'title':r['title'],'accession_number':r.get('accession_number'),'medium':r.get('medium'),'creators':[c['name'] for c in r.get('creators',[])],'url':'https://collection.carnegieart.org/objects/'+r['id'].split('/')[-1]} for r in records]
        continue
    r=records[0]
    dates=list(dict.fromkeys(v for v in r.get('creation_date',[]) if v))
    creators=[c['name'] + (f" ({c['role']})" if c.get('role') else '') for c in r.get('creators',[])]
    question=q['question'].lower()
    if 'accession number' in question:answer=r.get('accession_number')
    elif 'creation date' in question:answer=' – '.join(dates)
    elif 'acquisition date' in question:answer=r.get('acquisition_date')
    elif 'credit line' in question:answer=r.get('credit_line')
    elif 'department' in question:answer=r.get('department')
    elif 'acquir' in question:answer='; '.join(v for v in [r.get('acqisition_method'),r.get('credit_line')] if v)
    elif any(s in question for s in ['artist','creator']):answer='; '.join(creators)
    else:raise ValueError('Unrecognized requested field: '+question)
    if not answer:missing_field[q['id']]=r['id'];continue
    url='https://collection.carnegieart.org/objects/'+r['id'].split('/')[-1]
    paragraphs=[f'# {title} — Carnegie Museum of Art',f'Carnegie Museum of Art catalogs “{title}” under accession number {r["accession_number"]}.']
    if creators:paragraphs.append('The catalog credits '+ '; '.join(creators)+'.')
    if dates:paragraphs.append('The recorded creation date is '+ ' – '.join(dates)+'.')
    if r.get('department'):paragraphs.append('The work belongs to the '+r['department']+' department. Its classification is '+r.get('medium','')+'.')
    if r.get('medium_description'):paragraphs.append('Materials: '+r['medium_description']+'.')
    if r.get('acquisition_date'):paragraphs.append('The recorded acquisition date is '+r['acquisition_date']+'.')
    if r.get('acqisition_method'):paragraphs.append('Acquisition method: '+r['acqisition_method']+'.')
    if r.get('credit_line'):paragraphs.append('Credit line: '+r['credit_line']+'.')
    doc={'id':'day3_web_'+q['id'],'title':title+' — Carnegie Museum of Art','website':'Carnegie Museum of Art',
         'page_type':'collection_record','content':'\n\n'.join(paragraphs),
         'source_type':'derived_from_official_collection_record','source_uri':url,
         'target_question_id':f'tes_{int(q["id"]):03d}',
         'retrieved_at':x['retrieved_at'],'source_record_id':r['id'],
         'evidence_file':'raw/cmoa/'+path.name,
         'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
         'verification_scope':'Exact title matched to unique official record; painting filter only when explicitly requested. Factual prose generated from returned fields.'}
    web.append(doc);by_q[q['id']]={'document_id':doc['id'],'answer':answer,'source_url':url}
# Replace superseded day3-specific synthetic records; preserve originals for audit.
replaced=[d for d in original if d.get('target_question_id') in {f'tes_{int(q):03d}' for q in by_q}]
replaced_ids={d['id'] for d in replaced}
kept=[d for d in original if d['id'] not in replaced_ids]
combined=kept+web+local
if len({d['id'] for d in combined})!=len(combined):raise ValueError('Duplicate IDs')
coverage=[]
for q in questions:
    qid=q['id'];row={'id':qid,'question':q['question']}
    if qid in by_q:row.update(status='official_web_supported',**by_q[qid])
    elif qid in local_by_id:
        d=local_by_id[qid];row.update(status='saved_listing_supported',document_id=d['id'],source_urls=list(dict.fromkeys(e['source_url'] for e in d['evidence'])))
    elif initial[qid]:row.update(status='provided_answer_unverified',document_ids=initial[qid])
    elif qid in ambiguous:row.update(status='ambiguous_not_counted',candidates=ambiguous[qid])
    else:row['status']='uncovered'
    coverage.append(row)
counts={s:sum(r['status']==s for r in coverage) for s in sorted({r['status'] for r in coverage})}
covered=sum(counts.get(s,0) for s in ['official_web_supported','saved_listing_supported','provided_answer_unverified'])
report={'question_set':str(QUESTIONS),'questions':len(questions),'answer_document_coverage':covered,'coverage_fraction':covered/len(questions),
        'supported_this_pass':len(by_q)+len(local_by_id),'counts':counts,
        'original_supplement_documents':len(original),'superseded_original_documents':len(replaced),'official_documents':len(web),'saved_listing_documents':len(local),'combined_documents':len(combined),
        'definition':'Coverage means an answer-bearing document is associated with the question. Provided synthetic answers have NOT all been verified. This is not QA accuracy or an independent test result.',
        'target_reached':covered>=200,'base_snapshot_sha256':hashlib.sha256(BASE.read_bytes()).hexdigest(),
        'input_supplement_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'requested_field_missing':missing_field,'created_at':datetime.now(timezone.utc).isoformat()}
OUT.mkdir(parents=True)
shutil.copytree(CACHE,OUT/'raw/cmoa')
shutil.copyfile(SOURCE,OUT/'provided_original.json')
write(OUT/'supplement.json',combined);write(OUT/'new_official_documents.json',web);write(OUT/'new_saved_listing_documents.json',local)
write(OUT/'superseded_documents.json',replaced);write(OUT/'coverage.json',coverage);write(OUT/'report.json',report)
write(OUT/'remaining_questions.json',[r for r in coverage if r['status'] in ['ambiguous_not_counted','uncovered']])
# Compact prose preview, not a second input to be ingested.
(OUT/'new_documents_preview.md').write_text('\n\n---\n\n'.join(d['content']+'\n\nSource: '+d['source_uri'] for d in web+local))
print(json.dumps(report,indent=2))
