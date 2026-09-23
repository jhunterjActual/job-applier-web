"""Synthetic contracts; never contact public providers or use user data."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import public_job_feeds as feeds
from operations import OperationCancelled


def job(title='Software Engineer', url='https://jobicy.com/jobs/123-software-engineer'):
    return {'jobTitle': title, 'url': url, 'companyName': 'Example', 'jobGeo': 'USA',
            'jobDescription': '<p>Build useful software.</p><script>hidden()</script>', 'jobType': ['full-time']}


class PublicFeedTests(unittest.TestCase):
    def test_plain_text_flushes_unterminated_ampersand(self):
        self.assertEqual('Financial Analyst, R&D', feeds.plain('Financial Analyst, R&D'))

    def test_wwr_retains_country_and_state_alongside_region(self):
        payload = '<rss><channel><item><title>Example: Analyst</title><link>https://weworkremotely.com/remote-jobs/example-analyst</link><region>Anywhere in the World</region><country>United States</country><state>California</state><description>Useful description</description></item></channel></rss>'
        self.assertEqual('Anywhere in the World | United States | California',
                         feeds.parse('weworkremotely', payload)[0]['location'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch = patch.object(feeds, '_directory', return_value=self.directory)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.clock = patch.object(feeds.time, 'time', return_value=100000)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def payload(self, rows=None):
        return json.dumps({'jobs': [job()] if rows is None else rows})

    def test_jobicy_metadata_plain_description_and_dedup(self):
        rows = feeds.parse('jobicy', self.payload([job(), job()]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['description'], 'Build useful software.')
        self.assertEqual(rows[0]['location'], 'USA')
        self.assertEqual(rows[0]['work_arrangement'], 'remote')
        self.assertEqual(rows[0]['employment_type'], 'full-time')

    def test_wwr_company_region_title(self):
        text = '<rss><channel><item><title>Example: Engineer</title><link>https://weworkremotely.com/remote-jobs/example-engineer</link><region>Europe</region><description>&lt;p&gt;Build&lt;/p&gt;</description><type>Contract</type></item></channel></rss>'
        row = feeds.parse('weworkremotely', text)[0]
        self.assertEqual((row['title'], row['company'], row['location']), ('Engineer', 'Example', 'Europe'))

    def test_entities_and_wrong_roots_rejected(self):
        for text in ('<!DOCTYPE rss [<!ENTITY a "boom">]><rss><channel/></rss>', '<html/>', '<rss/>'):
            with self.assertRaises(feeds.SourceError):
                feeds.parse('weworkremotely', text)

    def test_url_boundary(self):
        for url in ('http://jobicy.com/jobs/123-engineer', 'https://jobicy.com.evil/jobs/123-engineer',
                    'https://user@jobicy.com/jobs/123-engineer', 'https://jobicy.com:443/jobs/123-engineer',
                    'https://jobicy.com/jobs/%2e%2e', 'https://jobicy.com/jobs',
                    'https://weworkremotely.com/remote-jobs/x/../../other'):
            self.assertIsNone(feeds.canonical_url(url))
        self.assertEqual(feeds.canonical_url(job()['url'] + '?ref=test#x'), job()['url'])

    def test_invalid_job_is_not_false_empty_success(self):
        for payload in ('{}', '{"jobs":null}', '{"jobs":[{}]}', self.payload([job(url='https://evil.example/job')])):
            with self.assertRaises(feeds.SourceError):
                feeds.parse('jobicy', payload)
        self.assertEqual(feeds.parse('jobicy', self.payload([])), [])

    def test_cache_reused_across_calls_and_details_never_fetch(self):
        with patch.object(feeds, '_fetch', return_value=self.payload()) as fetch:
            feeds._snapshot('jobicy', lambda: None)
            feeds._snapshot('jobicy', lambda: None)
            detail = feeds.details_for_url(job()['url'])
            self.assertEqual(detail['company'], 'Example')
            fetch.assert_called_once()
        self.assertNotIn('keywords', (self.directory / 'jobicy.json').read_text())
        self.assertIsNone(feeds.details_for_url('https://jobicy.com/jobs/987-other'))

    def test_failure_cooldown_persisted_before_network(self):
        def fail(*args):
            self.assertIsNone(json.loads((self.directory / 'jobicy.json').read_text())['succeeded_at'])
            raise feeds.SourceError('network')
        with patch.object(feeds, '_fetch', side_effect=fail) as fetch:
            for _ in range(2):
                with self.assertRaises(feeds.SourceError):
                    feeds._snapshot('jobicy', lambda: None)
            fetch.assert_called_once()
            self.now.return_value += feeds.FAILURE_RETRY_SECONDS
            with self.assertRaises(feeds.SourceError):
                feeds._snapshot('jobicy', lambda: None)
            self.assertEqual(fetch.call_count, 2)

    def test_expired_snapshot_not_served_on_failure(self):
        with patch.object(feeds, '_fetch', return_value=self.payload()):
            feeds._snapshot('jobicy', lambda: None)
        self.now.return_value += feeds.REFRESH_SECONDS
        self.assertIsNone(feeds.details_for_url(job()['url']))
        with patch.object(feeds, '_fetch', side_effect=feeds.SourceError('network')):
            with self.assertRaises(feeds.SourceError):
                feeds._snapshot('jobicy', lambda: None)
        self.assertIsNone(feeds.details_for_url(job()['url']))

    def test_corrupt_and_future_cache_fail_closed_without_requests(self):
        path = self.directory / 'jobicy.json'
        for data in ('oops', json.dumps({'version':1, 'provider':'jobicy', 'attempted_at':100001, 'succeeded_at':None, 'payload':None})):
            path.write_text(data)
            with patch.object(feeds, '_fetch') as fetch:
                with self.assertRaises(feeds.SourceError):
                    feeds._snapshot('jobicy', lambda: None)
                fetch.assert_not_called()

    def test_cancel_does_not_turn_into_source_failure(self):
        with patch.object(feeds, '_fetch', side_effect=OperationCancelled()):
            with self.assertRaises(OperationCancelled):
                feeds._snapshot('jobicy', lambda: None)
        record = json.loads((self.directory / 'jobicy.json').read_text())
        self.assertIsNone(record['succeeded_at'])

    def test_title_and_geography_filters_health_empty_vs_failure(self):
        health = {}
        with patch.object(feeds, '_snapshot', side_effect=[feeds.parse('jobicy', self.payload()), feeds.SourceError('network')]):
            rows = feeds.collect(['contract'], ['remote'], health, lambda:None)
        self.assertEqual(rows, [])
        self.assertEqual(health['weworkremotely']['source_empty'], 1)
        self.assertEqual(health['jobicy']['source_failures'], 1)
        self.assertFalse(feeds.matches(feeds.parse('jobicy', self.payload())[0], ['software engineer'], ['Germany']))

    def test_transport_cap_and_fixed_endpoint(self):
        response = io.BytesIO(b'x' * (feeds.MAX_BYTES + 1))
        response.status = 200
        opener = Mock()
        opener.open.return_value = response
        with patch.object(feeds.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(feeds.SourceError, 'oversized_response'):
                feeds._fetch('jobicy', lambda:None)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, feeds.ENDPOINTS['jobicy'])
        self.assertNotIn('Authorization', request.headers)

    def test_live_lock_excludes_second_refresh(self):
        with feeds._lock(self.directory, 'jobicy'):
            with self.assertRaises(feeds.SourceError):
                with feeds._lock(self.directory, 'jobicy'):
                    self.fail('second lock acquired')


if __name__ == '__main__':
    unittest.main()
