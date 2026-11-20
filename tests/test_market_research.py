from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from market_intelligence.extract_research import state_contracts
from market_intelligence.research import period_range,import_research


class RetainedResearchTests(unittest.TestCase):
    def test_rap_periods_do_not_invent_dates(self):
        self.assertEqual(period_range('April 2026 to April 2028'),(date(2026,4,1),date(2028,4,30)))
        self.assertEqual(period_range('2026-2028'),(None,None))
        self.assertEqual(period_range('April 2028 to April 2026'),(None,None))

    def test_unsupported_payload_and_contacts_fail_before_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bundle.json'
            path.write_text(json.dumps(dict(source='rap_directory',contains_contact_data=False,observed_at='2026-11-19T00:00:00+00:00',
                records=[dict(record_key='one',email='person@example.invalid')],provenance={})))
            with self.assertRaises(ValueError):import_research(None,path)

    def test_state_export_drops_descriptions_addresses_and_raw_payload(self):
        from market_intelligence.research import SCHEMAS
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);db=root/'state.db'
            fields=['state','source_file','row_no','agency','title','reference','procurement_method','unspsc','award_date',
                    'start_date','end_date','value','supplier','supplier_abn','panel','loaded_at','category_family','description','supplier_address','raw_json']
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute('CREATE TABLE contracts('+','.join(f+' TEXT' for f in fields)+')')
                conn.execute('CREATE TABLE sources(source_file TEXT,state TEXT,url TEXT,licence TEXT,modified TEXT,rows INTEGER)')
                conn.execute("INSERT INTO sources VALUES('test.csv','WA','https://example.invalid/source','CC BY 4.0','2026-11-19',1)")
                row=dict(state='WA',source_file='test.csv',row_no='1',title='ICT support',supplier='Example Ltd',
                         description='person@example.invalid confidential',supplier_address='PRIVATE ADDRESS',raw_json='PRIVATE RAW',unspsc='81111500')
                conn.execute('INSERT INTO contracts VALUES('+','.join('?' for _ in fields)+')',[row.get(f) for f in fields])
            out=root/'out.json';state_contracts(db,out)
            text=out.read_text();payload=json.loads(text)['records'][0]
            self.assertEqual(set(payload),SCHEMAS['state_contracts'])
            for private in ['person@example.invalid','PRIVATE ADDRESS','PRIVATE RAW']:self.assertNotIn(private,text)


if __name__=='__main__':unittest.main()
