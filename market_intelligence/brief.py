"""Evidence-linked internal market digest. Generates files only; never sends mail."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import re

from sqlalchemy import text

from .analytics import build_report
from .aps import build_aps_report
from .demand import build_demand
from .panel_access import build_panel_access
from .research import build_rap
from .watchlist import category_summary
from .reporting import json_value
from .resources import https_url


def link(label,url):
    try:https_url(url)
    except ValueError:return []
    return [dict(label=label,url=url)]


def build_brief(engine,as_of):
    buyers=build_report(engine,as_of);panels=build_panel_access(engine,as_of);rap=build_rap(engine,as_of)
    demand=build_demand(engine,as_of);aps=build_aps_report(engine,as_of);categories=category_summary(engine)
    with engine.connect() as conn:
        unresolved=conn.execute(text("SELECT count(*) FROM market.buyer_resolution WHERE match_status IN ('ambiguous','conflict','unmatched')")).scalar()
        unreviewed=conn.execute(text("SELECT count(*) FROM market.buyer_resolution WHERE review_status<>'approved' OR register_context_stale")).scalar()
        failed=conn.execute(text("SELECT count(*) FROM market.pipeline_runs WHERE status='failed' AND started_at>=:since"),{'since':as_of-timedelta(days=7)}).scalar()
    sections=[]
    rows=buyers['buyers'] if 'buyers' in buyers else buyers.get('ranked_buyers',[])
    items=[]
    for r in rows[:3]:
        value=r.get('awarded_value_365d')
        facts=(f"{r['recent_awards_count']:,} relevant original awards in the trailing 365 days"
               +(f", with published original values totalling ${value:,.0f}." if value is not None else '; original values unavailable.'))
        evidence=r.get('recent_award_evidence',[]) or r.get('evidence_contracts',[])
        url=next((e.get('source_url') for e in evidence if e.get('source_url')),None)
        items.append(dict(heading=r['buyer'],paragraphs=[facts,
            f"{r['expiry_count_365d']:,} contracts have recorded end dates in the next year. These are research signals; re-tenders, panel access and candidate fit are unconfirmed."],
            links=link('Official award evidence',url)))
    if items:sections.append(dict(title='Buyer research from recorded awards',items=items))
    selected=[r for r in panels['calendar'] if r['date']<=as_of+timedelta(days=90)][:3]
    if selected:
        sections.append(dict(title='Panel dates to verify',items=[dict(heading=r['panel_name'],paragraphs=[
            str(r['date'])+' · '+r['event'].replace('_',' ')+'. '+('Published notice date; application windows and extensions are unverified.' if r['basis'].startswith('published_notice') else 'Recorded human-verified evidence; recheck the current portal before acting.')],
            links=link('Panel evidence',r['source_url'])) for r in selected]))
    selected=[r for r in rap['targets'] if r['period_status']=='within_published_period' and r['reviewed_commitments']][:3]
    if selected:
        sections.append(dict(title='Page-cited RAP procurement research',items=[dict(heading=r['organisation'],paragraphs=[
            f"{r['rap_type']} RAP: {r['period_as_published']}. "+r['why_now']+'.',
            'Reviewed summary, PDF page '+str(r['reviewed_commitments'][0]['pdf_page'])+': '+r['reviewed_commitments'][0]['summary'],
            'Published commitment only; completion, available budget, legal-entity scope and Illoura eligibility remain unverified.'],
            links=link('Published RAP document',r['document_url'])) for r in selected]))
    selected=[r for r in demand['vacancy_trends'] if r['region']!='AUST' and r['ads_3m_average'] is not None
              and re.search(r'ICT|registered nurses|software|database|aged and disabled carers',r['occupation_title'],re.I)][:3]
    if selected:
        sections.append(dict(title='Published workforce demand indicators',items=[dict(heading=r['occupation_title']+' · '+r['region'],paragraphs=[
            str(r['period'])+f": {r['ads_3m_average']:,.1f} online advertisements (three-month average). "
            +(f"Year-on-year change {r['year_on_year']:+.1%}." if r['year_on_year'] is not None else 'Year-on-year comparison unavailable.'),
            'Advertisements are not a vacancy count. Illoura role mapping, available bench and clearance fit remain unverified.'],
            links=link('Jobs and Skills Australia source',r['source_url'])) for r in selected]))
    catch_all=categories['catch_all_share_of_our_family_value']
    checklist=[f'{unreviewed} buyer identities still need approval or rechecking, including {unresolved} ambiguous, conflicting or unmatched cases.',
               f"{sum(p['needs_manual_check'] for p in panels['panels'])} panels require evidence verification; {len(panels['unknown_profile_keys'])} profile fields are unknown.",
               'Supply aggregate bench counts, occupation mappings and CRM/invitation records before assessing delivery or invitation gaps.',
               (f'{catch_all:.1%} of original award value in the broad ICT/labour-hire families has only broad category evidence.' if catch_all is not None else 'Category evidence coverage unavailable.'),
               f'{failed} failed pipeline attempts recorded in the last seven days; see the run report for subsequent recoveries.']
    sections.append(dict(title='Internal review checklist',items=[dict(heading='Evidence and business inputs',paragraphs=checklist,links=[])]))
    return dict(schema='illoura.market_brief.v1',as_of=as_of,generated_at=datetime.now(timezone.utc),status='internal research supplement',
        sections=sections,summary=dict(buyers=len(rows),rap_entries=len(rap['targets']),aps_agencies=aps['summary']['agencies'],
                                      unresolved_identities=unresolved,category_catch_all_value_share=catch_all),
        limitations=['Market research is separate from the brief’s live-rechecked RFQs. No invitation, qualification or available-budget claim is made.',
                     'Each dataset has its own date; cross-source simultaneity is not asserted.',
                     'Creating this package neither sends email nor changes any schedule.'])


def write_brief(report,directory):
    from .reporting import write_report_files
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/'market_brief.json'
    path.write_text(json.dumps(report,default=json_value,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=[dict(section=s['title'],heading=i['heading'],paragraphs=' '.join(i['paragraphs'])) for s in report['sections'] for i in s['items']]
    write_report_files(report,directory,'market_digest',rows,['section','heading','paragraphs'],'Market research digest')
    return dict(json=str(path),sections=len(report['sections']),items=len(rows))
