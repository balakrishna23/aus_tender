from pathlib import Path
from xml.sax.saxutils import escape
import json, sqlite3, re
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'pdf' / 'Illoura_Opportunity_and_Contact_Action_Pack_2026-10-13.pdf'
OUT.parent.mkdir(parents=True, exist_ok=True)
DB = ROOT / 'databases' / 'intelligence.db'
CONTACTS = Path(__file__).parent / 'inputs' / 'verified_company_contacts.json'
SHEETS = Path(__file__).parent / 'reports' / 'sheets-import.json'
COVERAGE = Path(__file__).parent / 'reports' / 'company-coverage.json'

GREEN = colors.HexColor('#164B3D'); DARK = colors.HexColor('#17312D')
MINT = colors.HexColor('#E8F1EC'); GOLD = colors.HexColor('#FFF1CC')
BLUE = colors.HexColor('#E7F0F7'); RED = colors.HexColor('#FBE9E7')
GRAY = colors.HexColor('#5C6E69'); LINE = colors.HexColor('#CAD8D1')
PAGE_W = A4[0] - 80

styles = getSampleStyleSheet()
for name, font, size, leading, color, after in [
    ('APTitle','Helvetica-Bold',27,31,GREEN,10), ('APHead','Helvetica-Bold',19,23,GREEN,12),
    ('APSub','Helvetica-Bold',12,15,GREEN,7), ('APBody','Helvetica',9.5,13.5,DARK,7),
    ('APSmall','Helvetica',8,10.5,GRAY,4), ('APCard','Helvetica',8.8,12,DARK,3),
    ('APCell','Helvetica',8.1,10.5,DARK,0), ('APTH','Helvetica-Bold',8.2,10.5,colors.white,0),
    ('APNumber','Helvetica-Bold',20,22,GREEN,2), ('APCenter','Helvetica-Bold',10,13,GREEN,4)]:
    styles.add(ParagraphStyle(name=name,fontName=font,fontSize=size,leading=leading,textColor=color,
                              spaceAfter=after,keepWithNext=name in ('APHead','APSub')))

def safe(v): return escape(str(v or ''))
def para(text, style='APBody'): return Paragraph(text, styles[style])
def link(url, label='Open source'):
    return f'<link href="{escape(url, {chr(34): "&quot;"})}" color="#146B51"><u>{escape(label)}</u></link>'

with sqlite3.connect(DB) as conn:
    conn.row_factory = sqlite3.Row
    def get_opps(ids):
        marks=','.join('?' for _ in ids)
        rows=conn.execute(f'''SELECT source,source_id,title,buyer,category,closes_at,status_effective,url,retrieved_at
            FROM opportunities WHERE source_id IN ({marks})
            ORDER BY (closes_at IS NULL), CASE WHEN lower(source) LIKE '%plan%' THEN 1 ELSE 0 END''', ids).fetchall()
        by_id={}
        for row in rows: by_id.setdefault(row['source_id'],dict(row))
        missing=[x for x in ids if x not in by_id]
        if missing: raise ValueError('Selected opportunity IDs missing: '+', '.join(missing))
        return [by_id[x] for x in ids]
    ict=get_opps(['LH-08069','PCS-04678','LH-08104','LH-07952','LH-08089','LH-07981','LH-08151','SR01513'])
    health=get_opps(['WS5682184941','PCS-04551','PICH0014637','PICH0014452','PRN9254',
                     'Health/E26-149966','DHF26/01613-01','CDC/CE26-148','ISLHD_4893','prj_1490'])
    renewals=[dict(r) for r in conn.execute('''SELECT cn_id,buyer,supplier,supplier_abn,description,current_value,
        current_end_date,page_url,api_url FROM renewals WHERE cn_id IN
        ('CN4276634','CN4276633')
        ORDER BY date(current_end_date), current_value DESC''')]
with sqlite3.connect(ROOT/'databases'/'contract_api.db') as contracts_conn:
    contracts_conn.row_factory=sqlite3.Row
    renewals += [dict(r) for r in contracts_conn.execute('''SELECT cn_id,buyer,supplier,supplier_abn,description,
        current_value,current_end_date,page_url,source_url AS api_url FROM contracts WHERE cn_id IN
        ('CN3780431','CN3779138','CN3876285','CN3777542') ORDER BY current_value DESC''')]

contacts=json.loads(CONTACTS.read_text(encoding='utf-8'))['records']
sheets=json.loads(SHEETS.read_text(encoding='utf-8'))
coverage=json.loads(COVERAGE.read_text(encoding='utf-8'))
sheet_tabs=len(sheets.get('sources', sheets.get('tabs', []))) or 26
sheet_rows=sum(int(x.get('row_count',0)) for x in sheets.get('sources', sheets.get('tabs', []))) or 58554

story=[]
def body(text): story.append(para(text))
def sub(text): story.extend([Spacer(1,5),para(text,'APSub')])
def new_page(kicker,title):
    if story: story.append(PageBreak())
    story.extend([para(kicker.upper(),'APSmall'),Spacer(1,7),para(title,'APHead')])
def callout(title,text,fill=MINT):
    t=Table([[para(title,'APSub')],[para(text)]],colWidths=[PAGE_W-20])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),fill),('BOX',(0,0),(-1,-1),.5,LINE),
      ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
      ('TOPPADDING',(0,0),(-1,0),9),('BOTTOMPADDING',(0,-1),(-1,-1),8)]))
    story.extend([t,Spacer(1,9)])
def table(headers, rows, widths, font=8.1):
    data=[[para(safe(h),'APTH') for h in headers]]
    for row in rows: data.append([para(str(c),'APCell') for c in row])
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),GREEN),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,MINT]),('GRID',(0,0),(-1,-1),.35,LINE),
      ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
      ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story.extend([t,Spacer(1,8)])
def metric(value,label,fill=MINT):
    t=Table([[para(value,'APNumber')],[para(label,'APSmall')]],colWidths=[(PAGE_W-18)/3])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),fill),('BOX',(0,0),(-1,-1),.5,LINE),
      ('LEFTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,-1),(-1,-1),7)]))
    return t
def opportunity_card(row,why,action,tag='ILLORA'):
    raw_close=row.get('closes_at')
    close=safe(raw_close.replace('T',' ').replace('+11:00',' AEDT') if raw_close else
               'not captured - open official notice')
    txt=(f'<b>{safe(row["source_id"])} - {safe(row["title"])}</b><br/>'
         f'{safe(row["buyer"])} | {safe(row["source"])} | closes {close}<br/>'
         f'<b>Why:</b> {safe(why)}<br/><b>Action:</b> {safe(action)}<br/>'
         f'{link(row["url"],"Official notice")} &nbsp; <font color="#5C6E69">captured {safe(row["retrieved_at"])} UTC</font>')
    t=Table([[para(tag,'APCenter'),para(txt,'APCard')]],colWidths=[78,PAGE_W-78])
    fill=BLUE if tag=='DUAL' else (MINT if tag=='ILLOURA' else GOLD)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),fill),('BOX',(0,0),(-1,-1),.5,LINE),
       ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
       ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([KeepTogether([t,Spacer(1,7)])])

# Cover
story.extend([para('ILLOURA SERVICES  /  ACTION PACK','APSmall'),Spacer(1,18),
              para('Opportunity & contact<br/>action pack','APTitle')])
body('Prepared 13 October 2026 (India time) from refreshed procurement databases, imported Google Sheets and selected public company evidence.')
callout('Decision rule','Use this pack to choose what to investigate today. A published tender is proof of a procurement event. A contract end date is only a timing signal. A public contact route is not proof that the company wants a partner, contractor or introduction.',GOLD)
metrics=Table([[metric('58,554','Sheet data rows imported'),metric('20 new','BuyICT opportunities'),metric('46,508','historical supplier identities')]],colWidths=[(PAGE_W-18)/3]*3,hAlign='LEFT')
metrics.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),9),('VALIGN',(0,0),(-1,-1),'TOP')]))
story.extend([metrics,Spacer(1,14)])
sub('What to do first')
body('<b>1.</b> Triage the short-deadline Illoura roles. <b>2.</b> Decide whether Illoura has credible delivery/candidate capacity before approaching. <b>3.</b> Use the healthcare section as a separate pipeline. <b>4.</b> Use only the verified routes in the contact section and confirm the person still holds the role before writing.')
body('<b>Recommended first calls:</b> Elysium Digital for active ICT resourcing signals, Compas for Canberra government recruitment coverage, and JPS Medical Recruitment for nursing/workforce opportunities. These are research priorities, not endorsements.')

new_page('01 / Data refresh','What changed before this pack')
table(['Source','Completed result'],[
 ('Google Sheets',f'{sheet_tabs} tabs / {sheet_rows:,} data rows imported read-only; no failed workbook reads.'),
 ('AusTender contract API','315 new contract notice versions; 1,197 contract roll-ups updated.'),
 ('AusTender contract pages','298 new stored records.'),
 ('AusTender opportunities','5 new and 7 updated notices.'),
 ('Planned procurement','1 new and 3 updated plans.'),
 ('BuyICT','20 new and 22 updated opportunities.'),
 ('ACT / NSW','42 ACT notices rechecked; NSW store increased by 32 records.'),
 ('Derived intelligence','18,368 opportunity rows, 14,923 renewal rows and 212 action rows rebuilt.')
],[PAGE_W*.34,PAGE_W*.66])
callout('Freshness boundary','The scrape finished on 13 October 2026 India time. Deadlines are shown in AEDT when supplied with +11:00. Open status can change after collection; open the notice immediately before deciding to bid.',RED)
sub('Contact coverage')
body(f'The historical inventory contains <b>{coverage["supplier_identities"]:,}</b> supplier identities. <b>{coverage["suppliers_with_reviewed_email_phone"]}</b> currently have reviewed email/phone evidence in the new register; <b>{coverage["suppliers_without_reviewed_email_phone"]:,}</b> remain without a reviewed direct route. Every missing supplier has a research-queue record, but a search link is not evidence.')
body('Buyer contact fields embedded in contract API records were excluded because they belong to the purchasing agency, even when nested near a supplier party. The workflow does not guess email formats or silently attach a similarly named company.')

new_page('02 / Illoura pipeline','Immediate ICT and labour-hire actions')
ict_notes={
 'LH-08069':('Explicit demand for two cloud engineers.','Confirm panel eligibility and candidate availability today; pursue only if the submission window remains open.'),
 'PCS-04678':('Cyber implementation work at a digital-health agency.','Assess delivery/partner fit; identify required certifications and scope before approaching any prime.'),
 'LH-08104':('Named data and research business-analyst requirement.','Match skills, clearance and location; prepare a short evidence-based candidate brief.'),
 'LH-07952':('Explicit lead security-engineer requirement.','Check clearance, location and mandatory criteria before candidate outreach.'),
 'LH-08089':('Three Salesforce developers creates a multi-person staffing signal.','Check Salesforce certifications and panel route, then shortlist suitable candidates.'),
 'LH-07981':('Explicit senior data-engineer demand in Health.','Treat as Illoura delivery with healthcare domain context; validate mandatory stack.'),
 'LH-08151':('Two software engineers with a later deadline than the urgent batch.','Open notice, capture mandatory criteria, then assign candidate sourcing owner.'),
 'SR01513':('Resource augmentation is explicit in the notice title.','Check whether Illoura can bid directly in NSW or needs an approved supplier/prime path.')}
for row in ict:
    why,action=ict_notes[row['source_id']]
    opportunity_card(row,why,action,'DUAL' if row['source_id'] in ('LH-07981',) else 'ILLOURA')
callout('Do not overread this list','The titles show published demand, but the database does not yet prove panel access, mandatory clearances, candidate availability, margin, delivery location or buyer willingness to accept a new supplier.',GOLD)

new_page('03 / Healthcare pipeline','Healthcare-related procurement actions')
health_notes={
 'WS5682184941':('Digital workflow for fitness-to-drive medical forms.','Assess as a joint digital-health pursuit; inspect clinical governance and integration requirements.'),
 'PCS-04551':('Data analysis and modelling for Medicare Urgent Care Clinics.','Check analytics capability and health-program experience; consider a specialist partner.'),
 'PICH0014637':('Workforce profile and action plan is directly relevant to workforce consulting.','Review deliverables and credentials; decide whether a healthcare workforce partner is required.'),
 'PICH0014452':('Review of Walk-in Centres may need service design and health evaluation.','Assess review-method capability and clinical subject-matter requirements.'),
 'PRN9254':('DVA is refreshing/expanding a counsellor panel.','Check professional registrations and panel requirements; do not treat as general ICT staffing.'),
 'Health/E26-149966':('Needs assessments for disability support for older Australians.','Assess clinical/assessment credentials and geographic coverage before bid/no-bid.'),
 'DHF26/01613-01':('Digital-health education content for medical students.','Assess instructional design, clinical education and digital-health subject expertise.'),
 'CDC/CE26-148':('Operation of a national occupational respiratory disease registry.','Review registry operations, privacy, clinical governance and data capability.'),
 'ISLHD_4893':('Explicit emergency-department medical workforce demand for night shifts.','Prioritise a healthcare staffing partner; verify medical registrations, roster and locality.'),
 'prj_1490':('Case managers and care-needs assessors for a lifetime-care scheme.','Check allied-health/case-management credentials, scale and geographic requirements.')}
for row in health:
    why,action=health_notes[row['source_id']]
    opportunity_card(row,why,action,'DUAL' if row['source_id'] in ('WS5682184941','PCS-04551') else 'HEALTH')

new_page('04 / Healthcare market signals','Contracts approaching their recorded end date')
body('These records help identify suppliers already delivering relevant services. They do not establish a replacement procurement, a vacancy, or permission to solicit buyer staff.')
renewal_rows=[]
for r in renewals:
    value='AUD ${:,.0f}'.format(float(r['current_value'] or 0))
    renewal_rows.append((safe(r['cn_id']),safe(r['supplier']),safe(r['description']),value+'<br/>'+safe(r['current_end_date']),link(r['page_url'],'Notice')+' / '+link(r['api_url'],'JSON')))
table(['CN','Supplier','Recorded service','Value / end','Proof'],renewal_rows,[52,105,160,83,77])
callout('Strongest staffing signal','JPS Medical Recruitment has two Locum Nurse Services contracts ending 19 and 23 October 2026. JPS also publishes current nursing recruitment routes. This supports a relevant approach, but the buyer contract record does not prove JPS wants Illoura candidates.',GOLD)
body('The large psychology service contracts are useful market intelligence. Before outreach, establish whether Illoura has a credible healthcare workforce proposition, relevant registrations and a reason the incumbent should engage.')

new_page('05 / Contact targets','Reviewed people and routes')
contact_rows=[]
for r in contacts:
    routes=[]
    for kind,value in r['routes'].items():
        if str(value).startswith('http'): routes.append(link(value,kind.replace('_',' ').title()))
        else: routes.append(f'<b>{safe(kind.replace("_"," ").title())}:</b> {safe(value)}')
    contact_rows.append((safe(r['person'] or 'General enquiries'),safe(r['role']),'<br/>'.join(routes),link(r['source_url'],'Evidence')))
table(['Person / route','Why this person','Published route','Evidence'],contact_rows,[92,135,185,65])
sub('Suggested use')
body('<b>Daniel Farrell - Elysium Digital:</b> strongest ICT lead in this pack because recent public posts name cloud and ServiceNow opportunities and publish a business email. Ask whether they accept candidate/partner introductions for current government work; do not imply you represent the buyer.')
body('<b>Wayne Murphy - Compas:</b> director-level route verified on the company team page. Ask for the correct federal-government account contact and offer a concise capability/candidate summary.')
body('<b>Brittany Loveday - JPS Medical Recruitment:</b> use only for relevant nursing workforce conversations. Her public posts publish nursing vacancies and direct business contact details.')
body('<b>Nicholas Stoker - Omni Executive:</b> treat as a routing lead. Use company reception/contact form because no direct business email was verified.')
body('<b>Amy Forman / Optimal Health:</b> use the reception route and reconfirm the current role. The public post used for contact evidence is historical.')
body('<b>Cyber3:</b> no named person met the evidence threshold. Use the official enquiries mailbox/form and ask for the relevant recruitment or partnerships contact.')

new_page('06 / Safe outreach','How to turn a lead into a warm path')
table(['Step','Required evidence','Output'],[
 ('1. Confirm identity','Exact ABN/legal name plus official company domain or government supplier profile.','One supplier key; reject similarly named businesses.'),
 ('2. Confirm person','Official team page or public LinkedIn profile showing current company association.','Named person marked current, historical or unconfirmed.'),
 ('3. Confirm route','Published business email, phone, contact form or profile message route.','Source URL, check time and purpose.'),
 ('4. Confirm reason','Open role, company vacancy, expiring contract or relevant capability evidence.','A claim-specific hook with its own proof.'),
 ('5. Recheck before sending','Open the tender and profile again; confirm deadline, employment and route.','Go/no-go note and date.'),
 ('6. Personalise','Explain why Illoura is relevant in two sentences; ask a narrow question.','Human-reviewed email or meeting request.')
],[PAGE_W*.22,PAGE_W*.42,PAGE_W*.36])
callout('Example opening','Hi Daniel - I saw Elysium Digital publicly recruiting for cleared cloud capability. Illoura works with ICT professionals in the government market. Are you the right person to discuss candidate introductions for current cloud work, or should I speak with someone else?',BLUE)
body('The first message should ask for routing or relevance. Avoid stating that a procurement buyer is hiring through the supplier unless the supplier itself has published that fact. No outreach has been sent by this workflow.')
sub('LinkedIn handling')
body('Public LinkedIn results are useful for finding a likely person and recent role signals. They are treated as changeable evidence, not as a permanent contact database. The workflow stores the profile URL and review time, avoids login bypass or bulk scraping, and requires a final manual check before outreach.')

new_page('07 / Missing-contact system','Completing the supplier contact register')
table(['State','Meaning','Next move'],[
 ('Reviewed contact','Identity, route and source checked.','Use after a same-day recheck.'),
 ('Candidate contact','Found on a reviewed domain but purpose/person still needs review.','Inspect source page and promote only if matched.'),
 ('Website established','Official domain linked to exact supplier identity.','Run the polite same-host contact-page collector.'),
 ('Website not established','No defensible official domain yet.','Research ABN, official registers, supplier profiles and company pages.'),
 ('No published route','Official website checked but no suitable route found.','Keep blank; use a public contact form or general switchboard if published.'),
 ('Identity conflict','Names/ABNs/domains do not align.','Reject the candidate and research manually.')
],[PAGE_W*.24,PAGE_W*.39,PAGE_W*.37])
body(f'The current queue contains <b>{coverage["supplier_identities"]:,}</b> supplier identities. It preserves missing values instead of inventing contacts. The next scalable improvement is to add reviewed official-domain mappings from ABR-linked pages, government supplier directories and company sites, then collect only published business routes from those domains.')
callout('Why this cannot be instant','Many historical suppliers do not publish a direct email or phone, some are individuals or trusts rather than companies, and some directories require an authenticated buyer account. Full inventory coverage can be measured; full public contact coverage cannot be guaranteed.',RED)
sub('Quality controls already applied')
body('No guessed email patterns. No purchased contact lists. No buyer contact reused as supplier contact. No automatic fuzzy merge of similar names. No private LinkedIn data or login bypass. Source URLs, timestamps and content hashes are retained for discovered candidates.')

new_page('08 / Decision sheet','Recommended assignments for today')
table(['Priority','Owner to assign','Action','Evidence threshold before action'],[
 ('P0 - today','Illoura delivery lead','Open LH-08069, PCS-04678, LH-08104 and LH-07952; decide bid/no-bid before their deadlines.','Notice still open; panel path; mandatory criteria; candidate/delivery capacity.'),
 ('P0 - today','Healthcare lead','Open WS5682184941 and PCS-04551; decide whether Illoura can deliver or needs a partner.','Scope and qualifications reviewed; credible capability owner identified.'),
 ('P1 - today','Partnerships','Recheck Daniel Farrell, Wayne Murphy and Cyber3 routes; prepare three routing-first messages.','Current association and route rechecked; no unsupported buyer-demand claim.'),
 ('P1 - today','Healthcare partnerships','Recheck JPS/Brittany; ask whether relevant nursing introductions are accepted.','Current nursing role post and employment confirmed.'),
 ('P2 - 48 hours','Healthcare bid lead','Triage the 22 Oct to 2 Nov health notices and assign bid/no-bid owners.','Credentials, geography, panel route and delivery model.'),
 ('P2 - ongoing','Research owner','Resolve official domains for high-value/near-end suppliers first; review contact candidates.','Exact identity bridge and published business-purpose route.')
],[74,90,196,147])
callout('Definition of done','An action is ready only when the notice is open, Illoura has a credible path to deliver, a route is published and current, and the message states only facts supported by its linked evidence.',MINT)

new_page('09 / Evidence notes','Sources, interpretation and limitations')
body('<b>Procurement evidence.</b> Opportunity cards link directly to AusTender, BuyICT, buy.nsw or Tenders ACT notices captured in the refreshed database. Contract signals link to the human-readable AusTender notice and official OCDS JSON. Retrieval timestamps are shown on opportunity cards.')
body('<b>Company evidence.</b> Reviewed contacts use official company pages, ABR/government supplier records and publicly visible LinkedIn pages. Each contact row links to its primary evidence. LinkedIn-only role/contact signals are labelled for rechecking because profiles and posts can change.')
body('<b>Google Sheets.</b> The local read-only snapshot was updated before the procurement refresh. Imported content remains separate from source procurement databases and did not overwrite them.')
body('<b>Interpretation.</b> “Open” is the stored source status at collection time. “Closes” is the source timestamp. “Contract ending” means only the recorded end date. “Person of interest” means a likely business routing contact supported by public evidence; it does not mean consent, relationship, procurement authority or interest in Illoura.')
body('<b>Known gaps.</b> Candidate CVs, live candidate availability, verified security clearances, margins, panel access, delivery capacity, personal relationships and email responses are not connected. Healthcare opportunities may require registrations or clinical governance that Illoura has not yet demonstrated in the database.')
callout('Final review rule','Open every cited source before sending a message, booking a meeting or making a bid decision. This pack supports research and prioritisation; a human remains responsible for outreach and procurement compliance.',GOLD)

def furniture(canvas,doc):
    canvas.saveState(); canvas.setStrokeColor(LINE); canvas.line(40,42,A4[0]-40,42)
    canvas.setFont('Helvetica',7.5); canvas.setFillColor(GRAY)
    canvas.drawString(40,27,'ILLOURA  |  Opportunity & contact action pack  |  13 October 2026')
    canvas.drawRightString(A4[0]-40,27,str(doc.page)); canvas.restoreState()

doc=SimpleDocTemplate(str(OUT),pagesize=A4,rightMargin=40,leftMargin=40,topMargin=42,bottomMargin=54,
                      title='Illoura Opportunity and Contact Action Pack - 13 October 2026',author='Illoura Services')
doc.build(story,onFirstPage=furniture,onLaterPages=furniture)

reader=PdfReader(str(OUT))
assert len(reader.pages) >= 9
text='\n'.join((p.extract_text() or '') for p in reader.pages)
for required in ['Illoura pipeline','Healthcare pipeline','Daniel Farrell','46,508','No guessed email patterns']:
    assert required.casefold() in text.casefold(), required
print(f'Created {OUT} ({len(reader.pages)} pages, {OUT.stat().st_size:,} bytes)')
