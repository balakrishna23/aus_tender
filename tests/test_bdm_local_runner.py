import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch

import bdm_local_runner as local
import bdm_monitor as monitor


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name); self.state=self.out/'state.db'
        self.config=self.out/'monitor.yaml'; self.config.write_text('send_enabled: false\n',encoding='utf-8')

    def test_failure_does_not_claim_success_or_run_preview(self):
        with patch.object(local.subprocess,'run',return_value=Mock(returncode=1)) as run:
            self.assertEqual(local.run_cycle(self.config,self.out,self.state),1)
        run.assert_called_once()
        status=json.loads((self.out/'local_status.json').read_text())
        self.assertEqual(status['status'],'poll_failed'); self.assertFalse(status['email_enabled'])

    def test_default_cycle_checks_source_without_sending(self):
        with patch.object(local.subprocess,'run',return_value=Mock(returncode=0)) as run:
            self.assertEqual(local.run_cycle(self.config,self.out,self.state),0)
        self.assertIn('--refresh',run.call_args_list[0].args[0])
        self.assertNotIn('--send',run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][-1],'preview')

    def test_status_page_includes_dynamic_staleness_check(self):
        with closing(monitor.connect(self.state)) as db:
            with db:
                db.execute("INSERT INTO meta VALUES('last_live_refresh_at',?)",(datetime.now(timezone.utc).isoformat(),))
        local.write_status(self.out,self.state,monitor.DEFAULTS,{'status':'ok'})
        page=(self.out/'index.html').read_text()
        self.assertIn('Monitoring successfully',page)
        self.assertIn('Date.now()-Date.parse(lastSuccess)>45*60000',page)
        self.assertIn('no automatic email',page)

    def test_local_journal_keeps_all_events_without_duplicates(self):
        now=datetime(2026,11,6,tzinfo=timezone.utc)
        with closing(monitor.connect(self.state)) as db:
            row={'sys_id':'a','rfq_id':'LH-1','title':'Developer','buyer':'Agency','status':'open',
                 'rfq_closing_date':'9 November 2026','url':'https://www.buyict.gov.au/public'}
            monitor.observe(db,[row],monitor.DEFAULTS,now)
            row['title']='Senior Developer'; monitor.observe(db,[row],monitor.DEFAULTS,now)
            row['title']='Lead Developer'; monitor.observe(db,[row],monitor.DEFAULTS,now)
            folder=self.out/'journal'
            self.assertEqual(local.record_local_messages(db,folder,monitor.DEFAULTS),2)
            self.assertEqual(local.record_local_messages(db,folder,monitor.DEFAULTS),2)
            self.assertEqual(len(list((folder/'messages').glob('*.json'))),2)
            text=(folder/'queued_messages.txt').read_text(encoding='utf-8')
            self.assertEqual(text.count('Event: '),2)
            self.assertIn('superseded',text); self.assertIn('LOCAL RECORD',text)


if __name__=='__main__': unittest.main()
