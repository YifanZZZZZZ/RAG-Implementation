"""Build a reproducible partial-coverage ablation without modifying answer text.
Sampling is over question IDs with available documents, targeting a fraction of
ALL questions. Public questions are stratified at the fixed first-half boundary.
This does not guarantee absence of incidental facts about unselected questions.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import re

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    'public': 'public_evaluation_data.json',
    'leaderboard': 'leaderboard_queries.json',
    'day3': 'test_set_day_3.json',
}
PREFIXES = {'pub': 'public', 'lea': 'leaderboard', 'tes': 'day3', 'day3': 'day3'}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=ROOT/'data/supplements/day3_web_verified_v1/supplement.json')
    p.add_argument('--evaluation-dir', type=Path, default=ROOT/'data/evaluation')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fraction', type=float, default=0.6)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--extend-selection', type=Path, help='Preserve all selected questions from this earlier selection.json')
    args = p.parse_args()
    if not 0 <= args.fraction <= 1:
        raise ValueError('fraction must be between 0 and 1')
    if args.output.exists():
        raise FileExistsError('Use a new output directory')
    raw = args.input.read_bytes()
    docs = json.loads(raw)
    if not isinstance(docs, list) or not docs:
        raise ValueError('Supplement must be a nonempty list')
    datasets = {}
    dataset_hashes = {}
    for name, filename in DATASETS.items():
        data = (args.evaluation_dir/filename).read_bytes()
        rows = json.loads(data)
        ids = [str(row['id']) for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate question IDs in ' + name)
        datasets[name] = rows
        dataset_hashes[name] = sha(data)
    groups = defaultdict(list)
    doc_targets = {}
    doc_ids = set()
    for doc in docs:
        if doc['id'] in doc_ids:
            raise ValueError('Duplicate document ID')
        doc_ids.add(doc['id'])
        target = doc.get('target_question_id')
        if not target:
            if not doc['id'].startswith('background_'):
                raise ValueError('Unmapped document: ' + doc['id'])
            continue
        match = re.fullmatch(r'(pub|lea|tes|day3)_(\d+)', target)
        if not match:
            raise ValueError('Unknown target format: ' + target)
        name, qid = PREFIXES[match[1]], str(int(match[2]))
        if qid not in {str(row['id']) for row in datasets[name]}:
            raise ValueError('Target question absent: ' + target)
        if not isinstance(doc.get('content'), str) or not doc['content'].strip():
            raise ValueError('Empty content: ' + doc['id'])
        key = (name, qid)
        groups[key].append(doc)
        doc_targets[doc['id']] = key
    half_path = args.evaluation_dir/'public_first_half.json'
    if half_path.exists():
        half = json.loads(half_path.read_bytes())
        if half != datasets['public'][:(len(datasets['public'])+1)//2]:
            raise ValueError('public_first_half.json does not match the original first half')
    previous = json.loads(args.extend_selection.read_bytes()) if args.extend_selection else {}
    for name, old in previous.items():
        if name not in datasets:
            raise ValueError('Unknown prior dataset: ' + name)
        for qid in old['selected_question_ids']:
            if (name, str(qid)) not in groups:
                raise ValueError('Previously selected answer group is no longer available')
    selected = set()
    selection = {}
    report = {}
    for name, rows in datasets.items():
        midpoint = (len(rows)+1)//2
        strata = [('first_half', rows[:midpoint]), ('second_half', rows[midpoint:])] if name == 'public' else [('all', rows)]
        selections = []
        strata_report = []
        for stratum, items in strata:
            count = math.floor(len(items)*args.fraction + 0.5)
            eligible = [str(row['id']) for row in items if (name, str(row['id'])) in groups]
            if len(eligible) < count:
                raise ValueError(f'{name}/{stratum}: need {count} answer groups but only {len(eligible)} available')
            prior = set(previous.get(name, {}).get('selected_question_ids', []))
            chosen = prior.intersection(eligible)
            if len(chosen) > count:
                raise ValueError('Requested fraction would remove previously selected questions')
            remaining = [qid for qid in eligible if qid not in chosen]
            chosen.update(random.Random(f'{args.seed}:{name}:{stratum}').sample(remaining, count-len(chosen)))
            ordered = [str(row['id']) for row in items if str(row['id']) in chosen]
            selected.update((name, qid) for qid in ordered)
            selections.extend(ordered)
            strata_report.append({'stratum': stratum, 'questions': len(items), 'available_answer_groups': len(eligible),
                                  'selected': len(ordered), 'fraction': len(ordered)/len(items) if items else 0,
                                  'selected_ids': ordered})
        selection[name] = {'selected_question_ids': selections,
                           'unselected_question_ids': [str(row['id']) for row in rows if str(row['id']) not in selections]}
        report[name] = {'questions': len(rows), 'selected': len(selections), 'fraction': len(selections)/len(rows), 'strata': strata_report}
    kept = [doc for doc in docs if doc_targets.get(doc['id']) in selected]
    excluded = [{'document_id': doc['id'], 'target_question_id': doc.get('target_question_id'),
                 'reason': 'unassigned_background_excluded' if doc['id'] not in doc_targets else 'target_question_not_selected'}
                for doc in docs if doc_targets.get(doc['id']) not in selected]
    payload = (json.dumps(kept, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    manifest = {'protocol': 'question-target-coverage-ablation-v1', 'fraction_requested': args.fraction,
                'seed': args.seed, 'rounding': 'nearest integer, half up, per stratum',
                'sampling_population': 'available question-linked documents; target counts use all dataset questions',
                'source': str(args.input.resolve()), 'source_sha256': sha(raw), 'dataset_sha256': dataset_hashes,
                'input_documents': len(docs), 'retained_documents': len(kept), 'excluded_documents': len(excluded),
                'output_sha256': sha(payload), 'datasets': report,
                'content_policy': 'Selected document records preserved verbatim. No answers, scores or evaluation questions modified.',
                'coverage_caveat': 'These are explicit target-assignment rates, not guaranteed factual coverage or correctness. Original corpus and other retained documents can answer unselected questions. Previously unverified synthetic answers remain unverified.'}
    if args.extend_selection:
        manifest['extended_from_selection'] = str(args.extend_selection.resolve())
        manifest['previous_selection_sha256'] = sha(args.extend_selection.read_bytes())
    manifest['public_first_half_file'] = str(half_path.resolve()) if half_path.exists() else None
    args.output.mkdir(parents=True)
    (args.output/'supplement.json').write_bytes(payload)
    save(args.output/'selection.json', selection)
    save(args.output/'excluded_documents.json', excluded)
    save(args.output/'manifest.json', manifest)
    print(json.dumps({'retained_documents':len(kept),'coverage':{name:{k:v for k,v in data.items() if k!='strata'} for name,data in report.items()}},indent=2))


if __name__ == '__main__':
    main()
