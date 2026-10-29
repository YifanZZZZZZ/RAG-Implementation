"""Offline regression tests; no network calls or production corpus writes."""
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'city_pittsburgh.py'
spec=importlib.util.spec_from_file_location('city',SCRIPT)
city=importlib.util.module_from_spec(spec);spec.loader.exec_module(city)


class CityTests(unittest.TestCase):
    def test_content_and_table(self):
        html='''<div id="main-content"><!-- TEMPLATE NOISE --><h1>Service</h1>
        <p>Body information for residents.</p><details><summary>Hours</summary><p>Monday 9 AM</p></details>
        <h2>Contact Us</h2><p>Department phone: 123</p>
        <table><tr><th rowspan="2">Year</th><th colspan="2">Counts</th></tr>
        <tr><th>A</th><th>B</th></tr><tr><td>2026</td><td>1</td><td>2</td></tr></table>
        <ul><li>Parent<ul><li>Child</li></ul></li></ul></div><footer>FOOTER NOISE</footer>'''
        p=city.parse_html(html,city.BASE+'/Resident-Services/Test')
        s=p['content_markdown']
        self.assertIn('Monday 9 AM',s)
        self.assertIn('Department phone: 123',s)
        self.assertIn('| Year | Counts / A | Counts / B |',s)
        self.assertNotIn('NOISE',s)
        self.assertNotIn('<tr>',s)
        self.assertIn('  - Child',s)

    def test_discovery_and_thin(self):
        html='<div id="main-content"><h1>Safety</h1></div><div id="nav"><a href="/Safety/Animal-Care-Control">Animals</a></div>'
        p=city.parse_html(html,city.BASE+'/Safety')
        self.assertTrue(p['thin'])
        self.assertEqual(p['links'][0]['url'],city.BASE+'/Safety/Animal-Care-Control')

    def test_pagination(self):
        h='''<form action="/News-articles"><input name="state" type="hidden" value="abc">
        <input name="danger" type="submit" value="Other action">
        <select name="page"><option selected value="1">1</option></select>
        <div class="button-next"><input name="next" type="submit" value="Next"></div></form>'''
        data=dict(city.next_form(h,city.BASE+'/News-articles'))
        self.assertEqual(data,{'state':'abc','page':'1','next':'Next'})
        with self.assertRaises(ValueError):city.next_form(h.replace('/News-articles','https://example.com'),city.BASE+'/News-articles')

    def test_event_date_form(self):
        h='<form action="/Events-directory"><div id="main-content"><input name="x$eventDateFrom$NoJsDatePicker" value="10/28/2026"><input name="x$eventDateFrom$JavascriptEnabled" value="False"><input type="submit" name="search" value="Search"></div></form>'
        values=dict(city.next_form(h,city.BASE+'/Events-directory','2026-10-01'))
        self.assertEqual(values['x$eventDateFrom$NoJsDatePicker'],'10/01/2026')
        self.assertEqual(values['search'],'Search')

    def test_robots_and_urls(self):
        r=city.Robots('User-agent: *\nDisallow: /private/*\nAllow: /private/public$\nCrawl-delay: 2')
        self.assertFalse(r.allowed(city.BASE+'/private/a'))
        self.assertTrue(r.allowed(city.BASE+'/private/public'))
        self.assertEqual(r.delay,2)
        self.assertEqual(city.canonical('http://pittsburghpa.gov/Home?utm_source=x#top'),city.SOURCE_URL)
        self.assertEqual(city.classify('https://example.com/test'),'external')

    def test_resume_and_url_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sources=root/'source.csv'
            sources.write_text('source id,category,url\nCity of Pittsburgh webpage,General Info,'+city.SOURCE_URL+'\n')
            args=Namespace(sources=sources,output_root=root/'output',resume=False,retry_failed=False,
                           max_pages=1,max_depth=8,max_listing_pages=300,delay=0,max_wait=30,
                           max_mb=30,download_timeout=120,skip_pdfs=True,events_from='2026-10-01')
            page='<div id="main-content"><h1>Title</h1><p>'+('Useful information. '*10)+'</p><a href="/Resident-Services/Test">Details</a></div>'
            def fetch(self,url,data=None):
                if url.endswith('robots.txt'):return b'User-agent: *\n',url,'text/plain'
                return page.encode(),url,'text/html'
            with patch.object(city,'SEEDS',['Home']),patch.object(city.Collector,'fetch',fetch):
                c=city.Collector(args);c.run()
                self.assertEqual(len(c.queue),1)
                args.resume=True
                c=city.Collector(args);c.run()
                docs=[json.loads(l) for l in (c.out/'documents.jsonl').read_text().splitlines()]
                self.assertEqual(len(docs),2)
                self.assertEqual(len({d['doc_id'] for d in docs}),2)
                for doc in docs:
                    self.assertNotIn('blocks',doc)
                    self.assertEqual(Path(doc['markdown_path']).read_text(),doc['content_markdown']+'\n')
                self.assertFalse(c.queue)


if __name__=='__main__':unittest.main()
