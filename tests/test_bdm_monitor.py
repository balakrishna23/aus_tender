import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import bdm_monitor as m
from spiders.buy_ict import BuyIctApiSpider


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = m.connect(self.root/'state.db'); self.addCleanup(self.db.close)
        self.now = datetime(2026,11,6,0,tzinfo=timezone.utc)
        self.cfg = copy.deepcopy(m.DEFAULTS)
        self.cfg.update(sender='alerts@example.com', recipient='team@example.com')
        self.row = dict(sys_id='abc',rfq_id='LH-1',title='Cyber specialist',buyer='Agency',
                        status='open',rfq_closing_date='Monday, 09 November 2026 11:59pm, Canberra time',
                        rfq_published_date='Friday, 06 November 2026',requirements='{"Security clearance":"NV1"}',
                        url='https://www.buyict.gov.au/public?id=opportunity_details',scraped_at='2026-11-06T10:00:00')

    def observe(self, rows=None, delta=0):
        return m.observe(self.db, rows or [self.row],self.cfg,self.now+timedelta(minutes=delta))

    def events(self):
        return self.db.execute('SELECT * FROM events ORDER BY revision').fetchall()

    def test_baseline_and_timestamp_only_updates_do_not_alert(self):
        self.assertTrue(self.observe()['baseline_created'])
        self.row['scraped_at']='tomorrow'; self.row['updated_at']='tomorrow'
        self.assertEqual(self.observe()['new_events'],0); self.assertEqual(len(self.events()),0)

    def test_new_change_coalescing_closure_and_reopening(self):
        self.observe(); second=dict(self.row,sys_id='xyz',rfq_id='LH-2')
        self.assertEqual(self.observe([self.row,second])['new_events'],1)
        second['rfq_closing_date']='Tuesday, 10 November 2026 11:59pm, Canberra time'
        self.observe([self.row,second]); self.assertEqual([e['status'] for e in self.events()],['superseded','pending'])
        second['status']='closed'; self.observe([self.row,second])
        self.assertEqual(self.events()[-1]['kind'],'closed_or_expired')
        second['status']='open'; self.observe([self.row,second])
        self.assertEqual(self.events()[-1]['kind'],'reopened')

    def test_empty_duplicate_source_does_not_advance_state(self):
        with self.assertRaises(ValueError): m.observe(self.db,[],self.cfg,self.now)
        with self.assertRaises(ValueError): self.observe([self.row,self.row])
        self.assertFalse(self.db.execute('SELECT 1 FROM meta').fetchone())

    def test_expired_and_irrelevant_new_rows_not_queued(self):
        self.observe(); self.cfg['title_keywords']=['cyber']
        self.observe([self.row,dict(self.row,sys_id='2',rfq_id='LH-2',title='Painter'),
                      dict(self.row,sys_id='3',rfq_id='LH-3',rfq_closing_date='1 November 2026')])
        self.assertEqual(len(self.events()),0)

    def test_source_disappearance_does_not_invent_closure(self):
        self.observe(); self.observe([dict(self.row,sys_id='2',rfq_id='LH-2')])
        self.assertTrue(self.db.execute("SELECT is_open FROM observations WHERE source_id='abc'").fetchone()[0])

    def test_human_edits_survive_refresh_and_invalid_import_is_atomic(self):
        self.observe()
        m.update_edits(self.db,[dict(action_id='BUYICT:LH-1',owner='Lane',stage='preparing',notes='Keep me')],self.now)
        self.row['title']='Updated cyber role'; self.observe()
        row=m.result_rows(self.db)[0]
        self.assertEqual((row['owner'],row['stage'],row['notes']),('Lane','preparing','Keep me'))
        with self.assertRaises(ValueError):
            m.update_edits(self.db,[dict(action_id='BUYICT:LH-1',notes='should rollback'),dict(action_id='missing',stage='won')],self.now)
        self.assertEqual(m.result_rows(self.db)[0]['notes'],'Keep me')

    def ready_delivery(self):
        self.observe(); self.row['title']='New cyber requirements'; self.observe()
        with self.db: self.db.execute("INSERT INTO meta VALUES('last_live_refresh_at',?)",(self.now.isoformat(),))
        self.cfg['send_enabled']=True

    def test_disabled_or_stale_delivery_does_not_call_transport(self):
        self.ready_delivery(); transport=Mock(); self.cfg['send_enabled']=False
        with self.assertRaises(ValueError): m.deliver(self.db,self.cfg,self.root,self.now,transport)
        self.cfg['send_enabled']=True
        with self.assertRaises(ValueError): m.deliver(self.db,self.cfg,self.root,self.now+timedelta(hours=2),transport)
        transport.send.assert_not_called()

    def test_success_is_durable_and_does_not_repeat(self):
        self.ready_delivery(); transport=Mock(); transport.send.return_value='gmail123'
        self.assertEqual(m.deliver(self.db,self.cfg,self.root,self.now,transport)['sent'],1)
        self.assertEqual(m.deliver(self.db,self.cfg,self.root,self.now,transport)['sent'],0)
        transport.send.assert_called_once()
        msg=transport.send.call_args[0][0]; self.assertEqual(msg['To'],'team@example.com')
        self.assertEqual(msg['From'],'alerts@example.com')
        self.assertEqual(msg.get_content_type(),'multipart/alternative')

    def test_timeout_blocks_automatic_retry(self):
        self.ready_delivery(); transport=Mock(); transport.send.side_effect=TimeoutError()
        with self.assertRaises(RuntimeError): m.deliver(self.db,self.cfg,self.root,self.now,transport)
        with self.assertRaises(RuntimeError): m.deliver(self.db,self.cfg,self.root,self.now,transport)
        transport.send.assert_called_once()
        self.assertEqual(self.db.execute('SELECT status FROM deliveries').fetchone()[0],'uncertain')

    def test_queued_new_listing_is_not_sent_after_its_deadline(self):
        self.ready_delivery()
        self.assertEqual(m.pending_events(self.db,self.cfg,self.now+timedelta(days=5)),[])

    def test_same_content_can_return_after_a_material_change(self):
        self.observe(); original=self.row['title']; self.row['title']='Changed'; self.observe()
        self.row['title']=original; self.observe()
        self.assertEqual(len(self.events()),2)
        self.assertEqual([e['revision'] for e in self.events()],[2,3])

    def test_closed_detail_overrides_open_listing(self):
        self.assertEqual(BuyIctApiSpider._public_status({'rfqOpen':False},{'_status':'open'}),'closed')
        self.assertEqual(BuyIctApiSpider._public_status({'rfqOpen':True,'is_cancelled':True},{'_status':'open'}),'cancelled')

    def test_csv_formula_text_and_export(self):
        self.row['title']='=HYPERLINK("bad")'; self.observe(); m.export_results(self.db,self.root,self.now)
        self.assertIn("'=HYPERLINK",(self.root/'results.csv').read_text(encoding='utf-8-sig'))

    def test_failed_or_skipped_refresh_rejected(self):
        with patch.object(m.subprocess,'run',return_value=Mock(returncode=0)):
            with self.assertRaises(RuntimeError): m.refresh(self.root,self.root,self.now)

    def test_lock_excludes_another_monitor_operation(self):
        with m.lock(self.root/'monitor.lock'):
            with self.assertRaises(RuntimeError):
                with m.lock(self.root/'monitor.lock'): pass

    def test_friendly_csv_headers_and_title_rows_import(self):
        path=self.root/'edited.csv'
        path.write_text('Illoura results\n\nAction ID,Owner,Stage,Follow up (YYYY-MM-DD),Notes,Outcome\nBUYICT:LH-1,Sirish,qualified,2026-11-09,Keep,\n',encoding='utf-8')
        self.observe(); rows=m.load_edits(path); m.update_edits(self.db,rows,self.now)
        self.assertEqual(m.result_rows(self.db)[0]['stage'],'qualified')

    def test_xlsx_import_reads_results_and_preserves_ids(self):
        sheet=Mock(); sheet.values=[('Title',None),('Action ID','Follow up (YYYY-MM-DD)'),('BUYICT:LH-1',datetime(2026,11,9))]
        class Book:
            sheetnames=['Results']
            def __getitem__(self,key): return sheet
            def close(self): pass
        with patch('openpyxl.load_workbook',return_value=Book()):
            rows=m.load_edits(self.root/'edited.xlsx')
        self.observe(); m.update_edits(self.db,rows,self.now)
        self.assertEqual(m.result_rows(self.db)[0]['follow_up'],'2026-11-09')


class MailboxGuardTests(unittest.TestCase):
    """No real addresses in source: they come from private config or environment."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'monitor.yaml'
        env = patch.dict('os.environ', {}, clear=False); env.start(); self.addCleanup(env.stop)
        import os
        os.environ.pop('ILLOURA_MAIL_SENDER', None); os.environ.pop('ILLOURA_MAIL_RECIPIENT', None)

    def write(self, text):
        self.path.write_text(text, encoding='utf-8'); return self.path

    def test_source_defaults_contain_no_mailbox(self):
        self.assertEqual((m.DEFAULTS['sender'], m.DEFAULTS['recipient']), ('', ''))

    def test_unconfigured_preview_is_allowed(self):
        cfg = m.config(self.write('send_enabled: false\n'))
        self.assertEqual((cfg['sender'], cfg['recipient']), ('', ''))

    def test_private_config_pair_is_used_and_normalised(self):
        cfg = m.config(self.write('sender: Alerts@Example.com\nrecipient: team@example.com\n'))
        self.assertEqual((cfg['sender'], cfg['recipient']), ('alerts@example.com', 'team@example.com'))

    def test_environment_pair_is_used(self):
        with patch.dict('os.environ', {'ILLOURA_MAIL_SENDER': 'alerts@example.com',
                                       'ILLOURA_MAIL_RECIPIENT': 'team@example.com'}):
            cfg = m.config(None)
        self.assertEqual(cfg['recipient'], 'team@example.com')

    def test_delivery_requires_a_configured_pair(self):
        with self.assertRaises(ValueError):
            m.config(self.write('send_enabled: true\n'))

    def test_external_domain_recipient_is_refused(self):
        with self.assertRaises(ValueError):
            m.config(self.write('sender: alerts@example.com\nrecipient: buyer@agency.example.gov\n'))

    def test_multiple_recipients_are_refused(self):
        with self.assertRaises(ValueError):
            m.config(self.write('sender: alerts@example.com\nrecipient: "a@example.com, b@example.com"\n'))

    def test_deliver_rechecks_pair_for_unvalidated_dicts(self):
        cfg = dict(m.DEFAULTS, send_enabled=True)
        with self.assertRaises(ValueError):
            m.deliver(Mock(), cfg, Path(self.tmp.name), datetime(2026,11,6,tzinfo=timezone.utc), Mock())


if __name__=='__main__': unittest.main()
