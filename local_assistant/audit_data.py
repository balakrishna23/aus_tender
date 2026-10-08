"""Aggregate quality audit: no personal contact details exported."""
import hashlib,json,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
import assistant

ROOT=Path(__file__).parent


def audit():
    q=assistant.read_rows
    result={'audited_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'Stored data quality and retrieval coverage; not certification of source completeness.'}
    result['build_timestamp']=q('intelligence.db','SELECT built_at FROM build_info LIMIT 1')[0]['built_at']
    result['contracts']=q('contract_api.db',"""SELECT COUNT(*) AS total,
       SUM(CASE WHEN supplier IS NULL OR trim(supplier)='' THEN 1 ELSE 0 END) AS missing_supplier,
       SUM(CASE WHEN buyer IS NULL OR trim(buyer)='' THEN 1 ELSE 0 END) AS missing_buyer,
       SUM(CASE WHEN supplier_abn IS NULL OR trim(supplier_abn)='' THEN 1 ELSE 0 END) AS missing_supplier_abn,
       SUM(CASE WHEN current_end_date IS NULL OR trim(current_end_date)='' THEN 1 ELSE 0 END) AS missing_end_date,
       SUM(CASE WHEN current_end_date IS NOT NULL AND date(current_end_date) IS NULL THEN 1 ELSE 0 END) AS unparseable_end_date,
       SUM(CASE WHEN date(current_end_date)<date(start_date) THEN 1 ELSE 0 END) AS end_before_start,
       SUM(CASE WHEN current_value IS NULL THEN 1 ELSE 0 END) AS missing_value,
       SUM(CASE WHEN current_value<=0 THEN 1 ELSE 0 END) AS nonpositive_value,
       SUM(CASE WHEN page_url IS NULL OR trim(page_url)='' THEN 1 ELSE 0 END) AS missing_readable_source,
       MAX(retrieved_at) AS latest_retrieval,MAX(first_published) AS latest_publication
       FROM contracts""")[0]
    result['opportunities']=q('intelligence.db',"""SELECT source,COUNT(*) AS total,
        MAX(retrieved_at) AS latest_retrieval,
        SUM(CASE WHEN lower(status_effective)='open' THEN 1 ELSE 0 END) AS stored_open,
        SUM(CASE WHEN lower(status_effective)='open' AND julianday(closes_at)>julianday('now') THEN 1 ELSE 0 END) AS future_closing_open,
        SUM(CASE WHEN lower(status_effective)='open' AND closes_at IS NULL THEN 1 ELSE 0 END) AS open_without_closing_time,
        SUM(CASE WHEN lower(status_effective)='open' AND julianday(closes_at)<=julianday('now') THEN 1 ELSE 0 END) AS elapsed_stored_open
        FROM opportunities GROUP BY source""")
    result['panels']=q('intelligence.db','SELECT COUNT(*) AS member_records,COUNT(DISTINCT son_id) AS distinct_panels FROM panel_members')[0]
    from reasoning import date_value
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo('Australia/Canberra')).date()
    rows=q('intelligence.db','SELECT deed_start,deed_end FROM panel_members')
    result['panels']['expired_member_records']=sum(bool(date_value(r['deed_end']) and date_value(r['deed_end'])<today) for r in rows)
    result['panels']['incomplete_member_dates']=sum(not date_value(r['deed_start']) or not date_value(r['deed_end']) for r in rows)
    result['mmr']=q('intelligence.db',"SELECT COUNT(*) AS matched_rows,COUNT(DISTINCT organisation) AS organisations,MIN(list_date) AS oldest_list,MAX(list_date) AS latest_list, SUM(CASE WHEN COALESCE(match_review,'')!='confirmed' THEN 1 ELSE 0 END) AS unconfirmed_match_rows FROM mmr_primes")[0]
    result['contacts']=q('intelligence.db',"SELECT COUNT(*) AS records,SUM(CASE WHEN usable_for_outreach=1 AND stale=0 AND opted_out=0 THEN 1 ELSE 0 END) AS stored_usable,SUM(CASE WHEN stale=1 THEN 1 ELSE 0 END) AS flagged_stale,SUM(CASE WHEN opted_out=1 THEN 1 ELSE 0 END) AS opted_out FROM agency_contacts")[0]
    from public_research import findings
    public=findings([])
    result['public_research']={'records':len(public),'organisations':len({r['organisation'] for r in public}),
        'historical_raps':sum(r.get('period_status','').startswith('historical') for r in public),
        'proof_limits':'RAPs and published routes are policy/access evidence. The job advert does not establish contractor or agency acceptance.'}
    result['missing_private_inputs']=[
        {'input':'Candidate bench','owner':'Sirish','needed':['candidate reference','skills supported by CV','location/work arrangement','availability and checked date','clearance status and verification source','rate and currency','permission to use CV']},
        {'input':'Relationships','owner':'Lane and Sirish','needed':['organisation','known contact or team','relationship owner','last interaction','verified introduction path','restrictions or opt-out']},
        {'input':'Commercial assumptions','owner':'Sirish','needed':['pay and bill rates','hours','employment/contractor costs','approved margin policy','stage probabilities']},
        {'input':'MMR match review','owner':'Sirish','needed':['review loose supplier-name/ABN suggestions in existing private review file']}
    ]
    result['limits']=[
        'Contract descriptions are keyword signals; classification codes do not alone prove staffing suitability.',
        'Stored open notices are rechecked against the closing timestamp at query time.',
        'The panel crawler discovers panels linked from stored contracts; this is not an exhaustive panel directory.',
        'CVs and relationship histories cannot be inferred from public procurement records.',
        'No automatic continuous update of curated public research is enabled.'
    ]
    return result


if __name__=='__main__':
    output=ROOT/'reports';output.mkdir(exist_ok=True)
    result=audit()
    (output/'data-quality.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
