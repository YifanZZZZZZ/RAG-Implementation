# Pittsburgh Symphony: four requested sections

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/pittsburgh_symphony.py
# Reuse successful raw downloads; retry failed pages:
.venv/bin/python scripts/preprocess_documents/pittsburgh_symphony.py --resume
```

Scope:
- concerts: /calendar, five pages (verified page links); /production/<id>/<slug>
  details only. No date filter, checkout, artist links or further recursion.
- blog: /pso_home/blog is the canonical destination of homepage /blog. Follow
  listing pagination if present and same-site blog article links. External media
  review titles are retained on the listing, but external articles are not crawled.
- musicians: list and all unique /pso_home/biographies/musicians/... profiles.
- faq: extract every details.faq-item summary + faq-answer, including closed
  accordions. No need to click or follow answer links.

Current live probes found five blog articles, 91 unique musician profiles and 34
FAQ pairs. These are observed counts, not hardcoded extraction limits. Calendar
page count must match --concert-pages (default 5); repeated listing pages fail
validation. No recursion from any detail page. No PDFs/videos downloaded.

The main#maincontent container is used, removing global headers/footers, sidebar,
seat-availability timers, checkout notifications, forms, videos, related-content
containers and ticket/purchase/share controls. Meaningful anchor text remains,
but link destinations are not emitted in document bodies. Event dates/times,
program text, biography text and FAQ answers remain as Markdown. Raw snapshots
are original HTML, and can include excluded controls; do not ingest raw files.

Output name follows the source id (trimmed) in config/source.csv:

```
data/preprocessed_documents/Pittsburgh Symphony/
  collection.json
  selected_details.json
  summary.json
  diagnostics/                  # HTTP error status and response excerpts
  documents.jsonl               # aggregate, only current successful documents
  concerts/                     # listing pages + one document per detail URL
    raw/ markdown/ records/ documents.jsonl
  blog/
    raw/ markdown/ records/ documents.jsonl
  musicians/
    raw/ markdown/ records/ documents.jsonl
  faq/
    raw/ markdown/ records/ documents.jsonl
```

Choose root documents.jsonl OR section files, never both. summary.complete=false
and exit code 2 indicate failures or incomplete listings. A failed listing may
prevent discovery of its details; do not interpret a partial result as complete.
Raw cache permits resume; use a new --output-root for a fresh snapshot. Do not run
multiple collectors against one output directory.

## Access failures

Calendar/blog/musician/FAQ and biography/article samples returned 200 during
inspection. Some production pages returned 403 in requests and ordinary Chromium,
so live concert-detail extraction could not be validated end-to-end. The code
requires an actual main container and adequate text, and never ingests a block
page. Three consecutive 403/429 detail failures stop that section's remaining
requests; other sections still proceed. Diagnostics retain the error response.

If your browser can open a failed page, save its rendered HTML and map original
URLs to local paths in a JSON file:

```json
{
  "https://www.pittsburghsymphony.org/production/104554/tchaikovskys-violin-concerto": "tchaikovsky.html"
}
```

Paths resolve relative to the map file; manual files override cache.

```bash
.venv/bin/python scripts/preprocess_documents/pittsburgh_symphony.py --resume \
  --html-map data/manual/pso/html_map.json
```

Only parser tests and limited live probes were run during implementation, not a
full collection. Existing RAG snapshots and indexes are not changed.

## Visible browser for concert details

```bash
.venv/bin/python scripts/preprocess_documents/pittsburgh_symphony.py --resume --browser
```

Uses ordinary visible Chromium only for uncached concert detail pages. It executes
page JavaScript and waits for the main content. If no body appears, the terminal
asks the user to complete verification in the browser and press Enter, or type s
to skip. No automatic CAPTCHA solving or challenge bypass. The event ID is checked
before saving the rendered HTML. One browser context is reused within the run.
This mode does not guarantee access; if blocked even in the visible browser, use
the existing manual HTML map workflow with pages saved from your normal browser.
Existing listing and successful detail snapshots are reused with --resume.
Do not close the opened browser until the script completes.
