"""Acquire official quarterly Star Ratings on the server; no database access."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
import uuid

from . import acquire_common as common
from .acquire_common import AcquisitionError, Anchors, check_workbook, observed_now

ORIGIN = 'https://www.health.gov.au'
COLLECTION = ORIGIN + '/resources/collections/star-ratings-quarterly-data-extracts'
COPYRIGHT = ORIGIN + '/using-our-websites/copyright'
PERMISSION = 'permission_confirmed_by_project_owner_2026_11_20'
DATASET = 'aged_care_star_ratings'
MONTHS = 'january february march april may june july august september october november december'.split()
TITLE = r'star-ratings-quarterly-data-extract-(' + '|'.join(MONTHS) + r')-(\d{4})'


class PublisherClient(common.PublisherClient):
    def __init__(self, timeout=20, attempts=2, max_bytes=20_000_000):
        super().__init__(timeout, attempts, max_bytes, origin=ORIGIN)


def reference_from_title(title):
    match = re.fullmatch(TITLE, title, re.I)
    if not match or int(match[2]) == 0:
        raise AcquisitionError('Unrecognised quarterly extract title')
    return f'{int(match[2]):04d}-{MONTHS.index(match[1].lower()) + 1:02d}'


def discover_publication(body, base):
    candidates = set()
    for href in Anchors(body).links:
        url = urljoin(base, href)
        parts = urlsplit(url)
        match = re.fullmatch('/resources/publications/(' + TITLE + ')', parts.path, re.I)
        if match:
            common.official_url(url, ORIGIN)
            if parts.query:
                raise AcquisitionError('Ambiguous publication URL')
            candidates.add((reference_from_title(match[1]), parts._replace(fragment='').geturl()))
    if not candidates:
        raise AcquisitionError('No quarterly publication links')
    newest = max(p for p, _ in candidates)
    winners = [url for p, url in candidates if p == newest]
    if len(winners) != 1:
        raise AcquisitionError('Ambiguous newest publication')
    return winners[0], newest


def discover_workbook(body, base, reference):
    candidates = set()
    for href in Anchors(body).links:
        url = urljoin(base, href)
        parts = urlsplit(url)
        match = re.fullmatch(r'/sites/default/files/(\d{4}-(?:0[1-9]|1[0-2]))/(' + TITLE + r')\.xlsx', parts.path, re.I)
        if match:
            common.official_url(url, ORIGIN)
            if parts.query or reference_from_title(match[2]) != reference:
                raise AcquisitionError('Workbook reference differs from publication or has query')
            candidates.add((parts._replace(fragment='').geturl(), match[1]))
    if len(candidates) != 1:
        raise AcquisitionError('Expected exactly one quarterly workbook')
    return candidates.pop()


def acquire(output_root, client=None):
    run = Path(output_root).resolve() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + uuid.uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    manifest = dict(status='failed', started_at=observed_now(), files=[])
    client = client or PublisherClient()

    def retain(name, body, url):
        path = run / name
        with path.open('xb') as stream:
            stream.write(body)
        return dict(path=name, url=url, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())

    try:
        client.load_robots()
        collection, collection_url = client.fetch(COLLECTION, limit=2_000_000)
        common.official_url(collection_url, ORIGIN)
        publication, reference = discover_publication(collection, collection_url)
        page, page_url = client.fetch(publication, limit=2_000_000)
        common.official_url(page_url, ORIGIN)
        if urlsplit(page_url).path != urlsplit(publication).path:
            raise AcquisitionError('Publication redirect changed edition')
        url, upload = discover_workbook(page, page_url, reference)
        copyright_body, copyright_url = client.fetch(COPYRIGHT, limit=2_000_000)
        common.official_url(copyright_url, ORIGIN)
        if not copyright_body.strip():
            raise AcquisitionError('Empty copyright evidence')
        payload, resolved = client.fetch(url)
        common.official_url(resolved, ORIGIN)
        if urlsplit(resolved).path != urlsplit(url).path:
            raise AcquisitionError('Workbook redirect changed edition')
        check_workbook(payload)
        evidence = retain('copyright.html', copyright_body, copyright_url)
        item = retain('star-ratings.xlsx', payload, url)
        item.update(dataset=DATASET, status='complete', resolved_url=resolved,
                    publication_page=publication, reference_period=reference, upload_month=upload,
                    retrieved_at=observed_now(), copyright_page=COPYRIGHT, copyright_evidence=evidence,
                    publisher_licence='See retained publisher copyright and workbook Notes; no CC BY assertion',
                    commercial_reuse=PERMISSION,
                    permission_note='Project owner confirmed permission to use this data on 20 November 2026; separate from publisher copyright.',
                    publication_evidence=retain('publication.html', page, page_url),
                    collection_evidence=retain('collection.html', collection, collection_url))
        manifest.update(status='complete', files=[item])
    except Exception:
        manifest['error'] = 'acquisition_failed'
        raise
    finally:
        manifest['finished_at'] = observed_now()
        with (run / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
    return run / 'manifest.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', required=True)
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--attempts', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-bytes', type=int, default=20_000_000)
    args = parser.parse_args(argv)
    try:
        path = acquire(args.output_root, PublisherClient(args.timeout, args.attempts, args.max_bytes))
    except (AcquisitionError, OSError, ValueError):
        print('Health acquisition failed; no complete manifest promoted')
        return 1
    print(json.dumps(dict(status='complete', manifest=str(path))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
