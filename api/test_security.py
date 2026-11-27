import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import app as api
from datetime import datetime, timezone, timedelta
from unittest.mock import patch


class ReviewSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        keys = self.root / 'keys.json'
        keys.write_text(json.dumps([{'name': name, 'sha256': hashlib.sha256(token.encode()).hexdigest(),
            'enabled': True, **({'scopes': ['review']} if scoped else {})}
            for name, token, scoped in [('reader', 'read-key', False), ('reviewer', 'review-key', True),
                                        ('other', 'other-key', True)]]))
        patches = patch.multiple(api, KEYS=keys, DATA=self.root, REVIEW_INBOX=self.root / 'inbox',
                                 REVIEW_LEDGER=self.root / 'ledger')
        patches.start()
        self.addCleanup(patches.stop)
        api.rates.clear()
        api.review_rates.clear()
        api.failures.clear()
        # These routes do not need the MCP lifespan (its manager is single-use).
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer review-key'}
        self.body = dict(kind='profile', target='insurance', expected='a' * 64,
                         evidence_url='https://example.gov.au/proof', notes='', value='current')

    def post(self, body=None, headers=None):
        return self.client.post('/v1/review/decisions', headers=headers or self.headers,
                                json=self.body if body is None else body)

    def test_scope_and_revocation_enforced(self):
        self.assertEqual(self.post(headers={'Authorization': 'Bearer read-key'}).status_code, 403)
        self.assertEqual(self.client.get('/v1/review/status', headers={'Authorization': 'Bearer read-key'}).status_code, 403)
        self.assertEqual(self.post(headers={'Authorization': 'Bearer wrong-key'}).status_code, 401)
        self.assertFalse(api.REVIEW_INBOX.exists())

    def test_strict_schema_https_controls_and_bounded_body(self):
        for change in ({'extra': 'no'}, {'reviewer': 'spoof'}, {'evidence_url': 'http://example.gov.au'},
                       {'evidence_url': 'https://user:pass@example.gov.au'}, {'notes': 'bad\x00'},
                       {'kind': 'other'}, {'kind': []}, {'value': 123}):
            self.assertEqual(self.post(self.body | change).status_code, 422, change)
        self.assertEqual(self.post(self.body | {'notes': 'x' * 9000}).status_code, 413)
        self.assertFalse(api.REVIEW_INBOX.exists())

    def test_inbox_identity_paths_and_reviewer_status_isolation(self):
        self.assertEqual(self.post(self.body | {'inbox': '../escape'}).status_code, 422)
        result = self.post().json()
        files = list(api.REVIEW_INBOX.glob('*.jsonl'))
        self.assertEqual(len(files), 1)
        entry = json.loads(files[0].read_text())
        self.assertEqual(entry['reviewer'], 'reviewer')
        self.assertEqual(entry['id'], result['id'])
        self.assertTrue(entry['submitted_at'].endswith('+00:00'))
        self.assertEqual(self.client.get('/v1/review/status', headers=self.headers).json()['decisions'][0]['status'], 'queued')
        self.assertEqual(self.client.get('/v1/review/status', headers={'Authorization': 'Bearer other-key'}).json(), {'decisions': []})
        api.REVIEW_LEDGER.mkdir()
        (api.REVIEW_LEDGER / (result['id'] + '.json')).write_text(json.dumps({'status': 'applied', 'processed_at': 'now'}))
        self.assertEqual(self.client.get('/v1/review/status', headers=self.headers).json()['decisions'][0]['status'], 'applied')

    def test_per_key_rate_limit_and_total_inbox_cap(self):
        for _ in range(20):
            self.assertEqual(self.post().status_code, 202)
        self.assertEqual(self.post().status_code, 429)
        api.review_rates.clear()
        with patch.object(api.review_wire, 'INBOX_LIMIT', 1):
            self.assertEqual(self.post().status_code, 507)

    def test_page_no_external_resources_and_session_only_key(self):
        response = self.client.get('/review')
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.text, r'(?:src|href)=[\"\x27]https?://')
        self.assertNotIn('localStorage', response.text)
        self.assertIn('sessionStorage', response.text)
        self.assertIn("connect-src 'self'", response.headers['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertIn('textContent', response.text)

    def test_queue_fixed_columns_conflicts_first_and_official_links(self):
        wire = api.review_wire
        with sqlite3.connect(self.root / 'research.db') as db:
            for kind, columns in wire.COLUMNS.items():
                db.execute('CREATE TABLE ' + wire.TABLES[kind] + '(' + ','.join(columns) + ')')
            buyer = dict.fromkeys(wire.BUYER_COLUMNS)
            buyer.update(buyer_key='exact', buyer_name='A exact', buyer_abn='51824753556',
                         match_status='exact_abn', organisation_id='gov:one', review_status='unreviewed',
                         register_context_stale=0, candidate_ids='["gov:one"]', snapshots='["snapshot"]',
                         candidate_evidence=json.dumps([dict(organisation_id='gov:one', name='Government body',
                         abn='51824753556', source='agor', source_url='https://example.gov.au/body', snapshot_id='snapshot')]),
                         evidence='valid_abn_unique_in_current_register')
            db.execute('INSERT INTO buyer_resolution VALUES (' + ','.join('?' for _ in buyer) + ')', tuple(buyer.values()))
            buyer.update(buyer_key='conflict', buyer_name='Z conflict', match_status='conflict')
            db.execute('INSERT INTO buyer_resolution VALUES (' + ','.join('?' for _ in buyer) + ')', tuple(buyer.values()))
            db.execute('CREATE TABLE register_people(name, email)')
            db.execute("INSERT INTO register_people VALUES ('PRIVATE PERSON', 'PRIVATE CONTACT')")
        db.close()
        response = self.client.get('/v1/review/queue', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['buyer'][0]['buyer_key'], 'conflict')
        self.assertNotIn('PRIVATE', response.text)
        self.assertEqual(response.json()['buyer'][0]['candidate_evidence'][0]['source_url'], 'https://example.gov.au/body')
        self.assertRegex(response.json()['buyer'][0]['expected'], '^[a-f0-9]{64}$')

    def test_buyer_panel_enums_and_no_boolean_coercion(self):
        common = {k: self.body[k] for k in api.review_wire.COMMON}
        panel = common | dict(kind='panel', relevant=True, illoura_status='member')
        self.assertEqual(self.post(panel).status_code, 202)
        for change in ({'relevant': 'yes'}, {'relevant': 1}, {'illoura_status': 'eligible'}):
            self.assertEqual(self.post(panel | change).status_code, 422)
        buyer = common | dict(kind='buyer', decision='approved', organisation_id='gov:one')
        self.assertEqual(self.post(buyer).status_code, 202)
        self.assertEqual(self.post(buyer | dict(decision='rejected')).status_code, 422)
        self.assertEqual(self.post(buyer | dict(decision='rejected', organisation_id=None)).status_code, 202)


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        api.DATA = cls.root
        api.KEYS = cls.root / "keys.json"
        api.KEYS.write_text(
            json.dumps(
                [
                    {
                        "name": "test",
                        "sha256": hashlib.sha256(b"test-key").hexdigest(),
                        "enabled": True,
                    }
                ]
            )
        )
        with sqlite3.connect(cls.root / "contract_api.db") as db:
            db.executescript(
                'CREATE TABLE contracts(cn_id TEXT,buyer TEXT,current_value REAL); INSERT INTO contracts VALUES("CN1","Agency",100),("CN2","Agency",200); CREATE TABLE private_notes(secret TEXT); INSERT INTO private_notes VALUES("hidden");'
            )
        db.close()
        (cls.root / "manifest.json").write_text(
            json.dumps(
                {
                    "snapshot_started_utc": "2026-11-12",
                    "snapshot_finished_utc": "2026-11-12",
                    "notes": [],
                    "datasets": {
                        "contract_api": {
                            "tables": {"contracts": 2, "private_notes": 1},
                            "backup_finished_utc": "2026-11-12",
                        }
                    },
                }
            )
        )
        cls.context = TestClient(api.app)
        cls.client = cls.context.__enter__()
        cls.headers = {"Authorization": "Bearer test-key"}

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        cls.temp.cleanup()

    def setUp(self):
        api.failures.clear()

    def query(self, sql):
        return self.client.post(
            "/v1/query",
            headers=self.headers,
            json={"dataset": "contract_api", "sql": sql},
        )

    def test_authentication_and_catalog(self):
        self.assertEqual(self.client.get("/v1/catalog").status_code, 401)
        self.assertEqual(
            self.client.get("/v1/catalog", headers={"X-API-Key": "wrong"}).status_code,
            401,
        )
        response = self.client.get("/v1/catalog", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "private_notes", response.json()["datasets"]["contract_api"]["tables"]
        )

    def test_spec_views_are_allowlisted_but_generation_storage_is_private(self):
        names={'account_score','buyict_buyers','buyict_gaps','ipp_gap','ipp_levers','prime_targets',
               'partner_candidates','indigenous_partners','rap_targets','panel_access','forward_pipeline',
               'demand_vs_bench','lookalikes','whats_changed','rate_benchmarks'}
        self.assertTrue(names.issubset(set(api.ALLOWED['research'])))
        self.assertTrue({'view_rows','view_generations','current_view_generation'}.isdisjoint(api.ALLOWED['research']))

    def test_server_side_aggregation(self):
        r = self.query(
            "SELECT count(*) AS records,sum(current_value) AS total FROM contracts"
        )
        self.assertEqual(r.json()["rows"], [{"records": 2, "total": 300.0}])

    def test_readiness_fails_closed_when_health_stops(self):
        old = api.HEALTH
        api.HEALTH = self.root / "health.json"
        try:
            api.HEALTH.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
            self.assertEqual(self.client.get("/readyz").status_code, 200)
            self.assertEqual(self.client.get("/v1/operations").status_code, 401)
            api.HEALTH.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "checked_at_utc": (
                            datetime.now(timezone.utc) - timedelta(hours=1)
                        ).isoformat(),
                    }
                )
            )
            self.assertEqual(self.client.get("/readyz").status_code, 503)
        finally:
            api.HEALTH = old

    def test_republication_does_not_hide_stale_collection(self):
        file = self.root / "manifest.json"
        old = file.read_text()
        meta = json.loads(old)
        meta["snapshot_finished_utc"] = datetime.now(timezone.utc).isoformat()
        meta["collector"] = {
            "evidence_generated_at_utc": (
                datetime.now(timezone.utc) - timedelta(hours=30)
            ).isoformat()
        }
        try:
            file.write_text(json.dumps(meta))
            result = api.catalog()
            self.assertFalse(result["snapshot_stale"])
            self.assertTrue(result["data_stale"])
        finally:
            file.write_text(old)

    def test_research_freshness_uses_export_time_not_republication(self):
        path=self.root/'research.db'
        old=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS export_info(key TEXT,value TEXT)')
            db.execute('DELETE FROM export_info')
            db.executemany('INSERT INTO export_info VALUES (?,?)',[
                ('exported_at_utc',old),('schema_revision','023_research_freshness')])
            db.execute('CREATE TABLE IF NOT EXISTS sources(kind TEXT,source TEXT,effective_date TEXT,imported_at TEXT)')
            db.commit()
        meta=api.research_metadata(self.root)
        self.assertTrue(meta['stale'])
        self.assertEqual(meta['schema_revision'],'023_research_freshness')
        self.assertGreater(meta['age_seconds'],26*3600)

    def test_research_readiness_is_independent_from_procurement(self):
        old=api.HEALTH
        try:
            api.HEALTH=self.root/'health-test.json'
            api.HEALTH.write_text(json.dumps({'status':'ok','checked_at_utc':datetime.now(timezone.utc).isoformat(),
                                            'research_monitor':{'status':'attention'}}))
            self.assertEqual(self.client.get('/readyz').status_code,200)
            response=self.client.get('/research-readyz')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json(),{'status':'attention'})
            api.HEALTH.write_text(json.dumps({'status':'ok','checked_at_utc':datetime.now(timezone.utc).isoformat(),
                                            'research_monitor':[]}))
            self.assertEqual(self.client.get('/research-readyz').status_code,503)
        finally:api.HEALTH=old

    def test_snapshot_metadata_stays_pinned_during_publication(self):
        other = self.root / "replacement"
        other.mkdir(exist_ok=True)
        meta = json.loads((self.root / "manifest.json").read_text())
        meta["snapshot_finished_utc"] = "2030-01-01"
        (other / "manifest.json").write_text(json.dumps(meta))
        original = api.connect

        def switch_after_open(dataset, root=None):
            db = original(dataset, root)
            api.DATA = other
            return db

        try:
            with patch.object(api, "connect", side_effect=switch_after_open):
                result = self.query("SELECT count(*) AS n FROM contracts").json()
            self.assertEqual(result["snapshot_finished_utc"], "2026-11-12")
            self.assertEqual(result["rows"], [{"n": 2}])
        finally:
            api.DATA = self.root

    def test_no_writes_or_hidden_tables(self):
        for sql in [
            "DELETE FROM contracts",
            "SELECT * FROM private_notes",
            "SELECT * FROM sqlite_master",
            "SELECT load_extension('x')",
            "SELECT 1; DELETE FROM contracts",
            "WITH x AS (SELECT 1) DELETE FROM contracts",
            "PRAGMA writable_schema=ON",
            "ATTACH DATABASE ':memory:' AS x",
            "SELECT * FROM pragma_table_info('contracts')",
        ]:
            with self.subTest(sql=sql):
                self.assertEqual(self.query(sql).status_code, 400)
        self.assertEqual(
            self.query("SELECT count(*) AS n FROM contracts").json()["rows"][0]["n"], 2
        )

    def test_path_and_result_limits(self):
        r = self.client.post(
            "/v1/query",
            headers=self.headers,
            json={"dataset": "../../private", "sql": "SELECT 1"},
        )
        self.assertEqual(r.status_code, 404)
        r = self.client.post(
            "/v1/query",
            headers=self.headers,
            json={
                "dataset": "contract_api",
                "sql": "SELECT * FROM contracts",
                "limit": 1,
            },
        )
        self.assertTrue(r.json()["truncated"])
        self.assertEqual(len(r.json()["rows"]), 1)

    def test_expensive_query_stops(self):
        self.assertEqual(
            self.query(
                "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x"
            ).status_code,
            400,
        )

    def test_search_and_mcp(self):
        r = self.client.post(
            "/v1/search",
            headers=self.headers,
            json={"dataset": "contract_api", "table": "contracts", "term": "Agency"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["returned_rows"], 2)
        h = dict(self.headers, Accept="application/json, text/event-stream")
        r = self.client.post(
            "/mcp",
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post(
            "/mcp",
            headers=h,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["result"]["tools"]), 4)
        r = self.client.post(
            "/mcp",
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "query_dataset",
                    "arguments": {
                        "dataset": "contract_api",
                        "sql": "SELECT sum(current_value) AS total FROM contracts",
                    },
                },
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["result"].get("isError"), r.text)

    def test_failed_keys_are_throttled_per_client_and_spoofed_header_ignored(self):
        self.addCleanup(api.failures.clear)
        for i in range(api.FAILED_LIMIT):
            # The test client is not loopback, so X-Forwarded-For must not create new buckets.
            r = self.client.get(
                "/v1/catalog",
                headers={"X-API-Key": "wrong", "X-Forwarded-For": "203.0.113.%d" % i},
            )
            self.assertEqual(r.status_code, 401)
        r = self.client.get("/v1/catalog", headers=self.headers)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers["Retry-After"], str(api.FAILED_WINDOW_SECONDS))
        api.failures.clear()
        self.assertEqual(
            self.client.get("/v1/catalog", headers=self.headers).status_code, 200
        )

    def test_client_address_trusts_forwarded_header_only_from_loopback(self):
        class Request:
            def __init__(self, peer, forwarded):
                self.client = type("C", (), {"host": peer})()
                self.headers = {"x-forwarded-for": forwarded}

        self.assertEqual(
            api.client_address(Request("127.0.0.1", "203.0.113.5")), "203.0.113.5"
        )
        self.assertEqual(
            api.client_address(Request("198.51.100.1", "203.0.113.5")), "198.51.100.1"
        )
        self.assertEqual(
            api.client_address(Request("127.0.0.1", "203.0.113.5, 10.0.0.1")),
            "127.0.0.1",
        )
        self.assertEqual(api.client_address(Request("::1", "not-an-ip")), "::1")

    def test_failure_tracking_is_bounded(self):
        self.addCleanup(api.failures.clear)
        with patch.object(api, "MAX_TRACKED_CLIENTS", 5):
            for i in range(12):
                api.record_failure("198.51.100.%d" % i, 1000.0)
        self.assertEqual(len(api.failures), 5)
        self.assertEqual(list(api.failures)[-1], "198.51.100.11")

    def test_revoked_key_rate_state_is_evicted(self):
        api.rates["f" * 64].append(time.monotonic())
        self.assertEqual(
            self.client.get("/v1/catalog", headers=self.headers).status_code, 200
        )
        self.assertNotIn("f" * 64, api.rates)

    def test_body_size_limit_cannot_be_bypassed_without_a_declared_length(self):
        # 14 Nov cross test (Codex): a chunked body carries no Content-Length to check.
        def chunks():
            yield b'{"dataset": "contract_api", "sql": "SELECT 1", "padding": "'
            yield b"x" * 40000
            yield b'"}'

        response = self.client.post("/v1/query", content=chunks(), headers=self.headers)
        self.assertEqual(response.status_code, 413)
        declared = self.client.post(
            "/v1/query", content=b"x" * 40000, headers={**self.headers, "Content-Type": "application/json"}
        )
        self.assertEqual(declared.status_code, 413)
        # Ordinary requests are unaffected: a GET without a body and a small JSON POST.
        self.assertEqual(self.client.get("/v1/catalog", headers=self.headers).status_code, 200)
        small = self.client.post(
            "/v1/query", json={"dataset": "contract_api", "sql": "SELECT count(*) AS n FROM contracts"},
            headers=self.headers,
        )
        self.assertEqual(small.status_code, 200)

    def test_wrongly_shaped_key_file_is_an_outage_not_a_crash(self):
        original = api.KEYS.read_text()
        try:
            for content in ('{"unexpected": true}', '["text"]', "null", "42"):
                api.KEYS.write_text(content)
                response = self.client.get("/v1/catalog", headers=self.headers)
                self.assertEqual(response.status_code, 503, content)
        finally:
            api.KEYS.write_text(original)
        self.assertEqual(self.client.get("/v1/catalog", headers=self.headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
