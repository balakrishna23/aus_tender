"""Panel research tasks from observed contract links; never infer supplier access."""
from datetime import date, datetime
import html
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if value else None


def worklist(report):
    """Keep source buyer labels separate and expose inconsistent notice claims."""
    as_of = _day(report['as_of'])
    if as_of is None:
        raise ValueError('Explicit as-of date required')
    grouped = {}
    for buyer in report['buyers']:
        for route in buyer['known_panel_routes']:
            item = grouped.setdefault(route['son_id'], {
                'son_id': route['son_id'], 'buyers': {}, 'titles': set(),
                'dates': set(), 'urls': set(), 'conflict': False})
            key = buyer['buyer_key']
            # Repeated observations of the same route must not inflate activity.
            existing = item['buyers'].get(key)
            linked = int(route['linked_contracts'])
            if linked < 0:
                raise ValueError('Linked contract count cannot be negative')
            item['buyers'][key] = dict(
                buyer_key=key, buyer=buyer['buyer'], rank=buyer['rank'],
                linked_contracts=max(linked, existing['linked_contracts'] if existing else 0),
                match_status=buyer['match_status'], review_status=buyer['review_status'],
                register_context_stale=buyer['register_context_stale'])
            if route.get('panel_title'):
                item['titles'].add(route['panel_title'])
            expiry = _day(route.get('notice_expiry'))
            if expiry:
                item['dates'].add(expiry)
            if route.get('panel_source_url'):
                item['urls'].add(route['panel_source_url'])
            item['conflict'] |= route.get('distinct_expiry_claims', 0) > 1
    rows = []
    for item in grouped.values():
        conflict = item['conflict'] or len(item['dates']) > 1
        expiry = next(iter(item['dates'])) if len(item['dates']) == 1 and not conflict else None
        days = (expiry - as_of).days if expiry else None
        status = ('conflicting_notice_dates' if conflict else 'notice_date_unknown' if days is None
                  else 'recorded_notice_date_passed' if days < 0
                  else 'recorded_notice_date_within_90_days' if days <= 90
                  else 'recorded_notice_date_later')
        buyers = sorted(item['buyers'].values(), key=lambda b: (b['rank'], b['buyer_key']))
        rows.append(dict(
            son_id=item['son_id'], observed_titles=sorted(item['titles']),
            observed_notice_expiry_dates=sorted(item['dates']), notice_expiry=expiry,
            days_to_recorded_notice_expiry=days, date_status=status,
            title_status='conflicting_titles_require_review' if len(item['titles']) > 1 else 'observed_title_only',
            evidence_urls=sorted(item['urls']), buyers=buyers, buyer_count=len(buyers),
            linked_contracts=sum(b['linked_contracts'] for b in buyers),
            highest_buyer_rank=min(b['rank'] for b in buyers),
            intake_status='unknown', illoura_membership_status='unknown',
            eligibility_status='not_assessed', invitation_status='unknown',
            next_actions=[
                'Check the current official notice for extensions and replacement arrangements.',
                'Verify intake dates, categories, participation rules and any subcontracting route.',
                'Check Illoura membership and category coverage against documentary evidence.',
                'Record dated evidence before assessing access or delivery readiness.']))
    rows.sort(key=lambda r: (r['highest_buyer_rank'], r['son_id']))
    provenance = {key: report.get(key) for key in [
        'procurement_snapshot_id', 'procurement_source_sha256', 'procurement_exported_at',
        'procurement_parser_revision', 'procurement_source_metadata',
        'contract_collection_checkpoint', 'contract_checkpoint_age_days',
        'register_snapshots', 'scoring_config_sha256']}
    return dict(as_of=as_of, status='draft_for_review', **provenance,
                source_buyer_report_code_bundle_sha256=report.get('code_bundle_sha256'),
                worklist_implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                warnings=list(report.get('warnings', [])),
                coverage='Known panel links for buyers in the selected federal research window; no claim of complete panel coverage.',
                ordering='Highest associated buyer research rank, then standing offer ID; not an access assessment.',
                limitations=[
                    'Notice expiry is not an application deadline, invitation, re-tender or proof an arrangement is closed.',
                    'Contract links demonstrate observed purchasing routes, not current supplier membership or eligibility.',
                    'Buyer labels remain separate pending identity review; contract counts across panels can overlap.'],
                panels=rows)


def _link(url):
    try:
        parsed = urlsplit(url)
        safe = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
    except ValueError:
        safe = False
    return '<a href="'+html.escape(url, quote=True)+'">Source notice</a>' if safe else 'Source URL requires review'


def write_worklist(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory/'panel_worklist.json'
    page = directory/'panel_worklist.html'
    raw.write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
    cards = []
    date_labels = {
        'conflicting_notice_dates': 'Conflicting source dates; review required',
        'notice_date_unknown': 'Date unknown',
        'recorded_notice_date_passed': 'Recorded date has passed; check extensions',
        'recorded_notice_date_within_90_days': 'Recorded date is within 90 days; verify the current notice',
        'recorded_notice_date_later': 'Recorded date is more than 90 days away'}
    for row in report['panels']:
        buyers = '; '.join(b['buyer']+' (rank '+str(b['rank'])+', '+b['review_status']+')' for b in row['buyers'])
        title = '; '.join(row['observed_titles']) or 'Notice title unavailable'
        cards.append('<article><h2>'+html.escape(row['son_id'])+' — '+html.escape(title)+'</h2>'
                     '<p>'+html.escape(buyers)+'</p><dl><dt>Recorded notice expiry</dt><dd>'
                     +html.escape(str(row['notice_expiry'] or 'Unknown'))+' · '+html.escape(date_labels[row['date_status']])
                     +'</dd><dt>Observed linked contracts</dt><dd>'+str(row['linked_contracts'])
                     +'</dd><dt>Access, membership and invitations</dt><dd>Unknown; eligibility not assessed</dd></dl>'
                     +'<p>'+' · '.join(_link(url) for url in row['evidence_urls'])+'</p><ol>'
                     +''.join('<li>'+html.escape(action)+'</li>' for action in row['next_actions'])+'</ol></article>')
    page.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<title>Panel research worklist</title><style>body{font:16px/1.5 system-ui;max-width:1050px;margin:32px auto;padding:0 20px;'
                    'color:#163144;background:#f4f6f8;overflow-wrap:anywhere}article{background:white;padding:20px;margin:18px 0;'
                    'border:1px solid #d9e0e5;border-radius:10px}dt{font-weight:600}dd{margin:0 0 12px}a{color:#075c86}</style>'
                    '<h1>Panel research worklist</h1><p>Draft for internal review · As of '+str(report['as_of'])+'</p><p>'
                    +html.escape(' '.join(report['limitations']))+'</p><p>'+html.escape(' '.join(report['warnings']))+'</p>'
                    +''.join(cards)+'<p>Full provenance and observations: panel_worklist.json</p></html>', encoding='utf-8')
    return {'panels': len(report['panels']), 'json': str(raw), 'html': str(page)}
