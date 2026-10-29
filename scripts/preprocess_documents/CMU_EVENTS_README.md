# CMU Events Calendar

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/cmu_events.py
# Resume using cached monthly JSON and details:
.venv/bin/python scripts/preprocess_documents/cmu_events.py --resume
```

Default inclusive range: 2026-10-26 through 2027-01-01. America/New_York is used,
including its daylight-saving transition. --start / --end accept YYYY-MM-DD.
A changed date window requires a fresh --output-root to avoid mixing snapshots.

The public LiveWhale month API is queried once for each of October, November,
December and January. hide_repeats=false; month_view_day_limit=1000; max=1000.
Every day in the requested window must be present. Each day's event_count is
compared with returned records; missing dates/count mismatches or safety-cap
hits mark results incomplete. Adjacent-month cells and dates outside the range
are excluded. Empty days are explicitly recorded. The scope follows the main
calendar's exclude-from-main-calendar and Admin exclusions, not all private or
department-only calendars.

The old example's request-header syntax and month['events'] assumptions are not
used: the currently verified response contains days[].events. Query parameters
are URL encoded and use New York time, not Europe/Athens. A live October 26 probe
returned 13 events in both day and expanded month views.

Internal event details use the same /live/calendar/view/event/slug/... JSON that
the rendered detail page requests. Only summary, description and cost are
whitelisted. Contact sidebar, maps, related content, share and registration
controls are excluded. Activity time/location come from the calendar occurrence
so repeated events do not inherit an incorrect representative date.

Some calendar href values point directly to an external detail page. Only that
page is fetched; recognizable main/article content is extracted after removing
sidebars and site navigation. No sublinks, images or PDFs are crawled. Failures
retain calendar summary and are logged, rather than silently accepting a full
page or empty content. Each distinct detail URL is cached and reused across days.
No new recurrence dates are generated. Date/time source inconsistencies are not
silently corrected.

Output folder name follows source.csv:

```
data/preprocessed_documents/CMU events calendar/
  collection.json
  raw/months/YYYY-MM.json
  raw/details/<hash>.json   # internal detail API data
  raw/details/<hash>.html   # direct external detail pages
  months/YYYY-MM/article.md
  months/YYYY-MM/document.json
  months/YYYY-MM/documents.jsonl
  documents.jsonl          # aggregate: normally four monthly documents
  summary.json
```

The January document includes only January 1. Markdown uses month H1, day H2,
event H3 and detail headings H4. Raw API responses can include ignored sidebar
fields, but documents contain only the whitelisted main-body fields. Ingest only
the aggregate documents.jsonl OR the individual monthly files, never both.
complete=false and exit code 2 mean check summary.json. A complete snapshot does
not guarantee the site has already published every future event. No full-range
crawl was run during implementation; live probes and parser tests were used.

## Diagnosing 403

On 403/429, diagnostic status, selected non-sensitive headers and a limited
response excerpt are written to diagnostics/. Further requests to the same host
stop for that run. HTTP 403 is not automatically identified as Cloudflare.

Try the normal browser session transport:

```bash
.venv/bin/python scripts/preprocess_documents/cmu_events.py --resume --browser
```

This opens ordinary Chromium on the calendar and uses its request context for
API responses; robots rules and extraction checks remain active. It does not
solve challenges or guarantee access. If the browser homepage itself is rejected,
the run stops. Browser-mode response body size is checked after download.
Share the diagnostics status and response excerpt if this mode also fails;
do not repeatedly retry blocked requests. Failed request bodies are never used
as document content.
