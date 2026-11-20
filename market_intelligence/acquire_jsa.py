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
from http.client import HTTPException
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
import uuid
import io
import zipfile


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


class AcquisitionError(RuntimeError):
    """Safe, public error text; never include arbitrary response bodies."""


def observed_now():
    return datetime.now(timezone.utc).isoformat()


def official_url(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname == 'www.jobsandskills.gov.au'
                 and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        valid = False
    if not valid:
        raise AcquisitionError('Non-official or non-HTTPS publisher URL rejected')
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, allow=None):
        super().__init__()
        self.allow = allow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)  # checked before urllib sends the redirected request
        if self.allow is not None and not self.allow(newurl):
            raise AcquisitionError('robots.txt disallows redirect target')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublisherClient:
    """Two attempts at most, one second backoff, bounded reads and redirects."""
    def __init__(self, timeout=20, attempts=2, max_bytes=20_000_000):
        if not 0 < timeout <= 60 or attempts not in (1, 2) or not 0 < max_bytes <= 50_000_000:
            raise ValueError('timeout must be 0..60 seconds, attempts 1..2, size 1..50000000')
        self.timeout, self.attempts, self.max_bytes = timeout, attempts, max_bytes
        self.robots = None
        self.request_delay = 0
        self.opener = build_opener(OfficialRedirects(self.allowed))

    def allowed(self, url):
        official_url(url)
        # Only the initial robots fetch is exempt. Robots redirects are checked
        # for host/scheme, then the resulting policy applies to all other URLs.
        return self.robots is None or self.robots.can_fetch(USER_AGENT, url)

    def fetch(self, url, limit=None):
        official_url(url)
        if not self.allowed(url):
            raise AcquisitionError('robots.txt disallows publisher resource')
        limit = min(limit or self.max_bytes, self.max_bytes)
        if self.request_delay:
            time.sleep(self.request_delay)
        for attempt in range(self.attempts):
            try:
                deadline = time.monotonic() + self.timeout
                request = Request(url, headers={'User-Agent': USER_AGENT})
                with self.opener.open(request, timeout=self.timeout) as response:
                    resolved = official_url(response.geturl())
                    if response.status != 200:
                        raise AcquisitionError('Unexpected publisher HTTP status')
                    length = response.headers.get('Content-Length')
                    if length is not None and (not length.isdigit() or int(length) > limit):
                        raise AcquisitionError('Publisher resource exceeds size limit or has invalid length')
                    chunks, size = [], 0
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError()
                        chunk = response.read1(min(65536, limit + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limit:
                            raise AcquisitionError('Publisher resource exceeds size limit')
                        chunks.append(chunk)
                    if length is not None and size != int(length):
                        raise AcquisitionError('Publisher resource length mismatch')
                    return b''.join(chunks), resolved
            except HTTPError as exc:
                exc.close()
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    raise AcquisitionError('Publisher HTTP error ' + str(exc.code)) from None
                # Do not retry rate limiting; leave scheduling to the operator.
                if exc.code == 429:
                    raise AcquisitionError('Publisher rate limited the request') from None
            except (URLError, TimeoutError, socket.timeout, ConnectionError, HTTPException):
                pass
            if attempt + 1 < self.attempts:
                time.sleep(1)
        raise AcquisitionError('publisher did not respond within bounded attempts')

    def load_robots(self):
        body, resolved = self.fetch(ORIGIN + '/robots.txt', limit=1_000_000)
        self.robots = RobotFileParser(resolved)
        self.robots.parse(body.decode('utf-8', errors='replace').splitlines())
        # Never assume an unavailable robots policy permits collection.
        if b'<html' in body.lower() or b'<!doctype html' in body.lower():
            raise AcquisitionError('Publisher returned HTML instead of robots.txt')
        rate = self.robots.request_rate(USER_AGENT)
        self.request_delay = max(1, self.robots.crawl_delay(USER_AGENT) or 0,
                                 rate.seconds / rate.requests if rate and rate.requests else 0)
        if self.request_delay > 60:
            raise AcquisitionError('Publisher robots pacing exceeds acquisition bounds; review required')


class Anchors(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.feed(body.decode('utf-8', errors='replace'))

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)


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


def check_workbook(payload):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if not {'[Content_Types].xml', 'xl/workbook.xml'}.issubset(archive.namelist()):
                raise AcquisitionError('Publisher resource is not an XLSX ZIP workbook')
            if sum(item.file_size for item in archive.infolist()) > 100_000_000:
                raise AcquisitionError('Workbook expansion limit exceeded')
            if archive.testzip() is not None:
                raise AcquisitionError('Publisher workbook checksum failed')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise AcquisitionError('Publisher resource is not an XLSX ZIP workbook') from None


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
