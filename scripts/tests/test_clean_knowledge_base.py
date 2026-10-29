import json,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clean_knowledge_base import clean_markdown,canonical,discover,build

class CleaningTests(unittest.TestCase):
    def test_structure_and_facts(self):
        s='# Heading\n\n| Date | Price |\n| --- | --- |\n| 2026-10-28 | $45.26 |\n\n- Item\n  - Child\n\nContact us for accessibility information.\n\nPrivacy Policy\n\n```python\nx = "Privacy Policy"\n\n\n```'
        out,_=clean_markdown(s)
        self.assertIn('| 2026-10-28 | $45.26 |',out)
        self.assertIn('  - Child',out)
        self.assertIn('Contact us for accessibility information.',out)
        self.assertNotIn('\nPrivacy Policy\n',out)
        self.assertIn('x = "Privacy Policy"\n\n\n```',out)
        self.assertEqual(clean_markdown('    code = 1')[0],'    code = 1')
    def test_urls(self):
        self.assertEqual(canonical('https://EXAMPLE.com:443/a?utm_source=x&page=2#top'),'https://example.com/a?page=2')
        self.assertNotEqual(canonical('https://x/a?page=2'),canonical('https://x/a?page=3'))
    def test_discovery_dedup_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'input';out=Path(tmp)/'out'
            def put(rel,rows):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            a=dict(doc_id='a',source_url='https://x/a',content_markdown='# A\n\nBody',title='A')
            put('Site/documents.jsonl',[a,dict(a,doc_id='b',source_url='https://x/b')])
            put('Site/section/documents.jsonl',[a]);put('Site/_archive/documents.jsonl',[a])
            put('Site/Single Pages/documents.jsonl',[dict(a,content_markdown='# A\n\nChanged body'),dict(a,content_markdown='403 Forbidden')])
            paths,_=discover(root);self.assertEqual(len(paths),2)
            report=build(root,out)
            self.assertEqual(report['exact_duplicates'],1)
            self.assertEqual(report['output_documents'],2)
            self.assertEqual(report['quarantined'],1)
            self.assertEqual(report['same_url_conflicts'],1)
            docs=[json.loads(x) for x in (out/'documents.jsonl').read_text().splitlines()]
            self.assertEqual(len({d['doc_id'] for d in docs}),2)
            self.assertTrue(any(len(d['cleaning_provenance'])==2 for d in docs))
            with self.assertRaises(FileExistsError):build(root,out)
if __name__=='__main__':unittest.main()
