"""Server-side, direct HTTP acquisition of official REOS and TNV workbooks.

Example (run on the server, after reviewing publisher terms)::

    python -m market_intelligence.acquire_jsa --dataset jsa_reos jsa_tnv \
        --output-root reports/jsa_acquisitions --previous-manifest /path/manifest.json

No database access. Each invocation creates a fresh directory; only a complete
manifest is importable. Reference month comes from the filename, upload month
from its directory, and observation periods remain the workbook parser's concern.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit
import uuid


ORIGIN = 'https://www.jobsandskills.gov.au'
USER_AGENT = 'OpenData-Acquisition/1.0'
LICENCE_URL = 'https://creativecommons.org/licenses/by/4.0/'
DATASETS = {
    'jsa_reos': ('/data/recruitment-experiences-and-outlook-survey',
                 r'recruitment_insights_report_-_({month})_(\d{{4}})(?:_data_file)?\.xlsx'),
    'jsa_tnv': ('/data/total-new-vacancies',
                r'total_new_vacancies_({month})_(\d{{4}})(?:_data_file)?\.xlsx'),
}
MONTHS = ('january february march april may june july august september october november december').split()


from .acquire_common import AcquisitionError, Anchors, observed_now, check_workbook
from . import acquire_common as common


def official_url(url):
    return common.official_url(url, ORIGIN)


class OfficialRedirects(common.OfficialRedirects):
    def __init__(self, allow=None):
        super().__init__(allow, ORIGIN)


class PublisherClient(common.PublisherClient):
    def __init__(self, timeout=20, attempts=2, max_bytes=20_000_000):
        super().__init__(timeout, attempts, max_bytes, origin=ORIGIN)


def discover(body, source_url, dataset):
    pattern = re.compile(DATASETS[dataset][1].format(month='|'.join(MONTHS)), re.I)
    candidates = {}
    for href in Anchors(body).links:
        url = urljoin(source_url, href)
        parts = urlsplit(url)
        # Queries/fragments do not identify additional editions; ignore fragments.
        path = unquote(parts.path)
        match = re.fullmatch(r'/sites/default/files/(\d{4}-(?:0[1-9]|1[0-2]))/([^/]+)', path)
        if not match:
            continue
        filename = pattern.fullmatch(match[2])
        if not filename:
            continue
        official_url(url)
        if parts.query:
            raise AcquisitionError('Ambiguous workbook URL with query')
        url = parts._replace(fragment='').geturl()
        reference = f'{int(filename[2]):04d}-{MONTHS.index(filename[1].lower()) + 1:02d}'
        if reference.startswith('0000'):
            raise AcquisitionError('Invalid reference month')
        candidates[url] = (reference, match[1])
    if not candidates:
        raise AcquisitionError('Ambiguous discovery: zero matching workbook links')
    newest = max(v[0] for v in candidates.values())
    winners = [(url, *periods) for url, periods in candidates.items() if periods[0] == newest]
    if len(winners) != 1:
        raise AcquisitionError('Ambiguous discovery: multiple newest workbook links')
    return winners[0]


def licence_evidence(client, body, source_url):
    """Require a CC BY 4.0 link on the dataset or its official copyright page.

    This verifies the publisher's statement, not legal applicability to individual
    tables. The operator must review scope/exclusions and terms on the server.
    """
    def cc_link(page, base):
        return any(urljoin(base, href).rstrip('/') == LICENCE_URL.rstrip('/')
                   for href in Anchors(page).links)

    if cc_link(body, source_url):
        return source_url
    pages = set()
    for href in Anchors(body).links:
        url = urljoin(source_url, href)
        if re.search(r'copyright|licen[cs]', urlsplit(url).path, re.I):
            official_url(url)
            pages.add(urlsplit(url)._replace(fragment='').geturl())
    if len(pages) != 1:
        raise AcquisitionError('Publisher licence evidence missing or ambiguous; review licence page')
    page, resolved = client.fetch(pages.pop(), limit=2_000_000)
    if not cc_link(page, resolved):
        raise AcquisitionError('Publisher licence page does not link to CC BY 4.0; review required')
    return resolved


def earlier_hashes(manifests):
    hashes = set()
    for path in manifests:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('status') != 'complete':
            raise AcquisitionError('Previous manifest is not complete')
        for entry in data['files']:
            if entry.get('dataset') in DATASETS and entry.get('status') == 'complete':
                digest = entry.get('sha256', '')
                if not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise AcquisitionError('Invalid previous manifest checksum')
                hashes.add((entry['dataset'], digest))
    return hashes



def acquire(output_root, datasets, previous_manifests=(), client=None):
    datasets = list(dict.fromkeys(datasets))
    if not datasets or any(d not in DATASETS for d in datasets):
        raise ValueError('Choose jsa_reos and/or jsa_tnv')
    previous = earlier_hashes(previous_manifests)
    root = Path(output_root).resolve()
    run = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + uuid.uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    manifest = {'status': 'failed', 'started_at': observed_now(), 'datasets': [], 'files': []}
    client = client or PublisherClient()
    try:
        client.load_robots()
        for dataset in datasets:
            source_url = ORIGIN + DATASETS[dataset][0]
            body, page_url = client.fetch(source_url, limit=2_000_000)
            page_observed_at = observed_now()
            url, reference, upload = discover(body, page_url, dataset)
            evidence = licence_evidence(client, body, page_url)
            payload, resolved = client.fetch(url)
            official_url(resolved)
            check_workbook(payload)
            retrieved_at = observed_now()
            digest = hashlib.sha256(payload).hexdigest()
            path = run / (dataset + '.xlsx')
            with path.open('xb') as stream:
                stream.write(payload)
            manifest['datasets'].append({'key': dataset, 'source_url': source_url})
            manifest['files'].append(dict(
                dataset=dataset, status='complete', path=str(path), url=url, resolved_url=resolved,
                source_url=source_url, page_resolved_url=page_url,
                licence='CC BY 4.0', licence_url=LICENCE_URL, licence_evidence_url=evidence,
                bytes=len(payload), sha256=digest, retrieved_at=retrieved_at,
                reference_month=reference, upload_month=upload, page_observed_at=page_observed_at,
                acquisition_status='unchanged' if (dataset, digest) in previous else 'downloaded'))
        manifest['status'] = 'complete'
        manifest['acquisition_status'] = ('unchanged' if all(
            f['acquisition_status'] == 'unchanged' for f in manifest['files']) else 'downloaded')
    except Exception:
        # A failed new directory is retained, never promoted or allowed to alter
        # an earlier successful manifest. Avoid recording arbitrary exception text.
        manifest['error'] = 'acquisition_failed'
        raise
    finally:
        manifest['finished_at'] = observed_now()
        with (run / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
    return run / 'manifest.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', nargs='+', choices=sorted(DATASETS), required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--previous-manifest', type=Path, action='append', default=[])
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--attempts', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-bytes', type=int, default=20_000_000)
    args = parser.parse_args(argv)
    try:
        client = PublisherClient(args.timeout, args.attempts, args.max_bytes)
        path = acquire(args.output_root, args.dataset, args.previous_manifest, client)
    except (AcquisitionError, ValueError, OSError, KeyError):
        # Known acquisition failures have safe messages; unexpected/local failures
        # must not expose private paths or arbitrary manifest/response contents.
        import sys
        exc = sys.exception()
        print(str(exc) if isinstance(exc, AcquisitionError) else 'Acquisition failed: check local configuration', file=sys.stderr)
        return 1
    status = json.loads(path.read_text(encoding='utf-8'))['acquisition_status']
    print(json.dumps({'status': status, 'manifest': str(path)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
