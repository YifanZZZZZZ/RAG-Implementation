# Wikipedia cultural institutions

Run from the project root:

```sh
python scripts/preprocess_documents/wikipedia_cultural_institutions.py
```

Seven targets: Pittsburgh Symphony Orchestra, Pittsburgh Opera, Pittsburgh Cultural Trust, Carnegie Museums of Pittsburgh, Heinz History Center, The Frick Pittsburgh, List of museums in Pittsburgh. Only these articles are fetched; no references, venue pages, or museum subsidiaries are followed.

Each article writes to `data/preprocessed_documents/<source id from CSV>/Wikipedia/`:
- `raw.html`: original response for audit/reprocessing.
- `article.md`: readable, structured article text.
- `documents.jsonl`: one JSON record whose content_markdown equals article.md (apart from final newline).

Official website files remain untouched. No blocks or embedded HTML tables are emitted. Preserve textual infobox facts, headings, paragraphs, lists, tables and quotes; remove images, citation markers, maintenance/navigation templates, references, see also, bibliography and external links sections. The shared parser is adapted from the project's Wikipedia history parser. Article title and minimum-body checks reject wrong/empty pages. Source and revision URLs are saved where present for attribution/reproducibility. Wikipedia content remains subject to its applicable license; acknowledge Wikipedia in the report.

Existing successful output is skipped; use `--overwrite` to refresh Wikipedia output only. Each article also has an individual wrapper, e.g. `wikipedia_opera.py`. Alternatively:

```sh
python scripts/preprocess_documents/wikipedia_cultural_institutions.py --only opera
```

If HTTP is blocked, save the full article HTML in your browser, then:

```sh
python scripts/preprocess_documents/wikipedia_opera.py --html "data/manual/Pittsburgh Opera.html" --overwrite
```

Batch offline import supports `--html-map map.json --offline`. Map original Wikipedia URLs to HTML paths relative to the mapping JSON, or absolute paths. Example:

```json
{"https://en.wikipedia.org/wiki/Pittsburgh_Opera": "Pittsburgh Opera.html"}
```

Missing offline files and failed downloads are reported as errors (exit 2); no error page is saved as a document. This script does not automatically merge Wikipedia articles into the existing official-site aggregates. When building your RAG snapshot, include the seven nested Wikipedia/documents.jsonl files once each. Do not also embed article.md as a second copy.

Validation: synthetic extraction checks cover retained structure and excluded sections. A live request to the Opera Wikipedia page returned HTTP 403 in the development environment; full live downloads were not verified.

The museum-list target uses the existing CSV source `Museum More`. Output: `Museum More/Wikipedia/`. Run `python scripts/preprocess_documents/wikipedia_museum_list.py` to collect only this page. Its Name/Neighborhood/Type/Summary table is retained as Markdown; sortable-table UI instructions are removed. No linked museum articles are fetched.
