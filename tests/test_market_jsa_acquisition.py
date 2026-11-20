"""Offline publisher HTML/HTTP/workbook fixtures; no external requests or DB."""
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

import openpyxl

from market_intelligence import acquire_jsa as jsa
from market_intelligence.demand import parse_demand
from market_intelligence.parsers import SourceError
from market_intelligence.resources import verified_resource


REOS = jsa.ORIGIN + '/sites/default/files/2026-11/recruitment_insights_report_-_october_2026_data_file.xlsx'
TNV = jsa.ORIGIN + '/sites/default/files/2026-08/total_new_vacancies_may_2026.xlsx'


def html(*links, licence=True):
    if licence:
        links += (jsa.LICENCE_URL,)
    return ('<html>' + ''.join('<a href="' + link + '">Download</a>' for link in links) + '</html>').encode()


def workbook(dataset):
    wb = openpyxl.Workbook()
    if dataset == 'jsa_tnv':
        ws = wb.active
        ws.title = 'Data_1'
        ws.append(['region_level', 'region_code', 'region_title', 'date', 'tnv'])
        ws.append(['national', 'AUS', 'Australia', datetime(2026, 5, 1), 100])
        ws = wb.create_sheet('Data_2')
        ws.append(['date', 'occupation_code', 'occupation_level', 'occupation_title', 'tnv'])
        ws.append([datetime(2026, 5, 1), '2', 'anzsco1', 'Professionals', 50])
    else:
        wb.remove(wb.active)
        for name in ['1.1 Recruitment Rate (M)', '2.1 Increased Staff', '2.2 Decreased Staff',
                     '3.1 Recruitment Difficulty (M)', '4.1 Expected Increase (M)', '4.3 Expected Decrease (M)']:
            ws = wb.create_sheet(name)
            if name.startswith('3.1'):
                ws.append([None, None, 'Capital Cities', 'Rest of State areas'])
                ws.append(['Month', 'All recruiting employers'])
                ws.append([datetime(2026, 10, 1), .45, 'n.p.', 0])
            else:
                ws.append(['Month', 'All employers', 'Capital Cities', 'Rest of State areas',
                           '5 to 19 employees', '20 employees or more'])
                ws.append([datetime(2026, 10, 1), .51, .48, 'n.p.', 0, .72])
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, url, length=None, status=200):
        super().__init__(body)
        self.url, self.status = url, status
        self.headers = {} if length is None else {'Content-Length': str(length)}

    def geturl(self):
        return self.url


class FixturePublisher:
    """Replaces only the network opener, exercising the real HTTP client."""
    def __init__(self):
        self.payloads = {'jsa_reos': workbook('jsa_reos'), 'jsa_tnv': workbook('jsa_tnv')}
        self.routes = {jsa.ORIGIN + '/robots.txt': b'User-agent: *\nAllow: /\n',
                       jsa.ORIGIN + jsa.DATASETS['jsa_reos'][0]: html(REOS),
                       jsa.ORIGIN + jsa.DATASETS['jsa_tnv'][0]: html(TNV),
                       REOS: self.payloads['jsa_reos'], TNV: self.payloads['jsa_tnv']}
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        value = self.routes[request.full_url]  # unconfigured requests always fail offline
        if isinstance(value, HTTPError):
            raise HTTPError(request.full_url, value.code, value.msg, value.hdrs, io.BytesIO())
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url, len(value))

    def client(self, **kwargs):
        client = jsa.PublisherClient(**kwargs)
        client.opener = self
        return client


class JSAAcquisitionTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(jsa.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_both_manifests_verify_and_parse_with_separate_periods(self):
        fixture = FixturePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = jsa.acquire(tmp, ['jsa_reos', 'jsa_tnv'], client=fixture.client())
            data = json.loads(manifest.read_text())
            self.assertEqual(data['status'], 'complete')
            for entry, period, count in zip(data['files'], [date(2026, 10, 1), date(2026, 5, 1)], [28, 2]):
                dataset = entry['dataset']
                path, meta = verified_resource(manifest, dataset, lambda f: f['path'].endswith('.xlsx'))
                rows, latest = parse_demand(path, dataset)
                self.assertEqual((len(rows), latest), (count, period))
                self.assertEqual(entry['reference_month'], period.strftime('%Y-%m'))
                self.assertNotEqual(entry['upload_month'], entry['reference_month'])
                self.assertEqual(meta['sha256'], hashlib.sha256(fixture.payloads[dataset]).hexdigest())
                self.assertIsNotNone(datetime.fromisoformat(entry['page_observed_at']).tzinfo)
                self.assertIsNotNone(meta['retrieved_at'].tzinfo)
                self.assertEqual(entry['resolved_url'], entry['url'])
            self.assertEqual(fixture.requests[0][0].full_url, jsa.ORIGIN + '/robots.txt')
            self.assertTrue(all(req.get_header('User-agent') == jsa.USER_AGENT for req, _ in fixture.requests))

    def test_unchanged_has_new_compatible_manifest_and_preserves_old_bytes(self):
        fixture = FixturePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            first = jsa.acquire(tmp, ['jsa_tnv'], client=fixture.client())
            before = {p: p.read_bytes() for p in first.parent.iterdir()}
            second = jsa.acquire(tmp, ['jsa_tnv'], [first], fixture.client())
            self.assertNotEqual(first.parent, second.parent)
            second_data = json.loads(second.read_text())
            self.assertEqual(second_data['acquisition_status'], 'unchanged')
            self.assertEqual(second_data['files'][0]['status'], 'complete')
            path, _ = verified_resource(second, 'jsa_tnv', lambda f: True)
            self.assertEqual(path.read_bytes(), fixture.payloads['jsa_tnv'])
            self.assertEqual(before, {p: p.read_bytes() for p in first.parent.iterdir()})

    def test_partial_failure_does_not_promote_or_modify_success(self):
        fixture = FixturePublisher()
        with tempfile.TemporaryDirectory() as tmp:
            first = jsa.acquire(tmp, ['jsa_reos'], client=fixture.client())
            before = first.read_bytes()
            fixture.routes[TNV] = b'<html>not a workbook</html>'
            with self.assertRaisesRegex(jsa.AcquisitionError, 'not an XLSX'):
                jsa.acquire(tmp, ['jsa_reos', 'jsa_tnv'], [first], fixture.client())
            # Resolve both sides: Windows runners may expose the temp folder as a short (8.3) path.
            failed = next(p for p in Path(tmp).resolve().glob('*/manifest.json') if p.resolve() != first.resolve())
            self.assertEqual(json.loads(failed.read_text())['status'], 'failed')
            self.assertEqual(first.read_bytes(), before)
            with self.assertRaisesRegex(SourceError, 'incomplete'):
                verified_resource(failed, 'jsa_reos', lambda f: True)

    def test_newest_reference_not_upload_month_and_duplicate_anchor(self):
        older = REOS.replace('2026-11/', '2026-12/').replace('october', 'september')
        body = html(older, REOS, REOS + '#download', '/sites/default/files/2026-11/ivi_october_2026.xlsx')
        self.assertEqual(jsa.discover(body, jsa.ORIGIN, 'jsa_reos'), (REOS, '2026-10', '2026-11'))
        self.assertEqual(jsa.discover(html(TNV), jsa.ORIGIN, 'jsa_tnv'), (TNV, '2026-05', '2026-08'))

    def test_relative_case_insensitive_filename_and_future_year(self):
        link = '/sites/default/files/2027-01/total_new_vacancies_December_2026.xlsx'
        self.assertEqual(jsa.discover(html(link), jsa.ORIGIN, 'jsa_tnv'),
                         (jsa.ORIGIN + link, '2026-12', '2027-01'))

    def test_optional_data_file_suffix_does_not_hide_newer_release(self):
        newer = REOS.replace('october', 'november').replace('_data_file', '')
        self.assertEqual(jsa.discover(html(REOS, newer), jsa.ORIGIN, 'jsa_reos')[0], newer)
        tnv = TNV.replace('.xlsx', '_data_file.xlsx')
        self.assertEqual(jsa.discover(html(tnv), jsa.ORIGIN, 'jsa_tnv')[0], tnv)

    def test_zero_ambiguous_wrong_slug_and_official_host_required(self):
        cases = [(html(), 'zero'), (html(REOS), 'zero'),
                 (html(TNV, TNV.replace('2026-08/', '2026-09/')), 'multiple'),
                 (html(TNV.replace(jsa.ORIGIN, 'https://elsewhere.example')), 'Non-official'),
                 (html(TNV.replace('https:', 'http:')), 'Non-official'),
                 (html(TNV + '?version=1'), 'Ambiguous')]
        for body, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(jsa.AcquisitionError, message):
                jsa.discover(body, jsa.ORIGIN, 'jsa_tnv')

    def test_redirects_rejected_before_request(self):
        handler = jsa.OfficialRedirects()
        for url in ['http://www.jobsandskills.gov.au/x', 'https://elsewhere.example/x',
                    'https://user:secret@www.jobsandskills.gov.au/x', jsa.ORIGIN + ':444/x']:
            with self.subTest(url=url), self.assertRaises(jsa.AcquisitionError):
                handler.redirect_request(Request(TNV), None, 302, 'Found', {}, url)
        handler = jsa.OfficialRedirects(lambda url: False)
        with self.assertRaisesRegex(jsa.AcquisitionError, 'robots'):
            handler.redirect_request(Request(TNV), None, 302, 'Found', {}, REOS)

    def test_robots_denial_and_unavailable_policy_fail_closed(self):
        fixture = FixturePublisher()
        fixture.routes[jsa.ORIGIN + '/robots.txt'] = b'User-agent: *\nDisallow: /data/\n'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(jsa.AcquisitionError, 'robots'):
                jsa.acquire(tmp, ['jsa_tnv'], client=fixture.client())
            self.assertEqual(len(fixture.requests), 1)
        fixture.routes[jsa.ORIGIN + '/robots.txt'] = URLError('fixture failure')
        with patch.object(jsa.time, 'sleep'), tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(jsa.AcquisitionError, 'publisher did not respond'):
                jsa.acquire(tmp, ['jsa_tnv'], client=fixture.client())
        self.assertTrue(all(req.full_url.endswith('/robots.txt') for req, _ in fixture.requests))

    def test_timeout_two_attempts_backoff_and_no_extra_requests(self):
        fixture = FixturePublisher()
        fixture.routes[TNV] = TimeoutError()
        with patch.object(jsa.time, 'sleep') as sleep:
            with self.assertRaisesRegex(jsa.AcquisitionError, 'publisher did not respond'):
                fixture.client(timeout=1).fetch(TNV)
        self.assertEqual(len(fixture.requests), 2)
        self.assertTrue(all(timeout == 1 for _, timeout in fixture.requests))
        sleep.assert_called_once_with(1)

    def test_transient_status_retries_but_429_and_404_do_not(self):
        for code, attempts in [(503, 2), (429, 1), (404, 1)]:
            fixture = FixturePublisher()
            fixture.routes[TNV] = HTTPError(TNV, code, 'fixture', {}, None)
            with patch.object(jsa.time, 'sleep'), self.assertRaises(jsa.AcquisitionError):
                fixture.client().fetch(TNV)
            self.assertEqual(len(fixture.requests), attempts)

    def test_declared_size_stream_size_and_truncation_bounded(self):
        for payload, length, message in [(b'abc', 100, 'size limit'), (b'abcde', None, 'size limit'),
                                         (b'ab', 3, 'length mismatch')]:
            client = jsa.PublisherClient(max_bytes=4)
            with patch.object(client.opener, 'open', return_value=Response(payload, TNV, length)):
                with self.assertRaisesRegex(jsa.AcquisitionError, message):
                    client.fetch(TNV)

    def test_read_deadline(self):
        client = jsa.PublisherClient(timeout=1)
        with patch.object(client.opener, 'open', side_effect=lambda *a, **kw: Response(b'abc', TNV)), \
                patch.object(jsa.time, 'monotonic', side_effect=[0, 2, 3, 5]), \
                patch.object(jsa.time, 'sleep'):
            with self.assertRaisesRegex(jsa.AcquisitionError, 'publisher did not respond'):
                client.fetch(TNV)

    def test_robots_pacing_is_honoured_and_html_is_rejected(self):
        fixture = FixturePublisher()
        fixture.routes[jsa.ORIGIN + '/robots.txt'] = b'User-agent: *\nCrawl-delay: 3\nRequest-rate: 1/5\n'
        client = fixture.client()
        client.load_robots()
        with patch.object(jsa.time, 'sleep') as sleep:
            client.fetch(TNV)
            sleep.assert_called_once_with(5)
        fixture.routes[jsa.ORIGIN + '/robots.txt'] = b'<html>error page</html>'
        with self.assertRaisesRegex(jsa.AcquisitionError, 'instead of robots'):
            fixture.client().load_robots()

    def test_corrupt_and_non_workbook_zip_rejected(self):
        with self.assertRaisesRegex(jsa.AcquisitionError, 'not an XLSX'):
            jsa.check_workbook(b'PK\x03\x04not a zip')
        import zipfile
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('unrelated.txt', b'fixture')
        with self.assertRaisesRegex(jsa.AcquisitionError, 'not an XLSX'):
            jsa.check_workbook(output.getvalue())

    def test_licence_copyright_discovery_and_missing_evidence(self):
        fixture = FixturePublisher()
        copyright_url = jsa.ORIGIN + '/copyright'
        fixture.routes[copyright_url] = html(jsa.LICENCE_URL, licence=False)
        client = fixture.client()
        self.assertEqual(jsa.licence_evidence(client, html('/copyright', licence=False), TNV), copyright_url)
        for body in [html(licence=False), html('/copyright', '/licensing', licence=False)]:
            with self.assertRaisesRegex(jsa.AcquisitionError, 'missing or ambiguous'):
                jsa.licence_evidence(client, body, TNV)
        fixture.routes[copyright_url] = html(licence=False)
        with self.assertRaisesRegex(jsa.AcquisitionError, 'does not link'):
            jsa.licence_evidence(client, html('/copyright', licence=False), TNV)

    def test_previous_failed_manifest_and_invalid_options_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prior = Path(tmp) / 'old.json'
            prior.write_text('{"status": "failed"}')
            with self.assertRaisesRegex(jsa.AcquisitionError, 'not complete'):
                jsa.acquire(tmp, ['jsa_tnv'], [prior], FixturePublisher().client())
            self.assertEqual(list(Path(tmp).iterdir()), [prior])
        for kwargs in [{'timeout': 0}, {'timeout': 61}, {'attempts': 3}, {'max_bytes': 50_000_001}]:
            with self.assertRaises(ValueError):
                jsa.PublisherClient(**kwargs)

    def test_cli_offline_help_success_and_safe_failure(self):
        fixture = FixturePublisher()
        with tempfile.TemporaryDirectory() as tmp, patch.object(jsa, 'PublisherClient', return_value=fixture.client()), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(jsa.main(['--dataset', 'jsa_tnv', '--output-root', tmp]), 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'downloaded')
        with tempfile.TemporaryDirectory() as tmp, patch.object(jsa, 'acquire', side_effect=OSError('private fixture data')), \
                patch('sys.stderr', new_callable=io.StringIO) as output:
            self.assertEqual(jsa.main(['--dataset', 'jsa_tnv', '--output-root', tmp]), 1)
            self.assertNotIn('private fixture data', output.getvalue())


if __name__ == '__main__':
    unittest.main()
