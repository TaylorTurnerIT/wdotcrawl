import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from rmaint import RepoMaintainer
from wikidot import Wikidot, WikidotError


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.wd = Wikidot('http://spheresofpower.wikidot.com')
        self.wd.debug = False
        self.wd.delay = 0

    def sitemap(self, entries, since=0):
        xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        for loc, date in entries:
            xml += '<url><loc>{}</loc>{}</url>'.format(
                loc, '<lastmod>{}</lastmod>'.format(date) if date else '')
        xml += '</urlset>'
        with patch('wikidot.requests.get', return_value=Mock(status_code=200, text=xml)):
            return self.wd.get_pages_from_sitemap(since)

    def test_sitemap_normalizes_scheme_and_excludes_roots_and_foreign_hosts(self):
        base = 'spheresofpower.wikidot.com'
        entries = [(url, None) for url in [
            'http://' + base, 'https://' + base + '/',
            ' https://' + base + '/category:page?view=1#part ',
            'http://' + base + '/category:page',
            'https://' + base + '/nested/page/',
            'https://other.wikidot.com/foreign',
            'http://' + base + '.evil.test/foreign',
            'https://' + base + ':444/foreign',
            'ftp://' + base + '/foreign',
        ]]
        self.assertEqual(self.sitemap(entries), ['category:page', 'nested/page'])

    def test_sitemap_lastmod_and_deduplication(self):
        base = 'https://spheresofpower.wikidot.com/'
        self.assertEqual(self.sitemap([
            (base + 'old', '2020-01-01T00:00:00+00:00'),
            (base + 'new', '2026-10-08T00:00:00+00:00'),
            (base + 'new', None), (base + 'unknown', 'invalid'),
        ], 1700000000), ['new', 'unknown'])

    def test_unavailable_sitemap_falls_back(self):
        with patch('wikidot.requests.get', return_value=Mock(status_code=503)):
            self.assertIsNone(self.wd.get_pages_from_sitemap(1))

    def test_ajax_error_reports_module_id_and_response(self):
        response = Mock(status_code=200, text='{"status":"no_page","message":"Page missing"}')
        response.json.return_value = json.loads(response.text)
        with patch('wikidot.requests.request', return_value=response):
            with self.assertRaisesRegex(WikidotError, 'history/Test.*page_id=42.*no_page'):
                self.wd.query({'moduleName': 'history/Test', 'page_id': 42})

    def test_http_and_non_json_errors(self):
        for status, error in [(503, requests.HTTPError('unavailable')), (200, ValueError('bad JSON'))]:
            with self.subTest(status=status):
                response = Mock(status_code=status, text='upstream unavailable')
                if status == 503:
                    response.raise_for_status.side_effect = error
                else:
                    response.json.side_effect = error
                with patch('wikidot.requests.request', return_value=response):
                    with self.assertRaisesRegex(WikidotError, 'HTTP {}.*upstream unavailable'.format(status)):
                        self.wd.query({'moduleName': 'history/Test'})

    def test_ajax_success(self):
        response = Mock(status_code=200)
        response.json.return_value = {'status': 'ok', 'body': 'source', 'title': 'Title'}
        with patch('wikidot.requests.request', return_value=response):
            self.assertEqual(self.wd.queryex({}), ('source', 'Title'))

    def test_missing_id_never_requests_revisions(self):
        with patch('wikidot.requests.request') as request:
            with self.assertRaises(ValueError):
                self.wd.get_revisions(None, 10)
            request.assert_not_called()

    def test_page_lookup_404_and_http_failure(self):
        response = Mock(status_code=404)
        with patch('wikidot.requests.request', return_value=response):
            self.assertIsNone(self.wd.get_page_id('deleted'))
        response = Mock(status_code=503)
        response.raise_for_status.side_effect = requests.HTTPError('503')
        with patch('wikidot.requests.request', return_value=response):
            with self.assertRaises(requests.HTTPError):
                self.wd.get_page_id('unavailable')

    def test_missing_page_and_null_cache_do_not_block_valid_page(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'page_ids.json').write_text('{"missing": null}')
            wd = Mock()
            wd.get_page_id.side_effect = [None, 42]
            wd.get_revisions.return_value = [dict(id='7', date=100, user='user', comment='')]
            rm = RepoMaintainer(wd, directory)
            rm.buildRevisionList(['missing', 'valid'], since_time=1)
            wd.get_revisions.assert_called_once_with(42, 10000)
            self.assertEqual([r['page_name'] for r in rm.wrevs], ['valid'])
            self.assertEqual(json.loads(Path(directory, 'page_ids.json').read_text())['valid'], 42)

    def test_empty_sitemap_does_not_fetch_full_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            wd = Mock()
            wd.get_pages_from_sitemap.return_value = []
            rm = RepoMaintainer(wd, directory)
            rm.buildRevisionList(since_time=1)
            wd.list_pages.assert_not_called()
            wd.get_revisions.assert_not_called()
            self.assertEqual(rm.wrevs, [])

    def test_upstream_revision_error_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            wd = Mock()
            wd.get_page_id.return_value = 42
            wd.get_revisions.side_effect = WikidotError('permission denied')
            rm = RepoMaintainer(wd, directory)
            with self.assertRaisesRegex(WikidotError, "Page 'private'.*permission denied"):
                rm.buildRevisionList(['private'], since_time=1)
            self.assertFalse(Path(directory, '.wrevs').exists())


if __name__ == '__main__':
    unittest.main()
