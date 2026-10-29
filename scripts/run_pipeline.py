"""Batch orchestration, progress, hard per-job timeout and shared corpus output."""
import argparse
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from document_pipeline import VERSION, content_hash, normalize_url, utc_now

ROOT = Path(__file__).resolve().parents[1]
MANUAL_COLUMNS = ["source_id", "category", "url", "reason", "local_path", "saved_at"]


def json_write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def jsonl_write(path, rows):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(path)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "url" not in [x.strip().lower() for x in reader.fieldnames]:
            raise ValueError("Expected CSV with url column; export Numbers as CSV first")
        return [{k.strip().lower().replace(" ", "_"): (v or "").strip() for k, v in row.items() if k}
                for row in reader]


def supervise(job, timeout, progress_file, number, total):
    folder = Path(job["job_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    job_path = folder / "job.json"
    json_write(job_path, job)
    start = last_print = stage_start = time.monotonic()
    current = {"stage": "starting", "detail": ""}
    stderr = (folder / "worker_stderr.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("process_worker.py")), str(job_path)],
                               stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""

    def emit(event):
        elapsed = time.monotonic() - start
        record = {"timestamp": utc_now(), "event": event, "url": job["url"], "url_number": number,
                  "url_total": total, **current, "elapsed_seconds": round(elapsed, 1),
                  "stage_elapsed_seconds": round(time.monotonic() - stage_start, 1)}
        progress_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        progress_file.flush()
        filled = int(20 * (number - 1) / max(total, 1))
        print(f"[{'#'*filled}{'-'*(20-filled)}] URL {number}/{total} | {current['stage']} | {elapsed:.0f}s | {job['url']} | {current['detail']}", flush=True)

    def stop():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    emit("start")
    try:
        while True:
            if time.monotonic() - start > timeout:
                stop()
                current = {"stage": "timeout", "detail": f"Total per-URL limit {timeout:g}s exceeded"}
                emit("timeout")
                return {"status": "needs_manual", "reason": f"total_timeout_{timeout:g}s", "source_url": job["url"], "documents": []}
            for key, _ in selector.select(timeout=0.5):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        update = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if update.get("stage") != current.get("stage"):
                        stage_start = time.monotonic()
                    current = update
                    emit("stage")
                    last_print = time.monotonic()
            if time.monotonic() - last_print >= 5:
                emit("heartbeat")
                last_print = time.monotonic()
            if process.poll() is not None and not selector.get_map():
                break
        result_path = folder / "result.json"
        if result_path.exists() and process.returncode == 0:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            result = {"status": "needs_manual", "reason": f"worker_exit_{process.returncode}; inspect {folder / 'worker_stderr.log'}", "source_url": job["url"], "documents": []}
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        return result
    except BaseException:
        stop()
        current = {"stage": "interrupted", "detail": "Previously completed URLs are checkpointed"}
        emit("interrupted")
        raise
    finally:
        selector.close()
        process.stdout.close()
        stderr.close()


def run(mode):
    parser = argparse.ArgumentParser(description=f"{mode}: loading → semantic parsing → cleaning → exact deduplication")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="New run directory (must not already exist)")
    parser.add_argument("--url-timeout", type=float, default=240, help="Hard total deadline per URL/file in seconds")
    parser.add_argument("--limit", type=int)
    if mode == "auto":
        parser.add_argument("--sources", type=Path)
        parser.add_argument("--delay", type=float, default=2)
        parser.add_argument("--no-browser", action="store_true")
        parser.add_argument("--max-download-mb", type=int, default=80)
    else:
        parser.add_argument("--manifest", type=Path, required=True)
        parser.add_argument("--base-run", type=Path, help="Defaults to the manifest directory; merge its accepted corpus")
    args = parser.parse_args()
    if args.url_timeout <= 0 or args.limit is not None and args.limit < 1:
        parser.error("Timeout and limit must be positive")
    if mode == "auto" and (args.delay < 0 or args.max_download_mb <= 0):
        parser.error("Delay must be nonnegative and download limit positive")
    root = args.root.resolve()
    input_path = (args.sources or root / "config/source.csv") if mode == "auto" else args.manifest
    input_path = input_path.resolve()
    try:
        rows = read_csv(input_path)
        for row in rows:
            row["normalized_url"] = normalize_url(row["url"])
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.limit:
        rows = rows[:args.limit]
    out = (args.output or root / "data/processed" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + mode)).resolve()
    out.mkdir(parents=True, exist_ok=False)
    for directory in ["raw", "parsed", "markdown", "cleaned"]:
        (out / directory).mkdir()
    (out / "input_snapshot.csv").write_bytes(input_path.read_bytes())
    docs, jobs, duplicates, manual, discovery = [], [], [], [], []
    base = None
    if mode == "manual":
        base = (args.base_run or input_path.parent).resolve()
        base_docs = base / "documents.jsonl"
        if base_docs.exists():
            docs = [json.loads(line) for line in base_docs.read_text(encoding="utf-8").splitlines() if line.strip()]
            if any(d.get("pipeline_version") != VERSION for d in docs):
                parser.error("Base corpus uses a different pipeline version; use a v3 auto/manual run")
    by_hash = {d["content_hash"]: d for d in docs}
    versions = {}
    for name in ["requests", "beautifulsoup4", "pypdf", "playwright"]:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    config = {"version": VERSION, "mode": mode, "input": str(input_path), "base_run": str(base) if base else None,
              "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
              "libraries": versions, "link_expansion": "none; discovered links are an inventory, not downloaded",
              "training_annotations_created": 0, "training_datasets_used": [], "evaluation_answers_used": False}
    json_write(out / "run_config.json", config)

    def checkpoint():
        jsonl_write(out / "documents.jsonl", docs)
        jsonl_write(out / "fetch_log.jsonl", jobs)
        jsonl_write(out / "duplicates.jsonl", duplicates)
        jsonl_write(out / "discovered_links.jsonl", discovery)
        temp = out / "manual_queue.csv.tmp"
        with temp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANUAL_COLUMNS)
            writer.writeheader()
            writer.writerows(manual)
        temp.replace(out / "manual_queue.csv")
        stats = {"input_rows": len(rows), "completed_input_rows": len(jobs), "unique_corpus_documents": len(docs),
                 "manual_queue_urls": len(manual), "duplicate_records": len(duplicates),
                 "successful_jobs": sum(j["status"] == "success" for j in jobs),
                 "manual_required_jobs": sum(j["status"] == "needs_manual" for j in jobs),
                 "run_complete": len(jobs) == len(rows), "corpus_coverage_complete": False,
                 "human_validation": "not performed by this program"}
        json_write(out / "corpus_stats.json", stats)
        (out / "data_creation_notes.md").write_text(
            "# Data creation evidence\n\n"
            "Sources are the user-selected public URLs in input_snapshot.csv. This run does not recursively crawl descendants. "
            "Source category is metadata, not a rule for deleting content. Requests loads raw bytes; optional Playwright renders "
            "incomplete JavaScript pages. Access errors are sent to manual_queue.csv. Manually saved files use the same parser.\n\n"
            "Beautiful Soup preserves heading levels, paragraphs, inline links, nested lists, code indentation, definition lists, "
            "tables and card groups. It traverses div/span/time content rather than selecting a fixed subset of text tags. "
            "Cleaning removes known navigation/template components and repeated cards. pypdf preserves page layout; PDF table "
            "semantics and OCR are not implemented. Raw, parsed and cleaned artifacts are retained.\n\n"
            "URL normalization preserves meaningful query parameters and hash routes. Declared canonical URLs are recorded, "
            "not blindly used to merge pages. Exact structured-content SHA-256 deduplication preserves all source records. "
            "No approximate deduplication is used. Manual imports can merge the base automatic corpus.\n\n"
            "This pipeline creates 0 training annotations and uses no additional training datasets or evaluation answers. "
            "Public webpages are retrieval evidence. These statements describe this pipeline, not other project activities.\n\n"
            "Counts describe actual output, not full source coverage or factual validation:\n\n```json\n" + json.dumps(stats, indent=2) + "\n```\n",
            encoding="utf-8")

    checkpoint()
    print(f"Output: {out}", flush=True)
    seen = {}
    with (out / "progress.jsonl").open("a", encoding="utf-8") as progress:
        for number, row in enumerate(rows, 1):
            url = row["normalized_url"]
            if url in seen:
                duplicates.append({"kind": "url", "url": row["url"], "duplicate_of": seen[url]})
                jobs.append({"url": row["url"], "status": "duplicate_url"})
                checkpoint()
                continue
            seen[url] = row["url"]
            job_dir = out / "raw" / f"{number:04d}"
            job = {**row, "url": url, "mode": mode, "job_dir": str(job_dir),
                   "delay": getattr(args, "delay", 0), "browser": not getattr(args, "no_browser", True),
                   "max_bytes": getattr(args, "max_download_mb", 80) * 1024 * 1024}
            if mode == "manual":
                if not row.get("local_path"):
                    result = {"status": "needs_manual", "reason": "local_path_not_filled", "documents": []}
                else:
                    job["local_path"] = str((input_path.parent / row["local_path"]).resolve())
                    result = supervise(job, args.url_timeout, progress, number, len(rows))
            else:
                result = supervise(job, args.url_timeout, progress, number, len(rows))
            status = result["status"]
            for index, document in enumerate(result.get("documents", []), 1):
                key = f"{number:04d}_{index:04d}"
                json_write(out / "parsed" / f"{key}.json", {"source_url": row["url"], "blocks": document.pop("parsed_blocks", document["blocks"])})
                json_write(out / "cleaned" / f"{key}.json", document)
                (out / "markdown" / f"{key}.md").write_text(document["content_markdown"], encoding="utf-8")
                discovery.extend({**link, "parent_url": result.get("final_url", url)} for link in document.pop("discovered_links", []))
                if document["quality_flags"]:
                    continue
                source = {"source_url": row["url"], "requested_url": url, "final_url": result.get("final_url", url),
                          "declared_canonical_url": document.get("declared_canonical_url"),
                          "acquisition_method": result.get("acquisition_method"), "fetched_at": result.get("fetched_at"),
                          "saved_at": result.get("saved_at"), "imported_at": result.get("imported_at"),
                          "raw_path": result.get("raw_path"), "source_id": row.get("source_id", ""), "category": row.get("category", ""),
                          "page_number": document.get("page_number")}
                hashed = content_hash(document["blocks"])
                if hashed in by_hash:
                    by_hash[hashed]["sources"].append(source)
                    duplicates.append({"kind": "content", "url": row["url"], "duplicate_of": by_hash[hashed]["doc_id"]})
                else:
                    document.update(doc_id=hashed[:24], content_hash=hashed, pipeline_version=VERSION,
                                    sources=[source], category=row.get("category", ""),
                                    markdown_path=str(out / "markdown" / f"{key}.md"))
                    docs.append(document)
                    by_hash[hashed] = document
            jobs.append({k: v for k, v in result.items() if k != "documents"} | {"url": row["url"]})
            if status != "success":
                unresolved_path = row.get("local_path", "")
                if mode == "manual" and unresolved_path:
                    unresolved_path = str((input_path.parent / unresolved_path).resolve())
                manual.append({"source_id": row.get("source_id", ""), "category": row.get("category", ""),
                               "url": row["url"], "reason": result.get("reason", "unknown"),
                               "local_path": unresolved_path, "saved_at": row.get("saved_at", "")})
            checkpoint()
            filled = int(20 * number / max(len(rows), 1))
            print(f"[{'#'*filled}{'-'*(20-filled)}] Completed {number}/{len(rows)} | {status}", flush=True)
    print(f"Documents: {len(docs)}; manual queue: {len(manual)}\nOutput: {out}", flush=True)
