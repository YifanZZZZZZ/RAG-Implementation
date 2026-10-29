import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pittsburgh_symphony import parse,details,pagination
class Tests(unittest.TestCase):
 def test_concert(self):
  raw='''<main id="maincontent"><section class="seat-availability-timer">Time left to complete order</section><h1>Concert</h1><p>October 30, 2026 at 7:30 PM. Heinz Hall. This concert features an orchestra performing a full evening of music.</p><h2>Program</h2><ul><li>Symphony No. 5</li></ul><a href="/tickets">Buy Tickets</a><a href="https://youtube.com/watch?v=1">Watch video</a><aside>Other events</aside></main><footer>Donate now</footer>'''
  _,body,_=parse(raw,'concerts')
  for x in ['October 30','Heinz Hall','Symphony No. 5']:self.assertIn(x,body)
  for x in ['Time left','Buy Tickets','Watch video','Other events','Donate now']:self.assertNotIn(x,body)
 def test_faq(self):
  raw='''<main><details class="faq-item"><summary><span class="q-text">What should I wear?</span></summary><div class="faq-answer"><p>Wear comfortable clothing.</p></div></details></main>'''
  _,body,meta=parse(raw,'faq');self.assertEqual(meta['qa_pairs'],1);self.assertIn('comfortable',body)
 def test_links(self):
  raw='''<main><a href="/production/123/concert">Concert</a><a href="/production/123/concert">Duplicate</a><a href="/calendar?page=5">5</a><a href="https://other.com/production/123/concert">External</a></main>'''
  self.assertEqual(len(details(raw,'concerts')),1);self.assertEqual(pagination(raw,'concerts'),[1,5])
if __name__=='__main__':unittest.main()
