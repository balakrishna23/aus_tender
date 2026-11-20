"""Offline APS acquisition fixtures. Every HTTP request goes through a fake opener."""
from datetime import date, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request
import zipfile

import openpyxl

from market_intelligence import acquire_aps as aps
from market_intelligence import acquire_common as common
from market_intelligence.aps import parse_aps, verified_workbook
from market_intelligence.parsers import SourceError
from market_intelligence.resources import verified_resource
from test_market_aps import workbook

PAGE = aps.ORIGIN + '/employment-data/aps-employment-data-30-june-2026'
FILE = aps.ORIGIN + '/sites/default/files/2026-09/APS%20Employment%20Release%20Tables%20-%2030%20June%202026.xlsx'
OLD = FILE.replace('2026', '2025')


def html(*links):
    return ('<html>' + ''.join('<a href="' + link + '">Download</a>' for link in links) + '</html>').encode()


def payload(folder, year=2026, restriction=None, changed_layout=False):
    path = workbook(Path(folder) / 'fixture.xlsx')
    wb = openpyxl.load_workbook(path)
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.value = cell.value.replace('2025', str(year)).replace('2024', str(year - 1))
                elif cell.value in (2024, 2025):
                    cell.value += year - 2025
    if restriction:
        wb['Table of Contents'].append([restriction])
    if changed_layout:
        wb['Table 2']['B3'] = 'Changed category'
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url
        self.headers = {'Content-Length': str(len(body))}

    def geturl(self):
        return self.url


class FixturePublisher:
    def __init__(self, data):
        self.routes = {aps.ORIGIN + '/robots.txt': b'User-agent: *\nAllow: /\n',
                       PAGE: html(OLD, FILE), aps.COPYRIGHT_URL: html(aps.LICENCE_URL), FILE: data}
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        value = self.routes[request.full_url]
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url)

    def client(self):
        client = aps.PublisherClient(timeout=1)
        client.opener = self
        return client


class APSAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Resolve before constructing any fixture paths (Windows 8.3 temp aliases).
        self.root = Path(self.tmp.name).resolve()
        self.data = payload(self.root)
        self.publisher = FixturePublisher(self.data)
        sleep = patch.object(common.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def acquire(self, **kwargs):
        return aps.acquire(self.root / 'runs with spaces', client=self.publisher.client(), **kwargs)

    def assert_failed(self, message):
        with self.assertRaisesRegex(aps.AcquisitionError, message):
            self.acquire()
        files = list((self.root / 'runs with spaces').glob('*/manifest.json'))
        self.assertEqual(len(files), 1)
        manifest = json.loads(files[0].read_text())
        self.assertEqual((manifest['status'], manifest['files'], manifest['error']), ('failed', [], 'acquisition_failed'))

    def test_complete_importable_new_run_unchanged_and_evidence(self):
        manifest = self.acquire()
        data = json.loads(manifest.read_text())
        item = data['files'][0]
        self.assertEqual(data['status'], 'complete')
        self.assertEqual((item['reference_date'], item['upload_month']), ('2026-06-30', '2026-09'))
        self.assertNotEqual(item['reference_date'], item['retrieved_at'][:10])
        self.assertIsNotNone(datetime.fromisoformat(item['retrieved_at']).tzinfo)
        path, meta = verified_resource(manifest, aps.DATASET, lambda f: f['path'].endswith('.xlsx'))
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(meta['download_url'], FILE)
        self.assertEqual(verified_workbook(manifest)[1]['download_url'], FILE)
        self.assertEqual(parse_aps(path)[2:], (date(2026, 6, 30), date(2025, 6, 30)))
        for entry in item['licence_evidence']:
            evidence = Path(entry['path']).resolve(strict=True)
            self.assertTrue(evidence.is_relative_to(manifest.parent))
            self.assertEqual(hashlib.sha256(evidence.read_bytes()).hexdigest(), entry['sha256'])
        second = self.acquire(previous_manifests=[manifest])
        self.assertNotEqual(second.parent, manifest.parent)
        self.assertEqual(json.loads(second.read_text())['acquisition_status'], 'unchanged')
        self.assertTrue(all(req.headers['User-agent'] == aps.USER_AGENT for req, _ in self.publisher.requests))
        self.assertFalse(any(req.full_url == OLD for req, _ in self.publisher.requests))

    def test_discovery_requested_newest_duplicates_and_2025_compatibility(self):
        self.assertEqual(aps.discover(html(OLD, FILE, FILE + '#download'), PAGE)[0], FILE)
        old_page = PAGE.replace('2026', '2025')
        self.assertEqual(aps.discover(html(OLD, FILE), old_page, 2025)[0], OLD)
        self.publisher.routes[old_page] = html(OLD)
        self.publisher.routes[OLD] = payload(self.root, 2025)
        m = self.acquire(year=2025)
        self.assertEqual(parse_aps(verified_workbook(m)[0])[2], date(2025, 6, 30))

    def test_missing_multiple_wrong_year_queries_and_unsafe_links(self):
        for body in [html(), html(OLD), html(FILE, FILE.replace('2026-09/', '2026-10/')),
                     html(FILE + '?download=1'), html(FILE.replace('https:', 'http:')),
                     html(FILE.replace(aps.ORIGIN, 'https://elsewhere.example'))]:
            with self.subTest(body=body), self.assertRaises(aps.AcquisitionError):
                aps.discover(body, PAGE, 2026)
        with self.assertRaisesRegex(aps.AcquisitionError, 'disagree'):
            aps.discover(html(FILE), PAGE.replace('2026', '2025'))

    def test_robots_denial(self):
        self.publisher.routes[aps.ORIGIN + '/robots.txt'] = b'User-agent: *\nDisallow: /employment-data/\n'
        self.assert_failed('robots')
        self.assertEqual(len(self.publisher.requests), 1)

    def test_source_unavailable_and_bounded_attempts(self):
        self.publisher.routes[PAGE] = URLError('fixture')
        self.assert_failed('bounded attempts')
        self.assertEqual([r.full_url for r, _ in self.publisher.requests], [aps.ORIGIN + '/robots.txt', PAGE, PAGE])
        self.assertTrue(all(timeout == 1 for _, timeout in self.publisher.requests))

    def test_robots_unavailable(self):
        self.publisher.routes[aps.ORIGIN + '/robots.txt'] = TimeoutError()
        self.assert_failed('bounded attempts')
        self.assertEqual(len(self.publisher.requests), 2)
        self.assertTrue(all(r.full_url.endswith('/robots.txt') for r, _ in self.publisher.requests))

    def test_redirect_security_before_following(self):
        handler = aps.OfficialRedirects()
        for target in ['https://elsewhere.example/data', aps.ORIGIN.replace('https:', 'http:') + '/x',
                       'https://user:password@www.apsc.gov.au/x', aps.ORIGIN + ':444/x']:
            with self.subTest(target=target), self.assertRaises(aps.AcquisitionError):
                handler.redirect_request(Request(FILE), None, 302, 'Found', {}, target)
        with self.assertRaisesRegex(aps.AcquisitionError, 'robots'):
            aps.OfficialRedirects(lambda url: False).redirect_request(Request(FILE), None, 302, 'Found', {}, FILE)

    def test_copyright_required_even_with_cc_on_release(self):
        self.publisher.routes[PAGE] = html(FILE, aps.LICENCE_URL)
        self.publisher.routes[aps.COPYRIGHT_URL] = html()
        self.assert_failed('copyright page')
        self.assertFalse(any(req.full_url == FILE for req, _ in self.publisher.requests))

    def test_workbook_licence_exception(self):
        self.publisher.routes[FILE] = payload(self.root, restriction='Table 2: All rights reserved')
        self.assert_failed('scope or exceptions')

    def test_release_licence_uncertainty(self):
        self.publisher.routes[PAGE] = html(FILE, 'https://creativecommons.org/licenses/by-nc/4.0/')
        self.assert_failed('ambiguous')

    def test_copyright_unavailable(self):
        self.publisher.routes[aps.COPYRIGHT_URL] = TimeoutError()
        self.assert_failed('bounded attempts')
        self.assertFalse(any(req.full_url == FILE for req, _ in self.publisher.requests))

    def test_copyright_scope_restriction(self):
        self.publisher.routes[aps.COPYRIGHT_URL] = html(aps.LICENCE_URL) + b'<p>Data not covered under this licence</p>'
        self.assert_failed('scope or exceptions')

    def test_workbook_hyperlink_and_comment_exceptions(self):
        from openpyxl.comments import Comment
        wb = openpyxl.load_workbook(io.BytesIO(self.data))
        ws = wb['Table of Contents']
        ws['A1'] = 'Table licence'
        ws['A1'].hyperlink = 'https://creativecommons.org/licenses/by-nc/4.0/'
        output = io.BytesIO()
        wb.save(output)
        with self.assertRaisesRegex(aps.AcquisitionError, 'scope or exceptions'):
            aps.workbook_licence_statements(output.getvalue())
        ws['A1'].hyperlink = None
        ws['A1'].comment = Comment('All rights reserved', 'Fixture publisher')
        output = io.BytesIO()
        wb.save(output)
        wb.close()
        with self.assertRaisesRegex(aps.AcquisitionError, 'scope or exceptions'):
            aps.workbook_licence_statements(output.getvalue())

    def test_previous_manifest_failure_does_not_create_run(self):
        prior = self.root / 'prior.json'
        for data in [{'status': 'failed'}, {'status': 'complete', 'files': [
                {'dataset': aps.DATASET, 'status': 'complete', 'sha256': 'invalid'}]}]:
            prior.write_text(json.dumps(data))
            with self.assertRaises(aps.AcquisitionError):
                self.acquire(previous_manifests=[prior])
            self.assertFalse((self.root / 'runs with spaces').exists())

    def test_invalid_xlsx(self):
        self.publisher.routes[FILE] = b'<html>Not a workbook</html>'
        self.assert_failed('not an XLSX')

    def test_checksum_failure_in_zip(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('[Content_Types].xml', 'unique fixture marker')
            archive.writestr('xl/workbook.xml', 'fixture')
        corrupt = stream.getvalue().replace(b'unique fixture marker', b'broken fixture marker')
        self.publisher.routes[FILE] = corrupt
        self.assert_failed('checksum failed')

    def test_verified_import_checksum_and_reference_failure(self):
        m = self.acquire()
        data = json.loads(m.read_text())
        data['files'][0]['reference_date'] = '2025-06-30'
        m.write_text(json.dumps(data))
        with self.assertRaisesRegex(SourceError, 'reference date'):
            verified_workbook(m)
        data['files'][0]['reference_date'] = '2026-06-30'
        m.write_text(json.dumps(data))
        Path(data['files'][0]['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(SourceError, 'checksum or size'):
            verified_workbook(m)

    def test_changed_layout_still_fails_closed(self):
        self.publisher.routes[FILE] = payload(self.root, changed_layout=True)
        m = self.acquire()
        with self.assertRaisesRegex(SourceError, 'columns changed'):
            verified_workbook(m)

    def test_resolved_relative_paths_and_traversal(self):
        # Manifest paths are resolved absolute paths, independently of cwd.
        original = Path.cwd()
        try:
            os.chdir(self.root)
            m = aps.acquire(Path('relative runs') / '..' / 'runs with spaces', client=self.publisher.client())
        finally:
            os.chdir(original)
        data = json.loads(m.read_text())
        self.assertEqual(Path(data['files'][0]['path']), Path(data['files'][0]['path']).resolve())
        outside = self.root / 'outside.xlsx'
        outside.write_bytes(self.data)
        data['files'][0]['path'] = str(outside.resolve())
        m.write_text(json.dumps(data))
        with self.assertRaisesRegex(SourceError, 'escaped'):
            verified_workbook(m)

    def test_cli_success_safe_failure_and_options(self):
        with patch.object(aps, 'PublisherClient', return_value=self.publisher.client()), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(aps.main(['--output-root', str(self.root / 'cli')]), 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'downloaded')
        with patch.object(aps, 'acquire', side_effect=OSError('private fixture')), patch('sys.stderr', new_callable=io.StringIO) as output:
            self.assertEqual(aps.main(['--output-root', str(self.root)]), 1)
            self.assertNotIn('private fixture', output.getvalue())
        for year in [True, 0, 1999, 10000, '2026']:
            with self.assertRaises(ValueError):
                self.acquire(year=year)


if __name__ == '__main__':
    unittest.main()
