# Visit Pittsburgh: selected homepage modules

Run from the project root:

```bash
.venv/bin/python scripts/preprocess_documents/visit_pittsburgh.py --max-pages 30
.venv/bin/python scripts/preprocess_documents/visit_pittsburgh.py --resume
```

The first command is a limited inspection batch. Subsequent invocations process
up to 1000 pages each; repeat --resume if summary.json has pending URLs.
Only ONE crawler should write this output directory at a time.

Output: `data/preprocessed_documents/Visit Pittsburgh webpage/`, matching the CSV
source id after trimming whitespace. documents.jsonl is the only ingestion file;
raw/, markdown/ and records/ hold per-URL copies/checkpoints. state.json supports
resume; crawl_log.jsonl, discovered_urls.jsonl, failures.jsonl and
coverage_issues.jsonl explain coverage and skipped/failed content.

Rules:
- Homepage depth 0. Preserve its main body, excluding template/advertising and
  recommendation modules. Only links in What's Coming Up, More Fall Inspiration!
  and Plan Your Trip start traversal. Missing headings fail visibly.
- Child pages depth 1..3. Follow main-body links after removing Related Blogs,
  Related Blog Posts, Related Content, Suggested Content and other recognized
  recommendation sections. Never follow global menu/footer links. At depth 3,
  save the page but do not request its children. Breadth-first traversal dedupes URLs.
- Exact host www.visitpittsburgh.com only, HTTPS. Other subdomains and the bare
  domain are not followed. Every redirect is checked BEFORE requesting its target.
- No PDFs or other non-HTML attachments. Content-Type and PDF magic are checked
  before saving responses. No external API, partner website or iframe requests.
- Keep Markdown headings, paragraphs, lists and tables; no blocks field.
  markdown/<URL-hash>.md is content_markdown plus a final newline. Redirect aliases
  share the final-URL document. Links in body text do not authorize extra crawling.
- robots.txt respected, minimum 1 second between requests. Long crawl delays,
  HTTP errors and missing main containers fail visibly. No automatic rapid retries.
- --resume --retry-failed retries failures explicitly. Ctrl+C saves state and
  assembles documents.jsonl. Old corpus/chunks/indices are untouched.

## Dynamic content

Family Fun and some other directory pages load listings with JavaScript. The
static main body is retained and quality_flags marks dynamic_listing_incomplete.
Coverage log entries are historical; current document quality_flags show whether
an imported snapshot has resolved the gap. The collector does not call third-party
search APIs, because cross-domain retrieval is excluded by this task's scope.

For a fully loaded browser snapshot, save the rendered outerHTML (saving a page
can retain only the original shell). Create a JSON map of URL to local file:

```json
{
  "https://www.visitpittsburgh.com/things-to-do/family-fun/": "manual/family-fun.html"
}
```

Paths resolve relative to that JSON file. Pass it while resuming:

```bash
.venv/bin/python scripts/preprocess_documents/visit_pittsburgh.py \
  --resume --html-map /absolute/path/to/manual_map.json
```

Existing documents matching the map are reprocessed and their approved body links
are explored within the SAME depth limit. Pending mapped URLs use the snapshot
when reached. New map entries are NOT arbitrary extra crawl seeds. Add
--retry-failed if importing a previously failed page. This does not bypass access
controls or guarantee completeness of browser-paginated listing widgets.

Verified against the saved live homepage: 16 module cards, 15 unique URLs.
Three representative detail pages verified recommendation removal and dynamic
listing detection. No complete crawl has been run by the assistant.

Offline regression tests:

```bash
.venv/bin/python -m unittest discover -s scripts/preprocess_documents/tests -p 'test_visit_pittsburgh.py'
```

Uses requests/beautifulsoup4 and the project's city_pittsburgh Markdown renderer
and robots parser only; no city-specific crawl rules are used.
