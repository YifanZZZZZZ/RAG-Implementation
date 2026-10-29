# City of Pittsburgh scraper

From the ANLP HW2 project root:

```bash
.venv/bin/python scripts/preprocess_documents/city_pittsburgh.py --max-pages 30
```

This is a first inspection batch, not full coverage. Continue the SAME crawl:

```bash
.venv/bin/python scripts/preprocess_documents/city_pittsburgh.py --resume
```

Each invocation allows up to 3,000 request tasks by default; listing pages count.
Repeat --resume while summary.json reports pending > 0. If needed choose a higher
--max-pages. Ctrl+C saves the pending queue and assembles documents.jsonl.
Do not run two collectors against the same output directory simultaneously.

Retry recorded failures explicitly:

```bash
.venv/bin/python scripts/preprocess_documents/city_pittsburgh.py --resume --retry-failed
```

Output directory name is read from source.csv:

```text
data/preprocessed_documents/City of Pittsburgh webpage/
  documents.jsonl       # ingest this only: one document per final page/PDF URL
  raw/                   # original HTML/PDF, including each fetched listing page
  markdown/              # readable copy of each document body
  records/               # per-document JSON checkpoints; do not ingest twice
  state.json             # queue and completed tasks for resume
  crawl_log.jsonl        # status and elapsed time per request task
  discovered_urls.jsonl  # links, source pages, decisions and skipped reasons
  failures.jsonl         # created when failures occur
  coverage_issues.jsonl  # created for thin pages/listing limits
  summary.json
  robots.txt
```

`article.md` for a single-page source becomes `markdown/<URL-hash>.md` for this
multi-page source. Its text equals content_markdown plus a final newline.
Documents have provenance and Markdown only, never blocks or HTML table markup.
File names are URL hashes so similarly titled pages do not overwrite each other.
Redirect aliases share one final-URL document. Different URLs with identical
content retain separate provenance and share a content_hash; corpus-wide content
deduplication can use that hash later. Original snapshots/indices remain unchanged.

## Scope and completeness

- Starts from the five branches, all twelve shortcuts, News-articles and
  Events-directory. Reads global navigation within those branches and main/local
  navigation links. Navigation itself is excluded from body text.
- Follows internal public information pages, including links crossing branches.
  Default discovery depth is 8; skips are recorded. The crawler does NOT claim
  that a depth limit proves coverage. Raising depth after a crawl does not replay
  already processed pages: use a new --output-root for changed discovery limits.
- Reads OpenCities Next-page POST forms, preserving current form state. Checks
  returned page number and repeated result fingerprints. Defaults to at most
  300 pages PER listing; capped listings are explicitly logged. For a different
  listing cap start a new output root to replay discovery.
- Includes news and meeting details without keyword/date exclusion by default,
  prioritizing information coverage over corpus size. Dates stay in the text and
  metadata where exposed. It does not invent event dates or classify recurrence.
  Events-directory is explicitly searched from 2026-10-01 (override with
  --events-from YYYY-MM-DD), using the observed non-JavaScript date form. The
  server-returned date value is checked. There is no fixed end-date cutoff.
  Recurring activity overview pages are also followed through the site branches.
  Deleted/unlinked historical events cannot be guaranteed.
- External partners/payment/form applications are recorded, not recursively
  crawled or submitted. Other exact source.csv URLs are delegated to their own
  collectors (including the specified budget PDF).
- PDFs linked within the city domain are fetched by default (pypdf required).
  Each PDF is ONE document with page headings. Layout text is not a reconstruction
  of complex PDF tables. Empty pages are flagged; scanned PDFs need OCR/manual
  handling. Use --skip-pdfs to record attachments without fetching them.
- Other attachments (spreadsheets, Word, etc.) are recorded, not parsed.
- Main body includes accordion/tab content if already present in downloaded HTML.
  Missing/short content is logged, never counted as a complete document. This
  version does not render JavaScript, OCR images or access hidden API endpoints.
  For 403/429, dynamic shells, image-only facts or missing date-filter pages,
  inspect failures/coverage logs and handle separately. Requests has been verified
  on the homepage, service examples, one real news pagination transition and the events date filter.
- robots.txt rules apply (including wildcards). One-second minimum delay; a crawl
  delay above --max-wait (30 seconds) fails visibly instead of waiting an hour.
  HTTP errors are logged, not repeatedly retried. Explicit --retry-failed retries.
- Default request limits: connect 10s/read 30s, streaming 120s and 30 MiB/file.
  Raw failed parse responses remain available for diagnosis.

The queue can finish while excluded, thin, failed or dynamically undiscovered
pages remain. Review summary + logs; do not report queue exhaustion as complete
website coverage. Homepage/card summaries and detail text may overlap; retrieval
quality and further deduplication should be checked after collection.

Dependencies already available in the collection environment: requests,
beautifulsoup4, pypdf. Install if needed:

```bash
.venv/bin/python -m pip install -r scripts/requirements-collect.txt
```

Offline regression tests (no network or production output):

```bash
.venv/bin/python -m unittest discover -s scripts/preprocess_documents/tests -p 'test_city_pittsburgh.py'
```

The existing structure chunker still needs adaptation to Markdown-only documents.
No embedding, chunking or evaluation is run by this scraper.
