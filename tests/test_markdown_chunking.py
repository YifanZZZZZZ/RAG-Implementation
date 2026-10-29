import unittest
from rag.structure import markdown_rows,units_for_document
from tests.test_chunking import Tests
class MarkdownTests(Tests):
    def test_cleaned_markdown_units(self):
        text='# Museum\n\n## FAQ\n\n### When?\n\nOpen at 10 AM.\n\n### Where?\n\nPittsburgh.\n\n## Prices\n\n| Type | Price |\n| --- | --- |\n| Adult | $20 |\n\n- First\n  - Nested\n\n```python\n# not heading\nprint(1)\n```\n'
        doc={'doc_id':'md','title':'Museum','content_markdown':text,'sources':[{'source_url':'https://x/'}]}
        rows=markdown_rows(text)
        self.assertEqual(''.join(text[r['start']:r['end']] for r in rows),text)
        self.assertTrue({'heading','table','list','code'}.issubset({r['block']['type'] for r in rows}))
        units=units_for_document(doc)
        self.assertFalse(any('block_mapping_failed_use_markdown' in u['warnings'] for u in units))
        pieces=__import__('rag.chunking',fromlist=['split_document']).split_document(doc,self.b,'structure')
        self.audit(doc,pieces)
        self.assertTrue(any('When?' in p['prefix'] for p in pieces))
        self.assertTrue(any('Type' in p['prefix'] and 'Price' in p['prefix'] for p in pieces))
if __name__=='__main__':unittest.main()
