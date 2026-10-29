# Pittsburgh Events: the twelve monthly homepage modules

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/pittsburgh_events.py
```

Requires requests, beautifulsoup4 and Playwright (already present in this project).
If Chromium is missing:

```bash
.venv/bin/python -m playwright install chromium
```

The source folder name comes from source.csv:

```text
data/preprocessed_documents/Pittsburgh events calendar/
  selected_months.json
  documents.jsonl                 # aggregate, ingest this OR monthly files
  summary.json
  failures.jsonl                  # created if errors occur
  raw/
    home.html
    months/YYYY-MM.html          # fully/partially expanded rendered DOM
    months/YYYY-MM.events.json   # parsed occurrence cache
    months/YYYY-MM.status.json   # count checks and completeness
    details/<URL-hash>.html
    event_cache/<occurrence-id>.json # intermediate facts, NOT RAG documents
  months/
    2026-10/
      documents.jsonl             # exactly one monthly document
      document.json               # same document in readable JSON
      article.md                  # Markdown body of that document
    2026-11/
    ...
```

Only the homepage section titled Pittsburgh Events by Month supplies crawl seeds.
Exactly twelve unique month cards are required. Their years are read, not assumed
from the system clock. The inspected snapshot spans October 2026 to September
2027. Month folders use each occurrence's actual displayed date. An event on
multiple dates/times retains every occurrence inside one document per YYYY-MM.
Each occurrence is a Markdown heading with its date/time, venue and other fields.
Stable occurrence IDs include
URL, name, datetime, displayed time and venue; names alone never deduplicate.

Playwright clicks `.gsb-show-more` until absent, checks growth after every click,
and compares parsed/unique counts against the displayed Upcoming Events count.
The separate performer recommendation load-more control is intentionally ignored:
it is not the monthly event-occurrence list. No other homepage modules, performer
pages, venue pages or external ticket sellers are recursively crawled.
Default maximum 300 load-more clicks/month; hitting it or a failed click produces
an explicitly incomplete snapshot, not a success claim. --max-load-more raises
that safeguard. --headed shows the browser.

Each occurrence retains title, local date/time, venue, address, category and
visible price/ticket information. Prices are a collection-time snapshot. The
same-site ticket/detail URL is fetched once and cached. Explicit Event JSON-LD
is retained if available; many ticket URLs only expose a sales widget and have
no editorial Event body. Their intermediate event cache is labelled
`ticket_page_no_editorial_event_data`. No invented descriptions, time zones or
relationships are added, and navigation/footer text is not treated as event data.
Raw detail HTML is retained for inspection. The listing is always a provenance
source; no purchase or form-submission action occurs.

Resume after interruption/failure:

```bash
.venv/bin/python scripts/preprocess_documents/pittsburgh_events.py --resume
```

Completed monthly listings use their frozen snapshot. Incomplete listings are
retried. Already cached event details are skipped unless completeness changes.
Monthly documents are rebuilt from the local listing and detail caches.
To retry missing event details/rewrite documents from cached listings:

```bash
.venv/bin/python scripts/preprocess_documents/pittsburgh_events.py --resume --retry-details
```

A new --output-root creates a fresh collection rather than mixing a later rolling
calendar with the frozen month manifest. Do not run multiple collectors against
one output directory. summary.json's all_12_listings_complete describes only the
captured list counts, not event truth or complete editorial details. Check
 detail_failures, per-month parse_errors and failures.jsonl as well.

Convert an existing per-occurrence collection without downloading again (stop any
old collector process first):

```bash
.venv/bin/python scripts/preprocess_documents/pittsburgh_events.py --assemble-only
```

This includes all locally discovered occurrences, even when detail fetching was
interrupted. It does not claim missing months have been collected. After monthly
files and the aggregate are written successfully, known v1 per-occurrence record
JSON and Markdown files are removed. Raw HTML and intermediate event facts are
retained. Use --resume afterward to continue unfinished collection.

summary.json documents counts months; event_occurrences counts individual
performances. Check details_not_fetched and listing_complete for partial runs.
A fully captured empty month still has one document stating zero occurrences.

JSON uses content_markdown plus metadata, no blocks. Markdown mirrors the body
plus a final newline. Root documents.jsonl and monthly documents.jsonl overlap;
ingest only one representation. Old RAG snapshots and indices are not modified.
