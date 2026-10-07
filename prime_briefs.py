"""Evidence-only prime briefs for Lane. Outputs stay in git-ignored exports/.

No people, RAP commitments or delivery availability are inferred. MMR candidates
and illustrative participation maths are explicitly unconfirmed/hypothetical.
"""
import hashlib
import html
import json
import re
from pathlib import Path
from statistics import median
from datetime import datetime

import classify

NIAA_URL = 'https://www.niaa.gov.au/resource-centre/ipp-companies-active-mmr-contracts-0'
COLUMNS = ['organisation', 'abns', 'priority', 'score', 'owner', 'built_at', 'why', 'unknowns',
           'steps', 'offer', 'illustrative_maths', 'today_action', 'source_links', 'html_file']


def _number(value):
    return re.sub(r'\D', '', str(value or ''))


def _url(row):
    return (row.get('page_url') or row.get('source_url') or row.get('api_url')
            or (f"https://api.tenders.gov.au/ocds/findById/{row['cn_id']}" if row.get('cn_id') else ''))


def _contract(row):
    value = row.get('current_value')
    amount = f'${value:,.2f}' if value is not None else 'value unknown'
    return (f"{row['cn_id']} — {row.get('buyer')}; {row.get('description')}; {amount}; "
            f"ends {row.get('current_end_date')} ({row.get('days_left')} days). {_url(row)}")


def build(actions, primes, candidates, renewals, panels, links, labour_counts=None):
    labour_counts = labour_counts or {}
    out = []
    # Each linked contract contributes one rate sample even if several request IDs were extracted.
    analysis = {}
    for row in links:
        rate = row.get('estimated_day_rate')
        families = classify.classify(row.get('role_title') or '')['role_families'] or ''
        if rate and rate > 0 and 'analysis' in families.split(', '):
            analysis.setdefault(row.get('cn_id') or row.get('page_url'), row)
    selected = sorted((a for a in actions if a['action_type'] == 'Tier-2 offer to prime'
                       and a.get('status') not in ('done', 'declined')
                       and a.get('stage') not in ('won', 'lost', 'no fit')),
                      key=lambda a: -(a.get('score') or 0))[:10]
    for action in selected:
        name = action['organisation']
        entities = [p for p in primes if (p.get('group_name') or p['organisation']) == name]
        numbers = {_number(p['abn']) for p in entities if p.get('abn')}
        # A listed company can match multiple ABNs: count its NIAA obligations once.
        listed = {p['organisation']: p for p in entities}
        mmr = sum(p.get('mmr_contracts') or 0 for p in listed.values())
        dates = ', '.join(sorted({p['list_date'] for p in entities})) or 'unknown'
        big = sorted((r for r in candidates if _number(r.get('supplier_abn')) in numbers),
                     key=lambda r: -(r.get('current_value') or 0))[:3]
        expiring = sorted((r for r in renewals if _number(r.get('supplier_abn')) in numbers
                           and 0 <= r['days_left'] <= 180
                           and r.get('category_family') in ('ICT services', 'labour hire and recruitment')),
                          key=lambda r: (r['days_left'], -(r.get('current_value') or 0)))
        memberships = {r['son_id']: r for r in panels if _number(r.get('abn')) in numbers}
        candidate_text = '\n'.join('candidate (public data does not confirm an MMR): ' + _contract(r)
                                   for r in big) or 'No active $7.5m+ candidates found in stored data.'
        sectors = '; '.join(f"{r['cn_id']}: {r.get('category_family') or 'unknown'} — "
                            f"{'plausible' if r.get('category_family') in ('ICT services', 'labour hire and recruitment') else 'check'}"
                            for r in big) or 'No candidate sector to check.'
        # Keep print briefs concise; every included signal has its source.
        renewal_text = '\n'.join(_contract(r) for r in expiring[:3]) or 'No staffing/ICT-services renewals within 180 days.'
        panel_text = '; '.join(f"{r['son_id']} {r['panel']} ({r['panel_url']})"
                               for r in memberships.values()) or 'No watched-panel membership found.'
        staffing_count = sum(labour_counts.get(n, 0) for n in numbers)
        staffing_sources = [f'https://api.tenders.gov.au/ocds/findById/{cn}'
            for n in sorted(numbers) for cn in (labour_counts.get(n + ':ids') or [])[:3]]
        staffing_note = (f'{staffing_count} labour-hire contracts in stored history; '
                         'this is a historical signal, not proof of current delivery capacity. '
                         'Count derived from contract_api.db/contracts for these ABNs; source examples: '
                         + ' '.join(staffing_sources[:3]))
        if not labour_counts:
            staffing_note = 'Labour-hire count not available in this build.'
        steps = [
            ('1 Load', f'NIAA list as at {dates}: {mmr} MMR contracts. {NIAA_URL}'),
            ('2 Match', '; '.join(f"{p['organisation']}: ABN {p['abn']}; {p.get('match')}; review {p.get('match_review')}"
                                  for p in entities) or 'No matched ABN; review required.'),
            ('3 Candidates', candidate_text), ('4 Sector check', sectors),
            ('5 Tier-2 signals', renewal_text + '\nPanels: ' + panel_text),
            ('6 RAP facts', 'pending: RAP table — RAP: not yet researched'),
            ('7 People', 'pending: engine 6 — no verified prime contact or warm path'),
            ('8 Capability', 'pending: bench — roles, clearance and availability unconfirmed'),
            ('9 Score', f"{action['score']} ({action['priority']}); {action.get('score_parts') or ''}. "
                         f"Assumptions: {action.get('assumptions') or 'not supplied'}")]
        maths = 'Hypothetical only: no participation obligation confirmed for these candidates.'
        if big:
            maths += (f" Largest candidate {big[0]['cn_id']} ${big[0]['current_value']:,.2f} × 4% = "
                      f"${big[0]['current_value'] * .04:,.2f} over its life; not a confirmed target. {_url(big[0])}")
        if analysis:
            rate = median(r['estimated_day_rate'] for r in analysis.values())
            maths += (f' One contractor-year estimate: median analysis rate ${rate:,.2f}/day × '
                      f'220 assumed business days = ${rate * 220:,.2f}; contract-value estimates, not quoted rates. '
                      f' Based on {len(analysis)} linked analysis contracts. Rate source examples: '
                      + ' '.join(sorted({_url(r) for r in analysis.values() if _url(r)})[:3]))
        else:
            maths += ' Contractor-year estimate unavailable: no linked analysis-role rate samples.'
        sources = {NIAA_URL, action.get('evidence') or ''}
        sources.update(_url(r) for r in big + expiring[:3])
        sources.update(r['panel_url'] for r in memberships.values())
        sources.update(_url(r) for r in analysis.values())
        sources.update(staffing_sources)
        sources.discard('')
        hook = next((r for r in renewals if _number(r.get('supplier_abn')) in numbers
                     and _url(r) == action.get('evidence')), None)
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'prime'
        slug += '-' + hashlib.sha1(name.encode()).hexdigest()[:6]
        out.append(dict(
            organisation=name, abns=', '.join(sorted(numbers)), priority=action['priority'], score=action['score'],
            owner=action.get('owner') or 'Lane',
            built_at=datetime.now(classify.CANBERRA).isoformat(timespec='minutes'),
            why=f'{mmr} MMR contracts on NIAA list as at {dates}. {NIAA_URL}\nRAP: not yet researched. ' + staffing_note,
            unknowns='Ask which contracts carry MMRs, whether targets are contract- or organisation-based, '
                     'and where they are furthest behind. Public data does not answer these questions.',
            steps='\n'.join(f'{label}: {text}' for label, text in steps),
            offer='Cleared BAs, data and cyber people in Canberra (to confirm against the bench). '
                  'Immediate renewal hook: ' + (_contract(hook) if hook else
                  f"{action.get('why_now')}. {action.get('evidence') or ''}"),
            illustrative_maths=maths,
            today_action=f"{action['priority']} | owner {action.get('owner') or 'Lane'} | due {action.get('due')} | "
                         f"{action.get('contact_safety')} | desired outcome: intro meeting / supplier onboarding",
            source_links='\n'.join(sorted(sources)), html_file=f'exports/briefs/{slug}.html'))
    return out


def _markup(text):
    """Escape all data; link only explicit HTTP(S) sources."""
    escaped = html.escape(str(text or ''))
    escaped = re.sub(r'https?://[^\s<>\"]+', lambda m: f'<a href="{m[0]}">source</a>', escaped)
    return escaped.replace('\n', '<br>')


def render(brief):
    steps = brief['steps'].split('\n')
    rows = []
    for line in steps:
        if re.match(r'^[1-9] ', line):
            label, _, content = line.partition(': ')
            rows.append([label, content])
        elif rows:
            rows[-1][1] += '\n' + line
    sections = ''.join(f'<section><h2>{label}</h2><p>{_markup(brief[key])}</p></section>'
                       for label, key in [('Why this prime', 'why'), ('Ask them', 'unknowns')])
    table = ''.join(f'<tr><th>{_markup(label)}</th><td>{_markup(content)}</td></tr>' for label, content in rows)
    footer = ''.join(f'<section><h2>{label}</h2><p>{_markup(brief[key])}</p></section>'
                     for label, key in [('Offer and hook', 'offer'), ('Illustrative maths — hypothetical', 'illustrative_maths'),
                                        ('TODAY action', 'today_action')])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>{html.escape(brief['organisation'])} — Illoura prime brief</title>
<style>@page {{size:A4; margin:10mm}} *{{box-sizing:border-box}}
body{{font:12px/1.3 Arial,sans-serif;color:#203047;max-width:190mm;margin:20px auto}}
h1{{font-size:22px;margin:0 0 5px}} h2{{font-size:13px;margin:7px 0 2px;color:#166b63}}
p{{margin:0}} table{{width:100%;border-collapse:collapse;margin-top:7px}}
th,td{{border:1px solid #d5dce2;padding:4px;vertical-align:top;text-align:left}}
th{{width:23mm;background:#edf4f3}} a{{color:#166b63}} section,tr{{break-inside:avoid}}
.meta{{border-bottom:2px solid #166b63;padding-bottom:6px}} @media print{{body{{margin:0}}}}
</style><body><h1>{html.escape(brief['organisation'])}</h1>
<p class="meta">Illoura Services · Draft for review · ABNs {_markup(brief['abns'])}<br>
{_markup(brief['priority'])} · Score {brief['score']} · Owner {_markup(brief['owner'])} · Built {_markup(brief['built_at'])}</p>
{sections}<table>{table}</table>{footer}</body></html>'''


def export(briefs, project_dir):
    folder = Path(project_dir) / 'exports' / 'briefs'
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / '.generated.json'
    try:
        previous = set(json.loads(manifest.read_text(encoding='utf-8')))
    except (FileNotFoundError, ValueError):
        previous = set()
    current = set()
    for brief in briefs:
        path = Path(project_dir) / brief['html_file']
        temporary = path.with_suffix('.partial')
        temporary.write_text(render(brief), encoding='utf-8')
        temporary.replace(path)
        current.add(path.name)
    # Remove only files generated by this module; stale targets must not look current.
    for name in previous - current:
        if Path(name).name == name and re.fullmatch(r'[a-z0-9-]+-[0-9a-f]{6}\.html', name):
            (folder / name).unlink(missing_ok=True)
    temporary = manifest.with_suffix('.partial')
    temporary.write_text(json.dumps(sorted(current)), encoding='utf-8')
    temporary.replace(manifest)
