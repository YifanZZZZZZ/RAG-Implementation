import sys,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cmu_events import month_days,internal_body,api_url
class Tests(unittest.TestCase):
 def test_full_day_and_bounds(self):
  rows=[{'id':str(i),'title':'Event','href':'event/'+str(i),'ts_start':i} for i in range(13)]
  x={'days':[{'date':'20261025','events':[],'event_count':0},{'date':'20261026','events':rows,'event_count':13},{'date':'20261027','events':[],'event_count':0}]}
  days,errors=month_days(x,date(2026,10,26),date(2026,10,27))
  self.assertFalse(errors);self.assertEqual(len(days),2);self.assertEqual(len(days['2026-10-26']),13)
  x['days'][1]['events']=rows[:2]
  self.assertTrue(month_days(x,date(2026,10,26),date(2026,10,27))[1])
 def test_sidebar_exclusion(self):
  x={'event':{'id':'123','summary':'<p>Summary</p>','description':'<h2>Body</h2><p>Details</p>','cost':'Free','related_content':'BAD RELATED','contact_info':'BAD CONTACT','share_links':'BAD SHARE'}}
  body=internal_body(x,'123-event')
  self.assertIn('Details',body);self.assertIn('Free',body);self.assertNotIn('BAD',body)
  with self.assertRaises(ValueError):internal_body(x,'456-other')
 def test_timezone(self):
  self.assertIn('America%2FNew_York',api_url('month','20261001'))
if __name__=='__main__':unittest.main()
