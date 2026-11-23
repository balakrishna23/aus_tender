"""Acquire an APS June release on a server that can reach APSC.

Direct HTTP only; robots and official-host redirects are enforced. No workbook
URL or table layout is assumed. Default release: 30 June 2026. Example::

    python -m market_intelligence.acquire_aps --year 2026 --output-root reports/aps_acquisitions

A complete acquisition is evidence for import, not proof of parser compatibility.
The importer must validate the real release tables before promotion.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit
import uuid
import zipfile

from . import acquire_common as common
from .acquire_common import AcquisitionError, Anchors, check_workbook, observed_now

ORIGIN = 'https://www.apsc.gov.au'
USER_AGENT = common.USER_AGENT
DATASET = 'aps_employment'
LICENCE = 'Creative Commons Attribution 4.0 International'
LICENCE_URL = 'https://creativecommons.org/licenses/by/4.0/'
COPYRIGHT_URL = ORIGIN + '/copyright'


def official_url(url):
    return common.official_url(url, ORIGIN)


class PublisherClient(common.PublisherClient):
    def __init__(self, timeout=20, attempts=2, max_bytes=20_000_000):
        super().__init__(timeout, attempts, max_bytes, origin=ORIGIN)


class OfficialRedirects(common.OfficialRedirects):
    def __init__(self, allow=None):
        super().__init__(allow, ORIGIN)


def discover(body, source_url, year=None):
    """Select the requested reference year, or the unique newest June workbook.

    Upload month is provenance only. Duplicate anchors are harmless; multiple
    distinct URLs for the selected year need operator review.
    """
    candidates = {}
    for href in Anchors(body).links:
        url = urljoin(source_url, href)
        parts = urlsplit(url)
        match = re.fullmatch(
            r'/sites/default/files/(\d{4}-(?:0[1-9]|1[0-2]))/APS Employment Release Tables - 30 June (\d{4})\.xlsx',
            unquote(parts.path), re.I)
        if not match:
            continue
        official_url(url)
        if parts.query:
            raise AcquisitionError('Ambiguous workbook URL with query')
        reference = date(int(match[2]), 6, 30).isoformat()
        candidates[parts._replace(fragment='').geturl()] = (reference, match[1])
    selected = f'{year:04d}-06-30' if year is not None else max(
        (v[0] for v in candidates.values()), default=None)
    winners = [(url, *periods) for url, periods in candidates.items() if periods[0] == selected]
    if len(winners) != 1:
        raise AcquisitionError('Ambiguous discovery: expected one workbook for requested/newest reference date')
    page = re.fullmatch(r'/employment-data/aps-employment-data-30-june-(\d{4})/?', urlsplit(source_url).path)
    if not page or winners[0][1] != f'{int(page[1]):04d}-06-30':
        raise AcquisitionError('Release page and workbook reference dates disagree')
    return winners[0]


# Explicit restrictions must be reviewed, rather than inferred to be covered by
# a general site licence. This is a conservative text check, not legal analysis.
RESTRICTION = re.compile(
    r'all rights reserved|non[- ]commercial|no derivatives|permission (?:is )?required|'
    r'not (?:covered|licensed|available) (?:by|under)|excluded from (?:the |this )?licen[cs]e|'
    r'licen[cs]e exception|third[- ]party (?:copyright|material)|'
    r'(?:table|workbook|data|material).{0,100}(?:separate licen[cs]e|different licen[cs]e)', re.I)


def check_statements(text):
    text = re.sub(r'\s+', ' ', html.unescape(text))
    incompatible = re.search(r'creativecommons\.org/licenses/(?!by/4\.0(?:/|$))[a-z0-9/-]+', text, re.I)
    if RESTRICTION.search(text) or incompatible or re.search(r'CC[ -]BY[ -](?:NC|ND|SA)', text, re.I):
        raise AcquisitionError('Publisher licence scope or exceptions require review')


def licence_evidence(client, release_body):
    # Always fetch the official copyright page, even if the release links CC BY.
    body, resolved = client.fetch(COPYRIGHT_URL, limit=2_000_000)
    official_url(resolved)
    links = [urljoin(resolved, href).rstrip('/') for href in Anchors(body).links]
    if LICENCE_URL.rstrip('/') not in links:
        raise AcquisitionError('Official copyright page does not link to CC BY 4.0; review required')
    for page in (release_body, body):
        # Inspect visible text, retaining whitespace between HTML elements.
        text = re.sub(r'<[^>]*>', ' ', page.decode('utf-8', errors='replace'))
        check_statements(text)
        for href in Anchors(page).links:
            link = urlsplit(urljoin(resolved, href))
            if link.hostname in ('creativecommons.org', 'www.creativecommons.org') and '/licenses/' in link.path:
                if link.path.rstrip('/') != '/licenses/by/4.0':
                    raise AcquisitionError('Publisher licence evidence is ambiguous; review required')
    return body, resolved


def workbook_licence_statements(payload):
    """Inspect text for workbook/table exceptions without interpreting data layout."""
    import openpyxl
    try:
        # Read relationships, comments and metadata too: read-only cell iteration
        # does not expose comment text or hyperlink targets. ZIP expansion was
        # already bounded and CRC-checked by check_workbook.
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for entry in archive.infolist():
                if entry.filename.endswith(('.xml', '.rels')):
                    text = archive.read(entry).decode('utf-8', errors='replace')
                    check_statements(text)
                    check_statements(re.sub(r'<[^>]*>', ' ', text))
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=False)
        try:
            statements = []
            cells = 0
            for sheet in wb:
                for row in sheet.iter_rows(values_only=True):
                    cells += len(row)
                    if cells > 2_000_000:
                        raise AcquisitionError('Workbook inspection limit exceeded')
                    for value in row:
                        if not isinstance(value, str):
                            continue
                        check_statements(value)
                        if re.search(r'copyright|licen[cs]|creative commons|rights reserved', value, re.I):
                            statements.append(value)
                            if len(statements) > 1000:
                                raise AcquisitionError('Workbook licence inspection limit exceeded')
            return statements
        finally:
            wb.close()
    except AcquisitionError:
        raise
    except Exception:
        raise AcquisitionError('Publisher resource is not a readable XLSX workbook') from None


def earlier_hashes(manifests):
    hashes = set()
    for path in manifests:
        data = json.loads(Path(path).resolve(strict=True).read_text(encoding='utf-8'))
        if data.get('status') != 'complete':
            raise AcquisitionError('Previous manifest is not complete')
        for item in data['files']:
            if item.get('dataset') == DATASET and item.get('status') == 'complete':
                digest = item.get('sha256', '')
                if not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise AcquisitionError('Invalid previous manifest checksum')
                hashes.add(digest)
    return hashes


def latest_release(body, base):
    candidates = set()
    for href in Anchors(body).links:
        url = urljoin(base, href)
        parts = urlsplit(url)
        match = re.fullmatch(r'/employment-data/aps-employment-data-30-june-(\d{4})/?', parts.path)
        if match:
            official_url(url)
            if parts.query:
                raise AcquisitionError('Ambiguous APS release URL')
            candidates.add(int(match[1]))
    if not candidates:
        raise AcquisitionError('No June APS releases discovered')
    return max(candidates)


def acquire(output_root, year=2026, previous_manifests=(), client=None):
    if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 9999):
        raise ValueError('Choose a four-digit APS release year from 2000')
    previous = earlier_hashes(previous_manifests)
    run = Path(output_root).resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + uuid.uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    manifest = dict(status='failed', started_at=observed_now(), datasets=[], files=[])
    client = client or PublisherClient()
    try:
        client.load_robots()
        if year is None:
            listing, listing_url = client.fetch(ORIGIN + '/employment-data', limit=2_000_000)
            official_url(listing_url)
            year = latest_release(listing, listing_url)
            listing_path = run / 'employment-data.html'
            listing_path.write_bytes(listing)
            manifest['release_listing'] = dict(path=str(listing_path), url=listing_url, bytes=len(listing),
                sha256=hashlib.sha256(listing).hexdigest())
            december = []
            for href in Anchors(listing).links:
                link = urljoin(listing_url, href)
                match = re.fullmatch(r'/employment-data/aps-employment-data-31-december-(\d{4})/?', urlsplit(link).path)
                if match:
                    official_url(link)
                    december.append(int(match[1]))
            manifest['monitored_december_release'] = f'{max(december):04d}-12-31' if december else None
        source = ORIGIN + f'/employment-data/aps-employment-data-30-june-{year}'
        body, page_url = client.fetch(source, limit=2_000_000)
        official_url(page_url)
        observed = observed_now()
        url, reference, upload = discover(body, page_url, year)
        copyright_body, evidence_url = licence_evidence(client, body)
        payload, resolved = client.fetch(url)
        official_url(resolved)
        # A redirect may relocate a file, but cannot silently change its edition.
        if discover(('<a href="' + resolved + '">Download</a>').encode(), page_url, year)[1] != reference:
            raise AcquisitionError('Redirect changed workbook reference date')
        check_workbook(payload)
        statements = workbook_licence_statements(payload)
        retrieved = observed_now()
        digest = hashlib.sha256(payload).hexdigest()
        path = run / 'aps_employment.xlsx'
        path.write_bytes(payload)
        evidence = []
        for name, content, resource_url in [('release.html', body, page_url), ('copyright.html', copyright_body, evidence_url)]:
            evidence_path = run / name
            evidence_path.write_bytes(content)
            evidence.append(dict(path=str(evidence_path), url=resource_url, bytes=len(content),
                                 sha256=hashlib.sha256(content).hexdigest()))
        manifest['datasets'] = [dict(key=DATASET, source_url=source)]
        manifest['files'] = [dict(dataset=DATASET, status='complete', path=str(path),
            url=url, download_url=url, resolved_url=resolved, source_url=source, page_resolved_url=page_url,
            licence=LICENCE, licence_url=LICENCE_URL, licence_evidence_url=evidence_url,
            licence_evidence=evidence, workbook_licence_statements=statements,
            bytes=len(payload), sha256=digest, retrieved_at=retrieved,
            reference_date=reference, upload_month=upload, page_observed_at=observed,
            acquisition_status='unchanged' if digest in previous else 'downloaded')]
        manifest['status'] = 'complete'
        manifest['acquisition_status'] = manifest['files'][0]['acquisition_status']
    except Exception:
        manifest['error'] = 'acquisition_failed'
        raise
    finally:
        manifest['finished_at'] = observed_now()
        with (run / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
    return run / 'manifest.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--year', type=int, default=2026)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--previous-manifest', type=Path, action='append', default=[])
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--attempts', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-bytes', type=int, default=20_000_000)
    args = parser.parse_args(argv)
    try:
        path = acquire(args.output_root, args.year, args.previous_manifest,
                       PublisherClient(args.timeout, args.attempts, args.max_bytes))
    except (AcquisitionError, ValueError, OSError, KeyError):
        import sys
        exc = sys.exception()
        print(str(exc) if isinstance(exc, AcquisitionError) else 'Acquisition failed: check local configuration', file=sys.stderr)
        return 1
    print(json.dumps(dict(status=json.loads(path.read_text(encoding='utf-8'))['acquisition_status'], manifest=str(path))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
