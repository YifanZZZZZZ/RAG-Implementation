"""Two source-preserving chunkers with MiniLM AND Qwen embedding budget checks."""
import argparse
import time
from bisect import bisect_right
from collections import Counter
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
from .structure import units_for_document,preferred_boundaries

VERSION='dual-budget-chunking-v4-bounded'


def sha(data):return hashlib.sha256(data).hexdigest()


def encode(t,text,special=False):
    return t(text,add_special_tokens=special,truncation=False,verbose=False)['input_ids']


def title_for(doc):
    title=doc.get('title','').strip()
    if not title or title.lower() in {'response','manual','rendered'}:
        title=next((s['source_id'] for s in doc.get('sources',[]) if s.get('source_id')),title)
    return title


class Budgets:
    def __init__(self,minilm,qwen,max_tokens=256,qwen_limit=32768):
        if not minilm.is_fast or not qwen.is_fast:raise ValueError('Fast tokenizers required')
        if not 96<=max_tokens<=256:raise ValueError('MiniLM chunk budget must be in [96,256]')
        if qwen_limit<=0:raise ValueError('Invalid Qwen budget')
        self.m=minilm;self.q=qwen;self.limit=max_tokens;self.q_limit=qwen_limit
    def counts(self,text):
        return {'minilm':len(encode(self.m,text,True)),'qwen3_embedding':len(encode(self.q,text,True))}
    def fits(self,text):
        # No tokenizer truncation, including when final strings are re-encoded.
        return len(encode(self.m,text,True))<=self.limit and len(encode(self.q,text,True))<=self.q_limit
    def clip(self,text,n):
        offsets=self.m(text,add_special_tokens=False,return_offsets_mapping=True,verbose=False)['offset_mapping']
        return text if len(offsets)<=n else text[:offsets[n-1][1]]
    def prefix(self,doc,context=''):
        title=self.clip(title_for(doc),32)
        prefix=f'Title: {title}\n\n' if title else ''
        if context:prefix+='Context: '+self.clip(context,48)+'\n\n'
        if not self.fits(prefix):raise ValueError('Prefix exceeds budget')
        return prefix


def token_windows(text,prefix,budget,overlap=32):
    """Half-open character and source-token ranges; retain all source characters."""
    capacity=budget.limit-len(encode(budget.m,prefix,True))
    if not 0<=overlap<capacity:raise ValueError('Overlap must be smaller than available body budget')
    offsets=budget.m(text,add_special_tokens=False,return_offsets_mapping=True,truncation=False,verbose=False)['offset_mapping']
    if not offsets:
        if not budget.fits(prefix+text):raise ValueError('Whitespace-only span exceeds Qwen budget')
        yield 0,len(text),0,0
        return
    start=0
    while start<len(offsets):
        end=min(start+capacity,len(offsets))
        a=0 if start==0 else offsets[start][0]
        while end>start:
            b=len(text) if end==len(offsets) else offsets[end][0]
            if budget.fits(prefix+text[a:b]):break
            end-=1
        if end==start or end<len(offsets) and end-start<=overlap:raise ValueError('Cannot progress within dual token budgets')
        yield a,b,start,end
        if end==len(offsets):break
        start=end-overlap


def split_unit(unit,doc,budget):
    text=doc['content_markdown'];prefix=budget.prefix(doc,unit['context'])
    start=unit['start'];finish=unit['end'];body=text[start:finish]
    if len(body)<=4096 and budget.fits(prefix+body):
        yield start,finish,prefix,unit['kind']
        return
    preferred=[unit['start']+x for x in preferred_boundaries(body,unit['kind'])]
    capacity=budget.limit-len(encode(budget.m,prefix,True))
    last_log=time.monotonic();piece_count=0
    while start<finish:
        # Tokenize a bounded probe, expanding only for unusually token-sparse text.
        probe_end=min(finish,start+max(4096,capacity*16))
        while True:
            probe=text[start:probe_end]
            offsets=budget.m(probe,add_special_tokens=False,return_offsets_mapping=True,
                             truncation=False,verbose=False)['offset_mapping']
            if len(offsets)>capacity+1 or probe_end==finish:break
            probe_end=min(finish,start+2*(probe_end-start))
        if probe_end==finish and budget.fits(prefix+probe):
            end=finish;method=unit['kind']+'_tail'
        else:
            _,relative_end,_,_=next(token_windows(probe,prefix,budget,overlap=0))
            cap=start+relative_end
            index=bisect_right(preferred,cap)-1
            candidate=preferred[index] if index>=0 else start
            end=candidate if candidate>start else cap
            if candidate>start:
                method=('row_pack' if unit['kind'] in {'table','pdf_layout_rows'} else
                        'line_pack' if unit['kind']=='code' else
                        'list_item_pack' if unit['kind'] in {'list','facts'} else 'sentence_paragraph_split')
            else:method='token_fallback'
        if end<=start:raise ValueError('Structural split failed to advance')
        # Final exact token verification is also performed by build_chunks.
        yield start,end,prefix,method
        start=end;piece_count+=1
        if time.monotonic()-last_log>=5:
            print(f"  long {unit['kind']}: {start-unit['start']:,}/{finish-unit['start']:,} characters; {piece_count} pieces",flush=True)
            last_log=time.monotonic()


def split_document(doc,budget,strategy='fixed',overlap=32):
    text=doc.get('content_markdown','')
    if not isinstance(text,str) or not text.strip():raise ValueError(f"Empty document: {doc.get('doc_id')}")
    if strategy=='fixed':
        prefix=budget.prefix(doc)
        return [{'start':a,'end':b,'prefix':prefix,'method':'fixed_window',
                 'parts':[{'unit_id':0,'start':a,'end':b,'body_token_start':ts,'body_token_end':te}],
                 'section_paths':[],'warnings':[]}
                for a,b,ts,te in token_windows(text,prefix,budget,overlap)]
    if strategy!='structure':raise ValueError(strategy)
    units=units_for_document(doc)
    pieces=[]
    last_progress=time.monotonic()
    for i,unit in enumerate(units):
        if time.monotonic()-last_progress>=5:
            print(f'  structure units {i}/{len(units)}; pieces={len(pieces)}',flush=True)
            last_progress=time.monotonic()
        for a,b,prefix,method in split_unit(unit,doc,budget):
            piece={'start':a,'end':b,'prefix':prefix,'method':method,
                   'parts':[{'unit_id':i,'kind':unit['kind'],'start':a,'end':b,'block_indices':unit['block_indices']}],
                   'section_paths':[unit['section_path']],'warnings':unit['warnings'][:],
                   '_packable':unit['kind'] in {'paragraph','heading','quote','facts'}}
            if (pieces and piece['_packable'] and pieces[-1]['_packable']
                and pieces[-1]['end']==a and pieces[-1]['prefix']==prefix
                and budget.fits(prefix+text[pieces[-1]['start']:b])):
                previous=pieces[-1];previous['end']=b;previous['parts']+=piece['parts']
                previous['method']='section_paragraph_pack'
                previous['warnings']=sorted(set(previous['warnings']+piece['warnings']))
            else:pieces.append(piece)
    for p in pieces:p.pop('_packable')
    return pieces


def build_chunks(documents,budget,strategy,overlap=32):
    by_hash={};doc_stats=[];seen=set()
    for n,doc in enumerate(documents,1):
        if doc['doc_id'] in seen:raise ValueError('Duplicate document ID')
        seen.add(doc['doc_id'])
        started=time.monotonic()
        print(f"{strategy}: starting {n}/{len(documents)} | {title_for(doc)[:100]} | {len(doc['content_markdown']):,} characters",flush=True)
        pieces=split_document(doc,budget,strategy,overlap)
        covered=0;methods=Counter();warnings=set()
        for index,p in enumerate(pieces):
            if p['start']>covered or p['end']<=p['start']:raise ValueError('Source coverage gap or empty slice')
            covered=max(covered,p['end'])
            body=doc['content_markdown'][p['start']:p['end']];text=p['prefix']+body
            counts=budget.counts(text)
            if counts['minilm']>budget.limit or counts['qwen3_embedding']>budget.q_limit:raise ValueError('Chunk too long')
            methods[p['method']]+=1;warnings.update(p['warnings'])
            occurrence={'doc_id':doc['doc_id'],'window_index':index,'title':title_for(doc),
                'page_number':doc.get('page_number'),'category':doc.get('category'),'sources':doc.get('sources',[]),
                'char_start':p['start'],'char_end':p['end'],'method':p['method'],
                'section_paths':p['section_paths'],'source_parts':p['parts'],'warnings':p['warnings']}
            digest=sha(text.encode())
            if digest not in by_hash:
                by_hash[digest]={'chunk_id':'chunk_'+digest,'content_hash':digest,'text':text,'body_text':body,
                    'prefix':p['prefix'],'token_count':counts['minilm'],'token_counts':counts,
                    'occurrences':[],'source_urls':[]}
            chunk=by_hash[digest];chunk['occurrences'].append(occurrence)
            for s in doc.get('sources',[]):
                url=s.get('source_url') or s.get('final_url')
                if url and url not in chunk['source_urls']:chunk['source_urls'].append(url)
        if covered!=len(doc['content_markdown']):raise ValueError('Lost document tail')
        doc_stats.append({'doc_id':doc['doc_id'],'title':title_for(doc),'page_number':doc.get('page_number'),
            'windows':len(pieces),'character_coverage_complete':True,'methods':dict(methods),'warnings':sorted(warnings)})
        print(f'{strategy}: processed {n}/{len(documents)} documents | {len(pieces)} chunks | {time.monotonic()-started:.1f}s',flush=True)
    return list(by_hash.values()),doc_stats


def write_json(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def load_tokenizer(path):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(path,use_fast=True,local_files_only=True)


def tokenizer_metadata(path,t):
    record=path.with_name(path.name+'.download.json')
    return {'path':str(path),'download_record':json.loads(record.read_text()) if record.exists() else None,
            'fingerprint':sha(t.backend_tokenizer.to_str().encode()),'tokenizer_model_max_length':t.model_max_length,
            'special_tokens':t.num_special_tokens_to_add(False)}


def run():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New artifact directory, never overwritten')
    parser.add_argument('--strategy',choices=['fixed','structure'],required=True)
    parser.add_argument('--minilm-tokenizer',type=Path,default=root/'models/all-MiniLM-L6-v2')
    parser.add_argument('--qwen-tokenizer',type=Path,default=root/'models/Qwen3-Embedding-4B')
    parser.add_argument('--chunk-size',type=int,default=256)
    parser.add_argument('--overlap',type=int,default=None,help='Fixed only, default 32. Structure repeats context instead.')
    args=parser.parse_args()
    if args.strategy=='structure' and args.overlap is not None:parser.error('--overlap applies only to fixed strategy')
    source=args.input.resolve();output=args.output.resolve()
    if output.exists():raise FileExistsError(output)
    raw=source.read_bytes();input_sha=sha(raw)
    manifest_path=source.parent/'manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text()).get('sha256',input_sha)!=input_sha:
        raise ValueError('Snapshot SHA-256 mismatch')
    docs=[json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    if not docs:raise ValueError('Empty corpus')
    mp=args.minilm_tokenizer.resolve();qp=args.qwen_tokenizer.resolve()
    print('Loading local tokenizers only; no model weights, no network.',flush=True)
    m=load_tokenizer(mp);q=load_tokenizer(qp)
    st_config=mp/'sentence_bert_config.json'
    if not st_config.exists():raise ValueError('MiniLM sentence_bert_config.json required to verify actual input limit')
    actual_limit=json.loads(st_config.read_text())['max_seq_length']
    if args.chunk_size>actual_limit:raise ValueError('Exceeds MiniLM SentenceTransformer max_seq_length')
    budget=Budgets(m,q,args.chunk_size,min(32768,q.model_max_length))
    overlap=args.overlap if args.overlap is not None else 32
    chunks,doc_stats=build_chunks(docs,budget,args.strategy,overlap)
    output.mkdir(parents=True,exist_ok=False)
    with (output/'chunks.jsonl').open('w',encoding='utf-8') as f:
        for chunk in chunks:f.write(json.dumps(chunk,ensure_ascii=False)+'\n')
    all_methods=Counter()
    for s in doc_stats:all_methods.update(s['methods'])
    windows=sum(s['windows'] for s in doc_stats)
    stats={'documents':len(docs),'windows_before_dedup':windows,'unique_chunks':len(chunks),
        'duplicate_windows_merged':windows-len(chunks),
        'minilm_min_tokens':min(c['token_count'] for c in chunks),'minilm_max_tokens':max(c['token_count'] for c in chunks),
        'minilm_mean_tokens':round(sum(c['token_count'] for c in chunks)/len(chunks),2),
        'qwen_max_tokens':max(c['token_counts']['qwen3_embedding'] for c in chunks),
        'chunks_under_64_minilm_tokens':sum(c['token_count']<64 for c in chunks),
        'all_document_characters_covered':True,'methods':dict(all_methods),
        'documents_with_structural_warnings':sum(bool(s['warnings']) for s in doc_stats)}
    manifest={'implementation':VERSION,'strategy':args.strategy,'input_path':str(source),'input_sha256':input_sha,
        'tokenizers':{'minilm':tokenizer_metadata(mp,m),'qwen3_embedding':tokenizer_metadata(qp,q)},
        'limits':{'minilm':budget.limit,'qwen3_embedding':budget.q_limit,'count_includes_prefix_and_special_tokens':True},
        'fixed_body_overlap_tokens':overlap if args.strategy=='fixed' else None,
        'prefix_budgets_minilm_tokens':{'title':32,'context':48},
        'same_chunks_for_both_embedding_models':True,'query_instruction_added_to_documents':False,
        'data_cleaning_applied':False,'upstream_cleaning_versions':sorted({d.get('cleaning_version','none') for d in docs}),
        'snapshot_manifest':json.loads(manifest_path.read_text()) if manifest_path.exists() else None,'deduplication':'exact final text SHA256; preserve all occurrences',
        'source_code_hashes':{p.name:sha(p.read_bytes()) for p in Path(__file__).parent.glob('*.py')},
        'libraries':{p:importlib.metadata.version(p) for p in ['transformers','tokenizers','huggingface-hub']},
        'chunks_sha256':sha((output/'chunks.jsonl').read_bytes()),'created_at':datetime.now(timezone.utc).isoformat(),
        'limitations':['No OCR, cleanup, date inference or semantic-similarity model.',
                       'Structure uses original blocks or inferred Markdown spans; sentence boundaries are heuristic, not embedding-based semantics.',
                       'PDF rows remain layout text; table column/merged-cell relations are not reconstructed.',
                       'Overlong sentences, rows, URLs and code lines use token fallback.',
                       'MiniLM and Qwen must embed text without extra document prompts; query prompts are separate.']}
    write_json(output/'manifest.json',manifest);write_json(output/'stats.json',stats);write_json(output/'document_stats.json',doc_stats)
    sampled=[];seen=set();pdf_count=0
    for c in chunks:
        o=c['occurrences'][0]
        if o['doc_id'] in seen:continue
        if o['page_number'] is not None:
            if o['page_number'] not in {2,35,69,150,300}:continue
            pdf_count+=1
        seen.add(o['doc_id']);sampled.append(c)
    preview=['# '+args.strategy+' chunk samples','Source text is unchanged; baseline formatting may be split.']
    for c in sampled:
        o=c['occurrences'][0]
        preview.extend([f"\n## {o['title']} — {o['method']} — {c['token_counts']}\n",
            f"Source: {', '.join(c['source_urls'])}; PDF page: {o['page_number']}\n",c['text'],'\n---\n'])
    (output/'preview.md').write_text('\n'.join(preview),encoding='utf-8')
    print(json.dumps(stats,indent=2),flush=True);print(f'Output: {output}',flush=True)


if __name__=='__main__':run()
