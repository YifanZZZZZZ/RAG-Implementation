import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cmu_signature_events import discover,parse
class Tests(unittest.TestCase):
 def test_scope(self):
  raw='''<main><h1>Events</h1><section><h2>Signature Events</h2><h3><a href="/engage/events/homecoming">Homecoming</a></h3><p>A long description of our annual Homecoming celebration for alumni and the community.</p><h3><button>Presidential Tours</button></h3><p>Travel and meet fellow alumni.</p><a href="https://youtube.com/watch?v=1">Watch video</a></section><section><a href="/unrelated">Unrelated</a></section></main><footer>Top Tools About CMU</footer>'''
  self.assertEqual(discover(raw),['https://www.cmu.edu/engage/events/homecoming'])
  _,body=parse(raw,True)
  self.assertIn('Presidential Tours',body)
  for phrase in ['Top Tools','About CMU','Watch video','youtube.com','Unrelated']:self.assertNotIn(phrase,body)
 def test_detail_no_urls(self):
  raw='''<main><h1>Homecoming</h1><p>This is a detailed description of the annual event and all of its memorable traditions.</p><p><a href="/next-level">Schedule information</a></p><iframe src="https://youtube.com/embed/1"></iframe><a href="https://vimeo.com/1">Watch video</a></main><footer>Academics Our Impact Top Tools</footer>'''
  _,body=parse(raw)
  self.assertIn('Schedule information',body)
  for phrase in ['/next-level','vimeo.com','youtube.com','Top Tools','Watch video']:self.assertNotIn(phrase,body)
if __name__=='__main__':unittest.main()
