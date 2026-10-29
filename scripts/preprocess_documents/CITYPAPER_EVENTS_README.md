# Pittsburgh City Paper events

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/citypaper_events.py
# Continue using cached successful downloads:
.venv/bin/python scripts/preprocess_documents/citypaper_events.py --resume
```

Defaults to the eight pages at https://community.pghcitypaper.com/events and
?page=2 through ?page=8. No Downtown date filter is applied. Each page must
report the expected Page X of 8 marker; changed pagination is flagged, not
silently treated as complete. Use --pages N with a fresh output root if needed.
The listing links are restricted to same-host /events/<numeric-id>/<slug>.
Repeated URLs across dates/promoted cards are fetched once. Distinct URLs are
kept even if titles match. No links inside details are followed.

Detail extraction requires an H1 and About this event heading. Retains headings,
description, When, Where, prices and Contact. Removes Get tickets, Event website,
Is this your event/promote controls, navigation, footer and script content.
No destinations for those links are requested. Raw HTML is an unmodified audit
snapshot, so it can contain excluded controls; only cleaned documents enter RAG.

Output: data/preprocessed_documents/Pittsburgh City Paper events/
- collection.json: frozen crawl scope
- raw/listings/page-NN.html: listing snapshots
- raw/details/<URL-hash>.html: detail snapshots
- discovered_events.json: deduplicated detail URLs and listing provenance
- article.md, document.json, documents.jsonl: the same single aggregate document
- summary.json: counts, completeness, errors, source-truncated schedules
- failures.json: current-run failures when any

Read only documents.jsonl for RAG. --resume reconstructs the aggregate from
current successful pages; failures result in complete=false and exit code 2.
An empty blocked run does not overwrite an existing document. Check summary.json
for the most recent attempt. complete means all expected listings and discovered
details parsed, not that all recurring dates were visible: “…and N more” is
separately flagged as schedule_truncated_by_source; no dates are invented.

## HTTP 403 / manual HTML

Direct requests and ordinary headless Chromium were blocked by Cloudflare during
development. Browser-readable indexed text confirmed the routes/headings, but
live DOM extraction could not be verified end-to-end. Parser regression tests use
representative fixtures; blocked HTML is rejected rather than saved as content.
The script does not bypass challenges. Try running normally in your environment.
If blocked, save the accessible pages as HTML (or copy rendered outerHTML) and
create a JSON map, for example data/manual/citypaper/html_map.json:

```json
{
  "https://community.pghcitypaper.com/events": "page-1.html",
  "https://community.pghcitypaper.com/events?page=2": "page-2.html",
  "https://community.pghcitypaper.com/events/29161498/cheer-on-the-panthers-our-haus-25fe1929": "panthers.html"
}
```

Add the remaining listing and detail mappings; paths are relative to the map.
Manual files take precedence over cache and use the identical parsing rules.

```bash
.venv/bin/python scripts/preprocess_documents/citypaper_events.py --resume \
  --html-map data/manual/citypaper/html_map.json
# With no network requests (uncached/unmapped URLs are reported as failures):
.venv/bin/python scripts/preprocess_documents/citypaper_events.py --offline \
  --html-map data/manual/citypaper/html_map.json
```

Do not run multiple collectors in the same output folder. Old RAG snapshots and
indices are not changed by this source-specific script.
