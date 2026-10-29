# Convert the 13 selected PDFs

```bash
cd "/Users/zhangyifan/Desktop/ANLP HW2"
.venv/bin/python scripts/preprocess_documents/convert_selected_pdfs.py
```

The fixed manifest contains the 2025 operating budget and 12 user-selected tax
forms/instructions/reference PDFs. Chinese-comma-joined links have been split
into separate URLs. No related links are crawled. These sources are separate from
the City of Pittsburgh webpage corpus whose PDFs were excluded.

Folder names are read from config/source.csv:

```text
data/preprocessed_documents/
  City of Pittsburgh 2025 Operating Budget/
  City of Pittsburgh Tax Regulation/
```

Each folder contains raw/ (original PDFs), markdown/ (one Markdown per PDF URL),
records/ (one JSON per PDF URL), documents.jsonl (ingest this only), summary.json,
and conversion_log.jsonl. JSON uses content_markdown, not blocks. Markdown equals
that body plus a final newline. Never ingest records, raw and Markdown as extra
documents alongside documents.jsonl.

One PDF is one document. It contains explicit PDF page headings and layout text
inside Markdown text fences to preserve column spacing. Physical page numbers
and available PDF page labels are both recorded. This is text extraction, not
reconstruction of complex budget/tax tables or OCR of charts/images. Empty or
sparse pages and failed layout extraction are flagged. All-empty documents fail
rather than being added as usable documents. A normal quality flag count does
not guarantee correctness of every column relationship; inspect numeric answers
against the source before treating them as verified facts.

Form field labels/types/values are metadata only. Blank field names are not
injected into retrieval text. The files are public source forms, not filled user
tax returns. Filename/title years are retained as document_year; the 2022 files
are not relabeled 2026. The selected tax sources are forms, instructions, rights
and schedules; this set is not claimed to be the complete tax regulations corpus.

Existing completed records are skipped. Rerun the same command to retry failures.
--reprocess explicitly re-extracts using the cached PDF, not a forced redownload.

```bash
# Budget only
.venv/bin/python scripts/preprocess_documents/convert_selected_pdfs.py --only budget
# Taxes only
.venv/bin/python scripts/preprocess_documents/convert_selected_pdfs.py --only tax
# Local files only, using original URL filenames
.venv/bin/python scripts/preprocess_documents/convert_selected_pdfs.py \
  --pdf-dir /absolute/path/to/pdfs --offline
```

A local directory may contain only some files: without --offline, missing files
are downloaded. A local import does not fabricate fetch time. --output-root
allows separate outputs. Defaults: 150 MiB/file, 15s connection, 45s socket read,
300s streamed download. HTTP failures and non-PDF responses are logged; cross-host
redirects are refused. Failures return a nonzero exit status after continuing to
other files. Existing successful records remain available if reprocessing fails.

Requires requests and pypdf (already in the project's .venv). No changes to
existing RAG snapshots, indices or evaluations. Downstream Markdown-aware
structure chunking should treat each page's layout fence as rows/text, rather
than assuming it is programming code. It remains separate future work.
