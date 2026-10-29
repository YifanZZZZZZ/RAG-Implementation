"""Collect a public-document retrieval corpus, with reportable provenance.

No models, training annotations, or evaluation answers are used here.
Default: fetch CSV seeds only. --follow enables bounded, rule-based discovery.
--reprocess reads saved fetch logs and raw files without network access.
All runs produce new directories; previous snapshots are never overwritten.
"""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import re
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.1"
AGENT = "ANLP-course-project"
POLICY = {
    "corpus_role": "retrieval_knowledge_resource",
    "seed_selection": "User-curated public URLs in the sources CSV; preserve CSV snapshot.",
    "descendant_selection": "Site scope, page-type rules, and bounded breadth-first traversal.",
    "exclusions": "Login, checkout, search traps, media assets, news/stats feeds; no evaluation answers.",
    "event_scope": "2026-10 onward plus recurring/annual-event background; never infer missing years.",
    "date_handling": "Preserve original dates; flag explicitly older structured event instances for review.",
    "deduplication": "Canonical URL within run; exact normalized text hash within run; retain duplicate provenance.",
    "annotation": {"examples_created_by_this_pipeline": 0, "method": "none"},
    "extra_training_data_used_by_this_pipeline": [],
    "evaluation": "Public QA belongs to development evaluation, not this corpus or prompt examples.",
    "limits": "Heuristic extraction, no OCR or browser rendering; unresolved pages are quarantined.",
    "project_scope_note": "Training declarations describe this collector only, not other project scripts.",
}

# These are allowlisted scopes, not permission to crawl every page of a host.
SITE_HOSTS = {
    "wikipedia": {"en.wikipedia.org"},
    "britannica": {"britannica.com"},
    "government": {"pittsburghpa.gov"},
    "cmu": {"cmu.edu", "events.cmu.edu", "community.cmu.edu", "athletics.cmu.edu"},
    "tourism": {"visitpittsburgh.com"},
    "calendar": {"pittsburgh.events", "downtownpittsburgh.com", "pghcitypaper.com"},
    "performing_arts": {"pittsburghsymphony.org", "pittsburghopera.org", "trustarts.org"},
    "festival": {"picklesburgh.com", "pghtacofest.com", "pittsburghrestaurantweek.com", "littleitalydays.com", "bananasplitfest.com"},
    "sports": {"steelers.com", "mlb.com", "nhl.com"},
    "museum": {"carnegiemuseums.org", "carnegieart.org", "collection.carnegieart.org", "collections.carnegiemuseums.org", "carnegiemnh.org", "warhol.org", "kaminsciencecenter.org", "carnegiesciencecenter.org", "heinzhistorycenter.org", "thefrickpittsburgh.org"},
}
TERMS = {
    "government": r"tax|regulation|budget|history|about|event|finance",
    "cmu": r"about|history|tradition|leadership|fact.sheet|event|calendar|homecoming|carnival|reunion|campus|athletic|schedule",
    "tourism": r"things.to.do|event|festival|restaurant|culinary|dining|museum|sport|history|neighborhood|listing",
    "calendar": r"event|calendar|concert|comedy|theat|venue|schedule|page|month|october|november|december|january|february|march|april|may|june|july|august|september",
    "performing_arts": r"production|performance|concert|event|calendar|season|about|history|staff|team|chorus|audition|musician|biograph|faq|visit|polic|venue",
    "festival": r"about|history|schedule|entertain|vendor|restaurant|menu|event|festival|visit|faq|parking|location|lineup|winter|summer|destination|food|drink|program",
    "sports": r"roster|player|coach|front.office|schedule|history|team.info|hall.of|ballpark|stadium|facts|yearbook",
    "museum": r"collection|artwork|objects?|exhibit|visit|admission|hours|ticketing.faq|faq|history|about|event|calendar|librar|archive|rental",
}


def now():
    return datetime.now(timezone.utc).isoformat()


class Progress:
    """Live stage/elapsed display plus append-only, flushed progress events.

    In follow mode the bar measures the request budget, NOT corpus coverage.
    A heartbeat keeps slow network/parsing/save operations visible.
    """

    def __init__(self, out, total, follow, interval=5.0):
        self.total, self.follow, self.interval = total, follow, interval
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.started = self.url_started = self.stage_started = time.monotonic()
        self.state = {"completed": 0, "processed": 0, "failed": 0, "skipped": 0,
                      "pending": 0, "url": "", "stage": "initializing", "detail": ""}
        self.events = (out / "progress.jsonl").open("a", encoding="utf-8", buffering=1)
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.rendered = False

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop.set()
        self.thread.join()
        if exc_type:
            self.stage("interrupted" if exc_type is KeyboardInterrupt else "fatal_error", str(exc or ""))
        with self.lock:
            self.events.close()

    def _heartbeat(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                self._emit("heartbeat")

    def _emit(self, event):
        tick = time.monotonic()
        data = {**self.state, "event": event, "timestamp": now(),
                "run_elapsed_seconds": round(tick - self.started, 1),
                "url_elapsed_seconds": round(tick - self.url_started, 1),
                "stage_elapsed_seconds": round(tick - self.stage_started, 1),
                "total": self.total, "total_kind": "request_budget" if self.follow else "seed_urls"}
        self.events.write(json.dumps(data, ensure_ascii=False) + "\n")
        done = self.state["completed"]
        filled = min(20, int(20 * done / max(1, self.total)))
        bar = "#" * filled + "-" * (20 - filled)
        label = "budget used" if self.follow else "URLs finished"
        line = (f"[{bar}] {done}/{self.total} {label} | "
                f"OK={self.state['processed']} ERR={self.state['failed']} SKIP={self.state['skipped']} "
                f"queue={self.state['pending']} | {self.state['stage']} "
                f"stage={data['stage_elapsed_seconds']:.0f}s url={data['url_elapsed_seconds']:.0f}s | "
                f"{self.state['url']} {self.state['detail']}")
        if sys.stderr.isatty():
            # Three fixed lines keep the URL and stage visible on narrow terminals.
            # The JSONL log retains full strings when the display truncates them.
            import shutil
            width = shutil.get_terminal_size((140, 24)).columns
            lines = [f"[{bar}] {done}/{self.total} {label} | OK={self.state['processed']} ERR={self.state['failed']} SKIP={self.state['skipped']} queue={self.state['pending']}",
                     f"{self.state['stage']} | stage={data['stage_elapsed_seconds']:.0f}s url={data['url_elapsed_seconds']:.0f}s | {self.state['detail']}",
                     self.state['url']]
            prefix = "\033[3A" if self.rendered else ""
            display = "".join("\r\033[K" + text[:max(1, width - 1)] + "\n" for text in lines)
            print(prefix + display, end="", file=sys.stderr, flush=True)
            self.rendered = True
        else:
            print(line, file=sys.stderr, flush=True)

    def begin(self, url, pending):
        with self.lock:
            self.url_started = time.monotonic()
            self.state.update(url=url, pending=pending)
            self.stage("starting")

    def stage(self, name, detail=""):
        with self.lock:
            self.stage_started = time.monotonic()
            self.state.update(stage=name, detail=detail)
            self._emit("stage")

    def detail(self, text):
        # Heartbeat displays updates without printing a line for every chunk.
        with self.lock:
            self.state["detail"] = text

    def finish(self, status, pending, attempted=True):
        with self.lock:
            self.state["completed"] += int(attempted or not self.follow)
            self.state[{"processed": "processed", "error": "failed"}.get(status, "skipped")] += 1
            self.state["pending"] = pending
            self.stage(status)


def download(session, url, timeout, progress, allow_404=False):
    """Expose connection and byte-transfer stages; close responses on errors."""
    progress.stage("connecting", url)
    with session.get(url, timeout=timeout, stream=True) as response:
        if not (allow_404 and response.status_code == 404):
            response.raise_for_status()
        # iter_content returns decoded bytes; compressed Content-Length is not
        # a valid denominator for them, so don't show a misleading percentage.
        length = response.headers.get("Content-Length", "")
        total = int(length) if length.isdigit() and not response.headers.get("Content-Encoding") else None
        chunks, received = [], 0
        progress.stage("downloading", f"0 bytes; resource={url}")
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            chunks.append(chunk)
            received += len(chunk)
            count = f"{received:,}/{total:,} bytes" if total else f"{received:,} bytes"
            progress.detail(f"{count}; resource={url}")
        content = b"".join(chunks)
        progress.stage("downloaded", f"{received:,} bytes; HTTP {response.status_code}")
        return content, response.url, response.headers.get("Content-Type", "").lower(), response.status_code


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean(text):
    return " ".join(text.replace("\ufeff", "").replace("\xa0", " ").split())


def host(url):
    name = (urlparse(url).hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def canonical(url):
    p = urlparse(urldefrag(url)[0])
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunparse(p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(), query=urlencode(query)))


def site_for(url):
    return next((name for name, domains in SITE_HOSTS.items() if host(url) in domains), "unknown")


def page_type(url, soup=None):
    p = urlparse(url)
    path = p.path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if site_for(url) == "wikipedia":
        return "museum_directory" if "list_of_museums" in path else "encyclopedia"
    if re.search(r"tax.forms|tax-forms|tax-form", path):
        return "tax_directory"
    if re.search(r"/player[s]?/|/players-roster/.+", path):
        return "person"
    if "roster" in path:
        return "roster"
    if "schedule" in path:
        return "schedule"
    if re.search(r"/objects?/|/artworks?/", path):
        return "artwork"
    if re.search(r"/production/|/event/|/events/.+", path):
        return "event_detail"
    if re.search(r"calendar|eventsearch|/events/?$", path) or host(url) == "events.cmu.edu":
        return "event_directory"
    if re.search(r"faq|polic|audition|admission|visit", path):
        return "practical_information"
    return "landing" if path in {"", "/", "/Home", "/home"} else "article"


def link_decision(url, label, parent, group, typ):
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        return False, "unsupported_scheme"
    if re.search(r"\.(jpg|jpeg|png|gif|svg|webp|mp4|mp3|zip|css|js)$", p.path, re.I):
        return False, "media_asset"
    if re.search(r"/(login|logout|cart|checkout|donate|shop|search|news|video|photos|stats|scores)(/|$)", p.path, re.I):
        return False, "functional_or_excluded_feed"
    if any(k.lower() in {"s", "search", "sort", "sortby", "replytocom"} for k, _ in parse_qsl(p.query)):
        return False, "search_or_sort_variant"
    if group == "wikipedia":
        if typ == "museum_directory" and host(url) == "en.wikipedia.org" and p.path.startswith("/wiki/") and ":" not in p.path:
            return True, "museum_directory_article_link"
        return False, "wikipedia_seed_only"
    # Museum portals legitimately link between their official sites.
    allowed = {host(parent)} | (SITE_HOSTS["museum"] if group == "museum" else set())
    if group == "cmu":
        allowed |= SITE_HOSTS["cmu"]
    if host(url) not in allowed:
        return False, "outside_site_scope"
    if host(url) == "mlb.com" and not p.path.startswith(("/pirates", "/player/")):
        return False, "other_mlb_team"
    if host(url) == "nhl.com" and not p.path.startswith(("/penguins", "/player/")):
        return False, "other_nhl_team"
    if group not in TERMS:
        return False, "no_discovery_rule"
    if re.search(TERMS[group], p.path + " " + label, re.I):
        return True, "relevant_path_or_label"
    return False, "no_topic_match"


def structured_events(soup):
    events = []

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(str(t).endswith("Event") for t in types):
                events.append({k: value[k] for k in (
                    "name", "startDate", "endDate", "location", "performer", "organizer",
                    "eventStatus", "eventSchedule", "description", "url"
                ) if k in value})
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            pass
    return events


def table_blocks(table):
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cells = tr.find_all(["th", "td"], recursive=False)
        rows.append((cells, [clean(c.get_text(" ", strip=True)) for c in cells]))
    headers = None
    output = []
    for cells, values in rows:
        if not any(values):
            continue
        if cells and all(c.name == "th" for c in cells):
            # A spanning infobox title is not a column header for every row.
            headers = values if len(cells) > 1 and not any(c.has_attr("colspan") for c in cells) else None
            output.append(("Columns: " if headers else "") + " | ".join(values))
        elif headers and len(headers) == len(values):
            output.append(" | ".join(f"{k}: {v}" for k, v in zip(headers, values)))
        elif len(cells) == 2 and cells[0].name == "th":
            output.append(f"{values[0]}: {values[1]}")
        else:
            output.append(" | ".join(values))
    return "\n".join(output)


def extract_html(content, url):
    soup = BeautifulSoup(content, "html.parser")
    title = clean(soup.title.get_text(" ", strip=True)) if soup.title else ""
    group, typ = site_for(url), page_type(url)
    events = structured_events(soup)  # Before scripts are removed.
    flags = []
    # Use semantic containers first; unknown layouts are never silently accepted.
    selectors = (["#mw-content-text .mw-parser-output"] if group == "wikipedia" else
                 ["main", "[role=main]", "article", "#main-content", "#mainContent", ".entry-content"])
    root, selector = None, None
    for candidate in selectors:
        root = soup.select_one(candidate)
        if root is not None:
            selector = candidate
            break
    if root is None:
        root = soup.body or soup
        selector = "body_fallback"
        flags.append("unverified_content_container")

    links, seen = [], set()
    # Menus can help discover sections but must never become the body text.
    discovery_root = root if group == "wikipedia" else soup
    regulation_urls = set()
    if typ == "tax_directory":
        for table in soup.find_all("table"):
            index = None
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"], recursive=False)
                for i, c in enumerate(cells):
                    if "regulations" in c.get_text(" ", strip=True).lower():
                        index = i
                if index is not None and index < len(cells):
                    for a in cells[index].find_all("a", href=True):
                        regulation_urls.add(canonical(urljoin(url, a["href"])))
        if not regulation_urls:
            flags.append("regulations_column_not_found")

    for a in discovery_root.find_all("a", href=True):
        target = canonical(urljoin(url, a["href"]))
        if target == canonical(url) or target in seen:
            continue
        seen.add(target)
        label = clean(a.get_text(" ", strip=True))
        accepted, reason = link_decision(target, label, url, group, typ)
        if typ == "tax_directory":
            accepted = target in regulation_urls and host(target) == host(url)
            reason = "regulations_column" if accepted else "not_regulations_column"
        links.append({"url": target, "parent_url": url, "link_text": label,
                      "accepted": accepted, "reason": reason})

    # Capture institutional social handles without crawling social platforms.
    social = sorted({a["href"] for a in soup.find_all("a", href=True)
                     if host(urljoin(url, a["href"])) in {"instagram.com", "facebook.com", "twitter.com", "x.com"}})
    for el in root.select("script,style,noscript,nav,form,.mw-editsection,sup.reference,.reflist,.mw-references-wrap,.navbox,.hatnote,.toc"):
        el.decompose()
    if root.select("[rowspan], [colspan]"):
        flags.append("complex_table_needs_review")
    # Preserve structured rows and labeled facts in addition to retrieval text.
    # This handles roster, schedule, infobox and museum definition-list layouts
    # without guessing facts from names, dates or numeric strings.
    tables = [table_blocks(t) for t in root.find_all("table") if t.find_parent("table") is None]
    facts = []
    for dl in root.find_all("dl"):
        label = None
        for child in dl.find_all(["dt", "dd"], recursive=False):
            if child.name == "dt":
                label = clean(child.get_text(" ", strip=True))
            elif label:
                facts.append({"label": label, "value": clean(child.get_text(" ", strip=True))})
    blocks, sections, current = [], [], ""
    for el in root.select("h1,h2,h3,h4,h5,p,li,table,dl"):
        if el.find_parent(["p", "li", "table", "dl"]) is not None:
            continue
        if el.name == "table":
            text = table_blocks(el)
        elif el.name == "dl":
            text = "\n".join(clean(x.get_text(" ", strip=True)) for x in el.find_all(["dt", "dd"], recursive=False))
        else:
            text = clean(el.get_text(" ", strip=True))
        if not text or "lorem ipsum" in text.lower():
            continue
        if el.name.startswith("h"):
            current = text
        blocks.append(text)
        sections.append({"heading": current, "text": text, "kind": el.name})
    if not blocks:
        flags.append("no_semantic_blocks")
    if typ == "roster" and not tables:
        flags.append("roster_rows_unconfirmed")
    if typ == "schedule" and not tables and not events:
        flags.append("schedule_records_unconfirmed")
    if typ == "artwork" and not facts and not tables:
        flags.append("artwork_fields_unconfirmed")
    text = "\n\n".join(blocks)
    # Structured data supplements, rather than replaces, visible source text.
    if events:
        text += "\n\nStructured events:\n" + json.dumps(events, ensure_ascii=False)
    if social:
        text += "\n\nOfficial page social links:\n" + "\n".join(social)
    if re.search(r"just a moment|access denied|verify you are human", title, re.I):
        flags.append("access_challenge")
    if typ == "event_directory" and not events and not any(
        re.search(r"\b20\d{2}\b", b) and re.search(r"\b\d{1,2}:\d{2}\b|\bam\b|\bpm\b", b, re.I) for b in blocks
    ):
        flags.append("calendar_records_unconfirmed")
    if any(re.match(r"^\d{4}-\d{2}", str(e.get("startDate", ""))) and str(e["startDate"])[:7] < "2026-10" for e in events):
        flags.append("contains_pre_scope_event_instances")
    return {"title": title, "text": text.strip(), "site": group, "page_type": typ,
            "extractor": f"{group}/{typ}", "content_selector": selector,
            "sections": sections, "tables": tables, "labeled_facts": facts,
            "structured_events": events, "quality_flags": flags}, links


def write_jsonl(path, records):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read_sources(path):
    if not path.is_file():
        raise ValueError(f"CSV not found: {path}. Export Numbers as CSV, then use --sources PATH.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "url" not in [k.strip().lower() for k in reader.fieldnames]:
            raise ValueError("Sources must be CSV with source_id, category, url columns; not a Numbers file.")
        rows = [{k.strip().lower().replace(" ", "_"): (v or "").strip()
                 for k, v in r.items() if k} for r in reader]
    for row in rows:
        if urlparse(row.get("url", "")).scheme not in {"http", "https"}:
            raise ValueError(f"Invalid public source URL: {row.get('url')}")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-per-host", type=int, default=20)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--progress-interval", type=float, default=5.0,
                        help="Seconds between live status heartbeats (default: 5).")
    parser.add_argument("--reprocess", type=Path, help="Existing fetch_log.jsonl; no network.")
    args = parser.parse_args()
    if min(args.max_pages, args.max_per_host) < 1 or args.max_depth < 0 or args.delay < 0 or args.progress_interval <= 0 or (args.limit is not None and args.limit < 1):
        parser.error("Invalid limits; page limits must be positive, depth/delay nonnegative.")
    root = args.root.resolve()
    sources = args.sources or root / "config/source.csv"
    old_records = {}
    try:
        if args.reprocess:
            rows = []
            for line in args.reprocess.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("raw_path"):
                    rows.append({"url": record["url"], "saved": record})
            old_docs = args.reprocess.parent / "documents.jsonl"
            if old_docs.exists():
                for line in old_docs.read_text(encoding="utf-8").splitlines():
                    d = json.loads(line)
                    old_records.setdefault(d["source_url"], d)
        else:
            rows = read_sources(sources)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.limit:
        rows = rows[:args.limit]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    raw_dir, out = root / "data/raw" / run_id, root / "data/processed" / run_id
    raw_dir.mkdir(parents=True)
    out.mkdir(parents=True)
    if not args.reprocess:
        (out / "sources_snapshot.csv").write_bytes(sources.read_bytes())
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    versions = {n: importlib.metadata.version(n) for n in ["requests", "beautifulsoup4", "pypdf"]}
    (out / "run_config.json").write_text(json.dumps({"version": VERSION, "policy": POLICY, "arguments": config, "libraries": versions}, indent=2), encoding="utf-8")

    documents, review, links, logs, duplicates = [], [], [], [], []
    hashes, seen, counts = {}, set(), Counter()
    queue = deque({**r, "depth": 0, "selection_reason": "explicit_seed"} for r in rows)
    session = requests.Session()
    session.headers["User-Agent"] = AGENT + "/" + VERSION
    robots = {}
    attempted = 0

    def save():
        for name, data in [("documents", documents), ("review_documents", review), ("discovered_links", links), ("fetch_log", logs), ("duplicates", duplicates)]:
            write_jsonl(out / f"{name}.jsonl", data)
        stats = {"seed_count": len(rows), "pages_attempted": attempted, "ready_documents": len(documents),
                 "review_documents": len(review), "duplicates": len(duplicates),
                 "fetch_status": dict(Counter(x["status"] for x in logs)),
                 "ready_by_category": dict(Counter(d["category"] for d in documents)),
                 "quality_flags": dict(Counter(f for d in documents + review for f in d["quality_flags"])),
                 "link_decisions": dict(Counter(l["reason"] for l in links)),
                 "pending_queue": len(queue), "coverage_complete": False,
                 "manually_reviewed_by_collector": 0}
        (out / "corpus_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        (out / "data_creation_notes.md").write_text(
            "# Data creation evidence for this run\n\n"
            "## Knowledge resource and inclusion\n"
            "Public seed URLs were selected by the user. Descendants were selected with site and page-type heuristics, "
            "bounded by the recorded arguments. URL decisions are in discovered_links.jsonl; this is not exhaustive coverage.\n\n"
            "## Extraction\n"
            "Requests downloaded original bytes. Beautiful Soup extracted article blocks, labeled table rows and JSON-LD events. "
            "Wikipedia uses its article container; other sites use semantic-container fallbacks requiring review when unrecognized. "
            "pypdf extracted PDF pages in layout mode. No OCR, browser rendering, or model-based rewriting was used. "
            "Raw files, timestamps, selectors, extraction version and exact-text hashes are retained. "
            "documents.jsonl excludes unresolved pages; review_documents.jsonl retains them. Automated acceptance is not human validation.\n\n"
            "## Training annotations\n"
            "This collector created 0 annotated training examples. Structured fields are extracted source facts, not QA labels.\n\n"
            "## Additional training data\n"
            "This collector used no extra training datasets. Public webpages are retrieval evidence, not training examples. "
            "Evaluation questions and answers are not read by this script. Document any training or development-set-guided "
            "source selection elsewhere in the project separately.\n\n"
            "## Observed counts\n```json\n" + json.dumps(stats, indent=2) + "\n```\n",
            encoding="utf-8")

    total = args.max_pages if args.follow and not args.reprocess else len(
        {canonical(row["url"]) for row in rows})
    print(f"Outputs: {out}", flush=True)
    print("Progress log: " + str(out / "progress.jsonl"), flush=True)
    with Progress(out, total, args.follow and not args.reprocess, args.progress_interval) as progress:
        while queue and attempted < args.max_pages:
            item = queue.popleft()
            url = canonical(item["url"])
            if url in seen:
                continue
            seen.add(url)
            progress.begin(url, len(queue))
            log = {"url": url, "depth": item["depth"], "selection_reason": item["selection_reason"], "parent_url": item.get("parent_url")}
            if counts[host(url)] >= args.max_per_host:
                logs.append({**log, "status": "host_limit"})
                progress.finish("host_limit", len(queue), attempted=False)
                continue
            counts[host(url)] += 1
            attempted += 1
            url_started = time.monotonic()
            try:
                key = digest(url)[:20]
                if args.reprocess:
                    prior = item["saved"]
                    raw_path = root / prior["raw_path"]
                    progress.stage("reading_saved_file", str(raw_path))
                    content = raw_path.read_bytes()
                    final_url = prior.get("final_url", url)
                    fetched_at = prior.get("fetched_at")
                    content_type = prior.get("content_type", "")
                    item = {**old_records.get(url, {}), **item}
                    log["http_status"] = prior.get("http_status")
                else:
                    origin = urlunparse(urlparse(url)._replace(path="", params="", query="", fragment=""))
                    if origin not in robots:
                        progress.stage("checking_robots", origin + "/robots.txt")
                        robot_content, _, _, robot_status = download(
                            session, origin + "/robots.txt", (10, 30), progress, allow_404=True)
                        rp = RobotFileParser()
                        if robot_status == 404:
                            rp.parse([])
                        else:
                            rp.parse(robot_content.decode("utf-8-sig", errors="replace").splitlines())
                        robots[origin] = rp
                        progress.stage("rate_limit_wait", f"{args.delay:g}s after robots.txt")
                        time.sleep(args.delay)
                    if not robots[origin].can_fetch(AGENT, url):
                        raise ValueError("robots.txt disallows this URL")
                    wait_seconds = max(args.delay, robots[origin].crawl_delay(AGENT) or 0)
                    progress.stage("rate_limit_wait", f"{wait_seconds:g}s before page request")
                    time.sleep(wait_seconds)
                    content, final_url, content_type, status = download(session, url, (10, 60), progress)
                    log["http_status"] = status
                    fetched_at = now()
                    extension = "pdf" if content.startswith(b"%PDF-") else "html" if "html" in content_type else "txt" if "text/plain" in content_type else "bin"
                    raw_path = raw_dir / f"{key}.{extension}"
                    progress.stage("saving_raw", f"{len(content):,} bytes")
                    raw_path.write_bytes(content)
                log.update({"raw_path": str(raw_path.relative_to(root)), "final_url": final_url, "fetched_at": fetched_at, "content_type": content_type})
                common = {"source_id": item.get("source_id", key), "source_url": url, "final_url": final_url,
                          "category": item.get("category", "unknown"), "fetched_at": fetched_at,
                          "processed_at": now(), "raw_path": log["raw_path"], "extractor_version": VERSION,
                          "selection_reason": item["selection_reason"], "parent_url": item.get("parent_url"),
                          "corpus_role": "retrieval_knowledge_resource"}
                records = []
                if content.startswith(b"%PDF-"):
                    progress.stage("opening_pdf")
                    reader = PdfReader(raw_path)
                    total_pages = len(reader.pages)
                    for n, page in enumerate(reader.pages, 1):
                        progress.stage("extracting_pdf", f"page {n}/{total_pages}")
                        text = page.extract_text(extraction_mode="layout") or ""
                        records.append({**common, "doc_id": f"{key}_p{n}", "title": item.get("source_id", ""),
                                        "source_type": "pdf", "page_type": "pdf_page", "page_number": n,
                                        "extractor": "pypdf_layout", "text": text, "quality_flags": ["pdf_layout_needs_review"]})
                elif "html" in content_type or raw_path.suffix == ".html":
                    progress.stage("extracting_html", f"{len(content):,} bytes")
                    result, candidates = extract_html(content, final_url)
                    records.append({**common, **result, "doc_id": key, "source_type": "html"})
                    progress.stage("filtering_links", f"{len(candidates)} candidates")
                    for link in candidates:
                        link["category"] = common["category"]
                        link["scheduled"] = bool(link["accepted"] and args.follow and not args.reprocess and item["depth"] < args.max_depth)
                        links.append(link)
                        if link["scheduled"]:
                            queue.append({"url": link["url"], "category": common["category"], "source_id": common["source_id"],
                                          "depth": item["depth"] + 1, "parent_url": final_url, "selection_reason": link["reason"]})
                elif "text/plain" in content_type or raw_path.suffix == ".txt":
                    progress.stage("extracting_text")
                    records.append({**common, "doc_id": key, "title": item.get("source_id", ""), "source_type": "txt",
                                    "text": content.decode("utf-8-sig"), "extractor": "plain_text", "quality_flags": []})
                else:
                    raise ValueError(f"Unsupported content type: {content_type}")

                progress.stage("validating", f"{len(records)} records")
                for record in records:
                    if not record["text"].strip():
                        record["quality_flags"].append("empty_text")
                    record["text_sha256"] = digest(clean(record["text"]))
                    record["character_count"] = len(record["text"])
                    if record["text_sha256"] in hashes and record["text"].strip():
                        duplicates.append({"doc_id": record["doc_id"], "source_url": url, "duplicate_of": hashes[record["text_sha256"]]})
                        continue
                    hashes[record["text_sha256"]] = record["doc_id"]
                    blocking = {"empty_text", "unverified_content_container", "no_semantic_blocks", "access_challenge", "calendar_records_unconfirmed", "regulations_column_not_found", "complex_table_needs_review", "pdf_layout_needs_review", "contains_pre_scope_event_instances", "roster_rows_unconfirmed", "schedule_records_unconfirmed", "artwork_fields_unconfirmed"}
                    record["quality_status"] = "needs_review" if blocking.intersection(record["quality_flags"]) else "automated_checks_passed"
                    (review if record["quality_status"] == "needs_review" else documents).append(record)
                log.update(status="processed", extracted_records=len(records))
            except Exception as exc:
                log.update(status="error", error=f"{type(exc).__name__}: {exc}")
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    log["http_status"] = exc.response.status_code
                progress.stage("error", log["error"])
            log["processing_elapsed_seconds"] = round(time.monotonic() - url_started, 2)
            logs.append(log)
            progress.stage("saving_checkpoint", f"ready={len(documents)} review={len(review)}")
            save()
            progress.finish(log["status"], len(queue))
        progress.stage("saving_final_outputs")
        save()
        progress.stage("finished", f"ready={len(documents)} review={len(review)}; pending={len(queue)}")
    print(f"Ready: {len(documents)}; needs review: {len(review)}; outputs: {out}")


if __name__ == "__main__":
    main()
