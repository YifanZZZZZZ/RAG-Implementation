"""Fetch only the Pittsburgh article; preserve structure for later chunking.

Run from any directory. Use --html to parse an already saved page offline.
No child URLs or reference URLs are fetched. Existing RAG snapshots are untouched.
"""
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[2]

URL = "https://en.wikipedia.org/wiki/Pittsburgh"
VERSION = "wikipedia-pittsburgh-v2-markdown"
EXCLUDED = {"see also", "notes", "explanatory notes", "references",
            "citations", "further reading", "external links"}


def text(node):
    # Whitespace between inline tags prevents accidentally concatenating words.
    value = re.sub(r"[\u200b\ufeff\u00ad]", "", node.get_text(" ", strip=True))
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;:!?%])", r"\1", value)
    return re.sub(r"\s+(['’]s)\b", r"\1", value)


def parse_article(html):
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#mw-content-text .mw-parser-output")
    if root is None:
        root = soup.select_one(".mw-parser-output")
    if root is None:
        raise ValueError("Wikipedia article container missing; refusing to save a shell/error page.")
    heading = soup.select_one("#firstHeading")
    title = text(heading) if heading else "Pittsburgh"
    if title != "Pittsburgh":
        raise ValueError(f"Unexpected article title: {title!r}")
    canonical = soup.select_one('link[rel="canonical"]')
    canonical_url = urljoin(URL, canonical.get("href", URL)) if canonical else URL
    revision_link = soup.select_one('#t-permalink a[href]')
    revision_url = urljoin(URL, revision_link["href"]) if revision_link else None

    # Remove image/map-only infobox rows before stripping their images.
    for row in list(root.select("table.infobox tr")):
        if row.parent is not None and row.select_one(
            ".maptable, .switcher-container, .mw-kartographer-map, .infobox-image"
        ):
            row.decompose()

    # Only citation superscripts are removed, not units such as km².
    selectors = (
        "script, style, noscript, nav, form, button, .mw-editsection, "
        "sup.reference, .mw-ref, .reflist, .mw-references-wrap, "
        ".navbox, .vertical-navbox, .sidebar, .hatnote, .ambox, .metadata, "
        ".toc, #toc, .shortdescription, .sistersitebox, .sister-box, "
        ".portalbox, .authority-control, figure, .thumb, .gallery, "
        ".mw-empty-elt, .noprint, img, .mw-kartographer-map, .mw-kartographer-container"
    )
    for node in list(root.select(selectors)):
        if node.parent is not None:
            node.decompose()
    for br in root.find_all("br"):
        br.replace_with("; ")

    def table_markdown(table):
        # Expand merged cells before rendering; never emit HTML table tags.
        trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        grid = {}
        for r, tr in enumerate(trs):
            c = 0
            for cell in tr.find_all(["th", "td"], recursive=False):
                def span(key):
                    raw = str(cell.get(key, "1"))
                    n = int(raw) if raw.isdigit() else 1
                    if key == "rowspan" and n == 0:
                        n = sum(t.parent is tr.parent for t in trs[r:])
                    return max(1, min(n, 1000))
                height, width = min(span("rowspan"), len(trs) - r), span("colspan")
                while any((r, c + j) in grid for j in range(width)):
                    c += 1
                value = (text(cell), cell.name == "th")
                for i in range(height):
                    for j in range(width):
                        grid[r + i, c + j] = value
                c += width
        if not grid:
            return ""
        width = max(c for r, c in grid) + 1
        rows = [[grid.get((r, c), ("", False)) for c in range(width)] for r in range(len(trs))]
        caption = table.find("caption", recursive=False)
        prefix = text(caption) + "\n\n" if caption else ""
        if "infobox" in table.get("class", []):
            lines = []
            for row in rows:
                values = []
                for value, _ in row:
                    if value and (not values or value != values[-1]):
                        values.append(value)
                if len(values) == 1:
                    lines.append("- " + values[0])
                elif values:
                    lines.append("- " + values[0] + ": " + "; ".join(values[1:]))
            return prefix + "\n".join(lines)
        header_count = 0
        for row in rows:
            if any(v for v, _ in row) and all(h or not v for v, h in row):
                header_count += 1
            else:
                break
        headers = []
        for c in range(width):
            labels = []
            for row in rows[:header_count]:
                value = row[c][0]
                if value and (not labels or labels[-1] != value):
                    labels.append(value)
            headers.append(" / ".join(labels) or f"Column {c + 1}")
        def line(values):
            return "| " + " | ".join(v.replace("|", r"\|").replace("\n", " ") for v in values) + " |"
        lines = [line(headers), line(["---"] * width)]
        lines.extend(line([v for v, _ in row]) for row in rows[header_count:]
                     if any(v for v, _ in row))
        return prefix + "\n".join(lines)

    def list_markdown(node):
        lines = []
        raw_start = str(node.get("start", "1"))
        start = int(raw_start) if raw_start.isdigit() else 1
        for i, li in enumerate(node.find_all("li", recursive=False)):
            prefix = f"{start+i}. " if node.name == "ol" else "- "
            parts, pending = [], []
            def flush():
                value = re.sub(r"\s+", " ", " ".join(pending)).strip()
                if value:
                    parts.append(value)
                pending.clear()
            for child in li.children:
                if isinstance(child, Tag) and child.name in {"ul", "ol"}:
                    flush()
                    parts.append(list_markdown(child))
                else:
                    pending.append(text(child) if isinstance(child, Tag) else str(child))
            flush()
            item_lines = "\n".join(parts).splitlines()
            if item_lines:
                lines.append(prefix + item_lines[0])
                lines.extend(" " * len(prefix) + line for line in item_lines[1:])
        return "\n".join(lines)

    sections = ["# " + title]
    headings = []
    excluded_level = None

    def walk(container):
        nonlocal excluded_level
        for node in container.children:
            if not isinstance(node, Tag):
                continue
            if re.fullmatch(r"h[1-6]", node.name):
                level, label = int(node.name[1]), text(node)
                if excluded_level is not None:
                    if level > excluded_level:
                        continue
                    excluded_level = None
                if label.casefold() in EXCLUDED:
                    excluded_level = level
                    continue
                headings.append(label)
                sections.append("#" * level + " " + label)
            elif node.name in {"p", "table", "ul", "ol", "dl", "pre"}:
                if excluded_level is not None:
                    continue
                if node.name == "table":
                    value = table_markdown(node)
                elif node.name in {"ul", "ol"}:
                    value = list_markdown(node)
                elif node.name == "pre":
                    code = node.get_text()
                    fence = "`" * max(3, 1 + max([len(x) for x in re.findall(r"`+", code)] or [0]))
                    value = fence + "\n" + code + "\n" + fence
                elif node.name == "dl":
                    value = "\n".join("- " + text(x) for x in node.find_all(["dt", "dd"], recursive=False) if text(x))
                else:
                    value = text(node)
                if value:
                    sections.append(value)
            else:
                # Handles both classic heading wrappers and Parsoid section wrappers.
                walk(node)

    walk(root)
    content = "\n\n".join(sections)
    if len(content) < 1000 or "History" not in headings:
        raise ValueError("Article completeness check failed: missing History or insufficient content.")
    return title, content, canonical_url, revision_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "config/source.csv")
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/preprocessed_documents")
    parser.add_argument("--html", type=Path, help="Use an existing HTML file instead of downloading")
    parser.add_argument("--overwrite", action="store_true", help="Replace this source's existing output files")
    args = parser.parse_args()
    with args.sources.open(encoding="utf-8-sig", newline="") as f:
        matches = [r for r in csv.DictReader(f) if r["url"].strip() == URL]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Pittsburgh URL in source.csv")
    source = matches[0]
    name = source["source id"].strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Source name cannot safely be used as a directory name")
    out = args.output_root / name
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise FileExistsError(f"Output already exists: {out}. Choose --output-root or --overwrite.")

    now = datetime.now(timezone.utc).isoformat()
    if args.html:
        raw = args.html.read_bytes()
        fetched_at, final_url, method = None, URL, "local_html"
    else:
        print(f"Downloading {URL}", flush=True)
        response = requests.get(URL, timeout=(10, 60), headers={
            "User-Agent": "ANLP-HW2-Research/1.0 (single-page Wikipedia collector)"})
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("Expected HTML response")
        raw = response.content
        fetched_at, final_url, method = now, response.url, "http"
    title, content, canonical, revision = parse_article(raw)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    provenance = {"source_id": name, "category": source["category"].strip(),
                  "source_url": URL, "requested_url": URL, "final_url": final_url,
                  "fetched_at": fetched_at, "processed_at": now, "acquisition_method": method,
                  "raw_path": str((out / "raw.html").resolve()),
                  "imported_from": str(args.html.resolve()) if args.html else None}
    document = {"doc_id": hashlib.sha256(URL.encode()).hexdigest()[:24],
                "title": title, "source_id": name, "source_url": URL,
                "category": source["category"].strip(), "document_type": "encyclopedia_article",
                "fetched_at": fetched_at, "declared_canonical_url": canonical,
                "revision_url": revision, "pipeline_version": VERSION,
                "content_markdown": content, "content_hash": digest,
                "sources": [provenance], "quality_flags": [],
                "markdown_path": str((out / "article.md").resolve())}
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw.html").write_bytes(raw)
    (out / "article.md").write_text(content + "\n", encoding="utf-8")
    (out / "documents.jsonl").write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "documents": 1, "characters": len(content),
                      "format": "markdown-only"}, indent=2))


if __name__ == "__main__":
    main()
