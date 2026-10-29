# Pittsburgh Cultural Trust

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/trustarts.py
# Reuse successful raw HTML and retry missing pages:
.venv/bin/python scripts/preprocess_documents/trustarts.py --resume
```

Scope:
- Homepage See all Events points to /calendar?organization_id=1. Preserve this
  organization filter on all nine pages. Default --concert-pages 9. No date filter.
  Discover only event-card title links, including /production/, /event/, /exhibit/.
- /pct_home/blog: discover story links only from h3 titles under the section
  headed Featured Stories, never the Blog sidebar. The live sample has 64 stories
  and no pagination. Follow main-section listing pagination if added later.
- Details are terminal crawl nodes. No detail sublinks or images are requested.

Blog detail extraction uses section.blog-post article.post, plus the main title,
published date and author. Main calendar/event content excludes navigation,
footer, sidebars, checkout timers, ticket buttons, forms, videos and subscription
modals. Meaningful anchor text is retained without link destinations. Raw HTML
can still contain excluded markup; raw files are audit inputs, not RAG documents.

Output name follows config/source.csv, trimming surrounding whitespace:

```
data/preprocessed_documents/Pittsburgh Cultural Trust/
  collection.json
  selected_details.json
  summary.json
  diagnostics/
  documents.jsonl
  concerts/
    raw/ markdown/ records/ documents.jsonl
  blog/
    raw/ markdown/ records/ documents.jsonl
```

One document per successful listing/detail URL. Root documents.jsonl aggregates
both sections; use it OR section files, not both. The concerts folder name denotes
all events in the selected Trust calendar, not only musical concerts.

Check summary.json: complete=false and exit code 2 indicate missing pages/details.
Calendar page count changes and repeated page results fail explicitly. After three
consecutive blocked detail requests, stop that section and continue to the other;
remaining URLs are saved in selected_details.json. --resume reuses successful raw
files. Use a new --output-root for a fresh snapshot; never run concurrent collectors
against one folder. Old RAG snapshots/indices are not automatically updated.

## HTTP 403 and manual HTML

Limited live probes returned 200 for calendar, Featured Stories and one story.
An event detail returned 403. Accordingly the event-detail parser has fixture
coverage but has NOT been verified on an accessible live production page.
The script never accepts a block page lacking its main content container.

If your ordinary browser can open a failed event, save its rendered HTML and
create a JSON map from original URL to local HTML path:

```json
{
  "https://trustarts.org/production/110049/ken-russells-the-devils-1971": "devils.html"
}
```

Relative paths resolve against the map directory. Manual HTML takes priority over
cached files:

```bash
.venv/bin/python scripts/preprocess_documents/trustarts.py --resume \
  --html-map data/manual/trustarts/html_map.json
```

An optional --browser opens ordinary Chromium for uncached event details. It asks
the user to complete any verification manually or skip; it does not bypass access
restrictions and is not guaranteed to work. Do not repeatedly retry Access
Restricted pages. Main-section acquisition and detail failures remain separate.
No full crawl was performed during implementation.
