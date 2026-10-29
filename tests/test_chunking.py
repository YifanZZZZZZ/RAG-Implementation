"""Offline tests with a tiny tokenizer; full corpus verified with real tokenizers."""
import sys
from pathlib import Path
import tempfile
import unittest
from transformers import BertTokenizerFast
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.chunking import Budgets,build_chunks,split_document,token_windows
from rag.block_render import markdown
from rag.structure import units_for_document,sentence_boundaries

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        p=Path(cls.temp.name)/'vocab.txt'
        p.write_text('\n'.join(['[PAD]','[UNK]','[CLS]','[SEP]','[MASK]','title',':','a','b','test','venue','2026','.', '#','|','`','##s','##ing','oct','27','4','pm']))
        cls.t=BertTokenizerFast(vocab_file=str(p),model_max_length=512)
        cls.b=Budgets(cls.t,cls.t,128)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def doc(self,blocks,id='d',url='https://example.org/'):
        return {'doc_id':id,'title':'Test','blocks':blocks,'content_markdown':markdown(blocks),
                'sources':[{'source_url':url}],'page_number':None}
    def audit(self,doc,pieces):
        text=doc['content_markdown'];end=0
        for p in pieces:
            self.assertLessEqual(p['start'],end)
            self.assertTrue(self.b.fits(p['prefix']+text[p['start']:p['end']]))
            end=max(end,p['end'])
        self.assertEqual(end,len(text))
    def test_fixed_coverage_overlap_unicode_tail(self):
        doc=self.doc([{'type':'paragraph','text':'Andrés\n    A testing '*200+' TAIL'}])
        pieces=split_document(doc,self.b,'fixed',16);self.audit(doc,pieces)
        for a,b in zip(pieces,pieces[1:]):
            self.assertEqual(a['parts'][0]['body_token_end']-b['parts'][0]['body_token_start'],16)
        self.assertIn('Andrés',doc['content_markdown'][pieces[0]['start']:pieces[0]['end']])
    def test_short_and_dedup_provenance(self):
        a=self.doc([{'type':'paragraph','text':'A b'}]);b={**a,'doc_id':'two','sources':[{'source_url':'https://other.org'}]}
        chunks,stats=build_chunks([a,b],self.b,'fixed')
        self.assertEqual(len(chunks),1);self.assertEqual(len(chunks[0]['occurrences']),2)
        self.assertEqual(len(chunks[0]['source_urls']),2)
        again,_=build_chunks([a,b],self.b,'fixed');self.assertEqual(chunks,again)
    def test_event_card_never_mixed_with_neighbor(self):
        def card(n):return {'type':'group','children':[{'type':'heading','level':3,'text':'Event '+n},
            {'type':'paragraph','text':'Oct 27 4 PM'},{'type':'paragraph','text':'Venue '+n}]}
        doc=self.doc([card('A'),card('B')]);p=split_document(doc,self.b,'structure')
        self.assertEqual(len(p),2);self.assertEqual(p[0]['method'],'event');self.audit(doc,p)
        self.assertNotIn('Venue B',doc['content_markdown'][p[0]['start']:p[0]['end']])
    def test_overlong_event_repeats_title(self):
        doc=self.doc([{'type':'group','children':[{'type':'heading','level':3,'text':'Event A'},
            {'type':'paragraph','text':'Oct 27 4 PM'},{'type':'paragraph','text':'A b. '*180}]}])
        p=split_document(doc,self.b,'structure');self.audit(doc,p)
        self.assertGreater(len(p),1);self.assertTrue(all('Event A' in x['prefix'] for x in p))
    def test_statistic_number_and_label_stay_together(self):
        doc=self.doc([{'type':'heading','level':2,'text':'Population'}, {'type':'heading','level':3,'text':'16,582 +'},
            {'type':'heading','level':4,'text':'students representing 132+ countries'}])
        units=units_for_document(doc);stats=[x for x in units if x['kind']=='statistic']
        self.assertEqual(len(stats),1)
        self.assertIn('students',doc['content_markdown'][stats[0]['start']:stats[0]['end']])
    def test_heading_sections_not_merged(self):
        doc=self.doc([{'type':'heading','level':2,'text':'History'},{'type':'paragraph','text':'A b.'},
            {'type':'heading','level':2,'text':'Geography'},{'type':'paragraph','text':'B a.'}])
        p=split_document(doc,self.b,'structure');self.audit(doc,p)
        self.assertEqual(len(p),2)
    def test_sentence_abbreviations_decimals(self):
        text='Dr. Smith arrives at 4 p.m. Today costs 3.50 dollars. Next sentence.'
        ends=sentence_boundaries(text)
        self.assertEqual([text[:x].strip() for x in ends],['Dr. Smith arrives at 4 p.m. Today costs 3.50 dollars.'])
    def test_long_paragraph_and_code_fallback(self):
        for kind in ['paragraph','code']:
            doc=self.doc([{'type':kind,'text':'testing '*500}]);p=split_document(doc,self.b,'structure')
            self.audit(doc,p);self.assertTrue(any(x['method']=='token_fallback' for x in p))
    def test_table_rows_header_preserved(self):
        row=lambda a,b,header=False:[{'text':a,'header':header,'rowspan':1,'colspan':1},{'text':b,'header':header,'rowspan':1,'colspan':1}]
        doc=self.doc([{'type':'table','caption':'Budget','rows':[row('Year','Amount',True)]+[row('2026','100') for _ in range(60)]}])
        p=split_document(doc,self.b,'structure');self.audit(doc,p)
        self.assertGreater(len(p),1);self.assertTrue(all('Year | Amount' in x['prefix'] for x in p))
    def test_complex_table_no_invented_cells(self):
        doc=self.doc([{'type':'table','caption':'','rows':[[{'text':'A','header':True,'rowspan':2,'colspan':2}]]}])
        p=split_document(doc,self.b,'structure');self.audit(doc,p)
        self.assertIn('merged_cells',p[0]['warnings'][0])
    def test_pdf_number_only_not_removed(self):
        doc=self.doc([{'type':'heading','level':1,'text':'PDF page 69'},{'type':'pdf_layout','text':'69'}]);doc['page_number']=69
        c,s=build_chunks([doc],self.b,'structure');self.assertEqual(len(c),1)
        self.assertTrue(all(x['occurrences'][0]['page_number']==69 for x in c))
    def test_flat_events_include_preceding_time(self):
        blocks=[]
        for n in range(3):
            blocks += [{'type':'paragraph','text':f'{n+1}:00 PM'},
                {'type':'heading','level':3,'text':f'[Event {n}](<https://community.pghcitypaper.com/events/{n}/test>)'},
                {'type':'paragraph','text':f'Venue {n}'}]
        doc=self.doc(blocks,url='https://community.pghcitypaper.com/events')
        units=units_for_document(doc);events=[u for u in units if u['kind']=='event']
        self.assertEqual(len(events),3)
        for n,u in enumerate(events):
            t=doc['content_markdown'][u['start']:u['end']]
            self.assertIn(f'{n+1}:00 PM',t);self.assertIn(f'Event {n}',t);self.assertIn(f'Venue {n}',t)
        self.audit(doc,split_document(doc,self.b,'structure'))
    def test_mismatched_blocks_fallback_does_not_lose_content(self):
        doc=self.doc([{'type':'paragraph','text':'A'}]);doc['content_markdown']='Different text. '*100
        p=split_document(doc,self.b,'structure');self.audit(doc,p)
        self.assertIn('block_mapping_failed_use_markdown',p[0]['warnings'])
    def test_invalid_limits_and_overlaps(self):
        with self.assertRaises(ValueError):Budgets(self.t,self.t,320)
        with self.assertRaises(ValueError):list(token_windows('A b','',self.b,128))
        with self.assertRaises(ValueError):split_document(self.doc([]),self.b,'fixed')
    def test_qwen_limit_is_independently_enforced(self):
        budget=Budgets(self.t,self.t,128,qwen_limit=40)
        doc=self.doc([{'type':'paragraph','text':'A b '*150}])
        for strategy in ['fixed','structure']:
            p=split_document(doc,budget,strategy,overlap=4)
            self.assertTrue(all(budget.counts(x['prefix']+doc['content_markdown'][x['start']:x['end']])['qwen3_embedding']<=40 for x in p))

if __name__=='__main__':unittest.main()
