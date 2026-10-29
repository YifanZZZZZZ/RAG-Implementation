"""Freeze the first half or quarter of public questions, in original file order."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path,
                        default=ROOT / 'data/evaluation/public_evaluation_data.json')
    parser.add_argument('--portion', choices=['half', 'quarter'], default='half',
                        help='Select the first half or quarter, rounding up')
    parser.add_argument('--output', type=Path,
                        help='Default: public_first_half.json or public_first_quarter.json')
    args = parser.parse_args()
    divisor = 2 if args.portion == 'half' else 4
    if args.output is None:
        args.output = ROOT / f'data/evaluation/public_first_{args.portion}.json'
    if args.input.resolve() == args.output.resolve():
        raise ValueError('Output must differ from the original dataset')
    raw = args.input.read_bytes()
    rows = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty JSON list')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or 'id' not in row:
            raise ValueError('Each question must have its original ID')
        ident = str(row['id'])
        if ident in seen:
            raise ValueError('Duplicate question ID: ' + ident)
        seen.add(ident)
        if not isinstance(row.get('question'), str) or not row['question'].strip():
            raise ValueError('Missing question: ' + ident)
        answers = row.get('answers')
        if not isinstance(answers, list) or not answers or any(not isinstance(a, str) or not a.strip() for a in answers):
            raise ValueError('Missing reference answers: ' + ident)
    subset = rows[:(len(rows) + divisor - 1) // divisor]
    payload = (json.dumps(subset, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    manifest = {
        'strategy': f'first_{args.portion}_in_original_file_order', 'rounding': 'ceil',
        'source': str(args.input.resolve()),
        'source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_count': len(rows), 'selected_count': len(subset),
        'selected_ids': [str(row['id']) for row in subset],
        'subset_sha256': hashlib.sha256(payload).hexdigest(),
        'shuffled': False, 'reference_answers_preserved': True,
    }
    manifest_path = args.output.with_suffix('.manifest.json')
    # Repeated preparation reuses exactly the same subset; changed inputs fail closed.
    if args.output.exists() and args.output.read_bytes() != payload:
        raise FileExistsError('Existing subset differs; choose a new --output')
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise FileExistsError('Existing subset manifest differs; choose a new --output')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        with args.output.open('xb') as f:
            f.write(payload)
    if not manifest_path.exists():
        with manifest_path.open('x', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write('\n')
    print(f'First {len(subset)}/{len(rows)} questions: {args.output}')
    print(f'IDs: {subset[0]["id"]} ... {subset[-1]["id"]}')


if __name__ == '__main__':
    main()
