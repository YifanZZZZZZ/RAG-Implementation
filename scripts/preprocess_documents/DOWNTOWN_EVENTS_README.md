# Downtown Pittsburgh All Events

Run from the project root:

```bash
.venv/bin/python scripts/preprocess_documents/downtown_events.py --start 2026-10-28 --end 2027-01-01
```

Continue an interrupted run (same date range):

```bash
.venv/bin/python scripts/preprocess_documents/downtown_events.py --start 2026-10-28 --end 2027-01-01 --resume
```

The default window is October 28, 2026 through January 1, 2027. Both endpoints
are inclusive. Multi-day events overlapping the range are included. All tags
means no taxonomy filter. The script uses the calendar's public
`/wp-json/vibemap/v1/events-data` API and follows every pagination page, checking
unique IDs against the declared total. It does not scrape the Featured module.

Each series/content group needs only one representative detail page. Cached member pages are preferred. Full content and event
metadata are read from the matching WordPress event payload embedded in the
HTML. Other preloaded events, recommendations, navigation, images and footer
are excluded. No links inside the event detail are crawled, including external
organizer links. Dates and recurrence descriptions are preserved, not inferred
from slugs; source metadata can itself be inconsistent.

Output folder name follows source.csv:

```
data/preprocessed_documents/Downtown Pittsburgh events calendar/
  collection.json          # frozen date/filter configuration
  raw/api/page-0001.json   # listing responses, intermediate only
  raw/details/<id>.html    # event page snapshots, intermediate only
  article.md              # ALL selected events in one Markdown body
  document.json           # the same body and metadata
  documents.jsonl         # exactly ONE line/document for RAG
  summary.json            # count checks and failures
```

Read only documents.jsonl into RAG. Every event has a second-level heading;
its description has a third-level heading. Records sharing the explicit vibemap_event_id, title, venue, description,
price/status/organizer and recurrence facts are grouped. Different content or
venue variants stay separate. Records lacking a series ID are not merged by title.
This is conservative grouping, not an exact reproduction of the UI card count.
All observed date/time pairs are retained; identical pairs are shown once.
No unobserved dates are synthesized. Source metadata can describe a whole
exhibition range rather than a particular occurrence; the script preserves it.
JSON source_urls retains all member permalinks. event_count counts groups and
occurrence_records counts selected API records. complete refers to list checks
and representative detail extraction, not fetching every member's detail page. Listing descriptions are retained as fallback when a
detail fails, but complete=false and exit code 2 explicitly flag this case.
Resume retries failed requests; successfully downloaded raw files are reused.
API schema or detail markup changes fail visibly rather than ingesting empty
skeletons. Missing/invalid dates are logged, excluded, and mark output incomplete.

One run can take a long time: thousands of listed events each require a detail
request, with at least one second between requests. Do not run two collectors
in the same output folder. Use a new --output-root for a fresh calendar snapshot;
--resume intentionally retains cached API pages. The date cutoff cannot retrieve
future events that have not yet been published. complete checks the API snapshot
and extraction, not real-world completeness through the cutoff date.

## Faster reuse of existing data (v2)

Stop the old running collector with Ctrl+C before using the new script.

```bash
.venv/bin/python scripts/preprocess_documents/downtown_events.py --resume
```

A narrower date range may reuse the existing broader API snapshot. collection.json
keeps the original API query window (so cached pagination stays consistent);
selection.json and document.json record the new output window. Existing raw data
is retained; the single output document is replaced on successful assembly.
The listing progress can therefore still show 3149 cached records; the subsequent
Selected ... records -> ... series/content variants reports the reduced workload.

For NO network requests:

```bash
.venv/bin/python scripts/preprocess_documents/downtown_events.py --offline
```

Missing representative details fall back to the listing's full description,
are recorded in summary.json and yield complete=false / exit code 2. This is
intentional, not a failure to produce documents. Later --resume fetches missing
details. Cached listings must be complete for verified list coverage.
