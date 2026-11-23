"""Offline synthetic publisher responses only."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl
from market_intelligence import acquire_health as health

PUBLICATION = health.ORIGIN + '/resources/publications/star-ratings-quarterly-data-extract-october-2026'
WORKBOOK = health.ORIGIN + '/sites/default/files/2026-10/star-ratings-quarterly-data-extract-october-2026.xlsx'


def anchor(url):
    return ('<a href="' + url + '">Synthetic resource</a>').encode()


class Publisher:
    def __init__(self):
        workbook = openpyxl.Workbook()
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        self.routes = {health.COLLECTION: anchor(PUBLICATION), PUBLICATION: anchor(WORKBOOK),
                       WORKBOOK: stream.getvalue(), health.COPYRIGHT: b'<p>Publisher copyright terms.</p>'}
        self.calls = []

    def load_robots(self):
        self.calls.append('robots')

    def fetch(self, url, limit=None):
        if not self.calls:
            raise AssertionError('robots must precede navigation')
        self.calls.append(url)
        return self.routes[url], url


class AcquisitionTests(unittest.TestCase):
    def test_complete_immutable_runs_and_separate_permission(self):
        client = Publisher()
        with tempfile.TemporaryDirectory() as tmp:
            first = health.acquire(tmp, client)
            original = first.read_bytes()
            second = health.acquire(tmp, Publisher())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), original)
            item = json.loads(original)['files'][0]
            self.assertEqual(item['reference_period'], '2026-10')
            self.assertEqual(item['upload_month'], '2026-10')
            self.assertEqual(item['commercial_reuse'], health.PERMISSION)
            self.assertNotIn('licence', item)
            self.assertEqual(item['copyright_evidence']['bytes'], len(client.routes[health.COPYRIGHT]))
            self.assertEqual(client.calls[0], 'robots')

    def test_failure_retains_failed_manifest(self):
        client = Publisher()
        client.routes[WORKBOOK] = b'not a workbook'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(health.AcquisitionError):
                health.acquire(tmp, client)
            manifest = next(Path(tmp).glob('*/manifest.json'))
            self.assertEqual(json.loads(manifest.read_text())['status'], 'failed')

    def test_discovery_rejects_ambiguity_external_hosts_and_period_mismatch(self):
        for page in [b'', anchor(WORKBOOK.replace('www.health.gov.au', 'example.invalid')),
                     anchor(WORKBOOK.replace('october', 'july')),
                     anchor(WORKBOOK) + anchor(WORKBOOK.replace('2026-10/', '2026-11/'))]:
            with self.subTest(page=page), self.assertRaises(health.AcquisitionError):
                health.discover_workbook(page, PUBLICATION, '2026-10')
        newest, period = health.discover_publication(anchor(PUBLICATION) + anchor(PUBLICATION.replace('october', 'july')), health.COLLECTION)
        self.assertEqual((newest, period), (PUBLICATION, '2026-10'))

    def test_common_transport_enforces_bounds_host_and_robots(self):
        for kwargs in [dict(timeout=61), dict(attempts=3), dict(max_bytes=50_000_001)]:
            with self.assertRaises(ValueError):
                health.PublisherClient(**kwargs)
        client = health.PublisherClient()
        with self.assertRaises(health.AcquisitionError):
            client.fetch('https://example.invalid/workbook.xlsx')
        from urllib.robotparser import RobotFileParser
        client.robots = RobotFileParser()
        client.robots.parse(['User-agent: *', 'Disallow: /'])
        with patch.object(client.opener, 'open') as request:
            with self.assertRaises(health.AcquisitionError):
                client.fetch(WORKBOOK)
            request.assert_not_called()

    def test_cli_safe_failure(self):
        with patch.object(health, 'acquire', side_effect=OSError('private-path')), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(health.main(['--output-root', 'unused']), 1)
            self.assertNotIn('private-path', output.getvalue())
