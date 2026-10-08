"""One-off, robots-aware checks of curated official URLs; not web search."""
import hashlib,json,re,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.robotparser import RobotFileParser
from urllib.error import HTTPError
from public_research import PATH

ALLOWED={'dxc.com','www.ey.com','global.fujitsu','www.deloitte.com','kpmg.com',
         'www.hays.com.au','www.talentinternational.com','careers.ey.com','www.niaa.gov.au'}
AGENT='Illoura-evidence-pilot'


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urlsplit(newurl).scheme!='https' or urlsplit(newurl).hostname not in ALLOWED:
            raise ValueError('Redirect outside the approved official source hosts')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def run():
    package=json.loads(PATH.read_text(encoding='utf-8'))
    records=package['records']
    urls=list(dict.fromkeys(r['source_url'] for r in records))
    urls.append('https://www.niaa.gov.au/sites/default/files/documents/2026-05/ipp-companies-active-mmr-contracts-0.xlsx')
    robots={};checks=[]
    opener=build_opener(PublicRedirects())
    for url in urls:
        result={'source_url':url,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'scope':'HTTP availability and response identity; claim truth, adoption and current hiring are not inferred from HTTP 200.'}
        parsed=urlsplit(url);origin=parsed.scheme+'://'+parsed.netloc
        try:
            if parsed.scheme!='https' or parsed.hostname not in ALLOWED:raise ValueError('Unapproved source URL')
            if origin not in robots:
                parser=RobotFileParser()
                try:
                    with opener.open(Request(origin+'/robots.txt',headers={'User-Agent':AGENT}),timeout=12) as response:
                        parser.parse(response.read(500_000).decode('utf-8',errors='replace').splitlines())
                except HTTPError as exc:
                    if exc.code in (404,410):parser.parse([])
                    else:raise ValueError('Cannot establish robots permission: HTTP '+str(exc.code)) from exc
                robots[origin]=parser
            if not robots[origin].can_fetch(AGENT,url):
                result['status']='not fetched: robots disallows this path'
            else:
                delay=robots[origin].crawl_delay(AGENT) or robots[origin].crawl_delay('*') or 1
                time.sleep(min(60,max(1,delay)))
                with opener.open(Request(url,headers={'User-Agent':AGENT}),timeout=15) as response:
                    raw=response.read(20_000_001)
                    if len(raw)>20_000_000:raise ValueError('Response too large')
                    result.update(status='fetched',http_status=response.status,
                                  content_type=response.headers.get('Content-Type'),
                                  response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest(),
                                  final_url=response.url)
                    if parsed.hostname=='www.niaa.gov.au':
                        stored=Path(r'C:\work\aus_tender\inputs\mmr\ipp-companies-active-mmr-contracts-0.xlsx')
                        result['same_bytes_as_stored_list']=stored.is_file() and hashlib.sha256(stored.read_bytes()).digest()==hashlib.sha256(raw).digest()
                    if parsed.hostname=='careers.ey.com':
                        text=raw.decode('utf-8',errors='replace')
                        result['published_job_markers_present']=all(x in text for x in ['1737639','Cyber Defense Incident Responder'])
                        result['scope']='Published job markers in fetched page only; closing status, contractor engagement and agency acceptance remain unverified.'
        except Exception as exc:
            result.update(status='unavailable',error=str(exc))
        checks.append(result)
        print(json.dumps(result),flush=True)
    folder=Path(__file__).parent/'reports';folder.mkdir(exist_ok=True)
    (folder/'public-source-checks.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print('Fetched',sum(r['status']=='fetched' for r in checks),'of',len(checks),'curated source URLs; unavailable/disallowed checks are explicit.')


if __name__=='__main__':run()
