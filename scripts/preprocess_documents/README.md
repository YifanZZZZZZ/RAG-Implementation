# Per-source document scrapers

Each source URL gets a separate Python entry point. Output folder names come
from `config/source.csv` (`source id`, surrounding whitespace stripped).

## Wikipedia Pittsburgh

Run from the project root:

```bash
.venv/bin/python scripts/preprocess_documents/wikipedia_pittsburgh.py
```

Dependencies: `requests`, `beautifulsoup4` (available in `.venv`).

Output:

```text
data/preprocessed_documents/Wikipedia Pittsburgh/
  raw.html
  documents.jsonl
  article.md
```

Version `wikipedia-pittsburgh-v2-markdown` stores one document with provenance
metadata and `content_markdown`. There is no `blocks` field and no dependency
on the old block renderer. `article.md` contains the exact same Markdown body
plus a final newline. Raw HTML is retained separately for auditing.

Headings, paragraphs, nested lists and tables are preserved in Markdown.
Infobox rows become field-value list items. Other tables become Markdown tables:
merged cells are expanded, leading header rows are combined into column names,
and tables without explicit column headings use neutral `Column N` headings.
Complex HTML layout is intentionally not reproduced. No HTML table tags are
emitted. Review complex tables in article.md against the raw source if needed.

Only this article is requested. Links become visible text, without fetching
child or reference URLs. Navigation, edit controls, images, map-only infobox
rows, hatnotes, citation markers and bibliographic sections are excluded.

To regenerate existing output from saved HTML, without a new download:

```bash
.venv/bin/python scripts/preprocess_documents/wikipedia_pittsburgh.py \
  --html "data/preprocessed_documents/Wikipedia Pittsburgh/raw.html" \
  --overwrite
```

Local imports record the processing time and import path, not an invented
fetch time. Use `--output-root /some/directory` to write a separate copy.
Existing nonempty output folders require explicit `--overwrite`.

The old corpus, snapshots, indices and evaluation results are not updated.
IMPORTANT: the existing structure chunker uses blocks. Before applying it to
these new documents, update it to parse Markdown boundaries; otherwise it falls
back to unstructured text. Fixed-length chunking can use content_markdown.

The script validates HTTP status, HTML response type, the article container,
minimum body length and the History heading. These are basic checks, not proof
of complete extraction. HTTP errors fail visibly; saved HTML can be supplied
with --html for offline parsing.


## Wikipedia History of Pittsburgh

Independent entry point using the same Markdown-only document schema:

```bash
.venv/bin/python scripts/preprocess_documents/wikipedia_history_of_pittsburgh.py
```

Output folder (read from source.csv):
`data/preprocessed_documents/Wikipedia History of Pittsburgh/`.
It contains `raw.html`, `documents.jsonl` and `article.md` with the same roles
as the Pittsburgh source above. No blocks field or HTML table tags are emitted.
The two sources are not merged automatically.

This article additionally excludes its Bibliography section (including nested
Primary sources), removes citation page-number markers, and retains historical
quotations as Markdown blockquotes. The introductory paragraphs, historical
sections, tables, lists and jurisdiction timeline remain. Only the article URL
is fetched; linked articles and bibliographic references are not crawled.
Basic completeness checks require Native American era and Jurisdiction timeline.

Offline regeneration uses the same options:

```bash
.venv/bin/python scripts/preprocess_documents/wikipedia_history_of_pittsburgh.py \
  --html "data/preprocessed_documents/Wikipedia History of Pittsburgh/raw.html" \
  --overwrite
```

`article.md` equals the JSON document's `content_markdown` plus a final newline.
JSON additionally stores provenance and document metadata; raw.html is an audit
copy, not a second retrieval document. Only documents.jsonl should be ingested
into the knowledge resource. Markdown remains suitable for fixed token windows;
the existing structure chunker must first be adapted to parse Markdown.
