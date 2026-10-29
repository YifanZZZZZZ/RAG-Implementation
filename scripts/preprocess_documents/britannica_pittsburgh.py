"""Britannica Pittsburgh: article body only, Markdown plus source metadata.

Supports HTTP and --html manual imports. No linked articles are fetched.
Rejects error pages and obvious incomplete articles; cannot prove server completeness.
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
URL = "https://www.britannica.com/place/Pittsburgh"
VERSION = "britannica-pittsburgh-v1-markdown"

def text(node):
    value = re.sub(r'[\u200b\ufeff\u00ad]', '', node.get_text(' ', strip=True))
    value = re.sub(r'\s+', ' ', value).strip()
    return re.sub(r'\s+([,.;:!?%])', r'\1', value)

def render_table(table):
    trs = [tr for tr in table.find_all('tr') if tr.find_parent('table') is table]
    grid = {}
    for r, tr in enumerate(trs):
        c = 0
        for cell in tr.find_all(['th', 'td'], recursive=False):
            def span(key):
                raw = str(cell.get(key, 1))
                n = int(raw) if raw.isdigit() else 1
                if key == 'rowspan' and n == 0:
                    n = sum(t.parent is tr.parent for t in trs[r:])
                return min(1000, max(1, n))
            height, width = min(span('rowspan'), len(trs)-r), span('colspan')
            while any((r, c+j) in grid for j in range(width)):
                c += 1
            for i in range(height):
                for j in range(width):
                    grid[r+i, c+j] = (text(cell), cell.name == 'th')
            c += width
    if not grid:
        return ''
    width = max(c for r, c in grid)+1
    rows = [[grid.get((r,c), ('',False)) for c in range(width)] for r in range(len(trs))]
    n = 0
    for row in rows:
        if any(v for v,h in row) and all(h or not v for v,h in row):
            n += 1
        else:
            break
    headers = []
    for c in range(width):
        labels = []
        for row in rows[:n]:
            v = row[c][0]
            if v and (not labels or labels[-1] != v):
                labels.append(v)
        headers.append(' / '.join(labels) or f'Column {c+1}')
    def line(values):
        return '| ' + ' | '.join(v.replace('|', r'\|') for v in values) + ' |'
    caption = table.find('caption', recursive=False)
    parts = [text(caption)] if caption else []
    parts += [line(headers), line(['---']*width)]
    parts += [line([v for v,h in row]) for row in rows[n:] if any(v for v,h in row)]
    return '\n'.join(parts)

def to_markdown(root):
    """Walk DOM once, including accordion/tab bodies and free text in divs."""
    def inline(node):
        return text(node) if isinstance(node, Tag) else re.sub(r'\s+', ' ', str(node))
    def walk(node):
        parts, pending = [], []
        def flush():
            value = re.sub(r'\s+', ' ', ' '.join(pending)).strip()
            if value:
                parts.append(value)
            pending.clear()
        for child in node.children:
            if not isinstance(child, Tag):
                pending.append(str(child))
                continue
            name = child.name
            if name in {'div','section','article','aside','header','details','p','h1','h2','h3','h4','h5','h6','ul','ol','dl','table','blockquote','pre','hr'}:
                flush()
                if re.fullmatch('h[1-6]', name):
                    value = '#' * int(name[1]) + ' ' + text(child)
                elif name == 'table':
                    value = render_table(child)
                elif name in {'ul','ol'}:
                    lines = []
                    start = int(child.get('start', 1)) if str(child.get('start',1)).isdigit() else 1
                    for i, li in enumerate(child.find_all('li', recursive=False)):
                        prefix = f'{start+i}. ' if name == 'ol' else '- '
                        lines0 = walk(li).splitlines()
                        if lines0:
                            lines.append(prefix+lines0[0])
                            lines.extend(' '*len(prefix)+l for l in lines0[1:])
                    value = '\n'.join(lines)
                elif name == 'dl':
                    value = '\n'.join('- '+text(x) for x in child.find_all(['dt','dd'],recursive=False))
                elif name == 'blockquote':
                    value = '\n'.join('> '+l for l in walk(child).splitlines())
                elif name == 'pre':
                    code = child.get_text()
                    fence = '`'*max(3, 1+max([len(x) for x in re.findall(r'`+',code)] or [0]))
                    value = fence+'\n'+code+'\n'+fence
                elif name == 'hr':
                    value = ''
                else:
                    value = walk(child)
                if value.strip():
                    parts.append(value.strip())
            elif name == 'br':
                pending.append('; ')
            elif child.find(['div','p','h2','h3','ul','table']):
                flush()
                parts.append(walk(child))
            else:
                pending.append(inline(child))
        flush()
        return '\n\n'.join(p for p in parts if p)
    return walk(root)

def parse_article(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    root = soup.select_one('.topic-content .reading-channel')
    if root is None:
        root = soup.select_one('.topic-content')
    if root is None:
        raise ValueError('Britannica article container missing. Save the loaded article HTML and use --html.')
    heading = root.find('h1') or soup.find('h1')
    if not heading or text(heading) != 'Pittsburgh':
        raise ValueError('Unexpected or missing Pittsburgh article title')
    canonical = soup.select_one('link[rel=canonical]')
    canonical_url = urljoin(URL, canonical['href']) if canonical and canonical.get('href') else URL
    updated = soup.select_one('meta[name="last-modified"]')
    # Work only inside editorial sections containing topic-paragraph elements.
    # Q&A/news cards outside these sections are not article paragraphs.
    sections = [s for s in root.select('section') if s.select_one('p.topic-paragraph')]
    sections = [s for s in sections if not any(parent in sections for parent in s.parents)]
    if not sections:
        raise ValueError('No verified editorial sections; refusing to save widget text as article body')
    original_paragraphs = [text(p) for s in sections for p in s.select('p.topic-paragraph')]
    clean = BeautifulSoup('<div></div>', 'html.parser').div
    title = soup.new_tag('h1'); title.string = 'Pittsburgh'; clean.append(title)
    for section in sections:
        # Keep editorial elements in DOM order; discard injected modules by class.
        for module in list(section.select(
            'script,style,noscript,figure,.assemblies,.module-spacing,.marker,'
            '.link-module,.qa-quiz-module,.ai-qna-module,.ai-top-questions-module,'
            '[class*="newsletter"],[class*="hermes-cta"],.share-tools,nav,footer,form'
        )):
            if module.parent is not None:module.decompose()
        for element in list(section.find_all(['h2','h3','h4','h5','h6','p','ul','ol','dl','table','blockquote','pre'])):
            if element.parent is None:continue
            if element.find_parent(['ul','ol','dl','table','blockquote','pre']):continue
            if element.name=='p' and 'topic-paragraph' not in element.get('class',[]):continue
            clean.append(element.extract())
    content = to_markdown(clean)
    paragraphs = clean.select('p.topic-paragraph')
    # Guard against overbroad widget removal accidentally deleting article text.
    if [text(p) for p in paragraphs] != original_paragraphs:
        raise ValueError('Editorial paragraph preservation check failed; inspect HTML selectors')
    headings = [text(h) for h in clean.find_all(['h2','h3','h4','h5','h6'])]
    if len(content)<1500 or not {'History','The contemporary city'}.issubset(headings):
        raise ValueError('Incomplete article: expected History and The contemporary city. Save fully loaded HTML.')
    hidden = any(p.has_attr('hidden') or 'display:none' in p.get('style','').replace(' ','') for p in paragraphs)
    extraction = {'article_paragraphs':len(paragraphs),'headings':headings,
                  'last_modified':updated.get('content') if updated else None,
                  'quality_flags':['hidden_article_paragraphs_check_browser'] if hidden else [],
                  'scope':'Editorial body present in supplied HTML; no child URLs, AI Q&A, news or image captions.',
                  'completeness':'Expected sections and paragraph preservation verified; not proof of full live article.'}
    return 'Pittsburgh', content, canonical_url, None, extraction


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
        raise ValueError("Expected exactly one Britannica Pittsburgh URL in source.csv")
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
            "User-Agent": "ANLP-HW2-Research/1.0 (single-page Britannica collector)"})
        if response.status_code in {403, 429}:
            raise SystemExit(f"HTTP {response.status_code}: automatic collection is unavailable. "
                             "Use --html with a page saved from your browser; no document was written.")
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("Expected HTML response")
        raw = response.content
        fetched_at, final_url, method = now, response.url, "http"
    title, content, canonical, revision, extraction = parse_article(raw)
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
                "sources": [provenance], "quality_flags": extraction["quality_flags"],
                "extraction": extraction,
                "markdown_path": str((out / "article.md").resolve())}
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw.html").write_bytes(raw)
    (out / "article.md").write_text(content + "\n", encoding="utf-8")
    (out / "documents.jsonl").write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "documents": 1, "characters": len(content),
                      "format": "markdown-only", "extraction": extraction}, indent=2))


if __name__ == "__main__":
    main()
