"""One isolated URL/file job; parent enforces a wall-clock deadline."""
import json
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from document_pipeline import parse_file, utc_now


def stage(name, detail=""):
    print(json.dumps({"stage": name, "detail": detail}), flush=True)


def fetch(url, dest, max_bytes, session):
    stage("connecting", url)
    with session.get(url, stream=True, timeout=(10, 30)) as response:
        received, last = 0, time.monotonic()
        total = response.headers.get("Content-Length", "unknown")
        stage("downloading", f"0 bytes; declared size={total}")
        with dest.open("wb") as f:
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError("download_size_limit")
                f.write(chunk)
                if time.monotonic() - last >= 1:
                    stage("downloading", f"{received:,} bytes received")
                    last = time.monotonic()
        stage("downloaded", f"HTTP {response.status_code}; {received:,} bytes")
        return response.status_code, response.url, response.headers.get("Content-Type", "")


def render_browser(url, dest):
    # Normal isolated Chromium only; no cookies copied, stealth or challenge bypass.
    from playwright.sync_api import sync_playwright
    stage("browser_loading", url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            stage("browser_rendering", "waiting up to 12s for a stable rendered body")
            previous, stable = None, 0
            for _ in range(24):
                page.wait_for_timeout(500)
                length = page.locator("body").inner_text(timeout=5000)
                stable = stable + 1 if length == previous else 0
                previous = length
                if stable >= 3 and len(length) > 500:
                    break
            dest.write_text(page.content(), encoding="utf-8")
            return response.status if response else 200, page.url
        finally:
            browser.close()


def detect_kind(path, content_type=""):
    with path.open("rb") as f:
        prefix = f.read(2048).lstrip()
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if "html" in content_type.lower() or path.suffix.lower() in {".html", ".htm"} or b"<html" in prefix.lower() or b"<!doctype html" in prefix.lower():
        return "html"
    if path.suffix.lower() == ".md":
        return "md"
    if "text/plain" in content_type.lower() or path.suffix.lower() == ".txt":
        return "txt"
    raise ValueError("unsupported_file_type")


def run(job):
    folder = Path(job["job_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    result = {"source_url": job["url"], "final_url": job["url"], "source_id": job.get("source_id", ""),
              "category": job.get("category", ""), "attempts": [], "processed_at": utc_now(), "documents": []}
    try:
        if job["mode"] == "manual":
            source = Path(job["local_path"])
            stage("loading_manual_file", str(source))
            if not source.is_file():
                raise ValueError("manual_file_not_found")
            raw = folder / ("manual" + source.suffix.lower())
            shutil.copyfile(source, raw)
            kind = detect_kind(raw)
            result.update(acquisition_method="manual_browser_save", saved_at=job.get("saved_at") or None,
                          imported_at=utc_now(), original_file=str(source), raw_path=str(raw))
            result["documents"] = parse_file(raw, job["url"], kind, stage)
        else:
            session = requests.Session()
            session.headers["User-Agent"] = "ANLP-public-corpus/3.0"
            origin = urlsplit(job["url"])
            robots_url = urlunsplit((origin.scheme, origin.netloc, "/robots.txt", "", ""))
            stage("checking_robots", robots_url)
            robot_path = folder / "robots.txt"
            status, _, _ = fetch(robots_url, robot_path, 2 * 1024 * 1024, session)
            rp = RobotFileParser()
            if status == 404:
                rp.parse([])
            elif status == 200:
                rp.parse(robot_path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
            else:
                raise ValueError(f"robots_http_{status}")
            if not rp.can_fetch("ANLP-public-corpus", job["url"]):
                raise ValueError("robots_disallowed")
            delay = max(job["delay"], rp.crawl_delay("ANLP-public-corpus") or 0)
            stage("rate_limit_wait", f"{delay:g}s")
            time.sleep(delay)
            raw = folder / "response.bin"
            status, final_url, ctype = fetch(job["url"], raw, job["max_bytes"], session)
            result.update(http_status=status, final_url=final_url, fetched_at=utc_now(),
                          acquisition_method="http", raw_path=str(raw))
            result["attempts"].append({"method": "http", "http_status": status, "raw_path": str(raw)})
            # Access-denied pages go to the user, rather than repeated automated attempts.
            if status >= 400:
                raise ValueError(f"http_{status}")
            kind = detect_kind(raw, ctype)
            renamed = raw.with_suffix("." + kind)
            raw.rename(renamed)
            raw = renamed
            result["raw_path"] = str(raw)
            result["attempts"][-1]["raw_path"] = str(raw)
            result["documents"] = parse_file(raw, final_url, kind, stage)
            flags = {f for d in result["documents"] for f in d.get("quality_flags", [])}
            dynamic = flags.intersection({"insufficient_body", "javascript_shell", "calendar_content_missing"})
            if kind == "html" and dynamic and "access_challenge" not in flags and job["browser"]:
                stage("browser_fallback", ", ".join(sorted(dynamic)))
                try:
                    rendered = folder / "rendered.html"
                    status, final_url = render_browser(final_url, rendered)
                    attempt = {"method": "playwright", "http_status": status, "raw_path": str(rendered)}
                    result["attempts"].append(attempt)
                    if status >= 400:
                        raise ValueError(f"browser_http_{status}")
                    result["documents"] = parse_file(rendered, final_url, "html", stage)
                    result.update(raw_path=str(rendered), final_url=final_url, acquisition_method="playwright",
                                  fetched_at=utc_now())
                except Exception as exc:
                    result["browser_error"] = f"{type(exc).__name__}: {exc}"
            session.close()
        stage("quality_check")
        result["status"] = "needs_manual" if not result["documents"] or any(d.get("quality_flags") for d in result["documents"]) else "success"
        if result["status"] != "success":
            result["reason"] = "; ".join(sorted({f for d in result["documents"] for f in d.get("quality_flags", [])})) or "no_documents"
            if result.get("browser_error"):
                result["reason"] += "; " + result["browser_error"]
    except Exception as exc:
        result.update(status="needs_manual", reason=f"{type(exc).__name__}: {exc}")
    stage("saving_result")
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    stage("done", result["status"])


if __name__ == "__main__":
    run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
