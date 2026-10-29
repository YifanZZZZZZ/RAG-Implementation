# Britannica Pittsburgh

The CSV source name is `Encyclopedia Brittanica page` (spelling retained).
The scraper is independent and does not use Wikipedia's CSS selectors.

From the project root, use the existing manually saved HTML:

```bash
.venv/bin/python scripts/preprocess_documents/britannica_pittsburgh.py \
  --html "data/manual/Encyclopedia Brittanica page.html"
```

Or try one automatic request:

```bash
.venv/bin/python scripts/preprocess_documents/britannica_pittsburgh.py
```

403/429 fail visibly with instructions to import browser-saved HTML. Missing or
incomplete editorial content also fails rather than writing an empty document.
No bypass of access controls, pagination, or fetching of related articles occurs.

Default output:

```text
data/preprocessed_documents/Encyclopedia Brittanica page/
  raw.html
  article.md
  documents.jsonl
```

`documents.jsonl` has one document: `content_markdown` plus source metadata, no
blocks field. `article.md` is exactly that body plus a final newline. `raw.html`
is an audit copy and must not be ingested as an additional RAG document.

Uses BeautifulSoup with html.parser. Selects editorial sections containing
p.topic-paragraph inside .topic-content .reading-channel. Preserves the intro,
History and The contemporary city, paragraph text (including spans), heading
levels, and any editorial lists/tables. Removes injected newsletter, quiz,
advertising and media modules; excludes AI Q&A, news cards and image captions.
Paragraph preservation is checked against the original editorial paragraphs.
Expected sections are validated. This is not a guarantee that the website supplied
its complete live article, especially if the saved page was only partially loaded.

The available local HTML passed with 9 article paragraphs and two section
headings. Local import records the processing time without fabricating fetch time.
The source's last-modified meta value is recorded in extraction metadata.

Existing output is protected; add --overwrite to regenerate deliberately.
Use --output-root /another/directory to write a separate copy. No old corpus,
chunking, embedding indices or evaluation results are modified.
