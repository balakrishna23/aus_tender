from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from market_intelligence.extract_forward import FIELDS,export_forward


class ForwardExportTests(unittest.TestCase):
    def test_whitelist_immutable_and_source_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.db';out=root/'out.db'
            with closing(sqlite3.connect(source)) as conn,conn:
                conn.execute('CREATE TABLE opportunities('+','.join(f+' TEXT' for f in FIELDS)+',contact_email TEXT)')
                row=dict(source='AusTender plan',source_id='P1',title='ICT services contact person@example.invalid',buyer='Example Agency',
                         url='https://www.tenders.gov.au/plan',retrieved_at='2026-11-19',contact_email='person@example.invalid')
                conn.execute('INSERT INTO opportunities VALUES('+','.join('?' for _ in range(len(FIELDS)+1))+')',
                             [row.get(k) for k in FIELDS+['contact_email']])
            original=source.read_bytes()
            self.assertEqual(export_forward(source,out)['rows'],1)
            self.assertEqual(source.read_bytes(),original)
            self.assertNotIn(b'person@example.invalid',out.read_bytes())
            with closing(sqlite3.connect(out)) as conn:
                self.assertNotIn('contact_email',[r[1] for r in conn.execute('PRAGMA table_info(opportunities)')])
            with self.assertRaises(ValueError):export_forward(source,out)


if __name__=='__main__':unittest.main()
