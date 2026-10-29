import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trustarts import details,parse,pagination
class Tests(unittest.TestCase):
 def test_featured_excludes_sidebar(self):
  raw='''<main><section class="primary"><h2>Featured Stories</h2><h3><a href="/blog/real-story">Real Story</a></h3><p>This is the real article listing with a meaningful summary that needs to be retained in the knowledge resource.</p></section><aside><h2>Blog</h2><h3><a href="/blog/sidebar-story">Sidebar Story</a></h3></aside></main>'''
  self.assertEqual(details(raw,'blog'),['https://trustarts.org/blog/real-story'])
  _,body,_=parse(raw,'blog');self.assertNotIn('Sidebar Story',body)
 def test_article_body(self):
  raw='''<main><h1 class="page-title">Article Title</h1><h4 class="published-date">October 28, 2026</h4><section class="blog-post"><article class="post"><p>A meaningful article with sufficient text about theater, cultural programs and the city of Pittsburgh.</p><img src="photo.jpg"/><p><a href="/child">Relevant words</a></p></article></section><aside>Related story</aside><div class="email-capture modal"><h1>Looking for more?</h1>Newsletter</div></main>'''
  _,body,_=parse(raw,'blog')
  self.assertIn('Relevant words',body);self.assertIn('October 28',body)
  for x in ['photo.jpg','/child','Related story','Newsletter','Looking for more']:self.assertNotIn(x,body)
 def test_calendar_paths(self):
  raw='''<main><article class="event"><h3 class="title"><a href="/production/1/show">Show</a></h3></article><article class="event"><h3 class="title"><a href="/event/2/other">Other</a></h3></article><a href="/calendar?organization_id=1&amp;page=9">9</a></main>'''
  self.assertEqual(len(details(raw,'concerts')),2);self.assertEqual(pagination(raw,'concerts'),[1,9])
if __name__=='__main__':unittest.main()
