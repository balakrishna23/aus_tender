import csv
from datetime import date
from pathlib import Path
import tempfile
import unittest

from market_intelligence.inputs import INPUT_FIELDS,input_rows
from market_intelligence.parsers import SourceError


class BusinessInputTests(unittest.TestCase):
    def fixture(self,root,kind,rows,extra=()):
        path=root/(kind+'.csv')
        with path.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=INPUT_FIELDS[kind]+list(extra));writer.writeheader();writer.writerows(rows)
        return path

    def test_bench_is_aggregate_only_and_zero_is_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);row=dict(role_family='cyber',state='ACT',clearance='NV1',count='0',as_at='2026-11-19')
            rows=input_rows('bench',self.fixture(root,'bench',[row]),date(2026,11,19))
            self.assertEqual(rows[0]['count'],0)
            with self.assertRaises(SourceError):input_rows('bench',self.fixture(root,'bench',[dict(row,email='person@example.invalid')],['email']),date(2026,11,19))

    def test_duplicates_negative_counts_and_future_data_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=dict(role_family='cyber',state='ACT',clearance='NV1',count='3',as_at='2026-11-19')
            for rows in [[base,base],[dict(base,count='-1')],[dict(base,as_at='2026-11-20')]]:
                with self.assertRaises(SourceError):input_rows('bench',self.fixture(root,'bench',rows),date(2026,11,19))

    def test_empty_template_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SourceError):input_rows('role_map',self.fixture(Path(tmp),'role_map',[]),date(2026,11,19))


if __name__=='__main__':unittest.main()
