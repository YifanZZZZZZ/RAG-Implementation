# CMU About and direct navigation pages

```bash
.venv/bin/python scripts/preprocess_documents/cmu_about.py
```

Output folder name comes from source.csv:
`data/preprocessed_documents/About CMU & CMU History/`.
One document per selected URL, Markdown body plus provenance, no blocks.
`documents.jsonl` is the only ingestion file. Per-URL originals and readable
copies are under raw/, records/ and markdown/. selected_urls.json freezes the
exact crawl list and menu membership. crawl_log.jsonl records failures.

Scope: the About page's main content plus the ordinary menu links inside About,
Academics, Admission, Campus Life and Research. Excludes adjacent featured news
cards, utility navigation and footer links. Does not discover ANY links from
child pages, including their menus. Direct menu destinations on CMU subdomains
are included; redirects outside cmu.edu are rejected. Non-HTML content is not
converted. Anchor-only variants collapse to one page while preserving their
menu labels. The inspected menu yields 30 URLs: homepage + 29 direct destinations.

Body extraction supports main#main-content, main, .layout-content and #content.
Removes site header/footer/navigation, breadcrumb, search and form widgets.
Footer About CMU / Academics / Our Impact / Top Tools is excluded structurally;
legitimate body sections with those words are NOT removed by keyword matching.
Important: some pages are dynamic or use another template; unsupported/short
bodies and request failures are logged, not guessed. Existing HTML does not prove
all JS-loaded content has been retrieved. robots rules apply to each host.

Resume without re-fetching completed pages:

```bash
.venv/bin/python scripts/preprocess_documents/cmu_about.py --resume
```

Re-extract/refetch completed pages: --resume --reprocess.
For browser-saved HTML, --html-map accepts a JSON mapping from URL to file path
(relative paths resolve against the mapping file). It only substitutes URLs in
the frozen selected list, and does not add new crawl seeds. Mapped completed
pages are reprocessed on resume. HTML from manual import has no fabricated fetch
time. Do not run multiple collectors on this output folder simultaneously.

article.md for a single-page source is replaced here by markdown/<URL-hash>.md,
which equals content_markdown plus a final newline. raw/navigation-source.html
is the original About page used to discover the menu, not a second RAG document.
No old corpus, chunking, embedding or evaluation files are modified.
