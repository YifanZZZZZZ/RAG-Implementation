"""Shared semantic parsing, cleaning and content identity. No QA/model input."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag, Comment
from pypdf import PdfReader

VERSION = "3.0"
BLOCK_TAGS = set("address article aside blockquote dd details div dl dt figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main ol p pre section table ul".split())


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_url(url):
    p = urlsplit(url.strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Expected a public HTTP(S) URL without embedded credentials")
    hostname = p.hostname.lower()
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    port = p.port
    netloc = hostname + (f":{port}" if port and (p.scheme.lower(), port) not in {("http", 80), ("https", 443)} else "")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}]
    # Preserve hash-router fragments; ordinary in-page anchors are not pages.
    fragment = p.fragment if p.fragment.startswith(("/", "!")) else ""
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", urlencode(query), fragment))


def whitespace(value):
    return " ".join(html.unescape(str(value)).replace("\ufeff", "").split())


def content_hash(blocks):
    # Code indentation, table boundaries, headings and list nesting remain intact.
    serialized = json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def inline(node, base):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    if node.name == "br":
        return "\n"
    if node.name == "img":
        return ""  # Decorative images aren't evidence; meaningful captions are parsed.
    text = "".join(inline(c, base) for c in node.children)
    if node.name == "a" and node.get("href"):
        target = urljoin(base, node["href"])
        if urlsplit(target).scheme in {"https", "http", "mailto", "tel"} and whitespace(text):
            return f"[{whitespace(text)}](<{target}>)"
    if node.name in {"strong", "b"} and text.strip():
        return f"**{text}**"
    if node.name in {"em", "i"} and text.strip():
        return f"*{text}*"
    if node.name == "code":
        fence = "`" * (max([len(x) for x in re.findall(r"`+", text)] or [0]) + 1)
        return fence + text + fence
    if node.name == "sup":
        return "^" + text
    return text


def table_block(table, base):
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = []
        for cell in tr.find_all(["th", "td"], recursive=False):
            def span(key):
                try:
                    return max(1, min(1000, int(cell.get(key, 1))))
                except ValueError:
                    return 1
            cells.append({"text": whitespace(inline(cell, base)), "header": cell.name == "th",
                          "rowspan": span("rowspan"), "colspan": span("colspan")})
        if cells:
            rows.append(cells)
    caption = table.find("caption", recursive=False)
    return {"type": "table", "caption": whitespace(caption.get_text()) if caption else "", "rows": rows}


def dom_blocks(node, base):
    result, pending = [], []

    def flush():
        text = whitespace("".join(pending))
        pending.clear()
        if text:
            result.append({"type": "paragraph", "text": text})

    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            pending.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        # A linked card may contain full headings, paragraphs and lists.
        # Preserve those blocks instead of flattening the entire anchor label.
        if name == "a" and child.find(list(BLOCK_TAGS)):
            flush()
            children = dom_blocks(child, base)
            target = urljoin(base, child.get("href", ""))
            if child.get("href") and urlsplit(target).scheme in {"https", "http"}:
                children.append({"type": "paragraph", "text": f"[Source](<{target}>)"})
            if children:
                result.append({"type": "group", "children": children})
            continue
        if name not in BLOCK_TAGS:
            pending.append(inline(child, base))
            continue
        flush()
        if re.fullmatch("h[1-6]", name):
            result.append({"type": "heading", "level": int(name[1]), "text": whitespace(inline(child, base))})
        elif name == "pre":
            code = child.find("code")
            classes = (code or child).get("class", [])
            lang = next((c[len("language-"):] for c in classes if c.startswith("language-")), "")
            result.append({"type": "code", "language": lang,
                           "text": child.get_text().replace("\r\n", "\n").replace("\r", "\n")})
        elif name == "table":
            result.append(table_block(child, base))
        elif name in {"ul", "ol"}:
            result.append({"type": "list", "ordered": name == "ol", "start": child.get("start", "1"),
                           "items": [dom_blocks(li, base) for li in child.find_all("li", recursive=False)]})
        elif name == "dl":
            entries, label = [], ""
            for item in child.find_all(["dt", "dd"], recursive=False):
                if item.name == "dt":
                    label = whitespace(inline(item, base))
                else:
                    entries.append({"label": label, "value": whitespace(inline(item, base))})
            result.append({"type": "facts", "entries": entries})
        elif name == "blockquote":
            result.append({"type": "quote", "children": dom_blocks(child, base)})
        elif name in {"p", "address", "figcaption"}:
            result.append({"type": "paragraph", "text": whitespace(inline(child, base))})
        else:
            # Group cards rather than selecting only p/h tags: dates in div/span/time survive.
            tokens = set(child.get("class", []))
            children = dom_blocks(child, base)
            if tokens.intersection({"event", "event-element", "event-card", "card"}) and children:
                result.append({"type": "group", "children": children})
            else:
                result.extend(children)
    flush()
    return result


def clean_blocks(blocks):
    result, removed, seen = [], [], set()
    for original in blocks:
        block = json.loads(json.dumps(original))
        if "children" in block:
            block["children"], sub = clean_blocks(block["children"])
            removed.extend(sub)
        if block["type"] == "list":
            items = []
            for item in block["items"]:
                item, sub = clean_blocks(item)
                removed.extend(sub)
                if item:
                    items.append(item)
            block["items"] = items
        text = block.get("text", "")
        if block["type"] != "code" and ("text" in block and not text or "lorem ipsum" in text.lower()):
            removed.append({"reason": "empty_or_placeholder", "preview": text[:120]})
            continue
        key = content_hash([block])
        # Deduplicate self-contained cards and consecutive repeated paragraphs;
        # do not erase equal short labels under different headings.
        if (block["type"] == "group" and key in seen) or (result and block["type"] == "paragraph" and result[-1] == block):
            removed.append({"reason": "repeated_component", "preview": text[:120]})
            continue
        seen.add(key)
        result.append(block)
    return result, removed


def markdown(blocks):
    out = []
    for b in blocks:
        kind = b["type"]
        if kind == "heading":
            out.append("#" * b["level"] + " " + b["text"])
        elif kind == "paragraph":
            out.append(b["text"])
        elif kind in {"code", "pdf_layout"}:
            fence = "`" * max(3, max([len(x) for x in re.findall(r"`+", b["text"])] or [0]) + 1)
            out.append(fence + b.get("language", "text") + "\n" + b["text"] + "\n" + fence)
        elif kind == "list":
            lines = []
            try:
                start = int(b.get("start", 1))
            except ValueError:
                start = 1
            for i, item in enumerate(b["items"]):
                prefix = f"{start+i}. " if b["ordered"] else "- "
                parts = markdown(item).splitlines() or [""]
                lines.append(prefix + parts[0])
                lines.extend(" " * len(prefix) + line for line in parts[1:])
            out.append("\n".join(lines))
        elif kind == "facts":
            out.append("\n".join(f"- **{x['label']}**: {x['value']}" for x in b["entries"]))
        elif kind == "table":
            rows = b["rows"]
            if not rows:
                continue
            simple = all(c["rowspan"] == c["colspan"] == 1 for r in rows for c in r)
            simple = simple and len({len(r) for r in rows}) == 1 and all(c["header"] for c in rows[0])
            if b["caption"]:
                out.append(b["caption"])
            if simple:
                formatted = ["| " + " | ".join(c["text"].replace("|", "\\|") for c in row) + " |" for row in rows]
                formatted.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
                out.append("\n".join(formatted))
            else:
                # Raw HTML tables are valid within Markdown; preserve spans explicitly.
                rendered = ["<table>"]
                for row in rows:
                    cells = []
                    for c in row:
                        tag = "th" if c["header"] else "td"
                        cells.append(f'<{tag} rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">{html.escape(c["text"])}</{tag}>')
                    rendered.append("<tr>" + "".join(cells) + "</tr>")
                out.append("\n".join(rendered + ["</table>"]))
        elif kind in {"group", "quote"}:
            text = markdown(b["children"])
            out.append("\n".join("> " + line for line in text.splitlines()) if kind == "quote" else text)
    return "\n\n".join(x for x in out if x).strip()


def parse_html(content, base):
    soup = BeautifulSoup(content, "html.parser")
    title = whitespace(soup.title.get_text()) if soup.title else ""
    canonical = soup.find("link", rel=lambda rel: rel and "canonical" in rel)
    declared = urljoin(base, canonical["href"]) if canonical and canonical.get("href") else None
    structured = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            structured.append(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            pass
    links = []
    for a in soup.select("a[href]"):
        target = urljoin(base, a["href"])
        if urlsplit(target).scheme in {"http", "https"}:
            links.append({"url": target, "text": whitespace(a.get_text(" ", strip=True))})
    root, selector = soup.body or soup, "body"
    for candidate in ["#mw-content-text .mw-parser-output", "main", "[role=main]", "article", "#main-content", ".entry-content"]:
        if candidate == "article" and len(soup.select("article")) != 1:
            continue  # A listing's first article is not the whole page.
        found = soup.select_one(candidate)
        if found:
            root, selector = found, candidate
            break
    # Preserve public contact facts from page furniture as separate provenance blocks.
    contacts = []
    for footer in soup.find_all("footer"):
        for item in footer.select("address, p"):
            value = whitespace(inline(item, base))
            if re.search(r"\b(hours|open|closed|address|phone)\b|\b\d{3}[-.]\d{3}[-.]\d{4}\b", value, re.I) or item.name == "address":
                contacts.append({"type": "paragraph", "text": value})
    for a in soup.select('a[href*="instagram.com"], a[href^="mailto:"], a[href^="tel:"]'):
        contacts.append({"type": "paragraph", "text": f"Contact: {inline(a, base) or a['href']}"})
    removals = []
    for item in list(root.select("script,style,noscript,nav,footer,form,[role=navigation],.mw-editsection,sup.reference,.reflist,.mw-references-wrap,.navbox,.toc")):
        if item.parent is not None:
            removals.append({"reason": "page_furniture", "tag": item.name, "preview": item.get_text(" ", strip=True)[:100]})
            item.decompose()
    # Conservative class/id tokens; never remove every modal or arbitrary div.
    for item in list(root.find_all(True)):
        if item.parent is None:
            continue
        tokens = set(item.get("class", [])) | {item.get("id", "")}
        if tokens.intersection({"cookie-banner", "cookie-consent", "advertisement", "adsbygoogle", "sidebar", "login-modal", "newsletter-signup"}):
            removals.append({"reason": "template_component", "preview": item.get_text(" ", strip=True)[:100]})
            item.decompose()
    raw_blocks = dom_blocks(root, base)
    if "mw-parser-output" in selector:
        # References stay in the raw snapshot; exclude trailing bibliographic sections.
        for i, b in enumerate(raw_blocks):
            if b["type"] == "heading" and b["text"].lower() in {"references", "bibliography", "external links", "further reading", "explanatory notes"}:
                removals.append({"reason": "bibliographic_tail", "blocks_removed": len(raw_blocks)-i})
                raw_blocks = raw_blocks[:i]
                break
    if not any(b["type"] == "heading" and b["level"] == 1 for b in raw_blocks) and title:
        raw_blocks.insert(0, {"type": "heading", "level": 1, "text": title})
    blocks, cleaning = clean_blocks(raw_blocks)
    # Dedup footer contacts before appending; don't let contact-only pages pass quality checks.
    main_text = markdown(blocks)
    flags = []
    substantive = [b for b in blocks if b["type"] != "heading"]
    if not substantive or len(main_text.strip()) < 80:
        flags.append("insufficient_body")
    if re.search(r"just a moment|access denied|verify.*human|attention required|403 forbidden|robot check", title, re.I):
        flags.append("access_challenge")
    # A calendar landing shell is not an event corpus. Background articles aren't calendars.
    calendar_title = re.search(r"^(events calendar|cmu events calendar|browse events)(\s*[-|].*)?$", title, re.I)
    visible_text = root.get_text(" ", strip=True)
    if calendar_title and not re.search(r"\b\d{1,2}:\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b", visible_text, re.I):
        flags.append("calendar_content_missing")
    if re.search(r"enable javascript|loading\.\.\.", visible_text, re.I) and len(visible_text) < 600:
        flags.append("javascript_shell")
    if contacts:
        unique = {json.dumps(c, sort_keys=True): c for c in contacts}
        blocks += [{"type": "heading", "level": 2, "text": "Contact information from page"}] + list(unique.values())
    warnings = []
    if selector == "body":
        warnings.append("body_container_fallback")
    if any(b["type"] == "table" and any(c["rowspan"] > 1 or c["colspan"] > 1 for row in b["rows"] for c in row) for b in blocks):
        warnings.append("complex_table_preserved_as_html")
    return {"title": title, "blocks": blocks, "parsed_blocks": raw_blocks,
            "content_markdown": markdown(blocks), "declared_canonical_url": declared,
            "structured_data": structured, "discovered_links": links,
            "quality_flags": flags, "quality_notes": warnings,
            "parsing": {"container": selector, "removed": removals + cleaning}}


def parse_file(path, url, kind, stage=lambda *args: None):
    path = Path(path)
    if kind == "html":
        stage("parsing_html")
        return [parse_html(path.read_bytes(), url)]
    if kind == "pdf":
        stage("opening_pdf")
        reader = PdfReader(path)
        result = []
        for i, page in enumerate(reader.pages, 1):
            stage("parsing_pdf", f"page {i}/{len(reader.pages)}")
            text = page.extract_text(extraction_mode="layout") or ""
            # pypdf cannot reconstruct arbitrary table semantics. Preserve layout, label limits.
            blocks = [{"type": "heading", "level": 1, "text": f"PDF page {i}"},
                      {"type": "pdf_layout", "text": text.replace("\r\n", "\n")}]
            result.append({"title": path.stem, "page_number": i, "blocks": blocks, "parsed_blocks": blocks,
                           "content_markdown": markdown(blocks), "quality_flags": [] if text.strip() else ["empty_pdf_page"],
                           "quality_notes": ["pdf_layout_only_not_semantic_table_extraction"],
                           "parsing": {"method": "pypdf_layout", "removed": []}, "discovered_links": []})
        return result
    if kind in {"txt", "md"}:
        text = path.read_text(encoding="utf-8-sig")
        blocks = [{"type": "paragraph", "text": text}] if kind == "txt" else [{"type": "code", "language": "markdown_source", "text": text}]
        return [{"title": path.stem, "blocks": blocks, "parsed_blocks": blocks,
                 "content_markdown": text, "quality_flags": [] if text.strip() else ["empty_text"],
                 "quality_notes": [], "parsing": {"removed": []}, "discovered_links": []}]
    raise ValueError(f"Unsupported input type: {kind}")
