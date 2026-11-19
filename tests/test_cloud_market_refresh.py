"""Research deadlines/failures remain isolated from live RFQ sending."""
from datetime import datetime,timedelta,timezone
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT=Path(__file__).parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
worker=load('market_refresh_worker',ROOT/'deploy/illoura_market_refresh.py')
brief=load('market_refresh_brief',ROOT/'deploy/illoura_daily_brief.py')
NOW=datetime(2026,11,19,21,tzinfo=timezone.utc)


class ResearchRefreshIsolationTests(unittest.TestCase):
    def test_invalid_new_package_preserves_previous_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'market_brief.json';source.write_text('{}');destination=root/'published.json';destination.write_text('previous package')
            modules={'pwd':SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=source.stat().st_uid)),
                     'grp':SimpleNamespace(),
                     'market_research_context':SimpleNamespace(read_market_context=lambda *args:([],{'status':'unavailable'}))}
            with patch.dict('sys.modules',modules),self.assertRaises(ValueError):
                worker.publish_package(source,root,destination,root/'state')
            self.assertEqual(destination.read_text(),'previous package')
            self.assertFalse((root/'state').exists())

    def test_missing_config_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(worker,'command') as command:
            self.assertEqual(worker.refresh(Path(tmp)/'absent.json')['status'],'disabled')
            command.assert_not_called()

    def test_morning_reserve_is_preserved(self):
        with patch.object(brief.Path,'is_file',return_value=True),patch.object(brief,'utc_now',return_value=NOW),patch.object(brief,'bounded_command') as run:
            result=brief.refresh_market_research(NOW+timedelta(seconds=20))
            self.assertEqual(result['status'],'skipped');run.assert_not_called()

    def test_failed_research_is_returned_without_cancelling_brief(self):
        for failure in [subprocess.TimeoutExpired('research',10),subprocess.CalledProcessError(1,['research']),OSError('fixture')]:
            with patch.object(brief.Path,'is_file',return_value=True),patch.object(brief,'utc_now',return_value=NOW),patch.object(brief,'bounded_command',side_effect=failure) as run:
                self.assertEqual(brief.refresh_market_research(NOW+timedelta(hours=3))['status'],'failed')
                self.assertEqual(run.call_args.args[1],215)

    def test_success_is_bounded(self):
        with patch.object(brief.Path,'is_file',return_value=True),patch.object(brief,'utc_now',return_value=NOW),patch.object(brief,'bounded_command') as run:
            self.assertEqual(brief.refresh_market_research(NOW+timedelta(hours=3))['status'],'completed')
            self.assertEqual(run.call_args.args[0][-1],'210')


if __name__=='__main__':unittest.main()
