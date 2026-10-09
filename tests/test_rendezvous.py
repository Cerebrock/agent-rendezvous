import json
from pathlib import Path
import tempfile
import unittest
import urllib.request

import rendezvous as r


class MonitorTests(unittest.TestCase):
    def setUp(self):
        (r.ROOT / 'work').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=r.ROOT / 'work')
        self.directory = Path(self.tmp.name)
        self.db = r.connect(self.directory)
        self.source = {'id': 'test', 'name': 'Test', 'page': 'https://example.com/',
                       'url': 'https://example.com/feed', 'parser': 'flatboard'}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def result(self, records, error=None):
        return dict(source=self.source, observed='2026-10-09T04:00:00+00:00',
                    status=500 if error else 200, records=records, error=error)

    def test_baseline_then_new_and_edited_preserve_versions(self):
        a = r.item(1, 'agent', 'first', '2026-10-08T23:00:00Z')
        self.assertEqual(r.record(self.db, self.result([a])), (1, 0, True))
        self.assertEqual(r.recent_changes(self.db, 10), [])
        self.assertEqual(r.record(self.db, self.result([a])), (0, 0, False))
        edited = r.item(1, 'agent', 'corrected', '2026-10-08T23:00:00Z')
        new = r.item(2, 'peer', 'answer', '2026-10-09T01:00:00Z', reply_to=1)
        self.assertEqual(r.record(self.db, self.result([edited, new])), (1, 1, False))
        bodies = [x[0] for x in self.db.execute('SELECT body FROM versions ORDER BY id')]
        self.assertEqual(bodies, ['first', 'corrected', 'answer'])

    def test_limited_feed_does_not_infer_deletion(self):
        r.record(self.db, self.result([r.item(1, body='old')]))
        r.record(self.db, self.result([r.item(2, body='new')]))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM items').fetchone()[0], 2)

    def test_failure_preserves_history_and_initial_failure_is_not_baseline(self):
        r.record(self.db, self.result([], 'timeout'))
        self.assertEqual(r.record(self.db, self.result([r.item(1, body='data')])), (1, 0, True))
        r.record(self.db, self.result([], '401'))
        self.assertEqual(self.db.execute('SELECT body FROM items').fetchone()[0], 'data')
        self.assertEqual(r.statuses(self.db, [self.source])[0]['scan']['error'], '401')

    def test_votes_do_not_create_message_content_changes(self):
        raw = {'msgs': [{'id': 1, 'author': 'peer', 'text': 'same', 'created': '2026-10-09T00:00:00Z', 'rating': 1}]}
        first = r.parse(self.source, json.dumps(raw))
        raw['msgs'][0]['rating'] = 10
        self.assertEqual(first, r.parse(self.source, json.dumps(raw)))

    def test_discovery_retains_evidence_without_fetching_or_leaking_query_keys(self):
        x = r.item(1, 'peer', 'https://board.example/thread/1 https://secret.example/?token=private',
                   '2026-10-09T00:00:00Z', 'https://example.com/source')
        r.record(self.db, self.result([x]))
        found = r.candidates(self.db, [self.source])
        self.assertEqual([c['host'] for c in found], ['board.example'])
        self.assertEqual(found[0]['evidence'], ['https://example.com/source'])

    def test_report_escapes_untrusted_html(self):
        r.record(self.db, self.result([r.item(1, body='baseline')]))
        r.record(self.db, self.result([r.item(2, '<img src=x onerror=alert(1)>', '</td><script>ATTACK()</script>', url='javascript:ATTACK()')]))
        output = self.directory / 'report'
        r.report(self.db, [self.source], output)
        text = (output / 'index.html').read_text()
        self.assertNotIn('<script>ATTACK()', text)
        self.assertNotIn('href="javascript:', text)
        self.assertIn('&lt;script&gt;ATTACK()', text)

    def test_quoted_urls_and_candidate_links(self):
        r.record(self.db, self.result([r.item(1, body="'https://board.example/register' `https://other.example/post`")]))
        found = r.candidates(self.db, [self.source])
        self.assertEqual({c['host'] for c in found}, {'board.example', 'other.example'})
        output = self.directory / 'report'
        r.report(self.db, [self.source], output)
        text = (output / 'index.html').read_text()
        self.assertIn('href="https://board.example/"', text)
        self.assertNotIn('href="https://board.example/register', text)

    def test_timestamps_distinguish_observation_and_source(self):
        self.assertIsNone(r.timestamp('2026-10-09T00:00:00'))
        self.assertEqual(r.local_time('2026-10-09T02:47:09Z'), '08/10 23:47 ART')

    def test_redirect_does_not_visit_a_new_host(self):
        handler = r.ReadOnlyRedirect()
        req = urllib.request.Request('https://example.com/feed')
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, '', {}, 'https://other.example/post')
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, '', {}, 'https://example.com/publish')
        with self.assertRaises(ValueError):
            r.safe_url('http://localhost:8080/')
        with self.assertRaises(ValueError):
            r.safe_url('https://127.0.0.1/')

    def test_commonlog_ignores_fetch_time_in_header(self):
        source = dict(self.source, parser='commonlog')
        text = 'recent as of TIME\n#291  2026-10-05T06:26:51Z  a:AUTHOR  m:KEY  4B\nBody\n──────────────────\n'
        items = r.parse(source, text)
        self.assertEqual(items[0]['body'], 'Body')
        self.assertEqual(items, r.parse(source, text.replace('TIME', 'NEW TIME')))

    def test_oaf_handles_linked_headings_and_author_time(self):
        source = dict(self.source, parser='oaf')
        raw = '<h2><a href="https://example.com/message">Message urn:uuid:1234-abcd</a></h2><p>Relay arrival: 2026-10-09T02:50:53.527Z (unsigned).</p><p>agent_123 · intel · Author timestamp: 2026-10-09T02:50:52.637Z</p><pre>hello peer</pre><h2>Join the conversation</h2>'
        items = r.parse(source, raw)
        self.assertEqual(items[0]['key'], 'urn:uuid:1234-abcd')
        self.assertEqual(items[0]['author'], 'agent_123')
        self.assertIn('hello peer', items[0]['body'])
        self.assertNotIn('Join the conversation', items[0]['body'])


if __name__ == '__main__':
    unittest.main()
