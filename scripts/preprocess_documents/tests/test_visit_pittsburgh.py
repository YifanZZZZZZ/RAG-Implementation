import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import visit_pittsburgh as v


class VisitTests(unittest.TestCase):
    def test_depth_modules_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.csv'
            source.write_text('source id,category,url\nVisit Pittsburgh webpage ,General Info,'+v.HOME+'\n')
            args=Namespace(sources=source,output_root=root/'out',resume=False,retry_failed=False,html_map=None,max_pages=20,delay=0)
            home='<main><h1>Home</h1><p>'+('Home body. '*15)+'</p><a href="/outside-module/">Not a seed</a>'
            for label in ["What's Coming Up",'More Fall Inspiration!','Plan Your Trip']:
                home+='<div class="block"><h2>'+label+'</h2><a href="/one/">One</a></div>'
            home+='</main>'
            paths={'one':'two','two':'three','three':'four'}
            called=[]
            def fetch(self,url,robots=False):
                called.append(url)
                if robots:return b'User-agent: *\n',url,'http'
                if url==v.HOME:return home.encode(),url,'http'
                part=url.rstrip('/').rsplit('/',1)[-1]
                html='<main><h1>Details</h1><p>'+('Details. '*20)+'</p><a href="/'+paths[part]+'/">Next</a><a href="https://external.example/a">External</a><a href="/file.pdf">PDF</a><div class="block"><h2>Related Blogs</h2><p>REMOVE ME</p><a href="/related/">Related</a></div></main>'
                return html.encode(),url,'http'
            with patch.object(v.Crawler,'fetch',fetch):
                crawler=v.Crawler(args);crawler.run()
            docs=[json.loads(line) for line in (crawler.out/'documents.jsonl').read_text().splitlines()]
            self.assertEqual(sorted(d['depth'] for d in docs),[0,1,2,3])
            self.assertNotIn(v.HOME+'four/',called)
            self.assertNotIn(v.HOME+'related/',called)
            self.assertNotIn(v.HOME+'outside-module/',called)
            self.assertNotIn(v.HOME+'file.pdf',called)
            for d in docs:
                self.assertNotIn('REMOVE ME',d['content_markdown'])
                self.assertNotIn('blocks',d)
                self.assertEqual(Path(d['markdown_path']).read_text(),d['content_markdown']+'\n')

    def test_redirect_external_never_requested(self):
        crawler=object.__new__(v.Crawler)
        crawler.manual={};crawler.robots=None;crawler.last=0;crawler.args=Namespace(delay=0)
        response=requests.Response();response.status_code=302
        response.headers['Location']='https://external.example/a';response._content=b'';response._content_consumed=True
        with patch.object(requests.Session,'get',return_value=response) as get:
            crawler.session=requests.Session()
            with self.assertRaisesRegex(ValueError,'redirect_blocked'):crawler.fetch(v.HOME)
            self.assertEqual(get.call_count,1)


if __name__=='__main__':unittest.main()
