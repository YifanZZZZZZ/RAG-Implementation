# Heinz History Center scoped collector

Run in project root:

```sh
python scripts/preprocess_documents/heinz_history_center.py
```

Scope:
1. Four direct Family of Museums pages from the homepage.
2. Building the Next Chapter / History for All campaign page only.
3. Exactly two event listing pages, reached through Next Page. Never requests event details. Both pages form one document; card URL retained in event_listings.json. Preserve displayed dates (do not invent missing years), time, location, description and ticket notes. Page numbers and duplicate results checked.
4. Explore Exhibits routes through museum-category directories, including Past Exhibits, to individual exhibit pages. Category pagination is followed if present; no links are followed from an exhibit detail. Past exhibits are marked separately.
5. Detre Library & Archives body plus direct sub-navigation pages: Search, Digital Collections, Additional Resources, Reproductions, Student Research Visits, Programs, Contact. Does not follow database URLs, appointment links, blogs or links from those detail pages.

Output: data/preprocessed_documents/Heinz History Center/ (CSV source id).
- documents.jsonl: aggregate RAG input, one record per successful direct detail/index URL plus one aggregate events document.
- records/ and markdown/: readable copies; do not ingest these again alongside aggregate.
- raw/: original downloaded HTML, including index pages used for discovery.
- event_listings.json: extracted card fields and exact listing URLs.
- discovered.json: queued direct detail URLs with section labels.
- summary.json / failures.json: counts and explicit failures; partial runs exit 2.
- manual_html_map.example.json: failed URLs with placeholder filenames; replace with real saved files.

No images/videos/PDFs are downloaded. Navigation, breadcrumbs, sidebar, footer, related blog/event widgets and newsletter forms are excluded. The script imports the existing Carnegie downloader and Markdown helper, and downtown atomic writers. Temporary 5xx/network errors have bounded retry; access blocks are recorded without bypassing them.

Resume:

```sh
python scripts/preprocess_documents/heinz_history_center.py --resume
```

Manual recovery: save full HTML in your browser, map the exact original URL to a local file in a JSON object. Paths are relative to the mapping file unless absolute. For page 2 use the exact URL including `?current_page=2` (no fragment).

```json
{"https://www.heinzhistorycenter.org/events/?current_page=2": "events-page2.html"}
```

```sh
python scripts/preprocess_documents/heinz_history_center.py --resume --html-map data/manual/heinz/map.json
```

Add --offline to prohibit requests. If an index is blocked, its descendants cannot be discovered until its HTML is supplied; rerun after adding that file. Each run rebuilds documents.jsonl from current successful extraction and reports missing coverage. Existing Wikipedia/ is untouched and not included automatically. Raw and individual audit files from prior runs can remain; aggregate is the active snapshot. Use --output to test in a separate folder.

Validation: real saved homepage, research index, one family page, and event page 1 passed parsing checks (4 family links, 7 research links, 12 event summaries). Exhibit link/scope logic has synthetic coverage. Campaign, exhibit-directory and event-page-2 direct requests returned 403 during inspection; their full live extraction is not verified. No full crawl was run.
