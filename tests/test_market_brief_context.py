from datetime import date,datetime,timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('market_context',Path(__file__).parents[1]/'routine/scripts/market_research_context.py')
context=importlib.util.module_from_spec(spec);spec.loader.exec_module(context)


class MarketContextTests(unittest.TestCase):
    def fixture(self,path,**kw):
        data=dict(schema='illoura.market_brief.v1',as_of='2026-11-19',generated_at='2026-11-19T12:00:00+00:00',sections=[
            dict(title='Research',items=[dict(heading='Example',paragraphs=['Published research; access unknown.'],
                links=[dict(label='Official evidence',url='https://example.invalid/evidence')])])])
        data.update(kw);path.write_text(json.dumps(data));return path

    def test_valid_dated_package_and_stale_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=self.fixture(Path(tmp)/'market.json');now=datetime(2026,11,20,0,tzinfo=timezone.utc)
            sections,status=context.read_market_context(path,date(2026,11,20),now)
            self.assertEqual(status['status'],'included');self.assertIn('2026-11-19',sections[0]['title'])
            self.assertEqual(context.read_market_context(path,date(2026,11,27),datetime(2026,11,27,tzinfo=timezone.utc))[1]['status'],'unavailable')

    def test_bad_links_future_timestamp_and_missing_file_never_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'market.json';now=datetime(2026,11,19,13,tzinfo=timezone.utc)
            self.assertEqual(context.read_market_context(path,date(2026,11,19),now)[0],[])
            self.fixture(path,generated_at='2026-11-20T00:00:00+00:00')
            self.assertEqual(context.read_market_context(path,date(2026,11,19),now)[0],[])
            self.fixture(path);data=json.loads(path.read_text());data['sections'][0]['items'][0]['links'][0]['url']='javascript:alert(1)';path.write_text(json.dumps(data))
            self.assertEqual(context.read_market_context(path,date(2026,11,19),now)[0],[])


if __name__=='__main__':unittest.main()
