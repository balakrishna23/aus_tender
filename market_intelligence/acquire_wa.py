"""Bounded WA catalogue acquisition; no use of the legacy unbounded fetcher."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.robotparser import RobotFileParser
import uuid

from .acquire_common import PublisherClient as BaseClient, AcquisitionError, USER_AGENT

ORIGIN = 'https://catalogue.data.wa.gov.au'


class PublisherClient(BaseClient):
    def __init__(self):
        super().__init__(origin=ORIGIN, max_bytes=50_000_000)
        self.evidence = []

    def load_robots(self):
        # Explicit WA-only policy: the known HTTP 200 HTML fallback is an absent
        # robots file. Challenges still stop in fetch before reaching this code.
        body, url = self.fetch(ORIGIN + '/robots.txt', limit=1_000_000)
        if b'<html' in body.lower() or b'<!doctype html' in body.lower():
            if any(token in body.lower() for token in (b'captcha', b'awswaf', b'challenge', b'access denied')):
                raise AcquisitionError('Publisher WAF challenge; review required')
            self.robots = RobotFileParser(url)
            self.robots.parse(['User-agent: *', 'Allow: /'])
            self.evidence = ['robots_absent_html_served']
            self.request_delay = 2
        else:
            # Parse the already retrieved policy without a second request.
            self.robots = RobotFileParser(url)
            self.robots.parse(body.decode('utf-8', errors='replace').splitlines())
            rate = self.robots.request_rate(USER_AGENT)
            self.request_delay = max(2, self.robots.crawl_delay(USER_AGENT) or 0,
                                     rate.seconds / rate.requests if rate and rate.requests else 0)
            if self.request_delay > 60:
                raise AcquisitionError('Publisher robots pacing exceeds acquisition bounds')
        self.robots_body = body


def acquire(output_root, client=None):
    import state_contracts as state
    from .categories import tag
    from .extract_research import clean, save_bundle
    client = client or PublisherClient()
    run = Path(output_root).resolve() / uuid.uuid4().hex
    run.mkdir(parents=True, mode=0o700)
    manifest = dict(status='failed', files=[])
    try:
        client.load_robots()
        body, url = client.fetch(ORIGIN + '/api/3/action/package_search?' + urlencode({'q': '"Tenders WA"', 'rows': 100}), limit=5_000_000)
        try:
            state.validate_wa_response(SimpleNamespace(content=body, status_code=200, headers={}))
        except state.PublisherChallenge:
            raise AcquisitionError('Publisher WAF challenge; review required') from None
        catalogue = json.loads(body)
        result = catalogue['result']
        if not catalogue.get('success') or result['count'] > 100:
            raise AcquisitionError('WA catalogue discovery exceeds bounds')
        (run / 'catalogue.json').write_bytes(body)
        (run / 'robots.txt').write_bytes(client.robots_body)
        resources = []
        for package in result['results']:
            if state.WA_TITLE.search(package['title']):
                if package.get('license_id') not in ('cc-by', 'cc-by-4.0'):
                    raise AcquisitionError('WA licence requires review')
                for resource in package['resources']:
                    if resource.get('format', '').lower().strip('.') == 'csv':
                        resources.append((package, resource))
        if not 1 <= len(resources) <= 50:
            raise AcquisitionError('WA resource count outside bounds')
        records, editions = [], []
        resources.sort(key=lambda pair: pair[1]['id'])
        for i, (package, resource) in enumerate(resources):
            payload, resolved = client.fetch(resource['url'])
            try:
                raws = state.wa_csv_rows(SimpleNamespace(content=payload, status_code=200, headers={}))
            except state.PublisherChallenge:
                raise AcquisitionError('Publisher WAF challenge; review required') from None
            name = f'wa-{i:02d}.csv'
            (run / name).write_bytes(payload)
            manifest['files'].append(dict(path=str(run / name), sha256=hashlib.sha256(payload).hexdigest(),
                bytes=len(payload), url=resolved, dataset='wa_contracts', status='complete',
                retrieved_at=datetime.now(timezone.utc).isoformat(), source_url=ORIGIN,
                licence='CC BY 4.0', attribution='Department of Treasury and Finance (WA)'))
            editions.extend(re.findall(r'20\d{2}[-_]\d{2}', resource.get('name', '') + ' ' + resource['url']))
            source_file = resource['url'].rsplit('/', 1)[-1]
            for number, raw in enumerate(raws, 1):
                row = state.wa_row(raw)
                category, rule = tag(row['unspsc'], row['category_family'], row['description'])
                keep = ('agency', 'title', 'reference', 'procurement_method', 'unspsc', 'award_date', 'start_date',
                        'end_date', 'value', 'supplier', 'supplier_abn', 'panel')
                entry = {k: clean(row[k]) for k in keep}
                entry.update(state='WA', source_file=source_file, row_no=number, loaded_at=datetime.now(timezone.utc).isoformat(),
                    source_url=resolved, licence='CC BY 4.0; Department of Treasury and Finance (WA)',
                    source_modified=resource.get('last_modified') or package.get('metadata_modified'),
                    category_tag=category, category_rule=rule,
                    record_key=hashlib.sha256(f'WA|{source_file}|{number}'.encode()).hexdigest())
                records.append(entry)
        bundle = run / 'research.json'
        save_bundle('state_contracts', records, dict(source_url=ORIGIN, licence='CC BY 4.0',
            attribution='Department of Treasury and Finance (WA)', robots_evidence=client.evidence), bundle)
        manifest.update(status='complete', bundle=str(bundle), reference_period=max(editions, default='unknown'),
                        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), bundle_bytes=bundle.stat().st_size,
                        robots_evidence=client.evidence)
    finally:
        (run / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return run / 'manifest.json'
