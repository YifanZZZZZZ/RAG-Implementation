# Carnegie Museums

Run from project root:

```sh
python scripts/preprocess_documents/carnegie_museums.py
```

Outputs `data/preprocessed_documents/Pittsburgh Carnegie Museums/` (name read from source.csv).
`documents.jsonl` is the current RAG input; `markdown/` and `records/` are individual readable/JSON copies. `raw/` preserves source HTML. Do not ingest all three representations together.

Scope: homepage Current Exhibitions and magazine article cards (including In Conversation With Art and Each Other), plus View all Events and every forward Next Events page. Direct detail URLs may point to member museums; no links are followed from a detail page. No artificial date filter or default page cap. The site's default calendar may group recurring events: this collects its displayed cards, not every hidden recurrence. One document per canonical detail URL; repeated listing contexts/dates are retained. Tracking parameters are removed. Exact duplicate URL cards are merged; different articles are not merged merely by similar titles.

Preserves titles, bylines, paragraphs, lists, tables, listing dates and locations. Removes navigation, sidebars, images, videos, related widgets, social controls, ticket buttons and HTML comments. Article selectors are validated on the main museum site. Member-museum templates can differ and some return 403: these require saved HTML and possibly a selector adjustment. Failed details remain explicitly `detail_status: listing_only`; no invented detail text. `summary.json` reports pagination completeness and missing details; partial success exits with code 2. `failures.json` lists issues. Pagination loops, duplicate returned pages, invalid calendars, and network failures do not silently count as completion.

Resume successfully downloaded pages (failed requests are retried):

```sh
python scripts/preprocess_documents/carnegie_museums.py --resume
```

For blocked pages, save HTML in a browser you can access, then make a JSON mapping original URL to the HTML path. Paths may be absolute or relative to the JSON map. `manual_html_map.example.json` lists failed URLs, but its placeholder paths must be replaced with distinct actual files.

```json
{"https://carnegiemnh.org/explore/uprooted/": "uprooted.html"}
```

```sh
python scripts/preprocess_documents/carnegie_museums.py --resume --html-map data/manual/carnegie/map.json
```

Add `--offline` to prohibit requests (missing cache/files remain failures). Manual files take precedence over cache. No access-control bypass is attempted. For a short pagination diagnostic use `--max-pages 2`; the summary deliberately marks that crawl incomplete. Prefer a separate `--output` folder for diagnostics. Re-running regenerates the aggregate from discovered URLs; old raw/per-document audit files may remain, so use only documents.jsonl as the snapshot.
