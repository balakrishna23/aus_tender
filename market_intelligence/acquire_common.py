"""Shared direct-HTTP safeguards for official publisher acquisition."""
from datetime import datetime, timezone
from http.client import HTTPException
from html.parser import HTMLParser
import io
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
import zipfile

USER_AGENT = 'OpenData-Acquisition/1.0'

class AcquisitionError(RuntimeError):
    """Safe, public error text; never include arbitrary response bodies."""


def observed_now():
    return datetime.now(timezone.utc).isoformat()


def official_url(url, origin):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname == urlsplit(origin).hostname
                 and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        valid = False
    if not valid:
        raise AcquisitionError('Non-official or non-HTTPS publisher URL rejected')
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, allow=None, origin=None):
        super().__init__()
        self.allow = allow
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl, self.origin)  # checked before urllib sends the redirected request
        if self.allow is not None and not self.allow(newurl):
            raise AcquisitionError('robots.txt disallows redirect target')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublisherClient:
    """Two attempts at most, one second backoff, bounded reads and redirects."""
    def __init__(self, timeout=20, attempts=2, max_bytes=20_000_000, *, origin):
        self.origin = origin
        if not 0 < timeout <= 60 or attempts not in (1, 2) or not 0 < max_bytes <= 50_000_000:
            raise ValueError('timeout must be 0..60 seconds, attempts 1..2, size 1..50000000')
        self.timeout, self.attempts, self.max_bytes = timeout, attempts, max_bytes
        self.robots = None
        self.request_delay = 0
        self.opener = build_opener(OfficialRedirects(self.allowed, self.origin))

    def allowed(self, url):
        official_url(url, self.origin)
        # Only the initial robots fetch is exempt. Robots redirects are checked
        # for host/scheme, then the resulting policy applies to all other URLs.
        return self.robots is None or self.robots.can_fetch(USER_AGENT, url)

    def fetch(self, url, limit=None, *, form=None):
        official_url(url, self.origin)
        if not self.allowed(url):
            raise AcquisitionError('robots.txt disallows publisher resource')
        limit = min(limit or self.max_bytes, self.max_bytes)
        if self.request_delay:
            time.sleep(self.request_delay)
        for attempt in range(self.attempts):
            try:
                deadline = time.monotonic() + self.timeout
                headers = {'User-Agent': USER_AGENT}
                data = None
                if form is not None:
                    data = urlencode(form).encode('utf-8')
                    headers['Content-Type'] = 'application/x-www-form-urlencoded'
                request = Request(url, data=data, headers=headers)
                with self.opener.open(request, timeout=self.timeout) as response:
                    resolved = official_url(response.geturl(), self.origin)
                    if response.status == 202 or response.headers.get('x-amzn-waf-action') is not None:
                        raise AcquisitionError('Publisher WAF challenge; review required')
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
                challenge = exc.code == 202 or (exc.headers and exc.headers.get('x-amzn-waf-action') is not None)
                exc.close()
                if challenge:
                    raise AcquisitionError('Publisher WAF challenge; review required') from None
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
        body, resolved = self.fetch(self.origin + '/robots.txt', limit=1_000_000)
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


def check_workbook(payload):
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if not {'[Content_Types].xml', 'xl/workbook.xml'}.issubset(archive.namelist()):
                raise AcquisitionError('Publisher resource is not an XLSX ZIP workbook')
            if sum(item.file_size for item in archive.infolist()) > 100_000_000:
                raise AcquisitionError('Workbook expansion limit exceeded')
            if archive.testzip() is not None:
                raise AcquisitionError('Publisher workbook checksum failed')
    except AcquisitionError:
        raise
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise AcquisitionError('Publisher resource is not an XLSX ZIP workbook') from None
