"""Append supplied JSON documents to an immutable corpus snapshot; no inference."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True, help='Existing documents.jsonl')
    parser.add_argument('--supplement', type=Path, required=True, help='JSON list with content/title')
    parser.add_argument('--output', type=Path, required=True, help='New snapshot directory')
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a new output directory: ' + str(args.output))
    base_bytes = args.base.read_bytes()
    extra_bytes = args.supplement.read_bytes()
    base = [json.loads(line) for line in base_bytes.decode('utf-8').splitlines() if line.strip()]
    extra = json.loads(extra_bytes.decode('utf-8-sig'))
    if not base or not isinstance(extra, list) or not extra:
        raise ValueError('Base corpus and supplement must be nonempty')
    ids = set()
    for doc in base:
        if not isinstance(doc.get('content_markdown'), str) or not doc['content_markdown'].strip():
            raise ValueError('Invalid base document body')
        if doc['doc_id'] in ids:
            raise ValueError('Duplicate base document ID')
        ids.add(doc['doc_id'])
    added = []
    for index, row in enumerate(extra, 1):
        if not isinstance(row, dict) or not isinstance(row.get('content'), str) or not row['content'].strip():
            raise ValueError(f'Invalid supplementary content at row {index}')
        title = row.get('title', '')
        if not isinstance(title, str):
            raise ValueError(f'Invalid title at row {index}')
        doc_id = 'supplement_' + digest((digest(extra_bytes) + ':' + str(index)).encode())[:24]
        if doc_id in ids:
            raise ValueError('Supplement already present or ID collision')
        ids.add(doc_id)
        source = {'source_id': str(row.get('website') or 'Supplementary documents'),
                  'source_url': str(row.get('source_uri') or ''),
                  'source_type': row.get('source_type', 'unspecified'),
                  'original_id': row.get('id')}
        added.append({'doc_id': doc_id, 'title': title,
                      'content_markdown': row['content'],
                      'document_type': 'markdown', 'category': row.get('page_type', 'supplement'),
                      'source_id': source['source_id'], 'source_url': source['source_url'],
                      'source_type': source['source_type'], 'sources': [source],
                      'supplement_original_record': row,
                      'cleaning_version': 'supplement-import-v1-preserve-content'})
    # Preserve original corpus bytes and source records; chunking handles exact chunk deduplication.
    payload = base_bytes + (b'\n' if not base_bytes.endswith(b'\n') else b'')
    payload += ''.join(json.dumps(doc, ensure_ascii=False) + '\n' for doc in added).encode('utf-8')
    manifest = {'implementation': 'extend-snapshot-v1', 'sha256': digest(payload),
                'documents': len(base) + len(added), 'base_documents': len(base),
                'added_documents': len(added), 'base': str(args.base.resolve()),
                'base_sha256': digest(base_bytes), 'supplement': str(args.supplement.resolve()),
                'supplement_sha256': digest(extra_bytes),
                'frozen_at': datetime.now(timezone.utc).isoformat(),
                'policy': 'Preserve all base and supplied documents, content and provenance; no factual verification or document deduplication.'}
    parent_manifest = args.base.parent / 'manifest.json'
    if parent_manifest.exists():
        manifest['parent_manifest'] = json.loads(parent_manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'documents.jsonl').write_bytes(payload)
    (args.output / 'supplement_original.json').write_bytes(extra_bytes)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {len(base)} + {len(added)} = {len(base) + len(added)} documents to {args.output}')


if __name__ == '__main__':
    main()
