import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from citypaper_events import listing, detail

class ExtractionTests(unittest.TestCase):
    def test_listing_scope(self):
        raw='''<main><h1>Browse events</h1><a href="/events/123/example">Example</a>
        <a href="https://seller.com/tickets">Get tickets</a>
        <a href="/events/123/example">Repeated card</a><p>Page 1 of 8</p></main>'''
        self.assertEqual(list(listing(raw,1,8)),['https://community.pghcitypaper.com/events/123/example'])
        with self.assertRaises(ValueError):listing(raw,2,8)
    def test_detail_excludes_actions_preserves_body(self):
        raw='''<html><body><nav>Navigation</nav><main><h1>Example Event</h1>
        <span>$10</span><h2>When</h2><ul><li>October 28, 2026, 8 PM</li><li>…and 12 more</li></ul>
        <h2>Where</h2><p>Example Hall</p><h2>About this event</h2>
        <p>Full description with <strong>important information</strong>.</p>
        <a href="https://seller.com">Get tickets</a><a href="https://event.com">Event website</a>
        <h2>Contact</h2><p>555-1234</p>
        <aside><h2>Is this your event?</h2><p>Advertise your event.</p><a href="/promote">Promote it →</a></aside>
        </main><footer>Global footer</footer></body></html>'''
        title,body,flags=detail(raw)
        self.assertEqual(title,'Example Event')
        for unwanted in ['Get tickets','Event website','Is this your event','Advertise','Promote it','Global footer','Navigation','seller.com']:
            self.assertNotIn(unwanted,body)
        for wanted in ['$10','October 28','Example Hall','important information','555-1234']:
            self.assertIn(wanted,body)
        self.assertIn('schedule_truncated_by_source',flags)
    def test_reject_block_and_wrong_page(self):
        for html in ['<title>Attention Required! | Cloudflare</title>', '<main><h1>Browse events</h1></main>']:
            with self.assertRaises(ValueError):detail(html)

if __name__=='__main__':unittest.main()
