"""JSA layout changes, suppression, units, exact time windows and import rollback."""
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

import openpyxl

from market_intelligence.demand import parse_demand, number
from market_intelligence.parsers import SourceError


def ivi(path, duplicate=False):
    wb=openpyxl.Workbook();ws=wb.active;ws.title='4 digit 3 month average'
    months=[datetime(2025+(9+i)//12,(9+i)%12+1,1) for i in range(13)]
    ws.append(['ANZSCO_CODE','ANZSCO_TITLE','state']+months)
    row=['2613','Software and Applications Programmers','ACT']+[10]*12+[20]
    ws.append(row);ws.append(['.','Suppressed occupation','ACT']+['.']*13)
    if duplicate:ws.append(row)
    wb.save(path);wb.close()
    return path


def acquisition(root,path):
    payload=path.read_bytes()
    m=root/'manifest.json'
    m.write_text(json.dumps(dict(status='complete',files=[dict(dataset='jsa_ivi',status='complete',path=str(path),
        bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest(),retrieved_at='2026-11-19T12:00:00+00:00',
        licence='Creative Commons Attribution 4.0 International',licence_url='https://creativecommons.org/licenses/by/4.0/',
        source_url='https://www.jobsandskills.gov.au/data/internet-vacancy-index',url='https://www.jobsandskills.gov.au/example.xlsx')],datasets=[])))
    return m


class DemandParsers(unittest.TestCase):
    def test_ivi_suppression_and_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows,end=parse_demand(ivi(Path(tmp)/'ivi.xlsx'),'jsa_ivi')
        self.assertEqual((len(rows),end),(26,date(2026,10,1)))
        self.assertEqual(rows[12]['value'],20)
        self.assertTrue(all(r['value'] is None and r['occupation_code'] is None for r in rows[13:]))
        self.assertEqual(rows[0]['payload']['row'],2)

    def test_duplicate_series_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SourceError,'Duplicate'):
                parse_demand(ivi(Path(tmp)/'ivi.xlsx',duplicate=True),'jsa_ivi')

    def test_non_finite_and_unexpected_numbers_rejected(self):
        for value in ['NaN','Infinity','suppressed',True]:
            with self.assertRaises(SourceError):number(value)
        self.assertIsNone(number('.'))
        self.assertEqual(number(0),0)

    def test_shortage_classification_and_rating(self):
        with tempfile.TemporaryDirectory() as tmp:
            wb=openpyxl.Workbook();ws=wb.active;ws.title='2026 Unit group Shortage List'
            ws.append(['2026 Unit group Shortage List (ANZSCO 2022)'])
            ws.append(['Unit group Code','Unit group title','Rating','Shortage Driver'])
            ws.append(['2544','Registered Nurses','S','Retention gap'])
            path=Path(tmp)/'shortage.xlsx';wb.save(path);wb.close()
            rows,end=parse_demand(path,'jsa_shortages')
        self.assertEqual((rows[0]['rating'],rows[0]['classification'],end),('S','ANZSCO 2022',date(2026,1,1)))

    def test_projections_convert_thousands_without_counting_aggregates(self):
        with tempfile.TemporaryDirectory() as tmp:
            wb=openpyxl.Workbook();ws=wb.active;ws.title='Table_6 Occupation Unit Group'
            ws.append(['Occupation Level','NFD Indicator','ANZSCO Code','Occupation','Skill level 1','Baseline','Projected','Projected'])
            ws.append([None]*5+["May 2025 ('000)","May 2030 ('000)","May 2035 ('000)"])
            ws.append([1,'N','2','Professionals',1,100,120,140])
            ws.append([4,'N','2544','Registered Nurses',1,10,12,14])
            path=Path(tmp)/'projection.xlsx';wb.save(path);wb.close()
            rows,end=parse_demand(path,'jsa_projections')
        self.assertEqual((len(rows),end,rows[0]['value']),(3,date(2025,5,1),10000))
        self.assertEqual(rows[1]['measure'],'employment_projection')


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL test database not configured')
class DemandDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.engine=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose()

    def test_import_repeat_report_and_rollback(self):
        from sqlalchemy import text
        from market_intelligence.demand import import_demand,build_demand
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE market.current_demand_snapshots,market.demand_observations,market.demand_snapshots'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=ivi(root/'anzsco4_occupations_states_and_territories.xlsx');manifest=acquisition(root,path)
            self.assertEqual(import_demand(self.engine,manifest,'jsa_ivi')['status'],'complete')
            self.assertEqual(import_demand(self.engine,manifest,'jsa_ivi')['status'],'unchanged')
            report=build_demand(self.engine,date(2026,11,19))
            row=next(r for r in report['vacancy_trends'] if r['occupation_code']=='2613')
            self.assertEqual(row['year_on_year'],1.0)
            self.assertEqual(row['role_family'],'unmapped')
            prior=report['snapshots'][0]['id']
            ivi(path,duplicate=True);acquisition(root,path)
            with self.assertRaises(SourceError):import_demand(self.engine,manifest,'jsa_ivi')
            self.assertEqual(build_demand(self.engine,date(2026,11,19))['snapshots'][0]['id'],prior)


if __name__=='__main__':unittest.main()
