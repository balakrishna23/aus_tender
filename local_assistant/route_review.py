"""Review sheet for crawled supplier contacts, and import of the rows a person approved.

  python route_review.py                     # exports/supplier_route_review_<date>.xlsx
  python route_review.py --import FILE.xlsx  # approved rows -> inputs/approachable_company_routes.json -> DB

Only company channels (functional mailboxes, Australian business lines) are proposed. Named individuals
are counted, never proposed. Nothing reaches approachable_routes until a person writes 'approve'.
"""
import argparse,json,re,sqlite3
from collections import Counter
from contextlib import closing
from datetime import date,datetime,timezone
from pathlib import Path
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Alignment,Font,PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
import assistant,contact_discovery,sync_approachable_routes

EXPORTS=Path(__file__).resolve().parent.parent/'exports'
COLUMNS=['Priority','Company','ABN','Why this supplier','Website','BuyICT profile','Proposed email','Email type',
         'Email source page','Proposed phone','Phone source page','Other company channels','Named individuals found',
         'Decision','Note','supplier_key']
# Order of preference for Illoura's pitch: partnering/supplier desks, then general enquiries, then offices, then recruitment.
EMAIL_RANK=[r'partner|supplier|diversity|procure|tender|alliance|^bd\b|business|sales',
            r'enquir|inquir|info|contact|hello|admin|office|reception|general|mail',
            r'canberra|^act\b',
            r'sydney|melbourne|perth|brisbane|adelaide|hobart|darwin|^(nsw|vic|qld|wa|sa|tas|nt)\b',
            r'recruit|talent|people|^hr\b|career|jobs']

def email_rank(candidate):
    local=candidate['value'].lower().split('@')[0]
    rank=next((i for i,p in enumerate(EMAIL_RANK) if re.search(p,local)),len(EMAIL_RANK))
    return (rank,contact_discovery.link_rank(candidate['source_url']),candidate['value'].lower())

def choose(candidates):
    """Best company email and phone among one supplier's candidates; None where there is none."""
    emails=[c for c in candidates if c['kind']=='email' and c['purpose']=='functional business mailbox']
    emails=[c for c in emails if email_rank(c)[0]<len(EMAIL_RANK)] or emails
    phones=[c for c in candidates if c['kind']=='phone' and c['purpose']=='Australian business line']
    seen=Counter(re.sub(r'\D','',c['value'])[-9:] for c in phones)
    phones.sort(key=lambda c:(contact_discovery.link_rank(c['source_url']),-seen[re.sub(r'\D','',c['value'])[-9:]],c['value']))
    email=min(emails,key=email_rank) if emails else None
    return email,(phones[0] if phones else None)

def priorities(connection):
    """MMR primes and suppliers with active ICT/labour-hire contracts first."""
    intel=(assistant.DB_DIR/'intelligence.db').resolve().as_uri()+'?mode=ro'
    api=(assistant.DB_DIR/'contract_api.db').resolve().as_uri()+'?mode=ro'
    connection.execute('ATTACH DATABASE ? AS intel',(intel,));connection.execute('ATTACH DATABASE ? AS api',(api,))
    try:
        mmr={r[0] for r in connection.execute('SELECT abn FROM intel.mmr_primes')}
        active=dict(connection.execute("""SELECT supplier_abn,COUNT(*) FROM api.contracts WHERE current_end_date>=date('now')
            AND category_family IN ('ICT services','labour hire and recruitment','ICT equipment and general') GROUP BY 1"""))
    finally:
        connection.execute('DETACH DATABASE intel');connection.execute('DETACH DATABASE api')
    return mmr,active

def export(path=None):
    path=Path(path or EXPORTS/f'supplier_route_review_{date.today().isoformat()}.xlsx')
    with closing(sqlite3.connect((assistant.DB_DIR/'company_contacts.db').resolve().as_uri(),uri=True)) as connection:
        connection.row_factory=sqlite3.Row
        mmr,active=priorities(connection)
        sellers={r['abn']:r for r in connection.execute("SELECT abn,source_url,marketplaces_json,identifiers_json FROM buyict_sellers WHERE abn!=''")}
        queue=connection.execute("""SELECT q.supplier_key,q.website_url,s.name,s.abn FROM contact_research_queue q
            JOIN suppliers s ON s.supplier_key=q.supplier_key
            WHERE q.status='candidates_found' AND q.supplier_key NOT IN (SELECT supplier_key FROM approachable_routes)""").fetchall()
        rows=[]
        for entry in queue:
            candidates=[dict(r) for r in connection.execute('SELECT * FROM contact_candidates WHERE supplier_key=?',(entry['supplier_key'],))]
            email,phone=choose(candidates)
            if not email and not phone:continue
            seller=sellers.get(entry['abn'])
            marketplaces=json.loads(seller['marketplaces_json']) if seller else []
            identifiers=json.loads(seller['identifiers_json']) if seller else []
            why=[]
            if entry['abn'] in mmr:why.append('MMR prime')
            if active.get(entry['abn']):why.append(f"{active[entry['abn']]} active ICT/labour-hire contracts")
            if 'DMP2: ICT Labour Hire' in marketplaces:why.append('DMP2 ICT Labour Hire seller')
            if 'Indigenous business' in identifiers:why.append('Indigenous business (BuyICT)')
            priority=1 if entry['abn'] in mmr else 2 if active.get(entry['abn']) else 3
            company=[c for c in candidates if c['purpose'] in ('functional business mailbox','Australian business line')]
            rows.append([(priority,-active.get(entry['abn'],0),entry['name'].casefold()),priority,entry['name'],entry['abn'],'; '.join(why),entry['website_url'],seller['source_url'] if seller else '',
                email['value'] if email else '',email['purpose'] if email else '',email['source_url'] if email else '',
                phone['value'] if phone else '',phone['source_url'] if phone else '',
                len({c['value'].lower() for c in company})-bool(email)-bool(phone),
                len({c['value'].lower() for c in candidates if c['purpose'].startswith('named individual')}),
                '','',entry['supplier_key']])
    rows=[row[1:] for row in sorted(rows,key=lambda row:row[0])]
    book=Workbook();sheet=book.active;sheet.title='Review'
    sheet.append(COLUMNS)
    for row in rows:sheet.append(row)
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1F4E78')
    widths=[8,34,13,40,32,22,32,24,40,16,40,10,10,11,30,18]
    for index,width in enumerate(widths):sheet.column_dimensions[chr(65+index)].width=width
    sheet.freeze_panes='C2';sheet.auto_filter.ref=sheet.dimensions
    choice=DataValidation(type='list',formula1='"approve,reject"',allow_blank=True)
    sheet.add_data_validation(choice);choice.add(f'N2:N{max(2,len(rows)+1)}')
    for line in sheet.iter_rows(min_row=2):
        for cell in line:cell.alignment=Alignment(vertical='top',wrap_text=cell.column in (4,15))
    notes=book.create_sheet('How to review')
    for line in ['One row per supplier: the best company email and phone found on its own website.',
                 'Open the source page and check the address is published there for business enquiries.',
                 'Write approve or reject in Decision. You may correct the proposed email or phone first.',
                 'Approved rows are imported with: python local_assistant/route_review.py --import <this file>',
                 'Websites come from the company\'s BuyICT seller profile (ABN-linked) or a reviewed source map.',
                 'Named individuals are counted but not proposed; add one by hand in Note only if their role fits.',
                 'An approved route is a published business channel. It does not imply a relationship or interest.']:
        notes.append([line])
    notes.column_dimensions['A'].width=110
    path.parent.mkdir(exist_ok=True);book.save(path)
    print(json.dumps({'review_file':str(path),'suppliers':len(rows),
                      'by_priority':dict(Counter(r[0] for r in rows))}),flush=True)
    return path

def import_approved(path):
    book=load_workbook(path,read_only=True)
    try:rows=list(book['Review'].iter_rows(values_only=True))
    finally:book.close()
    header=list(rows[0])
    col={name:header.index(name) for name in COLUMNS}
    source=sync_approachable_routes.SOURCE
    register=json.loads(source.read_text(encoding='utf-8'))
    existing={(k,r['preferred_value'].lower()) for r in register['records'] for k in r['supplier_keys']}
    with closing(sqlite3.connect((assistant.DB_DIR/'company_contacts.db').resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        checked={(r[0],r[1].lower()):r[2] for r in connection.execute('SELECT supplier_key,value,checked_at_utc FROM contact_candidates')}
    added=0
    for row in rows[1:]:
        if str(row[col['Decision']] or '').strip().lower()!='approve':continue
        key=row[col['supplier_key']];email=str(row[col['Proposed email']] or '').strip();phone=str(row[col['Proposed phone']] or '').strip()
        if not key or not (email or phone):continue
        preferred=('email',email,row[col['Email source page']]) if email else ('phone',phone,row[col['Phone source page']])
        if (key,preferred[1].lower())in existing:continue
        secondary=('phone',phone) if email and phone else ('','')
        note=str(row[col['Note']] or '').strip()
        register['records'].append({'supplier_keys':[key],'company':row[col['Company']],'person':'',
            'role':'Published company mailbox' if email else 'Published company phone line',
            'preferred_kind':preferred[0],'preferred_value':preferred[1],'secondary_kind':secondary[0],'secondary_value':secondary[1],
            'source_url':preferred[2],'checked_at_utc':checked.get((key,preferred[1].lower()),datetime.now(timezone.utc).isoformat()),
            'proof':f"Company website page publishes {preferred[1]}; website linked to ABN {row[col['ABN']]} via "
                    f"{row[col['BuyICT profile']] or 'reviewed source map'}. Approved by reviewer."+(' Note: '+note if note else ''),
            'limit':'Functional business channel found by automated crawl and approved by a person. '
                    'No named contact, relationship or partner interest is implied.'})
        existing.add((key,preferred[1].lower()));added+=1
    source.write_text(json.dumps(register,indent=1,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'approved_rows_added':added}),flush=True)
    sync_approachable_routes.sync()
    return added

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--import',dest='import_file',help='Reviewed .xlsx to import approved rows from')
    parser.add_argument('--out',help='Where to write the review sheet')
    args=parser.parse_args()
    import_approved(args.import_file) if args.import_file else export(args.out)
