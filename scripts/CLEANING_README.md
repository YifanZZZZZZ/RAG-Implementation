# Unified knowledge cleaning

Existing `scripts/document_pipeline.py::clean_blocks` handles extraction-time block cleaning for the older pipeline. Per-site preprocessors also clean HTML. Neither is a unified cleaner for the current Markdown corpora. `scripts/clean_knowledge_base.py` adds that separate stage without changing extraction code or raw documents.

```sh
python scripts/clean_knowledge_base.py
```

Defaults: read `data/preprocessed_documents/`; write a new timestamped `data/knowledge_base/<snapshot>/`. Refuses existing output directories or output inside the input tree. No network/models/evaluation data are used.

```sh
python scripts/clean_knowledge_base.py --output "data/knowledge_base/clean-v1"
```

Input selection uses only documents.jsonl. Excludes raw, records, Markdown copies, archive/backup/legacy paths. Parent aggregates supersede their subordinate section/month aggregates. `Single Pages` and `Wikipedia` are independent additive branches and remain included. All selected/skipped paths and input file hashes appear in cleaning_report.json. If an upstream parent aggregate is stale, refresh it first; the cleaner cannot infer completeness from its children. Older data/processed and data/snapshots are not imported.

Cleaning preserves Markdown headings, paragraph boundaries, lists, table rows, hard line breaks and code blocks/indentation. Normalizes ordinary Unicode/line endings, selected entities, invisible characters and extra blank lines. Removes only exact standalone UI controls such as Privacy Policy and Cookie Settings; does not delete sentences or FAQ answers containing contact/privacy words. Does not rewrite dates, prices, years, addresses or units. Residual HTML and duplicate long prose blocks are flagged for review rather than aggressively removed. No global deletion of repeated headings, schedule rows or FAQ labels.

Dedup: exact cleaned Markdown hash (structure-sensitive) with combined original provenance. Source URLs remove tracking parameters/default ports/ordinary fragments while retaining meaningful query values and hash routers. Same URL with differing content is retained and flagged; never selects the longest or blindly follows canonical declarations. This is intentionally not fuzzy/semantic deduplication. Canonical URL mismatches are flagged, not silently merged.

Output:
- documents.jsonl: all retained documents, including review flags; compatible content_markdown/doc_id/sources fields for existing chunking.
- cleaning_report.json: input hashes, selected/skipped files, counts and changes.
- duplicates.jsonl: exact duplicate -> retained document mapping.
- review_queue.jsonl: short body, partial extraction, upstream quality flags, same-URL conflicts, residual markup and incomplete source run summaries.
- quarantine.jsonl: empty/invalid records and short explicit access/error pages, with original record preserved.

Every document retains original metadata plus cleaning_provenance. Hash-based doc_id is new; rebuild chunks/indexes rather than mixing old IDs. Cleaning cannot repair missing crawled information. Intentional listing_only_by_request is not treated as missing detail; failure-driven detail_status=listing_only is. Cached prior successful records retain their timestamps. Run-summary warnings can describe earlier runs; verify them before discarding data.

Review source variants before treating this as a final experiment snapshot. Do not ingest the duplicates/quarantine/review files as RAG documents. Both chunking variants should consume the same final documents.jsonl.

Tests:

```sh
python -m unittest discover -s scripts/tests -p 'test_clean_knowledge_base.py'
```
