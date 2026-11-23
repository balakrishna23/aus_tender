"""Bounded official RAP directory acquisition; private HTML, no document downloads."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from .acquire_common import AcquisitionError, PublisherClient, observed_now, official_url
from .rap_extract import digest

ORIGIN = 'https://www.reconciliation.org.au'
DIRECTORY = ORIGIN + '/reconciliation-action-plans/who-has-a-rap/'
TYPES = ('Reflect', 'Innovate', 'Stretch', 'Elevate')
LICENCE = 'Original copyright retained; internal research only'


def form_for(kind):
    return dict(keyword='', rapType=kind, orgState='', orgSector='', orgIndustry='')


class DirectoryHTML(HTMLParser):
    """Read only card fields and anchor hrefs; reject malformed card nesting."""
    VOID = set('area base br col embed hr img input link meta param source track wbr'.split())

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.cards, self.links, self.stack = [], [], []
        self.card = None
        try:
            self.feed(body.decode('utf-8', errors='strict'))
            self.close()
        except (UnicodeError, ValueError):
            raise AcquisitionError('Invalid RAP HTML encoding or structure') from None
        if self.stack:
            raise AcquisitionError('Unclosed RAP card')

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        href = attrs.get('href')
        if tag == 'a' and href is not None:
            self.links.append(href)
        if 'who-has-a-rap' in classes:
            if self.card is not None:
                raise AcquisitionError('Nested RAP card')
            self.card = dict(organisation=[], kind=[], period=[], links=[])
        if self.card is None:
            return
        ancestors = set().union(*(frame[1] for frame in self.stack))
        field = None
        if 'rap-organisation' in classes:
            field = 'organisation'
        elif 'rap-value' in classes:
            if 'rap-type' in ancestors and 'rap-period' in ancestors:
                raise AcquisitionError('Ambiguous RAP value')
            if 'rap-type' in ancestors:
                field = 'kind'
            elif 'rap-period' in ancestors:
                field = 'period'
        chunks = [] if field else None
        if field:
            self.card[field].append(chunks)
        if tag == 'a' and href is not None and 'rap-view' in ancestors:
            self.card['links'].append(href)
        if tag not in self.VOID:
            self.stack.append((tag, classes, chunks))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.card is None or tag in self.VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            raise AcquisitionError('Unbalanced RAP card')
        self.stack.pop()
        if not self.stack:
            self.cards.append(self.card)
            self.card = None

    def handle_data(self, data):
        for _, _, chunks in self.stack:
            if chunks is not None:
                chunks.append(data)


def parse_directory(body, collection_date):
    records = []
    for card in DirectoryHTML(body).cards:
        def value(field):
            nodes = card[field]
            if len(nodes) != 1:
                raise AcquisitionError('RAP card structure changed')
            return ' '.join(''.join(nodes[0]).split())
        organisation = value('organisation')
        kind = value('kind')
        period = value('period')
        links = card['links']
        if len(links) > 1 or not organisation:
            raise AcquisitionError('RAP card structure changed')
        url = links[0].strip() if links else ''
        identity = '|'.join((organisation, kind, period, url))
        records.append(dict(record_key=hashlib.sha256(identity.encode()).hexdigest(),
            organisation=organisation, rap_type=kind, period_as_published=period,
            document_url=url, source_url=DIRECTORY, document_sha256=None,
            collection_date=collection_date, licence=LICENCE))
    return records


def last_page(body):
    pages = []
    for href in DirectoryHTML(body).links:
        url = urljoin(DIRECTORY, href)
        parts = urlsplit(url)
        match = re.fullmatch(re.escape(urlsplit(DIRECTORY).path) + r'page/([1-9]\d*)/', parts.path)
        if parts.scheme == 'https' and parts.netloc == urlsplit(ORIGIN).netloc and match:
            pages.append(int(match[1]))
    if not pages or max(pages) > 1000:
        raise AcquisitionError('Missing or unbounded RAP pagination')
    return DIRECTORY + 'page/' + str(max(pages)) + '/'


def assemble(bodies, observed_at):
    day = observed_at[:10]
    records, counts = [], {}
    for kind in TYPES:
        rows = parse_directory(bodies[kind], day)
        if not rows or any(r['rap_type'] != kind for r in rows):
            raise AcquisitionError('Incomplete RAP type response')
        counts[kind] = len(rows)
        records.extend(rows)
    keys = {r['record_key'] for r in records}
    for window in ('default', 'last'):
        rows = parse_directory(bodies[window], day)
        if not rows or any(r['record_key'] not in keys for r in rows):
            raise AcquisitionError('RAP window is absent from type union')
    # Keep duplicate cards for the reviewer to quarantine, never silently discard conflicts.
    return dict(source='rap_directory', observed_at=observed_at, contains_contact_data=False,
        records=records, provenance=dict(source_url=DIRECTORY, licence=LICENCE, commercial_reuse='unclear',
            collection_date=day, collection_method='four_type_posts_v1', type_counts=counts,
            union_count=len(keys), complete=True))


def acquire(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    observed = observed_now()
    entries = []

    class RetainedClient(PublisherClient):
        def fetch(self, url, limit=None, *, form=None):
            body, resolved = super().fetch(url, limit, form=form)
            name = 'response_' + str(len(entries)) + '.bin'
            (root / name).write_bytes(body)
            entries.append(dict(name=name, url=url, final_url=resolved, form=form, status=200,
                                bytes=len(body), sha256=hashlib.sha256(body).hexdigest()))
            return body, resolved

    client = RetainedClient(origin=ORIGIN, max_bytes=3_000_000, attempts=1)
    client.load_robots()
    client.request_delay = max(3, client.request_delay)
    default, _ = client.fetch(DIRECTORY)
    client.fetch(last_page(default))
    for kind in TYPES:
        client.fetch(DIRECTORY, form=form_for(kind))
    manifest = dict(observed_at=observed, files=entries)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    bundle = load_collection(root)
    return dict(status='collected', rows=len(bundle['records']), type_counts=bundle['provenance']['type_counts'])


def load_collection(directory):
    """Rebuild completeness and provenance from exact retained responses on each pass."""
    root = Path(directory).resolve(strict=True)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    files = manifest['files']
    if len(files) != 7:
        raise AcquisitionError('Incomplete RAP response manifest')
    bodies = {}
    for item in files:
        path = (root / item['name']).resolve(strict=True)
        if not path.is_relative_to(root) or path.stat().st_size > 3_000_000:
            raise AcquisitionError('Invalid RAP response path or size')
        body = path.read_bytes()
        if item['status'] != 200 or len(body) != item['bytes'] or hashlib.sha256(body).hexdigest() != item['sha256']:
            raise AcquisitionError('RAP response checksum/status mismatch')
        official_url(item['url'], ORIGIN)
        official_url(item['final_url'], ORIGIN)
        if item['url'] == ORIGIN + '/robots.txt' and item['form'] is None:
            key = 'robots'
        elif item['url'] == DIRECTORY:
            key = 'default' if item['form'] is None else item['form'].get('rapType')
            if key != 'default' and (key not in TYPES or item['form'] != form_for(key)):
                raise AcquisitionError('Invalid RAP type request')
        else:
            key = 'last'
        if key in bodies:
            raise AcquisitionError('Duplicate RAP response')
        bodies[key] = body
    if set(bodies) != {'robots', 'default', 'last', *TYPES}:
        raise AcquisitionError('Incomplete RAP responses')
    expected_last = last_page(bodies['default'])
    last = next(f for f in files if f['url'] not in (ORIGIN + '/robots.txt', DIRECTORY))
    if last['url'] != expected_last or last['form'] is not None:
        raise AcquisitionError('Incorrect RAP last page')
    from urllib.robotparser import RobotFileParser
    from .acquire_common import USER_AGENT
    if b'<html' in bodies['robots'].lower():
        raise AcquisitionError('Invalid retained robots policy')
    robots = RobotFileParser()
    robots.parse(bodies['robots'].decode('utf-8').splitlines())
    if any(not robots.can_fetch(USER_AGENT, f[url]) for f in files for url in ('url', 'final_url')
           if f['url'] != ORIGIN + '/robots.txt'):
        raise AcquisitionError('Retained robots policy disallows resource')
    bundle = assemble(bodies, manifest['observed_at'])
    bundle['provenance']['collection_sha256'] = digest(manifest)
    return bundle
