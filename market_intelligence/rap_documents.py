"""Own-site RAP PDF acquisition and deterministic, page-cited document rules."""
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib import error, request, robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

# Database and parser imports stay lazy: `fetch` must also run in the collectors' venv,
# which has the stealth browser but not SQLAlchemy/defusedxml.


def text(sql):
    from sqlalchemy import text as sql_text
    return sql_text(sql)


PARSER_REVISION = 'rap-rules-3'
USER_AGENT = 'IllouraResearchBot/1.0 (internal research; respects robots.txt)'
MAX_BYTES = 150 * 1024 * 1024   # artwork-heavy plans exceed 40 MB (City of Fremantle)
BLOCKED_HOST = 'reconciliation.my.salesforce.com'
MONTH = (r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?'
         r'|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?(?![a-z])')
YEAR = r'(?:19|20)\d{2}'
TIMELINE = re.compile(r'\b(?:\d{1,2}\s+)?' + MONTH + r'(?:\s*(?:,|and|[-\u2013\u2014])\s*(?:\d{1,2}\s+)?' + MONTH + r')*\s*,?\s*' + YEAR + r'(?:\s*[,/-]\s*(?:' + MONTH + r'\s*)?' + YEAR + r')*', re.I)
# Several date lines in a row (e.g. "December 2025 March, June 2026 ...") form one timeline.
MORE_TIMELINE = re.compile(r'[\s,;]*(?:' + TIMELINE.pattern + r')', re.I)
CADENCE = re.compile(r'^\w+\s+(?:quarterly|annually|monthly|bi-?annually|biannually|ongoing)$', re.I)
QUARTER = re.compile(r'\b[1-4](?:st|nd|rd|th)\s+quarter\s+' + YEAR + r'(?:\s*[-\u2013\u2014]\s*(?:[1-4](?:st|nd|rd|th)\s+quarter\s+)?(?:' + YEAR + r'|ongoing))?', re.I)
# Case-sensitive: deliverables start with a capital; wrapped lines ("support improved ...") continue.
VERB = re.compile(r'^(?:Allocate|Maintain|Work|Increase|Develop|Implement|Investigate|Continue|Support|Ensure|Engage|Review|Establish|Promote|Provide|Report|Train|Build|Communicate|Create|Participate|Organise|Organize|Conduct|Identify|Register|Meet|Embed|Source|Commit|Encourage|Celebrate|Raise|Explore|Consult|Include|Measure|Appoint|Define|Demonstrate|Improve|Share|Recognise|Recognize|Partner|Collaborate|Monitor|Update|Submit|Complete|Deliver|Achieve|Publish|Hold|Host|Distribute|Circulate|Scope|Undertake|Facilitate|Invest|Grow|Offer|Attend|Display|Acknowledge|Seek|Strengthen|Expand|Introduce|Launch|Design|Co-design|Set|Track|Assess|Audit|Run|Sponsor|Fund|Advocate|Leverage|Plan|Prioritise|Prioritize|Procure|Purchase|Spend|Recruit|Retain|Mentor|Educate|Contact|Reach|Form|Convene|Nominate|Revise|Refresh|Renew)\b')
NUMBERED = re.compile(r'^\d{1,2}(?:\.\d{1,2})*\.?\s+(?=\S)')
ACTION_TITLE = re.compile(r'^Action\s+\d+\s*:')   # an action heading, never a deliverable
SUB_NUMBERED = re.compile(r'^\d{1,2}\.\d{1,2}\.?\s+\S')
TABLE_HEADER = re.compile(r'Action\s+Deliverable|Deliverables?\s+(?:Timelines?|Timeframes?)|(?:Timelines?|Timeframes?)\s+Responsib'
                          r'|Responsib\w*(?:\s+persons?)?\s+(?:Timelines?|Timeframes?)|Success is demonstrated|^Action\s+\d+:', re.I | re.M)
PHONE = re.compile(r'(?<![\w(])(?:\+61[ ()\d-]{8,18}\d|\(?0[2-478]\)?(?:[ -]*\d){8})(?!\d)')
PERSON = r"[A-Z][a-zA-Z'\u2019-]+(?: [A-Z][a-zA-Z'\u2019-]+){1,3}"
# Complete words that end in a ligature: never glue them to the next word ("staff awareness").
LIGATURE_WORDS = {'staff', 'off', 'stuff', 'tariff', 'cliff', 'sheriff', 'bluff', 'plaintiff', 'cuff', 'puff', 'scoff', 'handoff',
                  'kickoff', 'cutoff', 'layoff', 'payoff', 'signoff', 'takeoff', 'tradeoff', 'playoff', 'wifi', 'hifi'}


def _ligature_join(m):
    word = unicodedata.normalize('NFKC', m[1]).lower()
    return m[0] if word.rsplit('-', 1)[-1] in LIGATURE_WORDS else m[1]


def normalise(value):
    value = value.replace('\t', ' ')
    # PDF fonts split after ligature glyphs, and sometimes emit expanded fi/fl/ff.
    value = re.sub(r'([\w-]*[\ufb00-\ufb06]) +(?=[a-z])', _ligature_join, value)
    value = re.sub(r'([A-Za-z-]*[A-Za-z](?:ffi|ffl|fi|fl)) +(?=[a-z])', _ligature_join, value)
    value = re.sub(r'\b(ffi|ffl|fi|fl) +(?=[a-z])', r'\1', value)
    value = re.sub(r'\b(eff|diff|aff|suff) +(?=[a-z])', r'\1', value)
    value = re.sub(r'\b(off) +(?=(?:er|ers|ered|ering|ice|ices|icer|icers|icial|icially|ence|ences|ensive)\b)', r'\1', value)
    value = unicodedata.normalize('NFKC', value)
    return '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip() for line in value.splitlines())


def page_texts(pdf_path):
    from pypdf import PdfReader
    return [normalise(p.extract_text() or '') for p in PdfReader(pdf_path).pages]


def domain(host):
    """Conservative own-site boundary for common publisher domains, no PSL network lookup.

    Unknown country-code structures permit the exact host and its children only.
    This deliberately loses some sibling-domain coverage rather than crossing tenants.
    """
    host = (host or '').lower().rstrip('.')
    parts = host.split('.')
    if len(parts) < 2:
        return host
    if '.'.join(parts[-2:]) in {'com.au','org.au','net.au','gov.au','edu.au','asn.au','id.au','co.uk','org.uk','gov.uk','ac.uk','co.nz','org.nz','govt.nz','ac.nz'}:
        return '.'.join(parts[-3:])
    if len(parts[-1]) == 2 and len(parts) > 2:
        return host.removeprefix('www.')
    # Shared hosting suffixes must never make two tenants the same organisation.
    if '.'.join(parts[-2:]) in {'github.io','pages.dev','wordpress.com','blogspot.com','azurewebsites.net','appspot.com','cloudfront.net'}:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def own_site(url, base):
    host, origin = urlsplit(url).hostname or '', urlsplit(base).hostname or ''
    boundary = domain(origin)
    return host == boundary or host.endswith('.' + boundary)


def secure_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):
        raise ValueError('invalid URL')
    return urlunsplit(('https', p.hostname.lower().rstrip('.'), p.path or '/', p.query, ''))


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.href = None; self.label = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href'); self.label = []

    def handle_data(self, data):
        if self.href is not None:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href is not None:
            self.links.append((self.href, ' '.join(self.label))); self.href = None


def discover_links(html, base_url):
    parser = Links(); parser.feed(html)
    links = set()
    for href, label in parser.links:
        try:
            url = secure_url(urljoin(base_url, href))
        except ValueError:
            continue
        if (own_site(url, base_url) and urlsplit(url).path.lower().endswith('.pdf')
                and re.search(r'reconciliation|(?<![a-z])rap(?![a-z])', href + ' ' + label, re.I)):
            links.add(url)
    return sorted(links, key=lambda u: (max(re.findall(YEAR, u), default=''), u), reverse=True)[:3]


class HTTPClient:
    """Small streaming urllib adapter. Redirects are handled explicitly after policy checks."""
    def get(self, url, **kwargs):
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        try:
            response = request.build_opener(NoRedirect).open(
                request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=120)
        except error.HTTPError as exc:
            response = exc
        response.status_code = response.code
        response.iter_content = lambda size: iter(lambda: response.read(size), b'')
        return response


# Standing rule: no stealth on QLD, WA or data.gov.au; those hosts keep the plain transport only.
NO_STEALTH = ('qld.gov.au', 'wa.gov.au', 'data.gov.au')


def stealth_allowed(url):
    host = (urlsplit(url).hostname or '').lower()
    return not any(host == d or host.endswith('.' + d) for d in NO_STEALTH)


class Headers(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class BrowserResponse:
    def __init__(self, status, headers, payload, url):
        self.status_code = status; self.headers = Headers({k.lower(): v for k, v in headers.items()})
        self.payload = payload; self.url = url

    def iter_content(self, size):
        for start in range(0, len(self.payload), size):
            yield self.payload[start:start + size]

    def close(self):
        pass


class BrowserClient:
    """Stealth Chromium fallback, initialised like the browser collectors (undetected_playwright).

    Only used after the plain transport was refused. Policy stays in fetch(): robots.txt for
    every URL (including where a navigation lands), blocked host, own-site boundary, size cap.
    """
    def __init__(self):
        self.playwright = self.browser = self.context = None

    def start(self):
        from playwright.sync_api import sync_playwright
        from undetected_playwright import stealth_sync
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(accept_downloads=True)
        stealth_sync(self.context)

        def route(r):
            host = (urlsplit(r.request.url).hostname or '').lower()
            # Never contact the robots-blocked host, even as a subresource; skip heavy media.
            if host == BLOCKED_HOST or host.endswith('.' + BLOCKED_HOST) or r.request.resource_type in ('image','media','font'):
                return r.abort()
            return r.continue_()
        self.context.route('**/*', route)

    def get(self, url, **kwargs):
        if self.context is None:
            self.start()
        if urlsplit(url).path.endswith('/robots.txt'):
            r = self.context.request.get(url, max_redirects=0, timeout=120000, fail_on_status_code=False)
            return BrowserResponse(r.status, r.headers, r.body(), url)
        page = self.context.new_page(); downloads = []
        page.on('download', lambda d: downloads.append(d))   # Playwright needs a Python callable
        try:
            response = self.navigate(page, url, downloads)
            if response is not None and response.status in (403, 429, 503) and not downloads:
                page.wait_for_timeout(8000)   # interstitial check pages finish, then retry once
                if not downloads:
                    response = self.navigate(page, url, downloads)
            if downloads:
                path = downloads[0].path()
                size = os.path.getsize(path)
                if size > MAX_BYTES:
                    return BrowserResponse(200, {'Content-Length': str(size)}, b'', downloads[0].url)
                return BrowserResponse(200, {'Content-Type': 'application/pdf'}, Path(path).read_bytes(), downloads[0].url)
            if response is None:
                raise RuntimeError('navigation failed')
            content_type = response.headers.get('content-type', '')
            payload = page.content().encode() if 'html' in content_type else response.body()
            return BrowserResponse(response.status, {'Content-Type': content_type}, payload, page.url)
        finally:
            page.close()

    @staticmethod
    def navigate(page, url, downloads):
        try:
            return page.goto(url, wait_until='load', timeout=120000)
        except Exception:
            # A PDF navigation raises "Download is starting"; wait for the download event.
            if not downloads:
                try:
                    downloads.append(page.wait_for_event('download', timeout=15000))
                except Exception:
                    pass
            return None

    def close(self):
        for item in (self.context, self.browser):
            try:
                item and item.close()
            except Exception:
                pass
        if self.playwright:
            self.playwright.stop()
        self.playwright = self.browser = self.context = None


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def private_write(path, body):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
    os.chmod(path, 0o600)


def insert(conn, table, row):
    columns = list(row)
    conn.execute(text('INSERT INTO market.' + table + ' (' + ','.join(columns) + ') VALUES (' +
                      ','.join(':' + c for c in columns) + ')'), row)


def needs_browser(rows):
    """Retry with the stealth browser only when the plain transport was refused or saw no PDF."""
    if not rows or not stealth_allowed(rows[0]['url']):
        return False
    if any(r['status'] in ('downloaded','duplicate','blocked_host','too_large') for r in rows):
        return False
    # A robots.txt rule is final; only an unreachable robots.txt (e.g. a bot wall) is retried.
    if any(r['status'] == 'robots_disallowed' and r.get('detail') != 'robots_unavailable' for r in rows):
        return False
    return not (rows[0]['status'] == 'http_error' and rows[0]['http_status'] in (404, 410))


def fetch(candidates, data_root, client=None, *, clock=time.monotonic, sleeper=time.sleep, engine=None, browser=None):
    """Return and retain an audit row for every candidate/discovered-PDF attempt.

    Injectable client implements get(url, headers, timeout, stream, allow_redirects),
    returning status_code, headers, iter_content(size), close() and optionally url (where
    the request landed). The plain client never follows redirects. `browser` (BrowserClient)
    is a second pass for candidates the plain transport could not get; same policy checks.
    """
    active = {'client': client or HTTPClient()}
    root = Path(data_root) / 'raw' / 'rap_documents'
    # Only the RAP folders are made private; the shared raw/ folder keeps its permissions.
    (Path(data_root) / 'raw').mkdir(parents=True, exist_ok=True)
    private_dir(root); private_dir(root / 'fetches')
    robots = {}; last = {}; attempts = []

    def get(url):
        host = urlsplit(url).hostname
        if host in last:
            delay = 2 - (clock() - last[host])
            if delay > 0:
                sleeper(delay)
        last[host] = clock()
        return active['client'].get(url, headers={'User-Agent': USER_AGENT}, timeout=120, stream=True, allow_redirects=False)

    def body(response, cap):
        data = bytearray()
        try:
            length = response.headers.get('Content-Length', '')
            if length.isdigit() and int(length) > cap:
                raise OverflowError()
            for chunk in response.iter_content(65536):
                if len(data) + len(chunk) > cap:
                    raise OverflowError()
                data.extend(chunk)
            return bytes(data)
        finally:
            response.close()

    def robots_response(origin):
        # RFC 9309: follow at least five redirects (e.g. apex -> www) before deciding.
        url = origin + '/robots.txt'
        for _ in range(6):
            response = get(url)
            if response.status_code not in (301,302,303,307,308):
                return response
            location = response.headers.get('Location', ''); response.close()
            url = secure_url(urljoin(url, location))
            if urlsplit(url).hostname == BLOCKED_HOST:
                raise ValueError('blocked host')
        raise ValueError('robots redirect limit')

    def allowed(url):
        p = urlsplit(url); origin = 'https://' + p.netloc
        if origin not in robots:
            try:
                response = robots_response(origin)
            except Exception:
                robots[origin] = False
                return False
            code = response.status_code
            if code == 404:
                response.close(); robots[origin] = True
            elif code == 200:
                try:
                    content_type = response.headers.get('Content-Type','').lower()
                    rules = body(response, 1024 * 1024).decode('utf-8', errors='strict')
                    lines = [line.split('#',1)[0].strip() for line in rules.splitlines() if line.split('#',1)[0].strip()]
                    if ('html' in content_type or any(':' not in line for line in lines)
                            or lines and not any(re.match(r'(?:User-agent|Sitemap)\s*:',line,re.I) for line in lines)):
                        raise ValueError('unreadable robots')
                    parser = robotparser.RobotFileParser(); parser.parse(rules.splitlines()); robots[origin] = parser
                except Exception:
                    robots[origin] = False
            else:
                response.close(); robots[origin] = False
        policy = robots[origin]
        return policy if isinstance(policy, bool) else policy.can_fetch(USER_AGENT, url)

    def attempt(candidate, landing=True):
        row = dict(id=str(uuid4()),record_key=candidate['record_key'],organisation=candidate['organisation'],
                   url=candidate['url'],final_url=None,status='error',http_status=None,bytes=None,
                   sha256=None,robots_allowed=None,found_via=candidate.get('found_via'),detail=None)
        links = []
        try:
            url = secure_url(candidate['url'])
            for _ in range(6):
                row['final_url'] = url
                host = urlsplit(url).hostname
                if host == BLOCKED_HOST or host.endswith('.' + BLOCKED_HOST):
                    row.update(status='blocked_host',robots_allowed=None); break
                if not allowed(url):
                    unavailable = robots.get('https://' + urlsplit(url).netloc) is False
                    row.update(status='robots_disallowed',robots_allowed=False,
                               detail='robots_unavailable' if unavailable else 'robots_rule'); break
                row['robots_allowed'] = True
                response = get(url); code = response.status_code; row['http_status'] = code
                landed = getattr(response, 'url', None)
                if code == 200 and landed and secure_url(landed) != url:
                    # The browser follows redirects itself: apply the same checks where it landed.
                    landed = secure_url(landed); row['final_url'] = landed
                    if not own_site(landed, candidate['url']):
                        response.close(); row.update(status='error',detail='cross_site_redirect'); break
                    if not allowed(landed):
                        response.close(); row.update(status='robots_disallowed',robots_allowed=False,detail='robots_rule'); break
                if code in (301,302,303,307,308):
                    location = response.headers.get('Location', ''); response.close()
                    next_url = secure_url(urljoin(url, location))
                    if urlsplit(next_url).hostname == BLOCKED_HOST:
                        url = next_url; continue
                    if not own_site(next_url, candidate['url']):
                        row.update(status='error',detail='cross_site_redirect'); break
                    url = next_url; continue
                if code != 200:
                    response.close(); row['status'] = 'http_error'; break
                content_type = response.headers.get('Content-Type','').lower()
                payload = body(response, MAX_BYTES); row['bytes'] = len(payload)
                if not payload.startswith(b'%PDF'):
                    row['status'] = 'not_pdf'
                    if landing and ('html' in content_type or payload.lstrip().lower().startswith((b'<!doctype html',b'<html'))):
                        links = discover_links(payload.decode('utf-8',errors='replace'), url)
                        row['detail'] = 'landing_page'
                    break
                sha = hashlib.sha256(payload).hexdigest(); row['sha256'] = sha
                path = root / (sha + '.pdf')
                if path.exists():
                    row['status'] = 'duplicate'
                else:
                    private_write(path, payload); row['status'] = 'downloaded'
                break
            else:
                row['detail'] = 'redirect_limit'
        except OverflowError:
            row['status'] = 'too_large'
        except Exception:
            # Do not copy transport exceptions (which can contain credentials) into logs.
            row.update(status='error',detail='request_or_robots_error')
        row['fetched_at'] = datetime.now(timezone.utc).isoformat()
        private_write(root / 'fetches' / (row['id'] + '.json'), json.dumps(row).encode())
        if engine is not None:
            with engine.begin() as conn:
                insert(conn, 'rap_document_fetches', row)
        attempts.append(row)
        via = row['final_url'] if active['client'] is not browser else 'stealth_browser ' + str(row['final_url'])
        for link in links:
            attempt(dict(candidate,url=link,found_via=via), False)

    if isinstance(candidates, (str, Path)):
        with open(candidates, encoding='utf-8-sig', newline='') as stream:
            candidates = list(csv.DictReader(stream))
    retry = []
    for candidate in candidates:
        if not (candidate.get('url') or '').strip():
            continue
        start = len(attempts); attempt(candidate)
        if browser is not None and needs_browser(attempts[start:]):
            retry.append(candidate)
    if retry:
        # Fresh robots decisions through the browser; per-host spacing carries over.
        active['client'] = browser; robots.clear()
        try:
            for candidate in retry:
                attempt(dict(candidate,found_via='stealth_browser'))
        finally:
            browser.close()
    return dict(attempts=attempts,counts=dict(Counter(r['status'] for r in attempts)),browser_retries=len(retry))


def detect_period(pages):
    pattern = re.compile(r'\b((?:' + MONTH + r'\s+)?' + YEAR + r'\s*(?:[-\u2013\u2014]|to)\s*(?:' + MONTH + r'\s+)?(?:' + YEAR + r'|\d{2}\b))', re.I)
    for page in pages[:3]:
        found = pattern.search(' '.join(page.split()))
        if found:
            return found[1]
    return None


def years(value):
    found = [int(y) for y in re.findall(YEAR, value or '')]
    short = re.search(r'(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(\d{2})\b', value or '')
    if short and found:
        found.append(found[0] // 100 * 100 + int(short[1]))
    return set(range(min(found), max(found)+1)) if found else set()


def category(deliverable):
    s = deliverable.lower()
    if re.search(r'\bspend|\bexpenditure|\bpurchas|\$|procurement target', s):
        return 'procurement'
    if re.search(r'(?:commercial|supplier|business) relationships|relationships with.*(?:business|supplier)|supplier diversity', s):
        return 'supplier_relationships'
    if (re.search(r'cultural (?:learning|training|education|capability|competency|induction)|cross.cultural|cultural awareness', s)
            and not re.search(r'employ|recruit|retention', s)):
        return 'other'
    # Training / attendance items mention employees but are not employment commitments.
    if (re.search(r'\b(?:training|workshops?|attendance|induction|e-?learning)\b', s)
            and not re.search(r'recruit|retention|\bretain|employment|\bfte\b|traineeship|internship|apprentic|\bpositions?\b|career|\bhir(?:e|es|ing)\b', s)):
        return 'other'
    # "staff" alone (e.g. "circulate resources to all staff") is not an employment commitment; "intern" != "internal".
    if re.search(r'employ|recruit|retention|\bretain|\bfte\b|trainee|\binterns?\b|internship|apprentic|\bpositions?\b|workforce'
                 r'|talent|career|\bhir(?:e|es|ing)\b|staff base|(?:first nations|aboriginal|indigenous|islander)\s+'
                 r'(?:staff|employees|people employed|workforce|talent)', s):
        return 'employment'
    if 'supply nation' in s and re.search(r'member|membership|certif', s):
        return 'supply_nation'
    if re.search(r'procurement (?:strategy|strategies|policy|policies)', s):
        return 'procurement_strategy'
    if re.search(r'procurement|contracting|supplier', s):
        return 'procurement'
    return 'other'


def targets(deliverable):
    # For baseline -> future targets use the final numeric goal, retaining the full quote.
    result = []
    percents = list(re.finditer(r'(\d+(?:\.\d+)?)\s*%', deliverable))
    if percents:
        # "from X% ... to Y%" is baseline -> goal: keep Y. Otherwise every distinct percentage is a target
        # ("maintain a 7% minimum spend ... with an increase of 0.5% each year" keeps 7 and 0.5).
        if len(percents) == 1 or re.search(r'\bfrom\b[^%]{0,80}%.{0,160}?\bto\b', deliverable, re.I | re.S):
            chosen = [percents[-1][1]]
        else:
            chosen = list(dict.fromkeys(p[1] for p in percents))
        result.extend(('percent', Decimal(value), '%') for value in chosen)
    # Contract-size thresholds ("contracts valued at $250,000 or more") are not spend targets.
    money = [m for m in re.finditer(r'\$\s*(\d[\d,.]*)\s*(billion|million|bn|m|k)?\b', deliverable, re.I)
             if not re.search(r'(?:valued at|over|above|exceeding|greater than|more than|in excess of)\s*$', deliverable[max(0, m.start() - 30):m.start()], re.I)
             and not re.match(r'\s*(?:or more|or above|and above|\+)', deliverable[m.end():], re.I)]
    if money:
        m = money[-1]; scale = {None:1,'k':1000,'m':1000000,'million':1000000,'bn':1000000000,'billion':1000000000}
        result.append(('money',Decimal(m[1].replace(',','')) * scale[(m[2] or '').lower() or None],'AUD'))
    counts = list(re.finditer(r'\b(\d+)\s+(?:(?:Aboriginal|and|or|Torres|Strait|Islander|First|Nations|Australian|total|full.time|new|additional|Indigenous)[ /-]+){0,12}(businesses|suppliers|staff|employees|people|peoples|positions|trainees|interns|FTE)\b', deliverable, re.I))
    if counts:
        m = counts[-1]; result.append(('count',Decimal(m[1]),m[2]))
    elif percents and re.search(r'\bFTE\b|employment', deliverable, re.I):
        m = re.search(r'%\s*\((\d+)\)', deliverable[percents[-1].start():])
        if m:
            result.append(('count',Decimal(m[1]),'FTE'))
    return result or [('none',None,None)]


BULLET = re.compile(r'^[\u2022\u2023\u25cf\u25aa]\s*')
ROLE = re.compile(r'\b(?:Manager|Director|Officer|Lead|Chair|Board|Team|Executive|Head|Coordinator|Advis[eo]r|Committee|Group'
                  r'|CEO|Partner|Specialist|Counsel|Secretary|Commissioner|Owner|President|Leadership)\b')
CADENCE_TAIL = re.compile(r'\b(?:Monitor(?:ed)?|Review(?:ed)?|Report(?:ed)?|Measured|Updated)\s+'
                          r'(?:quarterly|annually|monthly|bi-?annually|biannually|ongoing)$')


def split_deliverable(joined):
    """deliverable / timeline / responsibility from one segment, or None without a timeline."""
    match = TIMELINE.search(joined) or QUARTER.search(joined)
    if not match:
        return None
    end = match.end()
    while (more := MORE_TIMELINE.match(joined, end)):
        end = more.end()
    deliverable = joined[:match.start()].strip()
    timeline = joined[match.start():end].strip()
    responsibility = joined[end:].strip() or None
    cadence = CADENCE_TAIL.search(deliverable)
    if cadence and cadence.start() >= 15:
        timeline = cadence[0] + ' ' + timeline; deliverable = deliverable[:cadence.start()].strip()
    if responsibility:
        # Page footers are not responsibility text.
        responsibility = re.split(r'\b(?:Relationships|Respect|Opportunities|Governance)\s+(?:Success|Measure)', responsibility)[0].strip()
        responsibility = re.split(r'\bDeloitte Australia\s*\||\bAECOM\s*\|', responsibility)[0].strip() or None
    else:
        # Responsibility printed before the timeline ("... organisation. Executive Director Jun 2028").
        tail = re.search(r'[.)]\s+([A-Z][^.]{2,100})$', deliverable)
        if tail and ROLE.search(tail[1]) and not VERB.match(tail[1]):
            responsibility = tail[1].strip(); deliverable = deliverable[:tail.start() + 1].strip()
    return dict(deliverable=deliverable, timeline=timeline, responsibility=responsibility)


def commitments(pages):
    output = []
    for page_number, page in enumerate(pages,1):
        lines = [l for l in page.splitlines() if l]
        # Action tables and bullet lists carry commitments; introductory prose does not.
        in_table = bool(TABLE_HEADER.search(page))
        dated, undated = [], []
        i = 0
        while i < len(lines):
            line = lines[i]; bullet = bool(BULLET.match(line)); numbered = bool(NUMBERED.match(line))
            start = NUMBERED.sub('', BULLET.sub('', line))
            if ACTION_TITLE.match(line) or re.match(r'^(?:Lead|Support):|^(?:Partner|Director|Manager|Lead)$', start) or not (
                    bullet or in_table and (VERB.match(start) or numbered and len(start) >= 15 or re.match(r'^\d+(?:\.\d+)?%',start))):
                i += 1; continue
            segment = [start]; j = i + 1
            while j < len(lines):
                following = lines[j]
                if CADENCE.match(following):   # "Monitor quarterly" belongs to the timeline
                    segment.append(following); j += 1; continue
                if (BULLET.match(following) or re.match(r'^\d{1,2}[.)]?\s*$',following) or SUB_NUMBERED.match(following)
                        or (VERB.match(following) and not re.match(r'^(?:Support:|Partner$|Director$|Manager$|Lead$)',following))
                        or re.match(r'^\d+(?:\.\d+)?%',following)
                        or re.match(r'^\d+\.\s+[A-Z]|^Action\s+\d+:|^Contact details|^Action Deliverable', following)):
                    break
                if len(' '.join(segment)) > 2000:
                    break
                segment.append(following); j += 1
            joined = ' '.join(segment)
            row = split_deliverable(joined)
            if row:
                dated.append(row)
            elif in_table:
                undated.append(joined.strip())
            i = max(i+1,j)
        rows = dated
        if in_table and not dated:
            # Column-by-column extraction separated dates from their rows: keep the deliverables, undated.
            rows = [dict(deliverable=d, timeline=None, responsibility=None) for d in undated if len(d) >= 25 and VERB.match(d)]
        for r in rows:
            if len(r['deliverable']) >= 15:
                for kind, value, unit in targets(r['deliverable']):
                    output.append(dict(category=category(r['deliverable']),deliverable=r['deliverable'],
                                       target_kind=kind,target_value=value,target_unit=unit,timeline=r['timeline'],
                                       responsibility=r['responsibility'],page=page_number,quote=r['deliverable']))
    return output


def people(pages):
    from .parsers import EMAIL_PATTERN
    output = []

    def add(context, page, quote, name=None, title=None, email=None, phone=None):
        row = dict(context=context,name=name,title=title,email=email,phone=phone,page=page,quote=quote,is_personal=True)
        if not any(all(r[k] == row[k] for k in ('context','name','title','email','phone')) for r in output):
            output.append(row)

    for number, page in enumerate(pages,1):
        lines = [l for l in page.splitlines() if l]
        flat = ' '.join(lines)
        block = re.search(r'Contact details\s+(.*)',page,re.I|re.S)
        captured_emails = set()
        if block:
            values = {}
            for m in re.finditer(r'(Name|Position|Title|Phone|Telephone|Email):\s*(.*?)(?=\n(?:Name|Position|Title|Phone|Telephone|Email):|\Z)',block[1],re.I|re.S):
                values[m[1].lower()] = ' '.join(m[2].split())
            if values:
                email = EMAIL_PATTERN.search(values.get('email',''))
                phone = PHONE.search(values.get('phone',values.get('telephone','')))
                add('contact',number,' '.join(block[0].split()),values.get('name'),values.get('position',values.get('title')),
                    email[0] if email else None,phone[0] if phone else None)
                if email:
                    captured_emails.add(email[0])
            else:
                # Unlabelled block: name line, title lines, then phone / email / address.
                after = [l.strip() for l in block[1].splitlines() if l.strip()][:8]
                name = next((l for l in after[:2] if re.fullmatch(PERSON, l)), None)
                if name:
                    title = []
                    for l in after[after.index(name) + 1:]:
                        if EMAIL_PATTERN.search(l) or PHONE.search(l) or re.search(r'\d', l):
                            break
                        title.append(l)
                    flat_block = ' '.join(after)
                    email = EMAIL_PATTERN.search(flat_block); phone = PHONE.search(flat_block)
                    add('contact',number,'Contact details '+flat_block,name,' '.join(title[:3]) or None,
                        email[0] if email else None,phone[0] if phone else None)
                    if email:
                        captured_emails.add(email[0])
        # A name followed by a CEO title, excluding Reconciliation Australia's own statement.
        for i, line in enumerate(lines[:-1]):
            if re.fullmatch(PERSON,line) and re.search(r'Chief Executive|\bCEO\b',lines[i+1],re.I):
                tail = ' '.join(lines[i+1:i+3])
                if 'Reconciliation Australia' not in tail and line != 'Reconciliation Australia':
                    title = lines[i+1]
                    if title.endswith(',') and i+2 < len(lines):
                        title += ' ' + lines[i+2]
                    add('ceo',number,line+' '+title,line,title)
        for m in re.finditer(r'RAP\s+(?:Executive\s+)?(Sponsors?|Champions?|Ambassadors?)\s+('+PERSON+r')\s*\(([^)]+)\)',flat):
            context = re.sub(r's$','',m[1].lower())
            add(context,number,m[0],m[2],m[3])
            if context == 'sponsor':
                preceding = re.search('('+PERSON+r')\s*\(([^)]+)\)$',flat[max(0,m.start()-130):m.start()])
                if preceding:
                    add('sponsor',number,preceding[0],preceding[1],preceding[2])
        # Names on a standalone champion heading (including paired champions).
        for i, line in enumerate(lines[:-1]):
            if re.search(r'RAP Champions?$',line):
                for name in re.split(r'\s*&\s*|\s+and\s+',lines[i+1]):
                    if re.fullmatch(PERSON,name):
                        add('champion',number,line+' '+name,name,'RAP Champion')
        group = False
        for i,line in enumerate(lines):
            if re.fullmatch(r'(?:Our |National |RAP )?(?:RAP )?Working[- ]Group(?: members| membership)?',line,re.I):
                group = True; continue
            if group and re.fullmatch(PERSON,line) and not re.search(r'Office|Managing|National|Group|Council',line) and i+1 < len(lines):
                title = lines[i+1]
                if re.match(r'(?:Chief|Director|Manager|Head|Chair|Partner|Lead|Officer|Executive|Senior|Regional|First Nations)',title):
                    add('working_group',number,line+' '+title,line,title)
            if group:
                pair = re.fullmatch('('+PERSON+r')\s+((?:Chief|Director|Manager|Head|Chair|Partner|Lead|Officer|Executive|Senior|Regional)\b.*)',line)
                if pair:
                    add('working_group',number,line,pair[1],pair[2])
                if re.match(r'^(?:Action |Our business|Relationships|Respect|Opportunities|Governance|Contact details)',line):
                    group = False
        for m in EMAIL_PATTERN.finditer(flat):
            if m[0] in captured_emails:
                continue
            nearby = flat[max(0,m.start()-80):m.end()+70]
            phone = PHONE.search(nearby)
            add('generic_contact',number,nearby,email=m[0],phone=phone[0] if phone else None)
    return output


def organisation_found(name, text):
    """The directory organisation appears on the first pages (abbreviations and 'PHN' accepted)."""
    from .parsers import legal_key, name_key
    document = set(name_key(text).split())
    if 'phn' in document:
        document |= {'primary', 'health', 'network'}
    abbreviations = [name_key(a).replace(' ', '') for a in re.findall(r'\(([A-Za-z&]{2,12})\)', name or '')]
    tokens = [t for t in legal_key(re.sub(r'\([^)]*\)', ' ', name or '')).split() if t not in ('the', 'of', 'and', 'for')]
    if not tokens:
        return False
    if set(tokens) <= document or any(a and a in document for a in abbreviations):
        return True
    return len(tokens) >= 3 and sum(t in document for t in tokens) / len(tokens) >= 0.75


def analyse(pages, directory_row):
    pages = [normalise(p) for p in pages]
    first = ' '.join(pages[:6]); cover = ' '.join(pages[:2])
    detected = re.search(r'\b(Reflect|Innovate|Stretch|Elevate)\b(?=\s+(?:Reconciliation|RAP|Action))',cover,re.I)
    if not detected:
        detected = re.search(r'\b(Reflect|Innovate|Stretch|Elevate)\b',pages[0] if pages else '',re.I)
    rap_type = detected[1].title() if detected else None
    period = detect_period(pages)
    found = organisation_found(directory_row.get('organisation',''), first)
    status = 'matched'
    if not first.strip() or not rap_type or not period:
        status = 'unreadable'
    elif not found:
        status = 'organisation_not_found'
    elif rap_type.casefold() != (directory_row.get('rap_type') or '').casefold():
        status = 'type_mismatch'
    elif not (years(period) & years(directory_row.get('period_as_published'))):
        status = 'period_mismatch'
    return dict(rap_type_detected=rap_type,period_detected=period,organisation_found=found,
                match_status=status,pages=len(pages),characters=sum(map(len,pages)),
                commitments=commitments(pages),people=people(pages))


def directory_rows(conn):
    return {r['record_key']:dict(r['payload'],record_key=r['record_key']) for r in conn.execute(text(
        "SELECT record_key,payload FROM market.current_research WHERE source='rap_directory'")).mappings()}


def extract(engine, data_root):
    root = Path(data_root) / 'raw' / 'rap_documents'
    with engine.begin() as conn:
        # Retained fetch audits also support acquisition with no attached database.
        for path in sorted((root / 'fetches').glob('*.json')):
            row = json.loads(path.read_text(encoding='utf-8'))
            if not conn.execute(text('SELECT 1 FROM market.rap_document_fetches WHERE id=:id'),row).scalar():
                insert(conn,'rap_document_fetches',row)
        directory = directory_rows(conn)
        stored = list(conn.execute(text('''SELECT DISTINCT ON (f.sha256) f.* FROM market.rap_document_fetches f
            LEFT JOIN market.rap_documents d ON d.sha256=f.sha256
            WHERE f.status IN ('downloaded','duplicate') AND (d.parser_revision IS DISTINCT FROM :revision)
            ORDER BY f.sha256,f.fetched_at DESC,f.id'''),dict(revision=PARSER_REVISION)).mappings())
    counts = Counter()
    for fetched in stored:
        if fetched['record_key'] not in directory:
            counts['no_directory_record'] += 1; continue
        sha = fetched['sha256']; path = root / (sha+'.pdf')
        if not path.exists():
            counts['missing_pdf'] += 1; continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            counts['hash_mismatch'] += 1; continue
        try:
            findings = analyse(page_texts(path),directory[fetched['record_key']])
        except Exception:
            findings = dict(pages=0,characters=0,rap_type_detected=None,period_detected=None,
                            organisation_found=False,match_status='unreadable',commitments=[],people=[])
        commitments_found = findings.pop('commitments'); people_found = findings.pop('people')
        document = dict(findings,sha256=sha,record_key=fetched['record_key'],organisation=fetched['organisation'],
                        source_url=fetched['final_url'] or fetched['url'],parser_revision=PARSER_REVISION,
                        extracted_at=datetime.now(timezone.utc))
        with engine.begin() as conn:
            # Lock the document before replacing children: concurrent runs stay idempotent.
            conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:sha))'),dict(sha=sha))
            conn.execute(text('DELETE FROM market.rap_commitments WHERE document_sha256=:sha'),dict(sha=sha))
            conn.execute(text('DELETE FROM market.rap_people WHERE document_sha256=:sha'),dict(sha=sha))
            conn.execute(text('DELETE FROM market.rap_documents WHERE sha256=:sha'),dict(sha=sha))
            insert(conn,'rap_documents',document)
            for row in commitments_found:
                insert(conn,'rap_commitments',dict(row,document_sha256=sha,record_key=fetched['record_key']))
            for row in people_found:
                insert(conn,'rap_people',dict(row,document_sha256=sha,record_key=fetched['record_key']))
        counts[document['match_status']] += 1
    return dict(parser_revision=PARSER_REVISION,documents=sum(counts.values()),counts=dict(counts))


def report(engine, output):
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text('''SELECT d.*,
            (SELECT count(*) FROM market.rap_commitments c WHERE c.document_sha256=d.sha256) AS commitments,
            (SELECT count(*) FROM market.rap_people p WHERE p.document_sha256=d.sha256) AS people
            FROM market.rap_documents d ORDER BY organisation,record_key,extracted_at DESC,sha256''')).mappings()]
    directory = Path(output); private_dir(directory)
    private_write(directory/'rap_documents.json',json.dumps(rows,default=str,indent=2).encode())
    import io
    stream = io.StringIO(newline='')
    columns = list(rows[0]) if rows else ['record_key','organisation','match_status','commitments','people']
    writer = csv.DictWriter(stream,fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    private_write(directory/'rap_documents.csv',stream.getvalue().encode())
    return dict(documents=len(rows),output=str(directory))


def main(argv=None):
    """Database-free fetch, runnable in the collectors' venv: `extract` later loads the audit files."""
    import argparse
    parser = argparse.ArgumentParser(description='Fetch own-site RAP PDFs (robots.txt respected)')
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--candidates', required=True)
    parser.add_argument('--stealth-fallback', action='store_true',
                        help='retry refused candidates with the collectors\' stealth browser')
    args = parser.parse_args(argv)
    result = fetch(args.candidates, args.data_root, browser=BrowserClient() if args.stealth_fallback else None)
    print(json.dumps(dict(counts=result['counts'], attempts=len(result['attempts']),
                          browser_retries=result['browser_retries']), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
