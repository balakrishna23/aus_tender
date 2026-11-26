"""Research failures alert independently without changing procurement readiness."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
spec=importlib.util.spec_from_file_location('audit_health',ROOT/'deploy/illoura_cloud_health.py')
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)


class ResearchMonitor(unittest.TestCase):
    def test_refresh_failure_and_old_package_are_independently_visible(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.touch();state=root/'state.json'
            state.write_text(json.dumps({'status':'failed','checked_at':now.isoformat()}))
            (root/'market_research.json').write_text(json.dumps({'generated_at':(now-timedelta(days=4)).isoformat()}))
            with patch.object(health,'research_freshness',return_value={'availability':'available','sources':{'abn':'latest_available'}}):
                result=health.research_monitor(root,now,state,config)
            self.assertEqual(result['status'],'attention')
            self.assertEqual(result['checks']['refresh']['last_result'],'failed')
            self.assertEqual(result['checks']['package']['status'],'attention')

    def test_current_package_and_all_publisher_checks_recover(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.touch();state=root/'state.json'
            state.write_text(json.dumps({'status':'complete','checked_at':now.isoformat()}))
            (root/'market_research.json').write_text(json.dumps({'generated_at':now.isoformat()}))
            with patch.object(health,'research_freshness',return_value={'availability':'available','sources':{'abn':'latest_available'}}):
                self.assertEqual(health.research_monitor(root,now,state,config)['status'],'ok')
            with patch.object(health,'research_freshness',return_value={'availability':'available','sources':{'jsa_ivi':'failed'}}):
                result=health.research_monitor(root,now,state,config)
                self.assertEqual(result['status'],'attention')
                self.assertEqual(result['checks']['publishers']['source_gaps'],['jsa_ivi'])

    def test_known_blocked_and_manual_sources_do_not_hold_the_alarm(self):
        now=datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.touch();state=root/'state.json'
            state.write_text(json.dumps({'status':'complete','checked_at':now.isoformat()}))
            (root/'market_research.json').write_text(json.dumps({'generated_at':now.isoformat()}))
            sources={'jsa_ivi':'latest_available','wa_contracts':'blocked','abn':'manual_refresh_needed',
                     'aps_employment':'new_edition_pending'}
            with patch.object(health,'research_freshness',return_value={'availability':'available','sources':sources}):
                result=health.research_monitor(root,now,state,config)
            self.assertEqual(result['status'],'ok')
            self.assertEqual(result['checks']['publishers']['failing'],[])
            self.assertEqual(result['checks']['publishers']['source_gaps'],['abn','aps_employment','wa_contracts'])
            for broken in ('failed','check_overdue'):
                with patch.object(health,'research_freshness',
                                  return_value={'availability':'available','sources':dict(sources,jsa_ivi=broken)}):
                    result=health.research_monitor(root,now,state,config)
                self.assertEqual(result['status'],'attention',broken)
                self.assertEqual(result['checks']['publishers']['failing'],['jsa_ivi'])

    def test_malformed_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.touch();state=root/'state.json';state.write_text('[]')
            with patch.object(health,'research_freshness',return_value={}):
                self.assertEqual(health.research_monitor(root,state=state,config=config)['status'],'attention')
