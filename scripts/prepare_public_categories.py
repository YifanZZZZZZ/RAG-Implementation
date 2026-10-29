"""Export fixed topic labels for the full public set or an exact-question subset.
These are evaluation-analysis labels, not model-training annotations.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--questions', type=Path, default=ROOT/'data/evaluation/public_evaluation_data.json')
    p.add_argument('--taxonomy', type=Path, default=ROOT/'config/public_question_categories.json')
    p.add_argument('--output', type=Path, default=ROOT/'data/evaluation/public_categories.json')
    args = p.parse_args()
    taxonomy = json.loads(args.taxonomy.read_text(encoding='utf-8'))
    known = {r['id']: r for r in taxonomy['labels']}
    if len(known) != len(taxonomy['labels']):
        raise ValueError('Duplicate taxonomy IDs')
    questions = json.loads(args.questions.read_text(encoding='utf-8'))
    labels = []
    seen = set()
    for q in questions:
        qid = str(q['id'])
        if qid in seen:
            raise ValueError('Duplicate question ID: ' + qid)
        seen.add(qid)
        if qid not in known or known[qid]['question'] != q['question']:
            raise ValueError('Unknown or changed question; review its category before scoring: ' + qid)
        labels.append({'id': qid, 'question': q['question'], 'category': known[qid]['category']})
    if not labels:
        raise ValueError('No questions')
    if args.output.resolve() in {args.questions.resolve(), args.taxonomy.resolve()}:
        raise ValueError('Output must not overwrite questions or taxonomy')
    payload = json.dumps(labels, ensure_ascii=False, indent=2)+'\n'
    if args.output.exists() and args.output.read_text(encoding='utf-8') != payload:
        raise FileExistsError('Different labels already exist; use a new output path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        with args.output.open('x', encoding='utf-8') as f:f.write(payload)
    print(json.dumps({'labels_file':str(args.output),'n':len(labels),
                      'counts':dict(Counter(r['category'] for r in labels)),
                      'taxonomy_version':taxonomy['version'],
                      'labels_sha256':hashlib.sha256(payload.encode()).hexdigest()},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
