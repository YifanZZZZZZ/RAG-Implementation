"""Local QA evaluation; no retrieval or answer generation is performed here."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import string

VERSION = 'qa-evaluation-v1'
METRICS = ['answer_recall', 'f1', 'rouge1', 'rouge2', 'rougeL', 'rouge_avg',
           'standard_only', 'judge_raw', 'judge_normalized', 'total']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_rows(path):
    path = Path(path)
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return json.loads(path.read_text())


def by_id(rows):
    if not isinstance(rows, list):
        raise ValueError('Expected a list of records')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or 'id' not in row:
            raise ValueError('Each record needs an id')
        ident = str(row['id'])
        if ident in result:
            raise ValueError(f'Duplicate id: {ident}')
        result[ident] = row
    return result


def clean_answer(answer):
    # Only explicit reader citation markers; never shorten based on gold answers.
    return re.sub(r'\s*\[D\d+\](?:\s*[,;]\s*\[D\d+\])*', '', answer).strip()


def load_inputs(gold_path, prediction_path, clean_citations=True):
    gold = by_id(read_rows(gold_path))
    if not gold:
        raise ValueError('Empty reference dataset')
    raw = read_rows(prediction_path)
    if isinstance(raw, dict):
        raw = [{'id': k, 'answer': v} for k, v in raw.items()]
    predictions = by_id(raw)
    if gold.keys() != predictions.keys():
        raise ValueError(f'ID mismatch: missing={sorted(gold.keys()-predictions.keys())}; '
                         f'extra={sorted(predictions.keys()-gold.keys())}')
    rows = []
    for ident, reference in gold.items():
        answers = reference.get('answers')
        if (not isinstance(reference.get('question'), str) or not reference['question'].strip()
            or not isinstance(answers, list) or not answers
            or any(not isinstance(a, str) or not a.strip() for a in answers)):
            raise ValueError(f'Invalid reference: {ident}')
        prediction = predictions[ident]
        if 'question' in prediction and prediction['question'] != reference['question']:
            raise ValueError(f'Question mismatch: {ident}')
        if not isinstance(prediction.get('answer'), str):
            raise ValueError(f'Prediction answer must be a string: {ident}')
        row = {'id': ident, 'question': reference['question'], 'references': answers,
               'answer': clean_answer(prediction['answer']) if clean_citations else prediction['answer']}
        row['input_hash'] = digest(row)
        rows.append(row)
    return rows


def normalize(text):
    text = text.lower().translate(str.maketrans('', '', string.punctuation))
    return re.sub(r'\b(a|an|the)\b', ' ', text).split()


def token_scores(prediction, reference):
    p, r = normalize(prediction), normalize(reference)
    overlap = sum((Counter(p) & Counter(r)).values())
    if not overlap:
        return 0.0, 0.0
    recall, precision = overlap / len(r), overlap / len(p)
    return recall, 2 * precision * recall / (precision + recall)


def standard_scores(answer, references, scorer):
    pairs = [token_scores(answer, r) for r in references]
    scores = {'answer_recall': max(p[0] for p in pairs), 'f1': max(p[1] for p in pairs)}
    rouge = [scorer.score(r, answer) for r in references]
    for key in ['rouge1', 'rouge2', 'rougeL']:
        scores[key] = max(r[key].fmeasure for r in rouge)
    scores['rouge_avg'] = sum(scores[k] for k in ['rouge1', 'rouge2', 'rougeL']) / 3
    scores['standard_only'] = sum(scores[k] for k in ['answer_recall', 'f1', 'rouge_avg']) / 3
    return scores


def aggregate(rows):
    result = {'n': len(rows), 'judge_scored_n': sum(r['judge_raw'] is not None for r in rows),
              'needs_review_n': sum(r.get('needs_review', False) for r in rows)}
    for key in METRICS:
        values = [r[key] for r in rows]
        # No aggregate over a selectively judged subset.
        result[key] = sum(values) / len(values) if values and all(v is not None for v in values) else None
    return result


def score_legacy_run(args):
    from rouge_score.rouge_scorer import RougeScorer
    from .judge import RUBRIC, validate_judgment
    rows = load_inputs(args.references, args.predictions)
    judges = {}
    if args.judge:
        judges = by_id(read_rows(args.judge))
        if judges.keys() - {r['id'] for r in rows}:
            raise ValueError('Judge contains unknown IDs')
    scorer = RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=False)
    for row in rows:
        row.update(standard_scores(row['answer'], row['references'], scorer))
        row.update(judge_raw=None, judge_normalized=None, total=None)
        if row['id'] in judges:
            j = judges[row['id']]
            validate_judgment(j)
            if j.get('input_hash') != row['input_hash'] or j.get('rubric_hash') != digest(RUBRIC):
                raise ValueError(f'Stale judge input or rubric: {row["id"]}')
            row.update(judge_raw=j['score'], judge_normalized=(j['score']-1)/4,
                       judge_reason=j['reason'], error_types=j['error_types'], needs_review=j['needs_review'])
            row['total'] = (row['answer_recall']+row['f1']+row['rouge_avg']+row['judge_normalized'])/4
    if judges:
        fingerprints = {j.get('judge_config_hash') for j in judges.values()}
        if len(fingerprints) != 1 or None in fingerprints:
            raise ValueError('Missing or mixed judge configurations')
    summary = {'system': args.system, 'dataset': str(Path(args.references).resolve()),
               'protocol': VERSION, 'input_hash': digest([{k:r[k] for k in ['id','input_hash']} for r in rows]),
               'rubric_hash': digest(RUBRIC), 'rouge_score_version': importlib.metadata.version('rouge-score'),
               'judge_config_hash': next(iter(fingerprints)) if judges else None,
               'score_scale': '0-1 except judge_raw (1-5)', **aggregate(rows)}
    groups = {}
    if args.labels:
        labels = by_id(read_rows(args.labels))
        if labels.keys() != {r['id'] for r in rows}:
            raise ValueError('Labels must cover exactly the reference IDs')
        buckets = defaultdict(list)
        for row in rows:
            label = labels[row['id']].get('category')
            if not isinstance(label, str) or not label.strip():
                raise ValueError('Each label needs a nonempty category')
            row['category'] = label
            buckets[label].append(row)
        groups = {label: aggregate(items) for label, items in buckets.items()}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'per_question.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (output/'by_category.json').write_text(json.dumps(groups, ensure_ascii=False, indent=2))
    with (output/'per_question.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'question', 'answer', *METRICS], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# New protocol: strict EM plus the user's normalized multiset token metrics.
OVERLAP_VERSION = 'qa-evaluation-v2-em-f1-pr'
OVERLAP_METRICS = ['exact_match', 'f1', 'precision', 'recall']


def f1_calc(pred, ref):
    p, r = normalize(pred), normalize(ref)
    tp = sum((Counter(p) & Counter(r)).values())
    # Matches the supplied code, including zero for two empty normalized answers.
    if tp == 0:
        return 0.0, 0.0, 0.0
    precision, recall = tp / len(p), tp / len(r)
    return 2 * precision * recall / (precision + recall), precision, recall


def overlap_scores(answer, references):
    pairs = [f1_calc(answer, ref) for ref in references]
    best = max(range(len(pairs)), key=lambda i: pairs[i][0])  # first reference wins ties
    f1, precision, recall = pairs[best]
    return {'exact_match': float(any(answer == r for r in references)),
            'f1': f1, 'precision': precision, 'recall': recall,
            'selected_reference_index': best, 'selected_reference': references[best]}


def overlap_aggregate(rows):
    return {'n': len(rows), **{k: sum(r[k] for r in rows)/len(rows) if rows else None
                               for k in OVERLAP_METRICS}}


def score_overlap_run(args):
    if args.judge:
        raise ValueError('--judge is only supported with --metric-set legacy')
    # Score the answer field exactly as supplied. Reader already removes its own citations.
    rows = load_inputs(args.references, args.predictions, clean_citations=False)
    for row in rows:
        row.update(overlap_scores(row['answer'], row['references']))
    summary = {'system': args.system, 'dataset': str(Path(args.references).resolve()),
               'predictions': str(Path(args.predictions).resolve()), 'protocol': OVERLAP_VERSION,
               'input_hash': digest([{k:r[k] for k in ['id','input_hash']} for r in rows]),
               'score_scale': '0-1; higher is better',
               'exact_match_policy': 'strict answer string equality against any reference; no extra stripping or citation removal',
               'token_normalization': 'lowercase; delete ASCII string.punctuation; remove a/an/the; split whitespace',
               'multi_reference_policy': 'maximum F1; precision/recall from the same reference; first reference wins ties',
               'aggregation': 'macro mean across questions; not F1 of mean precision/recall',
               **overlap_aggregate(rows)}
    groups = {}
    if args.labels:
        labels = by_id(read_rows(args.labels))
        if labels.keys() != {r['id'] for r in rows}:
            raise ValueError('Labels must cover exactly the reference IDs')
        buckets = defaultdict(list)
        for row in rows:
            label = labels[row['id']].get('category')
            if not isinstance(label, str) or not label.strip():
                raise ValueError('Each label needs a nonempty category')
            row['category'] = label
            buckets[label].append(row)
        groups = {label: overlap_aggregate(items) for label,items in buckets.items()}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'per_question.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'by_category.json').write_text(json.dumps(groups,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'per_question.csv').open('w',newline='',encoding='utf-8') as f:
        writer = csv.DictWriter(f,fieldnames=['id','question','answer',*OVERLAP_METRICS,
                                'selected_reference_index','selected_reference'],extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


STANDARD_METRICS = ['answer_recall', 'f1', 'rouge1', 'rouge2', 'rougeL', 'rouge_avg', 'total']


def score_standard_run(args):
    """Three-component lexical evaluation; no judge import or inference."""
    from rouge_score.rouge_scorer import RougeScorer
    if args.judge:
        raise ValueError('standard mode does not use --judge')
    rows = load_inputs(args.references, args.predictions)
    scorer = RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=False)
    for row in rows:
        values = standard_scores(row['answer'], row['references'], scorer)
        values['total'] = values.pop('standard_only')
        row.update(values)

    def means(items):
        return {'n': len(items), **{key: sum(r[key] for r in items)/len(items)
                                   for key in STANDARD_METRICS}}

    summary = {'system': args.system, 'dataset': str(Path(args.references).resolve()),
               'protocol': 'qa-evaluation-v3-standard-no-judge',
               'input_hash': digest([{'id': r['id'], 'input_hash': r['input_hash']} for r in rows]),
               'rouge_score_version': importlib.metadata.version('rouge-score'),
               'score_scale': '0-1',
               'total_formula': '(answer_recall + f1 + rouge_avg) / 3',
               'multi_reference_policy': 'maximum per metric across references',
               'aggregation': 'macro average over questions', **means(rows)}
    groups = {}
    if args.labels:
        labels = by_id(read_rows(args.labels))
        if labels.keys() != {r['id'] for r in rows}:
            raise ValueError('Labels must cover exactly the reference IDs')
        buckets = defaultdict(list)
        for row in rows:
            category = labels[row['id']].get('category')
            if not isinstance(category, str) or not category.strip():
                raise ValueError('Each label needs a nonempty category')
            row['category'] = category
            buckets[category].append(row)
        groups = {key: means(items) for key, items in buckets.items()}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (output/'by_category.json').write_text(json.dumps(groups, ensure_ascii=False, indent=2))
    (output/'per_question.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
    with (output/'per_question.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['id','question','answer',*STANDARD_METRICS], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def score_run(args):
    # Historical modes remain available for reproducing previous results.
    if getattr(args, "metric_set", "legacy") == "standard":
        return score_standard_run(args)
    if getattr(args, 'metric_set', 'legacy') == 'legacy':
        return score_legacy_run(args)
    return score_overlap_run(args)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--references', default='data/evaluation/public_evaluation_data.json')
    p.add_argument('--predictions', required=True, help='Reader answers.jsonl, predictions.json, or ID-to-answer JSON')
    p.add_argument('--metric-set', choices=['standard','em-f1-pr','legacy'], default='standard',
                   help='Default: Recall/F1/ROUGE and three-component total, without judge; historical modes remain available')
    p.add_argument('--judge', help='Optional judgments.jsonl from rag.judge')
    p.add_argument('--labels', help='Optional JSON/JSONL records: id, category')
    p.add_argument('--system', required=True)
    p.add_argument('--output', required=True, help='New output directory')
    score_run(p.parse_args())


if __name__ == '__main__':
    main()
