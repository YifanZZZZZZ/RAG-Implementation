"""Seven explicitly selected Wikipedia articles; no sublink crawling.

Shared Markdown parser adapted from the project Wikipedia history scraper.
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
from bs4 import BeautifulSoup, Tag, Comment

ROOT = Path(__file__).resolve().parents[2]

VERSION = "wikipedia-cultural-institutions-v1"
EXCLUDED = {"see also", "notes", "explanatory notes", "references",
            "citations", "bibliography", "further reading", "external links"}


def text(node):
    # Whitespace between inline tags prevents accidentally concatenating words.
    value = re.sub(r"[\u200b\ufeff\u00ad]", "", node.get_text(" ", strip=True))
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;:!?%])", r"\1", value)
    return re.sub(r"\s+(['’]s)\b", r"\1", value)


def parse_article(html, expected_title, url):
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    root = soup.select_one("#mw-content-text .mw-parser-output")
    if root is None:
        root = soup.select_one(".mw-parser-output")
    if root is None:
        raise ValueError("Wikipedia article container missing; refusing to save a shell/error page.")
    heading = soup.select_one("#firstHeading")
    title = text(heading) if heading else expected_title
    if title != expected_title:
        raise ValueError(f"Unexpected article title: {title!r}")
    canonical = soup.select_one('link[rel="canonical"]')
    canonical_url = urljoin(url, canonical.get("href", url)) if canonical else url
    revision_link = soup.select_one('#t-permalink a[href]')
    revision_url = urljoin(url, revision_link["href"]) if revision_link else None

    if expected_title == "List of museums in Pittsburgh":
        # UI instructions for the sortable HTML table are not museum facts.
        for paragraph in list(root.find_all("p")):
            if text(paragraph).startswith("To use the sortable table,"):
                paragraph.decompose()
        required = {"name", "neighborhood", "type", "summary"}
        if not any(required.issubset({text(th).casefold() for th in table.find_all("th")})
                   for table in root.find_all("table")):
            raise ValueError("Museum list table missing expected columns")

    # Remove image/map-only infobox rows before stripping their images.
    for row in list(root.select("table.infobox tr")):
        if row.parent is not None and row.select_one(
            ".maptable, .switcher-container, .mw-kartographer-map, .infobox-image"
        ):
            row.decompose()

    # This article embeds a historical quotation inside an image caption.
    # Keep the quote in document order while excluding the surrounding image.
    for figure in root.select("figure, .thumb"):
        for quote in list(figure.find_all("blockquote")):
            figure.insert_before(quote.extract())

    # Only citation superscripts are removed, not units such as km².
    selectors = (
        "script, style, noscript, nav, form, button, .mw-editsection, "
        "sup.reference, .mw-ref, .reflist, .mw-references-wrap, .reference, .rp, "
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
            elif node.name == "blockquote":
                if excluded_level is None:
                    start = len(sections)
                    walk(node)
                    quoted = sections[start:]
                    del sections[start:]
                    value = "\n\n".join(quoted) or text(node)
                    if value:
                        sections.append("\n".join("> " + line for line in value.splitlines()))
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
    if len(content) < 500 or not headings:
        raise ValueError("Article completeness check failed: missing sections or insufficient content.")
    return title, content, canonical_url, revision_url


# CSV names/categories are resolved from the registered website or Wikipedia URL.
TARGETS = {
    'symphony': ('Pittsburgh Symphony Orchestra', 'https://www.pittsburghsymphony.org/'),
    'opera': ('Pittsburgh Opera', 'https://pittsburghopera.org/'),
    'cultural-trust': ('Pittsburgh Cultural Trust', 'https://trustarts.org/'),
    'carnegie-museums': ('Carnegie Museums of Pittsburgh', 'https://carnegiemuseums.org/'),
    'heinz-history-center': ('Heinz History Center', 'https://www.heinzhistorycenter.org/'),
    'frick': ('The Frick Pittsburgh', 'https://www.thefrickpittsburgh.org/'),
    'museum-list': ('List of museums in Pittsburgh', 'https://en.wikipedia.org/wiki/List_of_museums_in_Pittsburgh'),
}


def collect(slug, args, sources, mapping):
    from downtown_events import atomic
    from urllib.parse import quote
    title, official = TARGETS[slug]
    url = 'https://en.wikipedia.org/wiki/' + quote(title.replace(' ', '_'))
    matches = [r for r in sources if r['url'].strip().rstrip('/') == official.rstrip('/')]
    if len(matches) != 1:
        raise ValueError('Expected exactly one source URL row in source.csv: '+official)
    source = matches[0]
    name = source['source id'].strip()
    if not name or name in {'.','..'} or '/' in name or '\\' in name:
        raise ValueError('Unsafe source folder name')
    out = args.output_root / name / 'Wikipedia'
    if (out/'documents.jsonl').exists() and not args.overwrite:
        print(f'SKIP existing: {out} (use --overwrite to refresh)',flush=True)
        return
    now = datetime.now(timezone.utc).isoformat()
    local = args.html or mapping.get(url)
    if local:
        raw = Path(local).read_bytes()
        method, fetched, final_url = 'local_html', None, url
    else:
        if args.offline: raise ValueError('Offline: no HTML mapping for '+url)
        print('Downloading '+url,flush=True)
        response = requests.get(url, timeout=(10,60), headers={
            'User-Agent':'ANLP-HW2-Research/1.0 (Wikipedia article collector)'})
        response.raise_for_status()
        if 'html' not in response.headers.get('Content-Type','').lower():
            raise ValueError('Expected HTML response')
        raw = response.content
        method, fetched, final_url = 'http', now, response.url
    actual_title, content, canonical, revision = parse_article(raw,title,url)
    document = {
        'doc_id':hashlib.sha256(url.encode()).hexdigest()[:24],
        'title':actual_title,'source_id':name,'source_url':url,
        'category':source['category'].strip(),'official_website':official if official != url else None,
        'document_type':'encyclopedia_article','acquisition_method':method,
        'fetched_at':fetched,'processed_at':now,'final_url':final_url,
        'declared_canonical_url':canonical,'revision_url':revision,
        'pipeline_version':VERSION,'content_markdown':content,
        'content_hash':hashlib.sha256(content.encode()).hexdigest(),
        'raw_path':str((out/'raw.html').resolve()),
        'markdown_path':str((out/'article.md').resolve()),
        'imported_from':str(Path(local).resolve()) if local else None,
        'quality_flags':[],
    }
    # Validate extraction before replacing any successful output.
    out.mkdir(parents=True,exist_ok=True)
    (out/'raw.html').write_bytes(raw)
    atomic(out/'article.md',content+'\n')
    atomic(out/'documents.jsonl',json.dumps(document,ensure_ascii=False)+'\n')
    print(f'SAVED {actual_title}: {len(content)} characters -> {out}',flush=True)


def main(default_target=None):
    import time
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only',choices=list(TARGETS),default=default_target)
    parser.add_argument('--sources',type=Path,default=ROOT/'config/source.csv')
    parser.add_argument('--output-root',type=Path,default=ROOT/'data/preprocessed_documents')
    parser.add_argument('--html',type=Path,help='Saved HTML for a single selected article')
    parser.add_argument('--html-map',type=Path,help='JSON: Wikipedia URL -> HTML path, relative to mapping file')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--overwrite',action='store_true',help='Refresh Wikipedia files only; never official website files')
    args=parser.parse_args()
    if args.html and not args.only: parser.error('--html requires --only or a single-article script')
    with args.sources.open(encoding='utf-8-sig',newline='') as f:
        sources=list(csv.DictReader(f))
    mapping={}
    if args.html_map:
        mapping={u:(args.html_map.resolve().parent/filename).resolve()
                 for u,filename in json.loads(args.html_map.read_text()).items()}
    targets=[args.only] if args.only else list(TARGETS)
    failures=[]
    for i,slug in enumerate(targets):
        print(f'[{i+1}/{len(targets)}] {slug}',flush=True)
        try: collect(slug,args,sources,mapping)
        except Exception as exc:
            failures.append((slug,str(exc)))
            print(f'FAILED {slug}: {exc}',flush=True)
        if not args.offline and not args.html and i+1<len(targets):time.sleep(1)
    if failures:
        print(json.dumps({'failures':failures},ensure_ascii=False,indent=2))
        raise SystemExit(2)


if __name__=='__main__': main()
