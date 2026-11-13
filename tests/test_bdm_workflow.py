import json
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
import re
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

import bdm_monitor as m
import bdm_workflow as w
import bdm_workbench as web
import bdm_local_runner as local


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=m.connect(self.root/'state.db');self.addCleanup(self.db.close)
        self.now=datetime(2026,11,10,12,tzinfo=timezone.utc)
        self.cfg=dict(m.DEFAULTS)
        self.row=dict(sys_id='a',rfq_id='LH-1',title='Developer',buyer='Agency',status='open',
                      rfq_closing_date='11 November 2026 11:59pm Canberra time',url='https://www.buyict.gov.au/public')
        m.observe(self.db,[self.row],self.cfg,self.now)

    def test_migration_idempotent_and_refresh_preserves_qualification(self):
        w.setup(self.db);w.setup(self.db)
        with self.db:w.save_qualification(self.db,'BUYICT:LH-1',{'access':'invited','capability':'fit'},0,self.now)
        self.row['title']='Senior developer';m.observe(self.db,[self.row],self.cfg,self.now)
        self.assertEqual(w.qualification(self.db,'BUYICT:LH-1')['access'],'invited')

    def test_concurrent_save_rejected(self):
        with self.db:w.save_qualification(self.db,'BUYICT:LH-1',{},0,self.now)
        with self.assertRaises(ValueError):w.save_qualification(self.db,'BUYICT:LH-1',{'access':'invited'},0,self.now)

    def test_ready_requires_evidence_and_no_pursue_reason(self):
        for patch_value in [{'readiness':'ready'},{'decision':'no pursue'},{'decision':'pursue'}]:
            with self.assertRaises(ValueError):w.save_qualification(self.db,'BUYICT:LH-1',patch_value,0,self.now)

    def test_expired_archived_without_erasing_stage_or_history(self):
        m.update_edits(self.db,[{'action_id':'BUYICT:LH-1','stage':'submitted','notes':'Keep me'}],self.now)
        later=self.now+timedelta(days=4)
        stats=w.reconcile(self.db,self.cfg,later)
        row=w.rows(self.db,later)[0]
        self.assertFalse(row['active']);self.assertEqual(row['notes'],'Keep me');self.assertEqual(row['stage'],'submitted')
        self.assertEqual(stats['expired_results'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM observations').fetchone()[0],1)

    def test_old_alerts_suppressed_and_latest_revision_retained(self):
        self.row['title']='New';m.observe(self.db,[self.row],self.cfg,self.now)
        cfg=dict(self.cfg,alert_since_utc=(self.now+timedelta(minutes=1)).isoformat())
        self.assertEqual(w.reconcile(self.db,cfg,self.now)['suppressed_events'],1)
        self.assertEqual(self.db.execute('SELECT status FROM events').fetchone()[0],'suppressed_stale')

    def test_desktop_duplicates_prevented_and_pending_email_unchanged(self):
        self.row['title']='New';m.observe(self.db,[self.row],self.cfg,self.now)
        cfg=dict(self.cfg,desktop_enabled=True)
        with patch.object(w,'toast',return_value=True) as toast:
            self.assertEqual(w.desktop_alerts(self.db,cfg,self.now)['requested'],1)
            self.assertEqual(w.desktop_alerts(self.db,cfg,self.now)['requested'],0)
            toast.assert_called_once()
        self.assertEqual(self.db.execute('SELECT status FROM events').fetchone()[0],'pending')

    def test_overdue_qualified_score_does_not_invent_fit(self):
        self.assertLess(w.rows(self.db,self.now)[0]['qualification_score'],50)
        with self.db:w.save_qualification(self.db,'BUYICT:LH-1',{'decision':'pursue','decision_reason':'Evidence checked','next_action_due':'2026-11-09','access':'invited','capability':'fit'},0,self.now)
        self.assertTrue(w.rows(self.db,self.now)[0]['overdue'])

    def test_card_invalid_stage_rolls_back_qualification(self):
        old=m.result_rows(self.db)[0]
        with self.assertRaises(ValueError):web.save_card(self.db,self.root,dict(action_id=old['action_id'],updated_at=old['updated_at'],qualification_version=0,qualification={'access':'invited'},human={'stage':'garbage'}),self.now)
        self.assertEqual(w.qualification(self.db,old['action_id'])['version'],0)

    def test_busy_source_distinct_from_failure(self):
        def run(command,**kwargs):
            kwargs['stdout'].write('Another run is still in progress; skipping this one.')
            return Mock(returncode=0)
        with patch.object(m.subprocess,'run',side_effect=run):
            with self.assertRaises(m.SourceBusy):m.refresh(self.root,self.root,self.now)

    def test_busy_runner_retries_then_defers_without_preview(self):
        cfg=self.root/'config.yaml';cfg.write_text('busy_retries: 1\nbusy_retry_seconds: 0\n')
        with patch.object(local.subprocess,'run',return_value=Mock(returncode=75)) as run:
            self.assertEqual(local.run_cycle(cfg,self.root,self.root/'local.db'),0)
        self.assertEqual(run.call_count,2)
        self.assertEqual(json.loads((self.root/'local_status.json').read_text())['status'],'deferred')

    def test_legacy_edits_write_back_without_losing_existing_notes(self):
        import action_tracker as tracker
        with closing(__import__('sqlite3').connect(self.root/'actions.db')) as legacy:
            legacy.executescript(tracker.SCHEMA)
            legacy.execute("INSERT INTO actions(action_id,organisation,action_type,owner,stage,status,notes,last_proposed,priority) VALUES('A-1','Partner','Partner action','Lane','proposal','in progress','Original','2026-11-10','PREPARE')");legacy.commit()
        w.legacy_actions(self.db,self.root,self.now)
        old=self.db.execute("SELECT * FROM results WHERE action_id='LEGACY:A-1'").fetchone()
        web.save_card(self.db,self.root,dict(action_id=old['action_id'],updated_at=old['updated_at'],qualification_version=0,qualification={},human={'notes':'Updated','stage':'proposal','owner':'Lane'}),self.now)
        with closing(__import__('sqlite3').connect(self.root/'actions.db')) as legacy:
            self.assertEqual(legacy.execute('SELECT notes,status FROM actions').fetchone(),('Updated','in progress'))
            self.assertEqual(legacy.execute('SELECT count(*) FROM action_history').fetchone()[0],1)

    def test_http_requires_same_origin_and_token_and_persists_edit(self):
        server=web.build_server(self.root/'state.db',self.root,self.root,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base='http://127.0.0.1:'+str(server.server_port)
        page=urllib.request.urlopen(base).read().decode()
        token=re.search("const csrf='([^']+)'",page)[1]
        result=json.load(urllib.request.urlopen(base+'/api/results'))['rows'][0]
        data=json.dumps(dict(action_id=result['action_id'],updated_at=result['updated_at'],qualification_version=0,qualification={'access':'invited'},human={'notes':'Saved over HTTP'})).encode()
        request=urllib.request.Request(base+'/api/save',data=data,headers={'Content-Type':'application/json','Origin':'https://untrusted.example','X-BDM-CSRF':token})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
        request=urllib.request.Request(base+'/api/save',data=data,headers={'Content-Type':'application/json','Origin':base,'X-BDM-CSRF':token})
        self.assertTrue(json.load(urllib.request.urlopen(request))['saved'])
        row=json.load(urllib.request.urlopen(base+'/api/results'))['rows'][0]
        self.assertEqual(row['notes'],'Saved over HTTP');self.assertEqual(row['qualification']['access'],'invited')

    def test_source_restriction_is_flagged_without_inventing_invitation(self):
        self.row['description']='This opportunity has been sent to selected suppliers only.'
        m.observe(self.db,[self.row],self.cfg,self.now)
        row=w.rows(self.db,self.now)[0]
        self.assertTrue(row['source_flags']);self.assertEqual(row['qualification']['access'],'unknown')

    def test_followup_reminder_once_per_day_and_legacy_read_stable(self):
        m.update_edits(self.db,[dict(action_id='BUYICT:LH-1',follow_up='2026-11-10')],self.now)
        cfg=dict(self.cfg,desktop_enabled=True)
        with patch.object(w,'toast',return_value=True) as toast:
            self.assertEqual(w.due_reminders(self.db,cfg,self.now),1)
            self.assertEqual(w.due_reminders(self.db,cfg,self.now+timedelta(hours=1)),0)
            toast.assert_called_once()

    def test_long_source_refresh_does_not_hold_edit_lock(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def refresh(*args):
            entered.set();release.wait(3);return 'fixture source report'
        def worker():
            try:m.main()
            except Exception as exc:errors.append(exc)
        argv=['bdm_monitor.py','--state',str(self.root/'state.db'),'--out',str(self.root/'exports'),'--data-dir',str(self.root),'poll','--refresh']
        with patch.object(m.sys,'argv',argv),patch.object(m,'refresh',side_effect=refresh),patch.object(m,'read_rows',return_value=[self.row]):
            thread=threading.Thread(target=worker);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with m.lock(self.root/'state.lock'):
                    pass
            finally:
                release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

    def test_csv_cannot_write_an_independent_shadow_legacy_decision(self):
        with self.assertRaisesRegex(ValueError,'working view'):
            m.update_edits(self.db,[{'action_id':'BUYICT:LH-1','notes':'must rollback'},{'action_id':'LEGACY:A-1','notes':'shadow'}],self.now)
        self.assertEqual(m.result_rows(self.db)[0]['notes'],'')


if __name__=='__main__':unittest.main()
