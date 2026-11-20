"""State contract awards: WA CSVs fetched and tagged, QLD files imported by header words."""
import os
import io
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import state_contracts as sc  # noqa: E402

WA_CSV = ('﻿Public_Authority,Title,Reference_Number,Description,Procurement_Method,UNSPSC_Code_1,'
          'Award_Date,Commencement_Date,Initial_Expiry_Date,Final_Expiry_Date,Original_Contract_Value,'
          'CUA_Number,Panel_Contract,Supplier_Name,Supplier_Address\n'
          'Department of Finance,ICT Support Services,DOF1,Service desk,Open,81112200,21/02/2024,26/02/2024,'
          '20/02/2026,20/02/2028,344868,,No,Acme IT Pty Ltd,"1 Hay St, Perth, 6000"\n'
          'Art Gallery,Offsite storage,AG1,Storage,Direct Sourcing,78131800,19/10/2023,,,,5641100,,No,IAS Logistics,x\n')


def wa_http(size=100):
    search = Mock(status_code=200, headers={'Content-Type': 'application/json'}, content=b'{"result":{}}')
    search.json.return_value = {'result': {'results': [{
        'title': 'Tenders WA Contract Award Details 2023-24 Financial Year', 'metadata_modified': '2026-09-18',
        'license_title': 'Creative Commons Attribution 4.0', 'organization': {'title': 'Treasury WA'},
        'resources': [{'format': '.csv', 'url': 'https://x/download/fy2324.csv', 'size': size,
                       'last_modified': '2026-09-18T00:00'}]}]}}
    file = Mock(status_code=200, headers={'Content-Type': 'text/csv'}, content=WA_CSV.encode('utf-8'))
    http = Mock()
    http.get.side_effect = lambda url, **kw: search if 'package_search' in url else file
    return http


class StateContractsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def test_wa_rows_are_mapped_tagged_and_an_unchanged_file_is_skipped(self):
        with closing(sc.connect()) as conn:
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 2)
            row = conn.execute("SELECT agency, supplier, value, award_date, end_date, unspsc, is_ict "
                               "FROM contracts WHERE reference='DOF1'").fetchone()
            self.assertEqual(row, ('Department of Finance', 'Acme IT Pty Ltd', 344868.0, '2024-02-21',
                                   '2028-02-20', '81112200', 1))
            self.assertEqual(conn.execute("SELECT is_ict FROM contracts WHERE reference='AG1'").fetchone(), (0,))
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 0)
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(size=101), sleep=lambda s: None), 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM contracts').fetchone(), (2,))

    def test_invalid_downloads_are_not_stored_and_next_run_retries(self):
        header, first, _ = WA_CSV.splitlines()
        cases = [
            (202, {}, b'', 'HTTP 202'),
            (204, {}, b'', 'HTTP 204'),
            (500, {}, b'upstream error', 'HTTP 500'),
            (200, {}, b' \r\n', 'empty response'),
            (200, {}, b'wrong,headers\na,b\n', 'invalid WA CSV header'),
            (200, {}, (header + '\r\n').encode('utf-8'), 'no data rows'),
            (200, {}, (header + '\n' + ',' * 14 + '\n').encode('utf-8'), 'no data rows'),
            (200, {}, (header + '\nshort,row\n').encode('utf-8'), 'width mismatch'),
            (200, {}, (header + '\n' + first + ',extra\n').encode('utf-8'), 'width mismatch'),
            (200, {}, b'\xff\xfe', 'download failed'),
        ]
        for status, headers, body, message in cases:
            with self.subTest(message=message, status=status), closing(sc.connect()) as conn:
                # Each successful retry below is rolled back for the next fixture.
                conn.execute('DELETE FROM contracts')
                conn.execute('DELETE FROM sources')
                conn.commit()
                http = wa_http()
                response = http.get('https://x/download/fy2324.csv')
                response.status_code, response.headers, response.content = status, headers, body
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(sc.fetch_wa(conn, http=http, sleep=lambda s: None), 0)
                self.assertIn(message, output.getvalue())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM contracts').fetchone()[0], 0)
                self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 2)

    def test_challenges_stop_requests_and_report_remaining_files(self):
        cases = [
            (202, {'X-Amzn-Waf-Action': 'challenge'}, b''),
            (200, {'Content-Type': 'text/html; charset=utf-8'}, b'challenge'),
            (200, {}, b'\xef\xbb\xbf \r\n<HTML>challenge</HTML>'),
            (200, {}, b'<!DOCTYPE html>challenge'),
        ]
        for status, headers, body in cases:
            with self.subTest(status=status, headers=headers), closing(sc.connect()) as conn:
                http = wa_http()
                search = http.get(sc.WA_SEARCH)
                response = http.get('https://x/download/fy2324.csv')
                response.status_code, response.headers, response.content = status, headers, body
                resources = search.json.return_value['result']['results'][0]['resources']
                resources.append(dict(resources[0], url='https://x/download/next.csv'))
                http.reset_mock()
                sleep = Mock()
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(sc.fetch_wa(conn, http=http, sleep=sleep), 0)
                self.assertEqual(http.get.call_count, 2)  # Search, then only the first file.
                sleep.assert_called_once_with(2)
                self.assertIn('publisher bot challenge; not stored', output.getvalue())
                self.assertIn('files not loaded: fy2324.csv, next.csv', output.getvalue())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)

    def test_zero_contract_source_is_retried_with_unchanged_metadata(self):
        with closing(sc.connect()) as conn:
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 2)
            conn.execute('DELETE FROM contracts')
            conn.execute('UPDATE sources SET rows=0')
            conn.commit()
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 2)
            # Actual contract count is checked even if metadata claims a successful load.
            conn.execute('DELETE FROM contracts')
            conn.commit()
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None), 2)
            http = wa_http()
            http.reset_mock()
            self.assertEqual(sc.fetch_wa(conn, http=http, sleep=lambda s: None), 0)
            self.assertEqual(http.get.call_count, 1)

    def test_failed_refresh_preserves_prior_contracts_and_source_metadata(self):
        with closing(sc.connect()) as conn:
            sc.fetch_wa(conn, http=wa_http(), sleep=lambda s: None)
            before = conn.execute('SELECT * FROM sources').fetchall()
            http = wa_http(size=101)
            http.get('https://x/download/fy2324.csv').content = b''
            self.assertEqual(sc.fetch_wa(conn, http=http, sleep=lambda s: None), 0)
            self.assertEqual(conn.execute('SELECT * FROM sources').fetchall(), before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM contracts').fetchone()[0], 2)
            self.assertEqual(sc.fetch_wa(conn, http=wa_http(size=101), sleep=lambda s: None), 2)

    def test_catalogue_challenge_fails_before_any_resource_requests(self):
        with closing(sc.connect()) as conn:
            http = wa_http()
            search = http.get(sc.WA_SEARCH)
            search.headers = {'x-amzn-waf-action': 'challenge'}
            search.status_code, search.content = 202, b''
            http.reset_mock()
            with self.assertRaisesRegex(sc.PublisherChallenge, 'publisher bot challenge; not stored'):
                sc.fetch_wa(conn, http=http, sleep=lambda s: None)
            self.assertEqual(http.get.call_count, 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 0)

    def test_qld_file_columns_are_matched_by_header_words(self):
        folder = self.tmp / 'qld'
        folder.mkdir()
        (folder / 'qhealth-2025-26.csv').write_text(
            'Contract disclosure report,,,,\n'
            'Agency,Award Contract Date,Contract Description,Supplier Name,Supplier ABN,Contract Value,'
            'Procurement Method\n'
            'Queensland Health,15/07/2025,Software licences and support,Acme IT,51 824 753 556,"$12,500.00",'
            'Open\n', encoding='utf-8')
        with closing(sc.connect()) as conn:
            self.assertEqual(sc.import_qld(conn, folder), 1)
            row = conn.execute('SELECT state, agency, award_date, supplier, supplier_abn, value, '
                               'procurement_method FROM contracts').fetchone()
        self.assertEqual(row, ('QLD', 'Queensland Health', '2025-07-15', 'Acme IT', '51824753556', 12500.0,
                               'Open'))

    def test_dates(self):
        self.assertEqual(sc.iso_date('1/01/2024'), '2024-01-01')
        self.assertEqual(sc.iso_date('21/02/2024'), '2024-02-21')
        self.assertEqual(sc.iso_date('45658'), '2025-01-01')
        self.assertIsNone(sc.iso_date('TBC'))


if __name__ == '__main__':
    unittest.main()
