"""Local integration test: no remote pages or evaluation answers required."""
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import importlib.util

SCRIPTS = Path(__file__).resolve().parents[1]
GOOD = '<html><title>Pittsburgh history</title><main><h1>History</h1><p>This is a sufficiently long factual fixture paragraph about a public museum in Pittsburgh and its historical collections.</p></main></html>'
MANUAL = GOOD.replace('history', 'culture').replace('historical collections', 'art collections')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path == '/slow':
            time.sleep(3)
        status = 404 if self.path == '/robots.txt' else 403 if self.path == '/forbidden' else 200
        body = '<html><title>Calendar</title><main>Loading</main></html>' if self.path == '/shell' else GOOD
        if self.path == '/dynamic':
            body = '<html><title>Calendar</title><main>Loading...</main><script>setTimeout(() => {document.querySelector("main").textContent = ' + json.dumps('November 14, 2026. Museum event information. ' * 20) + ';}, 100);</script></html>'
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        try:
            self.wfile.write(body.encode())
        except BrokenPipeError:
            pass

class WorkflowTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('playwright'), 'Optional Playwright is not installed')
    def test_browser_fallback(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'source.csv'
                source.write_text(f'url\nhttp://127.0.0.1:{server.server_port}/dynamic\n')
                output = root / 'auto'
                result = subprocess.run([sys.executable, str(SCRIPTS / 'collect.py'), '--sources', str(source), '--output', str(output), '--delay', '0', '--url-timeout', '40'], capture_output=True, text=True, timeout=50)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                docs = [json.loads(line) for line in (output / 'documents.jsonl').read_text().splitlines()]
                self.assertEqual(len(docs), 1, (output / 'fetch_log.jsonl').read_text())
                self.assertEqual(docs[0]['sources'][0]['acquisition_method'], 'playwright')
                self.assertIn('November 14, 2026', docs[0]['content_markdown'])
        finally:
            server.shutdown()
            server.server_close()

    def test_auto_then_manual(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / 'source.csv'
                with source.open('w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['source id', 'category', 'url'])
                    for name in ['good', 'same', 'forbidden', 'slow', 'shell']:
                        w.writerow([name, 'test', f'http://127.0.0.1:{server.server_port}/{name}'])
                auto = root / 'auto'
                def run(script, *args):
                    p = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True, timeout=35)
                    self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                    return p.stdout
                run('collect.py', '--sources', source, '--output', auto, '--no-browser', '--delay', '0', '--url-timeout', '1.5')
                docs = [json.loads(l) for l in (auto / 'documents.jsonl').read_text().splitlines()]
                self.assertEqual(len(docs), 1)
                self.assertEqual(len(docs[0]['sources']), 2)
                stats = json.loads((auto / 'corpus_stats.json').read_text())
                self.assertTrue(stats['run_complete'])
                self.assertEqual(stats['manual_queue_urls'], 3)
                manifest = auto / 'manual_queue.csv'
                with manifest.open(encoding='utf-8-sig') as f:
                    rows = list(csv.DictReader(f))
                self.assertTrue(any('timeout' in r['reason'] for r in rows))
                saved = root / 'saved.html'
                saved.write_text(MANUAL)
                for row in rows:
                    if row['source_id'] in {'forbidden', 'shell'}:
                        row['local_path'] = str(saved)
                with manifest.open('w', newline='', encoding='utf-8-sig') as f:
                    w = csv.DictWriter(f, fieldnames=rows[0].keys())
                    w.writeheader()
                    w.writerows(rows)
                merged = root / 'merged'
                run('process_manual.py', '--manifest', manifest, '--output', merged)
                docs = [json.loads(l) for l in (merged / 'documents.jsonl').read_text().splitlines()]
                self.assertEqual(len(docs), 2)
                self.assertEqual(len(docs[1]['sources']), 2)
                self.assertEqual(docs[1]['sources'][0]['acquisition_method'], 'manual_browser_save')
                self.assertIsNone(docs[1]['sources'][0]['saved_at'])
                stats = json.loads((merged / 'corpus_stats.json').read_text())
                self.assertEqual(stats['manual_queue_urls'], 1)
                self.assertIn('timeout', (auto / 'progress.jsonl').read_text())
        finally:
            server.shutdown()
            server.server_close()

if __name__ == '__main__':
    unittest.main()
