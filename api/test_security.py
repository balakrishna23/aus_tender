import hashlib,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
import app as api
from datetime import datetime,timezone,timedelta
from unittest.mock import patch

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        api.DATA=cls.root;api.KEYS=cls.root/'keys.json'
        api.KEYS.write_text(json.dumps([{'name':'test','sha256':hashlib.sha256(b'test-key').hexdigest(),'enabled':True}]))
        with sqlite3.connect(cls.root/'contract_api.db') as db:
            db.executescript('CREATE TABLE contracts(cn_id TEXT,buyer TEXT,current_value REAL); INSERT INTO contracts VALUES("CN1","Agency",100),("CN2","Agency",200); CREATE TABLE private_notes(secret TEXT); INSERT INTO private_notes VALUES("hidden");')
        db.close()
        (cls.root/'manifest.json').write_text(json.dumps({'snapshot_started_utc':'2026-11-12','snapshot_finished_utc':'2026-11-12','notes':[],'datasets':{'contract_api':{'tables':{'contracts':2,'private_notes':1},'backup_finished_utc':'2026-11-12'}}}))
        cls.context=TestClient(api.app);cls.client=cls.context.__enter__()
        cls.headers={'Authorization':'Bearer test-key'}
    @classmethod
    def tearDownClass(cls):cls.context.__exit__(None,None,None);cls.temp.cleanup()
    def query(self,sql):return self.client.post('/v1/query',headers=self.headers,json={'dataset':'contract_api','sql':sql})
    def test_authentication_and_catalog(self):
        self.assertEqual(self.client.get('/v1/catalog').status_code,401)
        self.assertEqual(self.client.get('/v1/catalog',headers={'X-API-Key':'wrong'}).status_code,401)
        response=self.client.get('/v1/catalog',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('private_notes',response.json()['datasets']['contract_api']['tables'])
    def test_server_side_aggregation(self):
        r=self.query('SELECT count(*) AS records,sum(current_value) AS total FROM contracts')
        self.assertEqual(r.json()['rows'],[{'records':2,'total':300.0}])
    def test_readiness_fails_closed_when_health_stops(self):
        old=api.HEALTH;api.HEALTH=self.root/'health.json'
        try:
            api.HEALTH.write_text(json.dumps({'status':'ok','checked_at_utc':datetime.now(timezone.utc).isoformat()}))
            self.assertEqual(self.client.get('/readyz').status_code,200)
            self.assertEqual(self.client.get('/v1/operations').status_code,401)
            api.HEALTH.write_text(json.dumps({'status':'ok','checked_at_utc':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}))
            self.assertEqual(self.client.get('/readyz').status_code,503)
        finally:api.HEALTH=old
    def test_republication_does_not_hide_stale_collection(self):
        file=self.root/'manifest.json';old=file.read_text();meta=json.loads(old)
        meta['snapshot_finished_utc']=datetime.now(timezone.utc).isoformat()
        meta['collector']={'evidence_generated_at_utc':(datetime.now(timezone.utc)-timedelta(hours=30)).isoformat()}
        try:
            file.write_text(json.dumps(meta));result=api.catalog()
            self.assertFalse(result['snapshot_stale'])
            self.assertTrue(result['data_stale'])
        finally:file.write_text(old)
    def test_snapshot_metadata_stays_pinned_during_publication(self):
        other=self.root/'replacement';other.mkdir(exist_ok=True)
        meta=json.loads((self.root/'manifest.json').read_text());meta['snapshot_finished_utc']='2030-01-01'
        (other/'manifest.json').write_text(json.dumps(meta))
        original=api.connect
        def switch_after_open(dataset,root=None):
            db=original(dataset,root)
            api.DATA=other
            return db
        try:
            with patch.object(api,'connect',side_effect=switch_after_open):
                result=self.query('SELECT count(*) AS n FROM contracts').json()
            self.assertEqual(result['snapshot_finished_utc'],'2026-11-12')
            self.assertEqual(result['rows'],[{'n':2}])
        finally:api.DATA=self.root
    def test_no_writes_or_hidden_tables(self):
        for sql in ['DELETE FROM contracts','SELECT * FROM private_notes','SELECT * FROM sqlite_master',"SELECT load_extension('x')",'SELECT 1; DELETE FROM contracts',"WITH x AS (SELECT 1) DELETE FROM contracts",'PRAGMA writable_schema=ON',"ATTACH DATABASE ':memory:' AS x","SELECT * FROM pragma_table_info('contracts')"]:
            with self.subTest(sql=sql):self.assertEqual(self.query(sql).status_code,400)
        self.assertEqual(self.query('SELECT count(*) AS n FROM contracts').json()['rows'][0]['n'],2)
    def test_path_and_result_limits(self):
        r=self.client.post('/v1/query',headers=self.headers,json={'dataset':'../../private','sql':'SELECT 1'})
        self.assertEqual(r.status_code,404)
        r=self.client.post('/v1/query',headers=self.headers,json={'dataset':'contract_api','sql':'SELECT * FROM contracts','limit':1})
        self.assertTrue(r.json()['truncated'])
        self.assertEqual(len(r.json()['rows']),1)
    def test_expensive_query_stops(self):
        self.assertEqual(self.query('WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x').status_code,400)
    def test_search_and_mcp(self):
        r=self.client.post('/v1/search',headers=self.headers,json={'dataset':'contract_api','table':'contracts','term':'Agency'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['returned_rows'],2)
        h=dict(self.headers,Accept='application/json, text/event-stream')
        r=self.client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(r.json()['result']['tools']),4)
        r=self.client.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'query_dataset','arguments':{'dataset':'contract_api','sql':'SELECT sum(current_value) AS total FROM contracts'}}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['result'].get('isError'),r.text)

if __name__=='__main__':unittest.main()
