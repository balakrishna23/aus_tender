"""Read-only candidate extraction for a human-reviewed newsletter edition."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(r'/opt/aus_tender')
BASE = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('Australia/Canberra')


def rows(db, sql, args=()):
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args)]


def rap_partner_evidence(project=PROJECT):
    """Expose reviewed RAP facts with fresh contract signals, keeping expired plans separate."""
    source=project/'databases/rap_evidence.db'
    if not source.is_file():
        return {'status':'not_available','current_period_partners':[],'expired_plan_partners':[]}
    from build_rap_partner_register import contract_context, LIMITS
    today=datetime.now(ZoneInfo('Asia/Kolkata')).date()
    records=rows(source,'''SELECT r.payload_json FROM company_reviews r
        WHERE r.reviewed_on=(SELECT max(x.reviewed_on) FROM company_reviews x
                             WHERE x.organisation=r.organisation)''')
    current=[];expired=[];future=[]
    for record in records:
        partner=json.loads(record['payload_json'])
        partner['contract_context']=contract_context(project,partner['candidate_abns'],today)
        if partner['period_start']<=str(today)<=partner['period_end']:
            partner['period_status']='Within published period';current.append(partner)
        elif partner['period_end']<str(today):
            partner['period_status']='Expired published period';expired.append(partner)
        else:
            partner['period_status']='Future published period';future.append(partner)
    return {'status':'available','as_of':str(today),'current_period_partners':current,
            'expired_plan_partners':expired,'future_plan_partners':future,'limitations':LIMITS,
            'instruction':'Use page-cited reviewed facts with their original review dates. Confirm the latest plan and supplier route before outreach. Past milestones are not proof of completion or missed targets.'}


def prepare(out):
    import sys
    sys.path.insert(0, str(PROJECT))
    from classify import closing_time
    moment = datetime.now(TZ)
    today = moment.date()
    root = PROJECT / 'databases'
    baseline = json.loads((out/'baseline.json').read_text(encoding='utf-8'))
    prior = out/'baseline.json'
    package = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'edition_date_canberra': str(today),
        'baseline': str(prior),
        'note': 'Candidate evidence for editorial review; not proof of supplier access, candidate fit, hiring or renewal intention.',
    }
    package['contract_total'] = rows(root/'contract_api.db', 'SELECT count(*) n FROM contracts')[0]['n']
    package['contract_total_before'] = baseline['contracts']
    package['build_info'] = rows(root/'intelligence.db', 'SELECT * FROM build_info')
    package['today'] = rows(root/'intelligence.db', 'SELECT * FROM today ORDER BY rank')
    live = rows(root/'intelligence.db', "SELECT * FROM opportunities WHERE is_relevant=1 AND status_effective='Open'")
    # Query-time dates protect against a stale source retaining an 'open' label.
    live = [r for r in live if r.get('closes_at') and datetime.fromisoformat(r['closes_at']).astimezone(TZ) > moment]
    live.sort(key=lambda r: r['closes_at'])
    package['live_opportunities'] = live
    buyict = rows(root/'buy_ict.db', 'SELECT rfq_id,title,buyer,rfq_published_date,rfq_closing_date,requirements,location_of_work,working_arrangements,url,scraped_at,status FROM buy_ict_tenders')
    prior_ids = set(json.loads((out/'baseline_buyict_ids.json').read_text(encoding='utf-8')))
    selected = []
    for record in buyict:
        end = closing_time(record['rfq_closing_date'])
        if end and end > moment and str(record['status']).lower() == 'open' and record['rfq_id'].startswith('LH-'):
            record['new_since_pre_sweep_snapshot'] = record['rfq_id'] not in prior_ids
            record['parsed_closing_at'] = end.isoformat()
            try:
                record['requirements'] = json.loads(record['requirements'] or '{}')
            except ValueError:
                pass
            selected.append(record)
    selected.sort(key=lambda r: r['parsed_closing_at'])
    package['buyict_labour_hire'] = selected
    package['renewals'] = rows(root/'contract_api.db', '''
        SELECT cn_id,buyer,supplier,supplier_abn,description,current_value,current_end_date,
               page_url,source_url,retrieved_at,amendment_count FROM contracts
        WHERE current_end_date BETWEEN ? AND ? AND
             (lower(description) LIKE '%business analyst%' OR lower(description) LIKE '%cyber%'
              OR lower(description) LIKE '%data analyst%' OR lower(description) LIKE '%data engineer%')
        ORDER BY current_end_date,cn_id LIMIT 60
        ''', (str(today), str(today+timedelta(days=90))))
    package['hot_list'] = rows(root/'intelligence.db', 'SELECT * FROM hot_list')
    package['research_findings'] = rows(root/'research_sources.db', 'SELECT * FROM reviewed_findings')
    package['news'] = rows(root/'research_sources.db', 'SELECT * FROM buyict_news ORDER BY published DESC LIMIT 15')
    package['source_checks'] = rows(root/'research_sources.db', 'SELECT source_url,organisation,kind,checked_at_utc,status,response_sha256,error FROM source_pages')
    package['rap_partner_evidence'] = rap_partner_evidence()
    target=out/'newsletter_evidence.json'
    target.write_text(json.dumps(package,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(target),'contracts':package['contract_total'],'new_contracts':package['contract_total']-package['contract_total_before'],
                      'live_relevant_opportunities':len(live),'live_buyict_labour_hire':len(selected)}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=BASE/'reports/newsletter_trial_20261024')
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    prepare(args.out)
