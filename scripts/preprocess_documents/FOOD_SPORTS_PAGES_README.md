# Food and sports: 16 explicit pages only

```sh
python scripts/preprocess_documents/food_sports_pages.py
```

No child links, event details, pagination, load-more, video or PDF requests. HTTP redirects for the requested page can occur. FAQ answers in HTML are preserved. Team/news pages capture static current-page titles/summaries rather than linked articles. Dates/years are retained as displayed, not inferred or updated. This can include outdated source text.

Strategies: Visit Pittsburgh main body with related-content modules removed; Taco Webflow header/content sections excluding venue/footer/credits; Restaurant Week WordPress entry-content; Little Italy/Banana WordPress/main fallbacks; MLB/Steelers/NHL main containers; Wikipedia shared structured article parser. Preserve headings, paragraphs, lists and tables; no JSON blocks or HTML table markup. Actual FAQ answers containing contact details remain; footer contact/privacy boilerplate is excluded. No broad keyword deletion of paragraphs mentioning contacts.

Each page has an independent `page_<slug>.py` entry point. List slugs with --help. Examples:

```sh
python scripts/preprocess_documents/page_food_festivals.py
python scripts/preprocess_documents/page_taco_faqs.py
python scripts/preprocess_documents/food_sports_pages.py --only penguins-news
```

Output `data/preprocessed_documents/<CSV source id>/Single Pages/`:
- documents.jsonl: source-level aggregate, one document per URL.
- records/<slug>.json and markdown/<slug>.md: copies for inspection.
- raw/<URL hash>.html: source HTML.
- last_run.json: selected-run failures, available document count.
- manual_html_map.example.json: failed URL -> placeholder local file.

Official-site and Wikipedia data for the same source share this aggregate but remain separate documents. Source names follow CSV literally (including existing `Pickleburgh` spelling, trimming whitespace). Prior datasets and Wikipedia/ are untouched. Include Single Pages/documents.jsonl in the RAG snapshot once; do not ingest its individual copies again. Earlier overlapping snapshots need URL deduplication when merged.

Resume downloads using --resume. Existing successful per-page records are retained if a refresh fails; inspect last_run.json and processed_at to distinguish old data from new success. No successful cached HTML exists for HTTP failures, so they are retried. Bounded retry for transient 5xx/network failures comes from the shared Fetcher; 403 is not bypassed.

Manual single page:

```sh
python scripts/preprocess_documents/page_little_italy_schedule.py --html "data/manual/little-italy-schedule.html" --offline
```

Batch manual mapping (paths relative to JSON file or absolute):

```json
{"https://littleitalydays.com/entertainment-schedule/": "schedule.html"}
```

```sh
python scripts/preprocess_documents/food_sports_pages.py --resume --html-map data/manual/food-sports/map.json
```

Add --offline to prohibit requests. Save rendered outerHTML if normal HTML only contains a dynamic shell. Missing expected body containers fail explicitly instead of saving navigation/error pages. Dynamic content not present in HTML cannot be guaranteed complete.

Validation: 8 actual HTML samples (both Visit Pittsburgh pages, Taco About/FAQ, Restaurant Week, MLB, Steelers, NHL) passed parsing. FAQ, restaurant prices and footer/related-content removal checked. Four Wikipedia pages, two Little Italy and two Banana pages returned 403 in development; their live content/template extraction is unverified. Generic fallback templates may require adjustment after manual files are available. The script has not been run to generate production documents.

## Additional Visit Pittsburgh pages

The registry now contains 20 unique URLs. Sports Teams was already present. Run only the five extra URLs with:

```sh
python scripts/preprocess_documents/food_sports_pages.py --visit-extras
```

New slugs: free-things, top-things, family-fun, restaurants-culinary. They use the existing CSV source `Visit Pittsburgh webpage`, under `Single Pages/`. Sports Teams keeps `Visit Pittsburgh Sports`. Query parameter hitsPerPage=96 is retained; it is not a guarantee of 96 HTML cards. In inspected static HTML, Family Fun and Restaurants contained empty JavaScript listing shells. These shells are removed and documents flagged dynamic_listing_missing; the run exits 2 and last_run.json records warnings. Save rendered DOM after loading completes and import with --html or --html-map. No loading of more pages or child URLs occurs.

## Picklesburgh News

Added https://www.picklesburgh.com/news/ (21 unique URLs total). Run `python scripts/preprocess_documents/page_picklesburgh_news.py` or use the batch command. Uses CSV source `Pickleburgh` and its existing Single Pages aggregate. Only current-page news headings/summaries are captured; no article links or pagination followed. The news parser selects the listing container rather than the first entry-content. Live inspection returned HTTP 403; real template validation remains pending. Import saved rendered HTML with `--html data/manual/picklesburgh-news.html --offline`.
