"""Dependency-free tests of actual API bodies; HTTP integration needs FastAPI/MCP.

AST extraction retains product bodies unchanged and excludes app registration only.
Transport stand-ins let the default Python run SQL and middleware risk tests offline.
"""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class Response:
    def __init__(self, content, status_code=200, headers=None):
        self.content, self.status_code, self.headers = content, status_code, headers or {}


def bodies(path, functions=None):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    kept = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            if module.split('.')[0] not in ('fastapi', 'mcp', 'pydantic', 'httpx'):
                kept.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if functions is None or node.name in functions:
                node.decorator_list = []
                kept.append(node)
        elif isinstance(node, ast.Assign) and functions is None:
            names = {n.id for n in node.targets if isinstance(n, ast.Name)}
            if names & {'DATA', 'KEYS', 'HOST', 'HEALTH', 'ALLOWED', 'SAFE_FUNCTIONS', 'log',
                        'rates', 'rate_lock', 'FAILED_LIMIT', 'FAILED_WINDOW_SECONDS',
                        'MAX_TRACKED_CLIENTS', 'failures', 'LOOPBACK'}:
                kept.append(node)
    scope = {'HTTPException': HTTPError, 'JSONResponse': Response, 'Request': object,
             'Query': object, 'Search': object}
    exec(compile(ast.Module(kept, type_ignores=[]), str(path), 'exec'), scope)
    return SimpleNamespace(**scope), scope


class APIRisks(unittest.TestCase):
    def setUp(self):
        self.api, self.g = bodies(ROOT / 'api/app.py')
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.g.update(DATA=self.root, KEYS=self.root / 'keys.json', HEALTH=self.root / 'health.json')
        self.token = 'synthetic-cross-test-token'
        self.g['KEYS'].write_text(json.dumps([{'name': 'fixture', 'sha256': hashlib.sha256(self.token.encode()).hexdigest(), 'enabled': True}]))
        (self.root / 'manifest.json').write_text(json.dumps({'snapshot_finished_utc': datetime.now(timezone.utc).isoformat()}))
        with sqlite3.connect(self.root / 'contract_api.db') as db:
            db.executescript("CREATE TABLE contracts(title TEXT); INSERT INTO contracts VALUES ('ordinary'); CREATE TABLE private_notes(secret TEXT); INSERT INTO private_notes VALUES ('private');")

        db.close()

    def request(self, path='/v1/query', headers=None, query='', peer='192.0.2.1', body=b''):
        request = SimpleNamespace(url=SimpleNamespace(path=path, query=query),
                                  headers=headers or {}, client=SimpleNamespace(host=peer), method='POST')
        request.body = AsyncMock(return_value=body)
        async def stream():
            yield body
        request.stream = stream
        return request

    def call(self, request):
        downstream = AsyncMock(return_value=Response({'ok': True}))
        result = asyncio.run(self.api.authenticate(request, downstream))
        return result, downstream

    def test_only_enabled_hash_authenticates(self):
        """Raw keys and revoked hashes must never authenticate."""
        for records, status in (([{'name': 'fixture', 'sha256': self.token, 'enabled': True}], 401),
                                ([{'name': 'fixture', 'sha256': hashlib.sha256(self.token.encode()).hexdigest(), 'enabled': False}], 401)):
            self.g['KEYS'].write_text(json.dumps(records))
            result, next_call = self.call(self.request(headers={'x-api-key': self.token}))
            self.assertEqual(result.status_code, status)
            next_call.assert_not_called()

    def test_bad_keys_isolated_and_expire(self):
        """One attacking client must not block other clients permanently."""
        for _ in range(20):
            self.api.record_failure('192.0.2.1', 100)
        self.assertTrue(self.api.too_many_failures('192.0.2.1', 101))
        self.assertFalse(self.api.too_many_failures('192.0.2.2', 101))
        self.assertFalse(self.api.too_many_failures('192.0.2.1', 701))
        self.g['MAX_TRACKED_CLIENTS'] = 3
        for n in range(50):
            self.api.record_failure('client-' + str(n), 800)
        self.assertEqual(len(self.g['failures']), 3)

    def test_query_auth_and_missing_key_refused(self):
        """Query-string credentials must never become authenticated API calls."""
        for request, status in ((self.request(), 401), (self.request(query='key=synthetic'), 400)):
            result, downstream = self.call(request)
            self.assertEqual(result.status_code, status)
            downstream.assert_not_called()

    def test_forwarded_address_trusted_only_from_loopback(self):
        """Remote callers must not rotate a forged header to evade throttling."""
        for peer, forwarded, expected in (('192.0.2.1', '192.0.2.2', '192.0.2.1'),
                                           ('127.0.0.1', '192.0.2.2', '192.0.2.2'),
                                           ('127.0.0.1', 'bad,input', '127.0.0.1')):
            self.assertEqual(self.api.client_address(self.request(peer=peer, headers={'x-forwarded-for': forwarded})), expected)

    def test_readyz_bad_timestamps_and_missing_files(self):
        """Readiness must fail closed for stale, corrupt, naive or future health."""
        now = datetime.now(timezone.utc)
        for data in (None, '{', json.dumps({'status': 'ok', 'checked_at_utc': '2026-11-14'}),
                     json.dumps({'status': 'ok', 'checked_at_utc': (now + timedelta(days=1)).isoformat()}),
                     json.dumps({'status': 'ok', 'checked_at_utc': (now - timedelta(hours=1)).isoformat()})):
            if data is not None:
                self.g['HEALTH'].write_text(data)
            self.assertEqual(self.call(self.request('/readyz'))[0].status_code, 503)

    def test_readyz_wrong_json_type_is_503(self):
        """A health JSON array must not make the readiness endpoint crash."""
        self.g['HEALTH'].write_text('[]')
        self.assertEqual(self.call(self.request('/readyz'))[0].status_code, 503)

    def test_corrupt_key_store_fails_closed(self):
        """Wrong-shaped key files must yield a controlled authentication outage."""
        self.g['KEYS'].write_text('{"unexpected":true}')
        result, downstream = self.call(self.request(headers={'x-api-key': self.token}))
        self.assertEqual(result.status_code, 503)
        downstream.assert_not_called()

    def test_chunked_oversize_rejected(self):
        """Omitting Content-Length must not bypass the API body-size limit."""
        result, downstream = self.call(self.request(headers={'x-api-key': self.token, 'transfer-encoding': 'chunked'}, body=b'x' * 32769))
        self.assertEqual(result.status_code, 413)
        downstream.assert_not_called()

    def test_declared_oversize_rejected(self):
        """Oversized declared bodies must be rejected before authentication work."""
        result, downstream = self.call(self.request(headers={'content-length': '32769'}))
        self.assertEqual(result.status_code, 413)
        downstream.assert_not_called()

    def test_sql_write_and_private_table_denied(self):
        """SQL expressions and CTEs must not bypass the read-only allowlist."""
        queries = ['DELETE FROM contracts', 'PRAGMA writable_schema=ON',
                   'SELECT * FROM private_notes', 'SELECT * FROM sqlite_master',
                   "SELECT load_extension('evil')", 'WITH x AS (SELECT * FROM private_notes) SELECT * FROM x',
                   'SELECT * FROM contracts; DELETE FROM contracts',
                   "ATTACH DATABASE ':memory:' AS extra"]
        before = (self.root / 'contract_api.db').read_bytes()
        for sql in queries:
            with self.subTest(sql=sql), self.assertRaises(HTTPError):
                self.api.query_data('contract_api', sql)
        self.assertEqual((self.root / 'contract_api.db').read_bytes(), before)
        self.assertEqual(self.api.query_data('contract_api', 'SELECT count(*) AS n FROM contracts')['rows'], [{'n': 1}])

    def test_row_bounds(self):
        """Unbounded or zero row limits must not become expensive SQL requests."""
        for limit in (0, -1, 201, 10**50):
            with self.subTest(limit=limit), self.assertRaises(HTTPError):
                self.api.query_data('contract_api', 'SELECT * FROM contracts', limit=limit)

    def test_bridge_path_injection_and_redirect_policy(self):
        """Bridge dataset names cannot rewrite its endpoint path or follow redirects."""
        bridge, scope = bodies(ROOT / 'api/claude_bridge.py', {'request', 'describe_dataset'})
        client = Mock()
        scope['client'] = client
        for dataset in ('../query', '/v1/catalog', 'x?key=y', 'x\ny', '--'):
            with self.assertRaises(ValueError):
                bridge.describe_dataset(dataset)
        client.request.assert_not_called()
        bridge.describe_dataset('contract_api')
        client.request.assert_called_once_with('GET', '/v1/schema/contract_api', json=None)
        tree = ast.parse((ROOT / 'api/claude_bridge.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'Client']
        self.assertEqual([ast.literal_eval(k.value) for k in calls[0].keywords if k.arg == 'follow_redirects'], [False])
