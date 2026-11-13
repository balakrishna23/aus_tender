"""Build a compact RAP evidence register from human-reviewed, page-cited findings.

Reads procurement databases; writes only its own derived register and report.
No crawling, model calls, emails, source-database edits or automatic outreach.
"""
import argparse
import hashlib
import html
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[1]
PROJECT = Path(r'/opt/aus_tender')
DEFAULT_OUT = BASE/'reports/rap_priority_analysis_20261105'
LIMITS = (
    'Published commitments do not establish available budget, missed targets, supplier acceptance or an Illoura relationship.',
    'Candidate ABNs come from stored procurement records and existing unreviewed mappings. RAP-to-legal-entity scope is not confirmed.',
    'Contract end dates are research signals, not announced renewals. Extensions, replacement procurements and subcontracting access are unconfirmed.',
    'Within the published period does not establish that this is the latest edition. Expired plans are historical evidence only.',
    'Broad ICT and recruitment classifications include software, equipment and non-IT staffing. Neither category nor an upcoming end date proves fit for Illoura.',
)


def read_connection(path):
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def contract_context(project, abns, as_of):
    """Exact-ABN research associations; never claim these prove the RAP's entity scope."""
    end = as_of+timedelta(days=180)
    marks = ','.join('?' for _ in abns)
    with closing(read_connection(project/'databases/contract_api.db')) as db:
        total = db.execute(f'SELECT count(*) FROM contracts WHERE supplier_abn IN ({marks})', abns).fetchone()[0]
        active = db.execute(f'SELECT count(*) FROM contracts WHERE supplier_abn IN ({marks}) AND date(start_date)<=date(?) AND date(current_end_date)>=date(?)', [*abns,str(as_of),str(as_of)]).fetchone()[0]
    with closing(read_connection(project/'databases/intelligence.db')) as db:
        records = [dict(r) for r in db.execute(f'''SELECT cn_id,buyer,supplier,supplier_abn,description,
            current_end_date,current_value,category_family,page_url,api_url FROM renewals
            WHERE supplier_abn IN ({marks}) AND date(current_end_date) BETWEEN date(?) AND date(?)
            ORDER BY current_end_date,cn_id''', [*abns,str(as_of),str(end)])]
        build = db.execute('SELECT built_at FROM build_info LIMIT 1').fetchone()[0]
    # Rolled-up CN IDs represent contracts, not their individual amendment versions.
    records = list({r['cn_id']:r for r in records}.values())
    services = [r for r in records if r['category_family'] in ('ICT services','labour hire and recruitment')]
    for r in records:
        r['days_to_stored_end'] = (date.fromisoformat(r['current_end_date'][:10])-as_of).days
    def example_priority(record):
        description=record['description'] or ''
        specific=bool(re.search(r'cyber|cloud|data platform|data engineer|business analys|ICT labour|IT labour|digital strategy|integration service|professional service',description,re.I))
        return (not specific,record['current_end_date'],record['cn_id'])
    examples=sorted(records,key=example_priority)[:3]
    return {'as_of':str(as_of),'intelligence_built_at':build,'candidate_abns':abns,
            'entity_match_status':'Candidate association only; RAP entity scope not confirmed',
            'stored_contracts':total,'active_stored_contracts':active,
            'it_contracts_ending_180_days':len(records),'services_or_labour_hire_ending_180_days':len(services),
            'examples':examples}


def eligible_companies(data, as_of):
    return [c for c in data if c['period_start']<=str(as_of)<=c['period_end']]


def build(findings, documents, project, out, as_of):
    source = json.loads(findings.read_text(encoding='utf-8'))
    docs = {r['organisation']:r for r in json.loads(documents.read_text(encoding='utf-8'))}
    companies = []
    for original in source['companies']:
        c = dict(original)
        d = docs[c['organisation']]
        if d.get('path'):
            path = Path(d['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=d['sha256']:
                raise ValueError('Source document changed: '+str(path))
        for f in c['findings']:
            if not 1<=f['pdf_page']<=d['page_count']:
                raise ValueError('Invalid source page for '+c['organisation'])
        c['document'] = d
        c['reviewed_on'] = source['review_date']
        c['review_method'] = 'PDF text and rendered procurement table reviewed' if d.get('path') else 'Official PDF text reviewed; local visual verification unavailable'
        c['period_status'] = 'Within published period' if c['period_start']<=str(as_of)<=c['period_end'] else ('Expired published period' if c['period_end']<str(as_of) else 'Future published period')
        c['delivery_status'] = 'Commitment completion, current budget and supplier acceptance unverified'
        c['contract_context'] = contract_context(project,c['candidate_abns'],as_of)
        companies.append(c)
    result = {'generated_at_utc':datetime.now(timezone.utc).isoformat(),'as_of':str(as_of),
              'scope':source['scope'],'limitations':LIMITS,'companies':companies}
    out.mkdir(parents=True,exist_ok=True)
    (out/'register.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    db_path = project/'databases/rap_evidence.db'
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS company_reviews(
            organisation TEXT, reviewed_on TEXT, period_start TEXT, period_end TEXT,
            payload_json TEXT NOT NULL, PRIMARY KEY(organisation,reviewed_on));
            CREATE TABLE IF NOT EXISTS commitments(
            evidence_key TEXT PRIMARY KEY, organisation TEXT, reviewed_on TEXT, action TEXT,
            pdf_page INTEGER, summary TEXT, responsible_role TEXT, timeline TEXT,
            source_url TEXT, payload_json TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS commitments_fts USING fts5(
            organisation,action,summary,responsible_role,timeline,evidence_key UNINDEXED);''')
        for c in companies:
            db.execute('INSERT OR REPLACE INTO company_reviews VALUES (?,?,?,?,?)',
                       (c['organisation'],c['reviewed_on'],c['period_start'],c['period_end'],json.dumps(c,ensure_ascii=False)))
            for f in c['findings']:
                key = hashlib.sha256((c['organisation']+'|'+c['reviewed_on']+'|'+f['action']).encode()).hexdigest()
                db.execute('INSERT OR REPLACE INTO commitments VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (key,c['organisation'],c['reviewed_on'],f['action'],f['pdf_page'],f['summary'],f['responsible_role'],f['timeline'],c['document']['source_url'],json.dumps(f,ensure_ascii=False)))
                values=(c['organisation'],f['action'],f['summary'],f['responsible_role'],f['timeline'])
                if db.execute('SELECT 1 FROM commitments_fts WHERE evidence_key=?',(key,)).fetchone():
                    db.execute('UPDATE commitments_fts SET organisation=?,action=?,summary=?,responsible_role=?,timeline=? WHERE evidence_key=?',(*values,key))
                else:db.execute('INSERT INTO commitments_fts VALUES (?,?,?,?,?,?)',(*values,key))
        db.commit()
    render(result,out)
    print(json.dumps({'report':str(out/'index.html'),'database':str(db_path),
                      'companies':len(companies),'commitments':sum(len(c['findings']) for c in companies),
                      'within_published_period':len(eligible_companies(companies,as_of))}))
    return result


def render(result,out):
    esc=lambda value:html.escape(str(value),quote=True)
    cards=[]
    for n,c in enumerate(result['companies'],1):
        ctx=c['contract_context'];doc=c['document'];expired=c['period_status']=='Expired published period'
        evidence=[]
        for f in c['findings']:
            href=doc['source_url']+'#page='+str(f['pdf_page'])
            evidence.append('<tr><td><strong>'+esc(f['action'])+'</strong><p>'+esc(f['summary'])+'</p></td><td>'+esc(f['timeline'])+'</td><td>'+esc(f['responsible_role'])+'</td><td><a href="'+esc(href)+'">PDF '+str(f['pdf_page'])+'</a><br><small>Printed '+esc(f['printed_page'])+'</small></td></tr>')
        examples=[]
        for r in ctx['examples']:
            examples.append('<li><a href="'+esc(r['page_url'] or r['api_url'])+'">'+esc(r['cn_id'])+'</a> - '+esc(r['description'])+'<br><small>'+esc(r['buyer'])+' | stored end '+esc(r['current_end_date'])+' | '+esc(r['category_family'])+'</small></li>')
        route='<a href="'+esc(c['route']['url'])+'">'+esc(c['route']['label'])+'</a>' if c['route']['url'] else esc(c['route']['label'])
        local='<a href="'+esc(Path(doc['path']).as_uri()+'#page='+str(c['findings'][0]['pdf_page']))+'">Open existing local PDF</a>' if doc.get('path') else 'Official web PDF only; no additional PDF stored'
        cards.append('<article id="company-'+str(n)+'" data-period="'+('expired' if expired else 'current')+'"><div class="row"><h2>'+esc(c['display_name'])+'</h2><span class="badge '+('expired' if expired else '')+'">'+esc(c['period_status'])+'</span></div><p class="muted">'+esc(doc['period'])+' | '+esc(c['priority'])+' | Evidence checked '+esc(c['reviewed_on'])+'</p><p>'+esc(c['priority_reason'])+'</p><div class="next"><strong>Next action</strong><p>'+esc(c['next_action'])+'</p></div><p><strong>Supplier route:</strong> '+route+'</p><div class="table-wrap"><table><thead><tr><th>Published commitment</th><th>Timeline</th><th>Responsible role</th><th>Evidence</th></tr></thead><tbody>'+''.join(evidence)+'</tbody></table></div><details><summary>Contract research signals: '+str(ctx['services_or_labour_hire_ending_180_days'])+' services/staffing contracts ending within 180 days</summary><p>'+str(ctx['stored_contracts'])+' stored contracts; '+str(ctx['active_stored_contracts'])+' with started, unelapsed terms. '+str(ctx['it_contracts_ending_180_days'])+' ICT-category contracts end within 180 days, including equipment/general ICT.</p><p><strong>Candidate ABNs:</strong> '+esc(', '.join(c['candidate_abns']))+'. Brand/group-to-RAP entity scope remains unconfirmed.</p><ul>'+(''.join(examples) or '<li>No matching upcoming contract example in the current renewal view. This is not proof of no commercial activity.</li>')+'</ul><p class="muted">Stored end dates are research signals, not announced renewals or available subcontracting work. Snapshot '+esc(ctx['as_of'])+'.</p></details><p class="muted">'+local+' | '+esc(c['review_method'])+'. '+esc(c['delivery_status'])+'.</p></article>')
    eligible=len(eligible_companies(result['companies'],date.fromisoformat(result['as_of'])))
    page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Illoura RAP partner evidence register</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f6f4;color:#20352f;font:16px/1.55 Arial,sans-serif}header{background:#173e35;color:white;padding:38px max(22px,calc((100% - 1120px)/2));}h1{font-size:32px;line-height:1.2;margin:10px 0}h2{margin:0;font-size:23px}.eyebrow{font-size:12px;letter-spacing:2px;color:#c8dece}main{max-width:1164px;margin:auto;padding:24px 22px 48px}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0 0 24px}.stat{background:white;border:1px solid #d6e0da;padding:16px}.stat b{display:block;font-size:27px}.intro{max-width:1000px}.toolbar{display:flex;gap:12px;margin:22px 0;flex-wrap:wrap}input,select{font:inherit;padding:12px;border:1px solid #aebfb4;border-radius:5px;background:white}input{flex:1;min-width:180px}article{background:white;border:1px solid #d6e0da;border-radius:7px;padding:24px;margin:20px 0}.row{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}.muted,small{color:#53675e;font-size:14px}.badge{background:#e2f1e7;color:#195638;padding:5px 10px;border-radius:20px;font-size:13px}.badge.expired{background:#fff0ce;color:#795000}.next{border-left:4px solid #6b8e70;padding:12px 16px;background:#f0f5ef}.next p{margin:5px 0}.table-wrap{overflow:auto;margin:20px 0}table{width:100%;border-collapse:collapse;min-width:650px;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #dce4df;padding:12px}th{background:#edf3ee}td:first-child{width:44%}td p{margin:5px 0}a{color:#075b68;overflow-wrap:anywhere}summary{cursor:pointer;font-weight:bold}details{padding:14px;background:#f5f7f5}li{margin:9px 0}footer{margin:24px 0;color:#53675e;font-size:13px}@media(max-width:600px){h1{font-size:26px}header{padding:26px 20px}main{padding:20px 12px}article{padding:18px}.stats{gap:7px}.stat{padding:12px}.stat b{font-size:23px}.stat span{font-size:13px}select{width:100%}}@media print{.toolbar{display:none}article{break-inside:avoid}details{display:block}header{background:white;color:#173e35}}
    </style></head><body><header><div class="eyebrow">ILLOURA SERVICES / PARTNER RESEARCH</div><h1>RAP partner evidence register</h1><p>First ten companies | Evidence review 5 November 2026</p></header><main>'''
    page+='<div class="stats"><div class="stat"><b>'+str(len(cards))+'</b><span>companies reviewed</span></div><div class="stat"><b>'+str(sum(len(c['findings']) for c in result['companies']))+'</b><span>page-cited commitments</span></div><div class="stat"><b>'+str(eligible)+' / '+str(len(cards)-eligible)+'</b><span>within period / expired</span></div></div><div class="intro"><p>Start with DXC, EY, Deloitte and Data#3 for capability preparation. Qualify CGI, Cognizant and Fujitsu. Establish successor plans for Hays, Leidos and KPMG before treating historical targets as current.</p><p>This is a research shortlist, not a list of confirmed buyers. Exact page references, owners and dates are recorded below; recommended actions are Illoura research judgements.</p><details><summary>Coverage and important distinctions</summary><p>'+esc(result['scope'])+'</p><ul>'+''.join('<li>'+esc(s)+'</li>' for s in result['limitations'])+'</ul></details></div><div class="toolbar"><input id="search" type="search" aria-label="Search companies and evidence" placeholder="Search company, procurement, portal, role or date"><select id="period" aria-label="Filter plan period"><option value="all">All plan periods</option><option value="current">Within published period</option><option value="expired">Expired plans</option></select></div><p id="count" aria-live="polite">Showing 10 companies</p>'+''.join(cards)+'<footer>Contract research snapshot '+esc(result['as_of'])+'. No outreach, applications, supplier registrations or new newsletter sends were performed. <a href="register.json">Download structured register</a>.</footer></main><script>const articles=[...document.querySelectorAll("article")],search=document.querySelector("#search"),period=document.querySelector("#period");function filter(){let shown=0;for(const a of articles){const match=a.textContent.toLowerCase().includes(search.value.toLowerCase())&&(period.value==="all"||a.dataset.period===period.value);a.hidden=!match;if(match)shown++;}document.querySelector("#count").textContent="Showing "+shown+" companies";}search.addEventListener("input",filter);period.addEventListener("change",filter);</script></body></html>'
    (out/'index.html').write_text(page,encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--findings',type=Path,default=DEFAULT_OUT/'reviewed_findings.json')
    parser.add_argument('--documents',type=Path,default=DEFAULT_OUT/'documents.json')
    parser.add_argument('--project',type=Path,default=PROJECT)
    parser.add_argument('--out',type=Path,default=DEFAULT_OUT)
    parser.add_argument('--as-of',type=date.fromisoformat,default=datetime.now(ZoneInfo('Asia/Kolkata')).date())
    args=parser.parse_args()
    build(args.findings,args.documents,args.project,args.out,args.as_of)
