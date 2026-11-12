"""A failed new run must be visible even while yesterday's data is still fresh."""
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest
from datetime import datetime,timezone,timedelta

deploy=Path(__file__).resolve().parents[1]/'deploy'
sys.path.insert(0,str(deploy))
spec=importlib.util.spec_from_file_location('cloud_health',deploy/'illoura_cloud_health.py')
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)

class CloudHealth(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'work';self.served=self.root/'served'
        self.run=self.work/'reports/daily_sweep_fixture';self.run.mkdir(parents=True)
        (self.served/'current').mkdir(parents=True)
        stamp=datetime.now(timezone.utc).isoformat()
        self.write(self.work/'latest_run.json',{'run_dir':str(self.run),'status':'sent'})
        self.write(self.run/'newsletter_evidence.json',{'generated_at_utc':stamp})
        self.write(self.served/'current/manifest.json',{'snapshot_finished_utc':stamp})
    def write(self,path,value):path.write_text(json.dumps(value))
    def check(self):return health.check(self.work,self.served,self.root/'missing-token.json')
    def test_failure_is_not_hidden_by_fresh_previous_run(self):
        self.write(self.work/'coordinator_status.json',{'status':'failed','started_at_utc':datetime.now(timezone.utc).isoformat()})
        result=self.check()
        self.assertEqual(result['checks']['publication']['status'],'ok')
        self.assertEqual(result['checks']['coordinator']['status'],'attention')
        self.assertEqual(result['status'],'attention')
    def test_hung_coordinator_requires_attention(self):
        self.write(self.work/'coordinator_status.json',{'status':'running','started_at_utc':(datetime.now(timezone.utc)-timedelta(hours=6)).isoformat()})
        self.assertEqual(self.check()['checks']['coordinator']['status'],'attention')
    def test_missing_mail_credentials_are_explicit(self):
        with patch.dict('os.environ',{'ILLOURA_GMAIL_SENDER':'alerts@example.com'}):
            result=self.check()
        self.assertEqual(result['checks']['mail_authorization']['status'],'failed')
        self.assertFalse(result['email_sent'])
    def test_unconfigured_sender_requires_attention_without_crashing(self):
        with patch.dict('os.environ',{},clear=False):
            os.environ.pop('ILLOURA_GMAIL_SENDER',None)
            result=self.check()
        self.assertEqual(result['checks']['mail_authorization']['status'],'unconfigured')
        self.assertEqual(result['status'],'attention')
        self.assertFalse(result['email_sent'])
    def test_source_contains_no_real_mailbox(self):
        self.assertNotIn('illouraservices.com',(deploy/'illoura_cloud_health.py').read_text(encoding='utf-8'))
    def test_retention_reclaimed_space_is_reported(self):
        self.write(self.served/'retention.json',{'status':'ok','reclaimed_bytes':4096,
            'observed_free_bytes_change':2048,'retained_generation_bytes':8000,
            'retained_backup_bytes':2000,'deleted':[{'name':'fixture'}]})
        result=self.check()['checks']['retention']
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['reclaimed_bytes'],4096)
        self.assertEqual(result['observed_free_bytes_change'],2048)
    def test_retention_failure_requires_attention(self):
        self.write(self.served/'retention.json',{'status':'attention','errors':[{'error_type':'OSError'}]})
        self.assertEqual(self.check()['checks']['retention']['status'],'attention')

if __name__=='__main__':unittest.main()
