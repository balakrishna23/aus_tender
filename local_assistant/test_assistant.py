import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import assistant


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.previous = assistant.DB_DIR
        assistant.DB_DIR = self.path
        today = datetime.now(ZoneInfo('Australia/Canberra')).date()
        with closing(sqlite3.connect(self.path / 'intelligence.db')) as c:
            c.executescript('''
                CREATE TABLE build_info(built_at TEXT);
                INSERT INTO build_info VALUES ('2026-10-07T23:20:42');
                CREATE TABLE renewals(cn_id,buyer,supplier,description,current_value,
                    current_end_date,contact_safety,page_url,api_url);
                CREATE TABLE opportunities(source,source_id,kind,title,buyer,closes_at,
                    expected_from,expected_to,status_effective,contact_safety,url,retrieved_at,is_relevant);
                CREATE TABLE today(rank,organisation,priority,action_type,why_now,owner,due,
                    contact_safety,evidence,assumptions);
                CREATE TABLE panel_members(son_id,panel,member,abn,deed_start,deed_end,panel_url);
            ''')
            c.execute('INSERT INTO renewals VALUES (?,?,?,?,?,?,?,?,?)',
                ('CN1','Agency','DXC','Cyber',100,(today+timedelta(days=5)).isoformat(),
                 'SAFE_GENERAL','https://example.org/CN1','https://example.org/api/CN1'))
            c.execute('INSERT INTO renewals VALUES (?,?,?,?,?,?,?,?,?)',
                ('CN0','Agency','DXC','Cyber',100,(today-timedelta(days=1)).isoformat(),
                 'SAFE_GENERAL','https://example.org/CN0',''))
            for ref, closes in [('old','2000-01-01T00:00:00+11:00'),('future','2099-01-01T00:00:00+11:00')]:
                c.execute('INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('BuyICT',ref,'tender','Cyber','Agency',closes,'','','Open',
                     'FORMAL_CHANNEL_ONLY','https://example.org/'+ref,'2026-10-07',1))
            c.commit()
        with closing(sqlite3.connect(self.path / 'contract_api.db')) as c:
            c.execute('CREATE TABLE contracts(cn_id,buyer,supplier,supplier_abn,description,current_value,'
                      'current_end_date,amendment_count,first_published,retrieved_at,page_url,source_url)')
            c.execute('INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('CN1','Agency','DXC','123','Cyber',100,'2099-01-01',2,'2026-10-01',
                 '2026-10-07','https://example.org/CN1','https://example.org/api/CN1'))
            c.commit()

    def tearDown(self):
        assistant.DB_DIR = self.previous
        self.temp.cleanup()

    def test_read_only_blocks_write(self):
        with self.assertRaises(sqlite3.OperationalError):
            assistant.read_rows('contract_api.db', 'DELETE FROM contracts')
        self.assertEqual(1, len(assistant.read_rows('contract_api.db','SELECT * FROM contracts')))

    def test_missing_database_not_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            assistant.read_rows('missing.db','SELECT 1')
        self.assertFalse((self.path/'missing.db').exists())

    def test_search_is_parameterized(self):
        rows, total = assistant.retrieve({'intent':'company','entity':"' OR 1=1 --"})
        self.assertEqual(([],0), (rows,total))

    def test_percent_not_wildcard(self):
        rows, total = assistant.retrieve({'intent':'company','entity':'%'})
        self.assertEqual(([],0), (rows,total))

    def test_source_preserved_and_no_false_verification(self):
        result = assistant.answer('DXC contracts',False,{'intent':'company','entity':'DXC'})
        self.assertEqual('https://example.org/CN1',result['facts'][0]['page_url'])
        self.assertIn('not a live-source verification',result['warnings'][0])
        self.assertFalse(result['model_used'])

    def test_renewals_recalculate_today_and_exclude_past(self):
        rows, total = assistant.retrieve({'intent':'renewals','entity':'DXC','days':10})
        self.assertEqual(1,total)
        self.assertEqual(5,rows[0]['days_until_end'])

    def test_expired_open_record_excluded(self):
        rows, total = assistant.retrieve({'intent':'opportunities','entity':'Cyber'})
        self.assertEqual(1,total)
        self.assertEqual('future',rows[0]['source_id'])
        self.assertEqual('FORMAL_CHANNEL_ONLY',rows[0]['contact_safety'])

    def test_model_cannot_invent_entity(self):
        with patch.object(assistant,'local_chat',return_value=(json.dumps(
                {'intent':'company','entity':'IBM','days':90}),{})):
            with self.assertRaises(ValueError):
                assistant.plan_question('Tell me about DXC')

    def test_invalid_evidence_id_rejected(self):
        with patch.object(assistant,'local_chat',return_value=('{"evidence_ids":["E99"]}',{})):
            result=assistant.answer('DXC contracts',True,{'intent':'company','entity':'DXC'})
        self.assertFalse(result['model_used'])
        self.assertIsNotNone(result['model_error'])

    def test_model_free_text_cannot_change_contract_value(self):
        with patch.object(assistant,'local_chat',return_value=(
                '{"evidence_ids":["E1"],"claim":"DXC urgently needs staff for a billion dollar contract"}',{})):
            result=assistant.answer('DXC contracts',True,{'intent':'company','entity':'DXC'})
        self.assertIn('AUD $100.00',result['explanation'])
        self.assertNotIn('billion',result['explanation'])
        self.assertIn('Current hiring and a warm introduction are unconfirmed',result['explanation'])

    def test_no_evidence_no_model_call(self):
        with patch.object(assistant,'local_chat') as model:
            result=assistant.answer('Unknown company',True,{'intent':'company','entity':'NeverHere'})
        model.assert_not_called()
        self.assertIn('does not prove',result['explanation'])

    def test_snapshot_replacement_seen_on_next_query(self):
        with closing(sqlite3.connect(self.path/'contract_api.db')) as c:
            c.execute("UPDATE contracts SET current_value=200")
            c.commit()
        result=assistant.answer('DXC contracts',False,{'intent':'company','entity':'DXC'})
        self.assertEqual(200,result['facts'][0]['current_value'])


if __name__=='__main__':
    unittest.main()
