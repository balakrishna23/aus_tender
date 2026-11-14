"""Actual middleware bodies with synthetic HTTP headers and key stores."""
import json
import unittest
import test_cross_codex_api as fixtures


class MiddlewareRoundTwo(unittest.TestCase):
    setUp = fixtures.APIRisks.setUp
    request = fixtures.APIRisks.request
    call = fixtures.APIRisks.call

    def test_get_without_content_length(self):
        r = self.request('/v1/status', headers={'x-api-key': self.token})
        r.method = 'GET'
        self.assertEqual(self.call(r)[0].status_code, 200)

    def test_content_length_boundaries(self):
        for value, expected in [('0', 200), (' 12', 413), ('+5', 413), ('1e3', 413),
                                ('32768', 200), ('32769', 413), ('', 413)]:
            with self.subTest(value=value):
                self.assertEqual(self.call(self.request(headers={
                    'x-api-key': self.token, 'content-length': value}))[0].status_code, expected)

    def test_long_digit_length_is_controlled_413(self):
        self.assertEqual(self.call(self.request(headers={
            'x-api-key': self.token, 'content-length': '9' * 5000}))[0].status_code, 413)

    def test_unicode_digit_length_is_controlled_413(self):
        self.assertEqual(self.call(self.request(headers={
            'x-api-key': self.token, 'content-length': '\u00b2'}))[0].status_code, 413)

    def test_header_names_are_case_insensitive(self):
        # Match the real Starlette header mapping, not a case-sensitive dict fake.
        # Starlette is optional in the offline suite. Its Headers mapping exposes
        # lower-case names; model that documented transport contract here.
        class Headers(dict):
            def __init__(self, values):
                super().__init__((k.lower(), v) for k, v in values.items())
        for name in ['Transfer-Encoding', 'tRaNsFeR-EnCoDiNg']:
            with self.subTest(name=name):
                r = self.request()
                r.headers = Headers({name: 'chunked', 'X-API-Key': self.token})
                self.assertEqual(self.call(r)[0].status_code, 413)

    def test_nonstring_hash_reports_authentication_unavailable(self):
        for value in [[], {}, 123, None]:
            with self.subTest(value=value):
                self.g['KEYS'].write_text(json.dumps([{'name': 'fixture', 'sha256': value, 'enabled': True}]))
                self.assertIn(self.call(self.request(headers={'x-api-key': self.token}))[0].status_code, (401, 503))

    def test_missing_or_nonstring_key_name_reports_503(self):
        import hashlib
        for fields in [{}, {'name': []}, {'name': 123}, {'name': None}]:
            with self.subTest(fields=fields):
                self.g['KEYS'].write_text(json.dumps([dict(fields, sha256=hashlib.sha256(self.token.encode()).hexdigest(), enabled=True)]))
                self.assertEqual(self.call(self.request(headers={'x-api-key': self.token}))[0].status_code, 503)
