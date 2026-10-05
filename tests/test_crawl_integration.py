"""Exercise Scrapy, request middleware, pipelines and SQLite without live services."""
import functools
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from project_paths import PROJECT_DIR

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class CrawlIntegrationTests(unittest.TestCase):
    def test_listing_to_database_and_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('''<div class="list-desc"><span>ATM ID:</span>
                <div class="list-desc-inner"><a href="/Atm/Show/one">ATM-1</a></div></div>''')
            detail = root / 'Atm' / 'Show' / 'one'
            detail.parent.mkdir(parents=True)
            detail.write_text('''<p class="lead">Sample tender</p>
                <div class="list-desc"><span><label>ATM ID</label></span><div class="list-desc-inner">ATM-1</div></div>
                <div class="list-desc"><span><label>Agency</label></span><div class="list-desc-inner">Agency One</div></div>''')
            server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            env = {**os.environ, 'AUSTENDER_DB_DIR': str(root / 'db'), 'GSHEETS_AUTO_SYNC': 'False'}
            script = '''
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from spiders.aus_tender import AusTenderSpider
import sys
settings = get_project_settings()
settings.set('LOG_FILE', sys.argv[2], priority='cmdline')
settings.set('DOWNLOAD_DELAY', 0, priority='cmdline')
settings.set('AUTOTHROTTLE_ENABLED', False, priority='cmdline')
process = CrawlerProcess(settings)
crawler = process.create_crawler(AusTenderSpider)
process.crawl(crawler, start_urls=[sys.argv[1]])
process.start()
stats = crawler.stats.get_stats()
assert stats.get('database/items_saved') == 1, stats
assert not stats.get('log_count/ERROR'), stats
'''
            try:
                for agency in ('Agency One', 'Agency Two'):
                    detail.write_text(detail.read_text().replace('Agency One', agency))
                    result = subprocess.run([sys.executable, '-c', script,
                                             f'http://127.0.0.1:{server.server_port}/index.html', str(root / 'crawl.log')],
                                            cwd=PROJECT_DIR, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr + (root / 'crawl.log').read_text())
                    with closing(sqlite3.connect(root / 'db' / 'aus_tender.db')) as conn:
                        self.assertEqual(conn.execute('SELECT atm_id, agency FROM tenders').fetchall(), [('ATM-1', agency)])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
