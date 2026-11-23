"""Server-side, direct HTTP acquisition of official JSA demand workbooks.

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
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time  # noqa: F401 - tests patch acquire_jsa.time (sleep/monotonic)
from urllib.parse import unquote, urljoin, urlsplit
import uuid


ORIGIN = 'https://www.jobsandskills.gov.au'
USER_AGENT = 'OpenData-Acquisition/1.0'
LICENCE_URL = 'https://creativecommons.org/licenses/by/4.0/'
DATASETS = {
    'jsa_ivi': ('/data/internet-vacancy-index',
                r'internet_vacancies_anzsco4_occupations_states_and_territories_-_({month})_(\d{{4}})\.xlsx'),
    'jsa_projections': ('/data/employment-projections',
                        r'employment_projections_-_({month})_(\d{{4}})_to_may_\d{{4}}\.xlsx'),
    'jsa_shortages': ('/data/occupation-shortage',
                     r'(\d{{4}})_unit_group_shortage_list_-_4_digit_anzsco\.xlsx'),
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
        if dataset == 'jsa_shortages':
            reference = f'{int(filename[1]):04d}-01'
        else:
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


class LicenceText(HTMLParser):
    """Keep list-item boundaries for exception extraction, including inline markup."""
    def __init__(self, body):
        super().__init__()
        self.parts = []
        self.feed(body.decode('utf-8', errors='replace'))

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.append(' ')

    def handle_endtag(self, tag):
        self.parts.append(';' if tag == 'li' else ' ')

    def text(self):
        return re.sub(r'\s+', ' ', ''.join(self.parts)).strip()


def licence_evidence(client, body, source_url):
    """Retain one official licence page, accepting only unambiguous CC BY 4.0."""
    pages = set()
    for href in Anchors(body).links:
        url = urljoin(source_url, href)
        if urlsplit(url).hostname in ('creativecommons.org', 'www.creativecommons.org'):
            continue
        if re.search(r'copyright|licen[cs]', urlsplit(url).path, re.I):
            official_url(url)
            pages.add(urlsplit(url)._replace(fragment='').geturl())
    if len(pages) != 1:
        raise AcquisitionError('Publisher licence evidence missing or ambiguous; review licence page')
    page, resolved = client.fetch(pages.pop(), limit=2_000_000)
    official_url(resolved)
    statement = 'Creative Commons Attribution 4.0 International'
    texts = []
    for content, base in ((body, source_url), (page, resolved)):
        visible = html.unescape(re.sub(r'<[^>]*>', ' ', content.decode('utf-8', errors='replace')))
        visible = re.sub(r'\s+', ' ', visible).strip()
        texts.append(visible)
        # Check every licence link, including conflicts alongside a valid deed.
        for href in Anchors(content).links:
            link = urlsplit(urljoin(base, href))
            if link.hostname in ('creativecommons.org', 'www.creativecommons.org'):
                if unquote(link.path).rstrip('/') not in ('', '/licenses/by/4.0') or link.query:
                    raise AcquisitionError('Conflicting Creative Commons licence evidence; review required')
        # Remove the sole accepted wording before rejecting other CC statements.
        remainder = visible.replace(statement, '')
        remainder = re.sub(r'Creative Commons BY 4\.0|CC[ -]BY[ -]4\.0', '', remainder, flags=re.I)
        contexts = [visible[m.start():m.end() + 60] for m in re.finditer(
            r'Creative Commons|\bCC[ -]BY\b|\bAttribution\b|\blicen[cs]e\b', visible, re.I)]
        conflict = re.compile(
            r'\b(?:[123]\.0|2\.5|BY[- ](?:NC|ND|SA)|non[- ]?commercial|'
            r'no[- ]?derivatives|share[- ]?alike|Zero|Public Domain)\b', re.I)
        if (re.search(r'(?:Creative Commons|\bCC[ -]BY)\s+(?:Attribution|BY|[0-9])', remainder, re.I)
                or re.search(r'\bCC0\b', visible, re.I)
                or any(conflict.search(context) for context in contexts)):
            raise AcquisitionError('Conflicting Creative Commons licence evidence; review required')
    deed = any(urljoin(resolved, href).rstrip('/') == LICENCE_URL.rstrip('/')
               for href in Anchors(page).links)
    if not deed and statement not in texts[1]:
        raise AcquisitionError('Publisher licence page does not link to CC BY 4.0 or state its explicit licence; review required')
    exceptions = re.search(r'exception(?:s)? of:\s*(.*?)(?:\s*Details of|$)',
                           LicenceText(page).text(), re.I)
    return dict(body=page, url=resolved, evidence_type='deed_link' if deed else 'explicit_statement',
                exceptions=[s.strip().rstrip('.') for s in exceptions[1].split(';')
                            if s.strip()] if exceptions else [])


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
        raise ValueError('Choose registered JSA workbook contracts')
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
            if discover(('<a href="' + resolved + '">Resource</a>').encode(), page_url, dataset)[1] != reference:
                raise AcquisitionError('Redirect changed workbook edition or parser contract')
            check_workbook(payload)
            retrieved_at = observed_now()
            digest = hashlib.sha256(payload).hexdigest()
            # Preserve the filename: IVI and shortage importers select exact contracts.
            path = run / unquote(urlsplit(url).path).rsplit('/', 1)[-1]
            with path.open('xb') as stream:
                stream.write(payload)
            evidence_path = run / (dataset + '_copyright.html')
            evidence_path.write_bytes(evidence['body'])
            manifest['datasets'].append({'key': dataset, 'source_url': source_url})
            manifest['files'].append(dict(
                dataset=dataset, status='complete', path=str(path), url=url, resolved_url=resolved,
                source_url=source_url, page_resolved_url=page_url,
                licence='CC BY 4.0', licence_url=LICENCE_URL, licence_evidence_url=evidence['url'],
                evidence_type=evidence['evidence_type'],
                licence_evidence=[dict(path=str(evidence_path), url=evidence['url'],
                                      bytes=len(evidence['body']), sha256=hashlib.sha256(evidence['body']).hexdigest(),
                                      evidence_type=evidence['evidence_type'])],
                licence_exceptions=evidence['exceptions'],
                licence_scope_review='Exception scope reviewed by Claude on 20 Nov 2026: workbooks are JSA own data; '
                                     'not third-party, Coat of Arms, trade mark, image or photograph content.',
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
