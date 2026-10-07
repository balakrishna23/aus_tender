"""
NSW Buy.nsw.gov.au Notice Fetcher
Standalone async script (NOT a Scrapy spider) — uses pure Playwright + stealth
to bypass AWS WAF, then writes directly to the nsw_tenders SQLite database.

Run:  python nsw_tender_fetch.py
      python nsw_tender_fetch.py --pages 3   (3 pages per query = 30 results)

Stealth must be applied BEFORE page.goto() — this is why we can't use
scrapy-playwright (which applies PageMethods AFTER navigation).
"""
import json
import re
import asyncio
import logging
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
import activity_log
from project_paths import database_directory

from playwright.async_api import async_playwright
from undetected_playwright import stealth_async

# ── Configuration ─────────────────────────────────────────────────────────────
BASE        = 'https://buy.nsw.gov.au'
# IT first (Illoura spec); healthcare kept for the later healthcare vertical.
CATEGORIES  = 'information-and-technology%2Crecruitment-services%2Chealthcare'
NOTICE_TYPES = ['can', 'son']   # can = contract awards, son = standing offers
DB_PATH     = database_directory() / 'nsw_tender.db'
LOG_FILE    = Path(__file__).parent / 'logs' / 'nsw_tender.log'

HEALTH_KEYWORDS = [
    'nurs', 'midwif', 'enrolled nurse', 'registered nurse', 'clinical',
    'allied health', 'locum', 'agency staff', 'health worker', 'care worker',
    'personal care', 'aged care', 'home care', 'community nurs',
    'community health', 'palliative', 'wound care', 'ict labour',
    'labour hire', 'professional services', 'workforce', 'recruitment',
    'staffing', 'aboriginal health', 'indigenous health', 'first nations',
    'accho', 'ndis', 'disability support', 'support worker', 'veteran',
    'hospital', 'health service', 'health district', 'nsw health',
    'hnelhd', 'slhd', 'islhd', 'mlhd', 'snswlhd', 'justice health',
    'ambulance', 'medical', 'telehealth', 'ehealth', 'digital health',
    'healthshare', 'health infrastructure',
]

EXTRACT_NOTICES_JS = """
() => {
    const results = [];
    const container = document.querySelector('#search-results');
    if (!container) return results;
    container.querySelectorAll('li').forEach(li => {
        const link = li.querySelector('a[href*="/notices/"]');
        if (!link) return;
        const href = link.getAttribute('href') || '';
        if (!href.includes('/notices/')) return;
        results.push({ href, text: li.innerText.trim() });
    });
    return results;
}
"""

NEXT_PAGE_JS = """
() => {
    const nextBtn = document.querySelector('a.nsw-direction-link:not(.disabled)[href*="page="]');
    if (nextBtn && nextBtn.innerText.includes('Next')) return nextBtn.href;
    return null;
}
"""

# ── Logging ────────────────────────────────────────────────────────────────────
# Configured only when run as a script: importing this module (tests, other tools) must not
# send their log lines into logs/nsw_tender.log.
log = logging.getLogger('nsw_tender_fetch')


def setup_logging():
    LOG_FILE.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )


ICT_KEYWORDS = [
    'information and technology', 'ict', 'digital', 'software', 'cyber', 'cloud', 'data', 'network',
    'application', 'platform', 'it services', 'it contractor', 'contingent workforce', 'labour hire',
]


ICT_PATTERN = re.compile(r'\b(?:' + '|'.join(re.escape(k) for k in ICT_KEYWORDS) + r')\b')


def is_relevant(text):
    t = (text or '').lower()
    return any(kw in t for kw in HEALTH_KEYWORDS) or bool(ICT_PATTERN.search(t))


# ── Text parsing ───────────────────────────────────────────────────────────────
def _parse_notice_text(text):
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    notice_id   = lines[0] if lines else ''
    notice_type = lines[1] if len(lines) > 1 else ''

    LABEL_MAP = {
        'can id':            'can_id',
        'son id':            'son_id',
        'agency':            'agency',
        'category':          'category',
        'publish date':      'publish_date',
        'contract period':   'contract_period',
        'estimated amount':  'value',
        'contractor name':   'contractor',
        'is an aboriginal':  'aboriginal_biz',
        'supplier name':     'contractor',
        'standing offer id': 'son_id',
    }
    data = {v: '' for v in LABEL_MAP.values()}

    i = 2
    while i < len(lines) - 1:
        label_raw = lines[i].lower()
        val = lines[i + 1]
        for key, field in LABEL_MAP.items():
            if label_raw.startswith(key):
                data[field] = val
                i += 2
                break
        else:
            i += 1

    ref = data.get('can_id') or data.get('son_id') or notice_id
    return {
        'notice_id':       notice_id,
        'notice_type':     notice_type,
        'reference':       ref,
        'agency':          data['agency'],
        'category':        data['category'],
        'publish_date':    data['publish_date'],
        'contract_period': data['contract_period'],
        'value':           data['value'],
        'contractor':      data['contractor'],
        'aboriginal_biz':  data['aboriginal_biz'],
    }


# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute('''CREATE TABLE IF NOT EXISTS nsw_tenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        portal TEXT DEFAULT 'nsw_tender',
        reference TEXT,
        title TEXT,
        agency TEXT,
        category TEXT,
        status TEXT,
        close_date TEXT,
        publish_date TEXT,
        value TEXT,
        description TEXT,
        contact_name TEXT DEFAULT '',
        contact_email TEXT DEFAULT '',
        illoura_relevant INTEGER DEFAULT 0,
        url TEXT UNIQUE,
        scraped_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    columns = {row[1] for row in con.execute('PRAGMA table_info(nsw_tenders)')}
    for column in ('notice_type', 'location', 'contact_phone', 'all_details') + DETAIL_COLUMNS:
        if column not in columns:
            con.execute(f'ALTER TABLE nsw_tenders ADD COLUMN {column} TEXT')
    con.commit()
    return con


def upsert(con, item):
    try:
        con.execute('''INSERT INTO nsw_tenders
            (portal, reference, title, agency, category, status,
             close_date, publish_date, value, description,
             illoura_relevant, url, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                reference=excluded.reference,
                title=excluded.title,
                close_date=excluded.close_date,
                agency=excluded.agency,
                category=excluded.category,
                status=excluded.status,
                publish_date=excluded.publish_date,
                value=excluded.value,
                description=excluded.description,
                illoura_relevant=excluded.illoura_relevant,
                scraped_at=excluded.scraped_at,
                updated_at=CURRENT_TIMESTAMP
        ''', (
            item['portal'], item['reference'], item['title'],
            item['agency'], item['category'], item['status'],
            item['close_date'], item['publish_date'], item['value'],
            item['description'], int(item['illoura_relevant']),
            item['url'], item['scraped_at'],
        ))
        con.commit()
    except Exception as e:
        log.error(f"DB error: {e} — url={item.get('url')}")
        raise


# ── Main Playwright logic ──────────────────────────────────────────────────────
async def fetch_notices(notice_type: str, max_pages: int, con: sqlite3.Connection):
    """Fetch one notice type (can or son) with pagination."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        ctx = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='en-AU',
            viewport={'width': 1366, 'height': 768},
        )

        page = await ctx.new_page()
        # Apply stealth BEFORE first navigation
        await stealth_async(page)

        start_url = (
            f'{BASE}/notices/search'
            f'?noticeTypes={notice_type}'
            f'&categories={CATEGORIES}'
            f'&mode=regular'
        )

        current_url = start_url
        page_num    = 1
        total       = 0

        while current_url and page_num <= max_pages:
            log.info(f"  [{notice_type}] Page {page_num}: {current_url}")
            try:
                resp = await page.goto(current_url, wait_until='load', timeout=30000)
                status = resp.status if resp else '?'
                log.info(f"    HTTP {status}")
                if resp is None or resp.status >= 400:
                    raise RuntimeError(f"NSW listing request failed: HTTP {status}")

                # Wait for results (up to 20s)
                try:
                    await page.wait_for_selector('#search-results li a', timeout=20000)
                except Exception as e:
                    log.warning(f"    Selector timed out: {e}")

                notices = await page.evaluate(EXTRACT_NOTICES_JS)
                if not notices:
                    raise RuntimeError('No notices extracted; verify the listing page and selectors')
                log.info(f"    {len(notices)} notices on page {page_num}")

                for n in notices:
                    href   = n.get('href', '')
                    text   = n.get('text', '')
                    url    = (BASE + href) if href.startswith('/') else href
                    parsed = _parse_notice_text(text)

                    full             = f"{parsed['agency']} {parsed['category']} {text}"
                    illoura_relevant = is_relevant(full)

                    item = {
                        'portal':           'nsw_tender',
                        'reference':        parsed['reference'],
                        'title':            parsed['notice_id'],
                        'agency':           parsed['agency'],
                        'category':         parsed['category'],
                        'status':           parsed['notice_type'],
                        'close_date':       '',
                        'publish_date':     parsed['publish_date'],
                        'value':            parsed['value'],
                        'description':      (
                            f"Contract period: {parsed['contract_period']} | "
                            f"Contractor: {parsed['contractor']} | "
                            f"Aboriginal biz: {parsed['aboriginal_biz']}"
                        ),
                        'illoura_relevant': illoura_relevant,
                        'url':              url,
                        'scraped_at':       datetime.utcnow().isoformat(),
                    }
                    upsert(con, item)
                    total += 1

                    if illoura_relevant:
                        log.info(
                            f"    *** RELEVANT [{parsed['reference']}] "
                            f"{parsed['agency'][:35]} | {parsed['category'][:30]}"
                        )

                # Pagination
                next_href = await page.evaluate(NEXT_PAGE_JS)
                if next_href and next_href != '#':
                    current_url = next_href if next_href.startswith('http') else (BASE + next_href)
                    page_num += 1
                    await asyncio.sleep(2)
                else:
                    break

            except Exception as e:
                log.error(f"    Error on page {page_num}: {e}")
                await browser.close()
                raise

        await browser.close()
        log.info(f"  [{notice_type}] Done — {total} notices saved.")
        return total


EXTRACT_OPPORTUNITIES_JS = """
() => Array.from(document.querySelectorAll('#search-results li'))
    .filter(li => li.querySelector('a[href*="/prcOpportunity/"]'))
    .map(li => {
        const dd = {};
        li.querySelectorAll('dt').forEach(dt => { dd[dt.innerText.trim()] = (dt.nextElementSibling || {}).innerText || ''; });
        const closes = Array.from(li.querySelectorAll('p')).find(p => /^\\s*Closes:/.test(p.innerText));
        return {
            href: li.querySelector('a[href*="/prcOpportunity/"]').getAttribute('href'),
            title: (li.querySelector('h3') || {}).innerText || '',
            closes: closes ? closes.innerText.replace(/^\\s*Closes:\\s*/, '').trim() : '',
            categories: Array.from(li.querySelectorAll('p.subcat')).map(p => p.innerText.trim()),
            reference: Array.from(li.querySelectorAll('ul.pills li')).map(p => p.innerText.trim()).join(', '),
            description: (li.querySelector('p.limit') || {}).innerText || '',
            type: (dd['Opportunity type'] || '').trim(),
            agency: (dd['Agency'] || '').trim(),
        };
    })
"""


# Overview fields and page sections of an opportunity, and the columns they are stored in.
OVERVIEW_COLUMNS = {
    'Type': 'procurement_type', 'Covered procurement': 'covered_procurement',
    'Multi agency use': 'multi_agency_use', 'Related opportunities': 'related_opportunities',
    'Estimated timeframe for delivery': 'timeframe_for_delivery',
    'Estimated decision date': 'estimated_decision_date',
}
SECTION_COLUMNS = {
    'Project goals': 'project_goals', 'Scope': 'scope', 'Related files': 'related_files',
    'Requirements': 'requirements', 'Evaluation': 'assessment_criteria', 'Notices': 'notices',
    'Amendments': 'amendments',
}
PAGE_SECTIONS = ['Description', 'Overview', *SECTION_COLUMNS, 'Terms and conditions',
                 'Download opportunity package', 'Log in to respond']
DETAIL_COLUMNS = tuple(OVERVIEW_COLUMNS.values()) + tuple(SECTION_COLUMNS.values())
EMPTY_SECTION = re.compile(r'^No (notices|amendments) ha(ve|s) been', re.I)
BOILERPLATE = {'Assessment Criteria', 'General assessment criteria that was used to evaluate this opportunity',
               'Opportunity requirements'}


def _parse_opportunity_detail(text):
    """Every field and section of an opportunity page (its rendered text)."""
    sections, current = {}, None
    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue
        if line in PAGE_SECTIONS:
            current = sections.setdefault(line, [])
        elif current is not None:
            current.append(line)
    # Overview: "Label:" lines, each followed by its value line(s); the whole page if the
    # Overview heading is missing (older layout).
    overview, label = {}, None
    for line in sections.get('Overview') or [raw.strip() for raw in text.splitlines() if raw.strip()]:
        match = re.fullmatch(r'([A-Z][^:]{1,60}):', line)
        if match:
            label = match.group(1)
            overview[label] = []
        elif label:
            overview[label].append(line)
    contact = overview.get('Primary contact', [])
    email = next((x for x in contact if '@' in x), '')
    phone = next((x for x in contact if re.fullmatch(r'[\d\s()+-]{6,}', x)), '')
    name = next((x for x in contact if x not in (email, phone)), '')
    info = {
        'publish_date': ' '.join(overview.get('Publish date', [])),
        'locations': '; '.join(overview.get('Locations', [])),
        'contact_name': name, 'contact_email': email, 'contact_phone': phone,
    }
    for label, column in OVERVIEW_COLUMNS.items():
        info[column] = '; '.join(overview.get(label, []))
    for heading, column in SECTION_COLUMNS.items():
        lines = [x for x in sections.get(heading, []) if x not in BOILERPLATE]
        info[column] = '' if lines and EMPTY_SECTION.match(lines[0]) else '\n'.join(lines)
    details = {'Overview': {k: '; '.join(v) for k, v in overview.items() if v}}
    details.update({k: '\n'.join(v) for k, v in sections.items()
                    if v and k not in ('Overview', 'Download opportunity package', 'Log in to respond')})
    info['all_details'] = json.dumps(details, ensure_ascii=False) if any(details.values()) else ''
    return info


def upsert_opportunity(con, item):
    """Insert a new open opportunity, or refresh listing fields; detail fields are kept when absent."""
    existing = con.execute('SELECT 1 FROM nsw_tenders WHERE url = ?', (item['url'],)).fetchone()
    detail = ('contact_phone', 'all_details') + DETAIL_COLUMNS
    keep_detail = ''.join(f"{c}=COALESCE(NULLIF(excluded.{c}, ''), {c}),\n            " for c in detail)
    con.execute(f'''INSERT INTO nsw_tenders
        (portal, reference, title, agency, category, status, notice_type, close_date, publish_date,
         description, contact_name, contact_email, location, illoura_relevant, url, scraped_at,
         {', '.join(detail)})
        VALUES (:portal, :reference, :title, :agency, :category, :status, :notice_type, :close_date,
                :publish_date, :description, :contact_name, :contact_email, :location,
                :illoura_relevant, :url, :scraped_at, {', '.join(':' + c for c in detail)})
        ON CONFLICT(url) DO UPDATE SET
            {keep_detail}
            title=excluded.title, agency=excluded.agency, category=excluded.category,
            status=excluded.status, notice_type=excluded.notice_type, close_date=excluded.close_date,
            description=excluded.description, illoura_relevant=excluded.illoura_relevant,
            publish_date=COALESCE(NULLIF(excluded.publish_date, ''), publish_date),
            contact_name=COALESCE(NULLIF(excluded.contact_name, ''), contact_name),
            contact_email=COALESCE(NULLIF(excluded.contact_email, ''), contact_email),
            location=COALESCE(NULLIF(excluded.location, ''), location),
            scraped_at=excluded.scraped_at,
            updated_at=CASE WHEN close_date IS NOT excluded.close_date OR title IS NOT excluded.title
                            OR status IS NOT excluded.status THEN CURRENT_TIMESTAMP ELSE updated_at END
    ''', {**{c: '' for c in detail}, **item})
    con.commit()
    return existing is None


async def fetch_opportunities(con: sqlite3.Connection, max_pages: int = 60):
    """All currently open NSW opportunities (RFT/RFQ/EOI...), every category.

    Detail pages are opened only for opportunities not seen before. Opportunities
    that drop off the open list are marked Closed.
    """
    known = {row[0] for row in con.execute(
        "SELECT url FROM nsw_tenders WHERE portal = 'nsw_opportunity' AND publish_date != '' "
        "AND COALESCE(all_details, '') != ''")}  # saved before every page field was kept: re-read once
    seen, schemes, new, detail_failures = set(), set(), 0, 0
    events = []  # for activity.db
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = await browser.new_context(
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
            locale='en-AU', viewport={'width': 1366, 'height': 768})
        page = await ctx.new_page()
        await stealth_async(page)
        detail = await ctx.new_page()
        await stealth_async(detail)
        reported = None
        try:
            for page_num in range(1, max_pages + 1):
                url = f'{BASE}/opportunity/search?mode=regular&page={page_num}'
                try:
                    resp = await page.goto(url, wait_until='load', timeout=45000)
                except Exception as e:
                    log.warning(f"    Listing page {page_num} failed ({e}); retrying once")
                    await asyncio.sleep(10)
                    resp = await page.goto(url, wait_until='load', timeout=60000)
                if resp is None or resp.status >= 400:
                    raise RuntimeError(f'NSW opportunity listing failed: HTTP {resp.status if resp else "?"}')
                try:
                    await page.wait_for_selector('a[href*="/prcOpportunity/"]', timeout=20000)
                except Exception:
                    pass
                if reported is None:
                    count = re.search(r'([\d,]+)\s+results', await page.inner_text('body'))
                    reported = int(count.group(1).replace(',', '')) if count else None
                cards = await page.evaluate(EXTRACT_OPPORTUNITIES_JS)
                # Prequalification schemes share the list but are standing registers, not tenders.
                schemes.update(await page.evaluate(
                    "() => Array.from(document.querySelectorAll('#search-results a[href*=\"/scheme/\"]'))"
                    ".map(a => a.getAttribute('href'))"))
                fresh = [c for c in cards if BASE + c['href'] not in seen]
                if not fresh:
                    break
                for card in fresh:
                    opp_url = BASE + card['href']
                    seen.add(opp_url)
                    info = {'publish_date': '', 'locations': '', 'contact_name': '', 'contact_email': ''}
                    if opp_url not in known:
                        try:
                            await detail.goto(opp_url, wait_until='load', timeout=45000)
                            await detail.wait_for_timeout(1500)
                            # Only the opportunity itself, not the site's menus.
                            body = 'main' if await detail.query_selector('main') else 'body'
                            info = _parse_opportunity_detail(await detail.inner_text(body))
                        except Exception as e:
                            # Saved from the listing without a publish date, so the next run
                            # retries the detail page; one slow page must not sink the run.
                            detail_failures += 1
                            log.warning(f"    Detail page failed, will retry next run: {opp_url} ({e})")
                        await asyncio.sleep(1)
                    category = '; '.join(card['categories'])
                    is_new = upsert_opportunity(con, {
                        'portal': 'nsw_opportunity', 'reference': card['reference'],
                        'title': card['title'].strip(), 'agency': card['agency'], 'category': category,
                        'status': 'Open', 'notice_type': card['type'], 'close_date': card['closes'],
                        'publish_date': info['publish_date'], 'description': card['description'].strip(),
                        'contact_name': info['contact_name'], 'contact_email': info['contact_email'],
                        'location': info['locations'],
                        **{c: info.get(c, '') for c in ('contact_phone', 'all_details') + DETAIL_COLUMNS},
                        'illoura_relevant': int(is_relevant(f"{card['title']} {category} {card['agency']}")),
                        'url': opp_url, 'scraped_at': datetime.utcnow().isoformat(),
                    })
                    new += is_new
                    if is_new:
                        events.append(('nsw_tender', 'new', card['reference'], card['title'].strip(), opp_url))
                await asyncio.sleep(1)
        finally:
            await browser.close()

    if reported is not None and len(seen) + len(schemes) < reported:
        raise RuntimeError(f'NSW opportunities incomplete: listed {len(seen)} tenders + '
                           f'{len(schemes)} schemes of {reported}')
    if not seen:
        raise RuntimeError('No NSW opportunities extracted; verify the listing page and selectors')
    # Only after a complete walk: anything no longer listed has closed.
    placeholders = ','.join('?' * len(seen))
    events += [('nsw_tender', 'closed', ref, title, url) for ref, title, url in con.execute(
        f"SELECT reference, title, url FROM nsw_tenders WHERE portal = 'nsw_opportunity' "
        f"AND status = 'Open' AND url NOT IN ({placeholders})", list(seen))]
    closed = con.execute(
        f"UPDATE nsw_tenders SET status = 'Closed', updated_at = CURRENT_TIMESTAMP "
        f"WHERE portal = 'nsw_opportunity' AND status = 'Open' AND url NOT IN ({placeholders})",
        list(seen)).rowcount
    con.commit()
    activity_log.record_items(events)
    if detail_failures > max(3, len(seen) // 10):
        # A few slow pages are normal; this many means the site is blocking or has changed.
        raise RuntimeError(f'NSW detail pages failing: {detail_failures} of {len(seen)}')
    log.info(f"  [opportunities] Coverage: listed {len(seen)} open tenders + {len(schemes)} prequalification schemes of {reported}; {new} new; {closed} closed since last run")
    return len(seen)


async def main(max_pages: int = 5):
    con = init_db()
    log.info(f"=== NSW Tender Fetch started — max {max_pages} pages per notice type ===")
    total = 0
    try:
        log.info("Fetching open opportunities")
        total += await fetch_opportunities(con)
        for nt in NOTICE_TYPES:
            log.info(f"Fetching notice type: {nt}")
            n = await fetch_notices(nt, max_pages, con)
            total += n
    finally:
        con.close()
    log.info(f"=== DONE — {total} total notices saved to {DB_PATH} ===")
    return total


if __name__ == '__main__':
    setup_logging()
    parser = argparse.ArgumentParser(description='Fetch NSW buy.nsw.gov.au notices')
    parser.add_argument('--pages', type=int, default=5, help='Max pages per notice type (default 5 = 50 notices)')
    args = parser.parse_args()
    if args.pages < 1:
        parser.error('--pages must be positive')
    asyncio.run(main(max_pages=args.pages))
