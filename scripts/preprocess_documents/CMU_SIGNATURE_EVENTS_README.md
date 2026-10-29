# CMU Signature Events

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/cmu_signature_events.py
# Reuse existing raw HTML and rebuild cleaned documents:
.venv/bin/python scripts/preprocess_documents/cmu_signature_events.py --resume
```

Only the section whose heading is Signature Events is extracted from the parent
page. Includes accordion text (Presidential Tours, CMU125). Its direct event
links are the only discovery seeds; detail pages are never used for discovery.
The inspected page has six card links plus one CMU125 link: seven details plus
one section overview = eight documents. Counts are discovered, not hardcoded.

Footer/navigation, About CMU/Academics/Our Impact/Top Tools site template content,
video embeds/files/YouTube/Vimeo links and watch-video controls are removed.
Other anchor wording stays as ordinary text; no detail child URL destinations
are saved in Markdown. Title and accordion headings, paragraphs, lists and tables
are kept. Raw HTML retains original markup for audit and is not RAG input.

Folder name comes from source.csv. Both the current /engage/events URL and the
legacy /engage/alumni/events/campus/index.html row are recognized, without editing
the CSV. Output is:

```
data/preprocessed_documents/CMU campus events page/
  selected_urls.json
  raw/<URL-hash>.html
  markdown/<URL-hash>.md
  records/<URL-hash>.json
  documents.jsonl
  summary.json
```

Each selected URL produces one document. Ingest only documents.jsonl; it includes
only the selected pages successfully processed this run. Check summary.json for
failures. Requests have timeout, download bounds, robots checks and CMU-only
redirects. No PDF/video download. No full crawl was run during implementation;
real overview/Homecoming samples and extraction tests were used.

If a page is blocked, --html-map accepts a JSON object mapping original page URLs
to saved HTML files. Relative file paths resolve against the map file directory.
Manual files take precedence over existing cache:

```bash
.venv/bin/python scripts/preprocess_documents/cmu_signature_events.py --resume \
  --html-map data/manual/cmu_signature/html_map.json
```
