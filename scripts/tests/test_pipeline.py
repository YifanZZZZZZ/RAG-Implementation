import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_pipeline import parse_html, normalize_url, content_hash, markdown, clean_blocks


class ParserTests(unittest.TestCase):
    def parse(self, body):
        return parse_html(('<html><title>Example</title><body><main>' + body + '</main></body></html>').encode(), 'https://example.org/page')

    def test_div_span_time(self):
        d = self.parse('<article class="event"><h2>Concert</h2><div><time>November 14, 2026, 11:15 AM</time></div><div class="venue">Heinz Hall</div><span>Additional event information for audience members.</span></article>')
        self.assertIn('November 14, 2026, 11:15 AM', d['content_markdown'])
        self.assertIn('Heinz Hall', d['content_markdown'])
        self.assertTrue(any(b['type'] == 'group' for b in d['blocks']))

    def test_heading_list_code_links(self):
        d = self.parse('<h1>Architecture</h1><h2>Attention</h2><ul><li>One<ul><li>Nested</li></ul></li></ul><pre><code class="language-python">if True:\n    print(1)\n</code></pre><p>Read <a href="/details">details</a>.</p>')
        text = d['content_markdown']
        self.assertIn('## Attention', text)
        self.assertIn('  - Nested', text)
        self.assertIn('    print(1)', text)
        self.assertIn('https://example.org/details', text)

    def test_code_whitespace_affects_hash(self):
        a = [{'type': 'code', 'text': 'if a:\n    b()'}]
        b = [{'type': 'code', 'text': 'if a:\nb()'}]
        self.assertNotEqual(content_hash(a), content_hash(b))

    def test_table_spans(self):
        d = self.parse('<table><tr><th colspan="2">Facts</th></tr><tr><th>ID</th><td>01.1</td></tr></table>')
        self.assertIn('colspan="2"', d['content_markdown'])
        self.assertIn('01.1', d['content_markdown'])

    def test_simple_table(self):
        d = self.parse('<table><tr><th>Player</th><th>College</th></tr><tr><td>A</td><td>B</td></tr></table>')
        self.assertIn('| Player | College |', d['content_markdown'])
        self.assertIn('| A | B |', d['content_markdown'])

    def test_navigation_removed_unicode_kept(self):
        d = self.parse('<nav>REMOVE ME</nav><h1>Title</h1><p>Andrés&nbsp;Franco is the conductor for this event at the venue in Pittsburgh.</p>')
        self.assertNotIn('REMOVE ME', d['content_markdown'])
        self.assertIn('Andrés Franco', d['content_markdown'])

    def test_canonical_is_advisory(self):
        d = parse_html(b'<html><head><link rel="canonical" href="/other"></head><body><p>Some content</p></body></html>', 'https://example.org/current')
        self.assertEqual(d['declared_canonical_url'], 'https://example.org/other')

    def test_url_identity(self):
        self.assertEqual(normalize_url('HTTPS://Example.COM:443/a?page=2&year=2026&utm_source=x#intro'), 'https://example.com/a?page=2&year=2026')
        self.assertEqual(normalize_url('https://example.org/#/events'), 'https://example.org/#/events')

    def test_repeated_cards_but_not_distinct_dates(self):
        card = '<div class="event"><h2>Concert</h2><div>November 14, 2026</div></div>'
        d = self.parse(card + card + card.replace('14', '15'))
        groups = [b for b in d['blocks'] if b['type'] == 'group']
        self.assertEqual(len(groups), 2)

    def test_calendar_shell(self):
        d = parse_html(b'<html><title>CMU Events Calendar</title><main><h1>Calendar</h1><p>Search events</p></main></html>', 'https://events.cmu.edu/')
        self.assertIn('calendar_content_missing', d['quality_flags'])

    def test_challenge(self):
        d = parse_html(b'<html><title>Just a moment</title><body>Verify you are human</body></html>', 'https://example.org/')
        self.assertIn('access_challenge', d['quality_flags'])

    def test_link_wrapped_card_preserves_structure(self):
        d = self.parse('<a href="/event/test"><div><h4>Concert title</h4><div>Heinz Hall</div><ul><li>Music</li></ul></div></a>')
        text = d['content_markdown']
        self.assertIn('#### Concert title', text)
        self.assertIn('\n\nHeinz Hall\n\n', text)
        self.assertIn('- Music', text)
        self.assertIn('[Source](<https://example.org/event/test>)', text)

    def test_multiple_articles_not_truncated(self):
        d = parse_html(b'<body><article><p>First event</p></article><article><p>Second event</p></article></body>', 'https://example.org/')
        self.assertIn('Second event', d['content_markdown'])


if __name__ == '__main__':
    unittest.main()
