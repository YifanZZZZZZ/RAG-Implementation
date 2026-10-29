"""Create traceable prose supplements from already-collected event listings.
No web requests, invented answers, or changes to evaluation questions.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

# Confirmed against the saved October–December 2026 monthly listings.
RULES = [
    ('31', 'Mark Paul', 'Pittsburgh Improv', '2026-11-08'),
    ('35', 'Dweezil Zappa', 'Roxian Theatre', '2026-10-31'),
    ('37', 'The Rock Orchestra: Arena of Fire', 'Petersen Events Center', '2026-11-15'),
    ('38', 'Tropidelic', "Mr Small's Theatre", '2026-11-15'),
    ('39', 'Ian Fidance', 'Pittsburgh Improv', '2026-12-31'),
    ('54', 'Pittsburgh Penguins vs. Seattle Kraken', 'PPG Paints Arena', '2026-12-18'),
    ('56', 'Pittsburgh Penguins vs. Tampa Bay Lightning', 'PPG Paints Arena', '2026-12-15'),
    ('99', 'Donny Benet', 'Thunderbird Cafe', '2026-11-08'),
    ('137', 'Ismo - Show', 'Pittsburgh Improv', '2026-11-27'),
    ('166', 'Julia Jacklin', "Mr Small's Theatre", '2026-11-11'),
    ('171', "Mo'Nique", 'Pittsburgh Improv', '2026-11'),
    ('172', 'Pittsburgh Penguins vs. Toronto Maple Leafs', 'PPG Paints Arena', '2026-11-28'),
]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--supplement', type=Path, required=True)
    p.add_argument('--questions', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='New output folder')
    a = p.parse_args()
    if a.output.exists():
        raise FileExistsError('Use a new output directory')
    raw = a.base.read_bytes()
    corpus = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    existing = json.loads(a.supplement.read_text())
    questions = json.loads(a.questions.read_text())
    qids = {str(q['id']) for q in questions}
    events = []
    for doc in corpus:
        if not doc.get('source_url', '').startswith('https://pittsburgh.events/'):
            continue
        for block in re.split(r'(?m)(?=^## )', doc['content_markdown']):
            head = re.match(r'^## (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d) — (.+)', block)
            venue = re.search(r'^- Venue: (.+)$', block, re.M)
            if head and venue:
                events.append((doc, block, head[1], head[2].strip(), venue[1].strip()))
    additions = []
    for qid, name, venue, date in RULES:
        if qid not in qids:
            raise ValueError('Question ID absent: ' + qid)
        matches = [e for e in events if e[2].startswith(date) and e[3] == name and e[4] == venue]
        if not matches:
            raise ValueError('Expected saved evidence absent for day3 ' + qid)
        title = f'{name} at {venue} — {date}'
        sentences = []
        evidence = []
        seen = set()
        for doc, block, timestamp, _, _ in sorted(matches, key=lambda e: e[2]):
            if timestamp in seen:
                continue
            seen.add(timestamp)
            dt = datetime.fromisoformat(timestamp)
            day = dt.strftime('%B %d, %Y').replace(' 0', ' ')
            shown = re.search(r'^- Displayed time: (.+)$', block, re.M)
            clock = shown[1] if shown else dt.strftime('%I:%M %p').lstrip('0')
            sentences.append(f'{name} is listed at {venue} on {day}, at {clock}.')
            evidence.append({'doc_id': doc['doc_id'], 'source_url': doc['source_url'],
                             'excerpt': block.strip()})
        additions.append({'id': f'day3_derived_{qid}', 'title': title,
            'website': 'Pittsburgh Events — saved listings', 'page_type': 'event_or_schedule',
            'content': '# ' + title + '\n\n' + '\n\n'.join(sentences),
            'source_type': 'derived_from_saved_documents',
            'source_uri': f'derived://saved-corpus/test_set_day_3/{qid}',
            'target_question_id': f'day3_{int(qid):03d}',
            'evidence': evidence, 'base_sha256': hashlib.sha256(raw).hexdigest(),
            'verification_scope': 'Supported by saved listing; not independently checked against live event schedule.'})
    all_ids = [d['id'] for d in existing + additions]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError('Duplicate supplement ID')
    a.output.mkdir(parents=True)
    combined = existing + additions
    (a.output / 'supplement.json').write_text(json.dumps(combined, ensure_ascii=False, indent=2)+'\n')
    (a.output / 'added_documents.json').write_text(json.dumps(additions, ensure_ascii=False, indent=2)+'\n')
    report = {'existing': len(existing), 'added': len(additions), 'total': len(combined),
              'added_day3_question_ids': [r[0] for r in RULES],
              'base_sha256': hashlib.sha256(raw).hexdigest(),
              'supplement_sha256': hashlib.sha256(a.supplement.read_bytes()).hexdigest(),
              'policy': 'Existing supplied synthetic entries preserved, not validated. New prose derived from saved listings with evidence; no new scraping. No claim of complete test coverage.'}
    (a.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
