from pathlib import Path
from xml.sax.saxutils import escape
from datetime import datetime
import json, sqlite3, textwrap
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4, A3, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'output'/'pdf'; OUTDIR.mkdir(parents=True,exist_ok=True)
ICT_OUT=OUTDIR/'Illoura_ICT_Action_Pack_2026-10-13.pdf'
HEALTH_OUT=OUTDIR/'Healthcare_Action_Pack_2026-10-13.pdf'
CONTACT_OUT=OUTDIR/'Historical_Supplier_Contact_Directory_2026-10-13.pdf'
INTEL=ROOT/'databases'/'intelligence.db'; CONTACT_DB=ROOT/'databases'/'company_contacts.db'
GREEN=colors.HexColor('#164B3D'); DARK=colors.HexColor('#17312D'); GRAY=colors.HexColor('#5C6E69')
MINT=colors.HexColor('#E8F1EC'); GOLD=colors.HexColor('#FFF1CC'); BLUE=colors.HexColor('#E7F0F7')
RED=colors.HexColor('#FBE9E7'); LINE=colors.HexColor('#CAD8D1')
W=A4[0]-80
styles=getSampleStyleSheet()
for n,f,s,l,c,a in [('XTitle','Helvetica-Bold',27,31,GREEN,10),('XHead','Helvetica-Bold',19,23,GREEN,10),
 ('XSub','Helvetica-Bold',12,15,GREEN,6),('XBody','Helvetica',9.5,13.5,DARK,7),('XSmall','Helvetica',8,10.5,GRAY,3),
 ('XCard','Helvetica',8.8,12,DARK,2),('XCell','Helvetica',8.2,10.7,DARK,0),('XTH','Helvetica-Bold',8.2,10.7,colors.white,0)]:
    styles.add(ParagraphStyle(name=n,fontName=f,fontSize=s,leading=l,textColor=c,spaceAfter=a,keepWithNext=n in ('XHead','XSub')))

def p(text,style='XBody'): return Paragraph(text,styles[style])
def clean(v): return escape(str(v or ''))
def href(url,label): return f'<link href="{escape(url)}" color="#146B51"><u>{escape(label)}</u></link>'

with sqlite3.connect(INTEL) as db:
    db.row_factory=sqlite3.Row
    def fetch(ids):
        marks=','.join('?' for _ in ids)
        rows=db.execute(f'''SELECT source,source_id,title,buyer,category,closes_at,status_effective,url,retrieved_at
          FROM opportunities WHERE source_id IN ({marks})
          ORDER BY (closes_at IS NULL),CASE WHEN lower(source) LIKE '%plan%' THEN 1 ELSE 0 END''',ids).fetchall()
        found={}
        for row in rows: found.setdefault(row['source_id'],dict(row))
        missing=[i for i in ids if i not in found]
        if missing: raise ValueError('Missing opportunities: '+','.join(missing))
        return [found[i] for i in ids]
    ict=fetch(['LH-08069','PCS-04678','LH-08104','LH-07952','LH-08089','LH-07981','LH-08151','SR01513'])
    health=fetch(['WS5682184941','PCS-04551','PICH0014637','PICH0014452','PRN9254','Health/E26-149966',
                  'DHF26/01613-01','CDC/CE26-148','ISLHD_4893','prj_1490'])
    health_agencies=[dict(r) for r in db.execute('''SELECT organisation,name,email,phone,source_url,verified
      FROM agency_contacts WHERE usable_for_outreach=1 AND organisation IN
      ('Department of Health, Disability and Ageing','Department of Veterans'' Affairs')
      ORDER BY organisation,verified DESC LIMIT 8''')]
with sqlite3.connect(ROOT/'databases'/'contract_api.db') as db:
    db.row_factory=sqlite3.Row
    health_contracts=[dict(r) for r in db.execute('''SELECT cn_id,supplier,supplier_abn,description,current_value,
      current_end_date,page_url,source_url FROM contracts WHERE cn_id IN
      ('CN4276634','CN4276633','CN3780431','CN3779138','CN3876285','CN3777542')
      ORDER BY date(current_end_date),current_value DESC''')]
with sqlite3.connect(CONTACT_DB) as db:
    db.row_factory=sqlite3.Row
    suppliers=[dict(r) for r in db.execute('''SELECT supplier_key,name,abn,identity_basis,notice_count,
      latest_publication,location_json,address_completeness,inventory_source_url FROM suppliers ORDER BY name COLLATE NOCASE,supplier_key''')]
    routes={r['supplier_key']:dict(r) for r in db.execute('''SELECT * FROM approachable_routes
      ORDER BY supplier_key,added_at_utc''')}

ict_notes={
 'LH-08069':('Explicit demand for two cloud engineers.','Confirm panel eligibility and candidate availability immediately.'),
 'PCS-04678':('Cyber implementation work at a digital-health agency.','Assess delivery or partner fit and required certifications.'),
 'LH-08104':('Named data and research business-analyst requirement.','Match skills, clearance and location; prepare a concise candidate brief.'),
 'LH-07952':('Explicit lead security-engineer requirement.','Check clearance, location and mandatory criteria.'),
 'LH-08089':('Three Salesforce developers is a multi-person staffing signal.','Check certifications and panel route, then shortlist candidates.'),
 'LH-07981':('Explicit senior data-engineer demand.','Validate mandatory stack and healthcare-domain expectations.'),
 'LH-08151':('Two software engineers with a later submission window.','Capture mandatory criteria and assign a sourcing owner.'),
 'SR01513':('Resource augmentation is explicit in the title.','Check whether Illoura can bid directly or needs an approved supplier path.')}
health_notes={
 'WS5682184941':('Digital workflow for fitness-to-drive medical forms.','Assess clinical governance and integration requirements.'),
 'PCS-04551':('Data analysis for Medicare Urgent Care Clinics.','Check analytics capability and health-program experience.'),
 'PICH0014637':('Workforce profile and action plan.','Review workforce-consulting credentials and need for a health partner.'),
 'PICH0014452':('Review of Walk-in Centres.','Assess health evaluation, service-design and clinical SME needs.'),
 'PRN9254':('Refresh and expansion of a counsellor panel.','Check professional registrations and panel conditions.'),
 'Health/E26-149966':('Disability-support assessments for older Australians.','Assess clinical credentials and geographic coverage.'),
 'DHF26/01613-01':('Digital-health course for medical students.','Assess instructional design and clinical education expertise.'),
 'CDC/CE26-148':('Operation of a national disease registry.','Review privacy, clinical governance and data operations requirements.'),
 'ISLHD_4893':('Explicit emergency-department medical workforce demand.','Use a healthcare staffing route and verify registration/roster requirements.'),
 'prj_1490':('Case managers and care-needs assessors.','Check allied-health credentials, scale and geography.')}

def footer(title):
    def draw(c,doc):
        c.saveState();c.setStrokeColor(LINE);c.line(40,42,A4[0]-40,42);c.setFont('Helvetica',7.5);c.setFillColor(GRAY)
        c.drawString(40,27,f'ILLOURA | {title} | 13 October 2026');c.drawRightString(A4[0]-40,27,str(doc.page));c.restoreState()
    return draw
def callout(story,title,text,fill=GOLD):
    t=Table([[p(title,'XSub')],[p(text)]],colWidths=[W-16]);t.setStyle(TableStyle([
      ('BACKGROUND',(0,0),(-1,-1),fill),('BOX',(0,0),(-1,-1),.5,LINE),('LEFTPADDING',(0,0),(-1,-1),9),
      ('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,0),8),('BOTTOMPADDING',(0,-1),(-1,-1),7)]))
    story.extend([t,Spacer(1,8)])
def table(story,headers,rows,widths):
    data=[[p(clean(h),'XTH') for h in headers]]+[[p(str(x),'XCell') for x in row] for row in rows]
    t=Table(data,colWidths=widths,repeatRows=1);t.setStyle(TableStyle([
      ('BACKGROUND',(0,0),(-1,0),GREEN),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,MINT]),
      ('GRID',(0,0),(-1,-1),.35,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
      ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story.extend([t,Spacer(1,8)])
def card(story,row,why,action,label,fill):
    close=(row['closes_at'] or 'not captured - open notice').replace('T',' ').replace('+11:00',' AEDT')
    text=f'''<b>{clean(row['source_id'])} - {clean(row['title'])}</b><br/>{clean(row['buyer'])} | {clean(row['source'])} | closes {clean(close)}<br/>
      <b>Why:</b> {clean(why)}<br/><b>Action:</b> {clean(action)}<br/>{href(row['url'],'Official notice')} &nbsp; <font color="#5C6E69">captured {clean(row['retrieved_at'])} UTC</font>'''
    t=Table([[p(label,'XSub'),p(text,'XCard')]],colWidths=[74,W-74]);t.setStyle(TableStyle([
      ('BACKGROUND',(0,0),(0,0),fill),('BOX',(0,0),(-1,-1),.5,LINE),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story.append(KeepTogether([t,Spacer(1,6)]))
def section(story,kicker,title): story.extend([PageBreak(),p(kicker.upper(),'XSmall'),Spacer(1,6),p(title,'XHead')])

def make_ict():
    s=[p('ILLOURA SERVICES / ICT ACTIONS','XSmall'),Spacer(1,18),p('Illoura ICT<br/>action pack','XTitle'),
       p('A focused staffing, delivery and partnership shortlist from the refreshed 13 October 2026 procurement data.')]
    callout(s,'Use this pack','Open the cited notice before acting. A published role proves procurement demand; it does not prove panel access, candidate availability, margin or buyer interest in a new supplier.')
    s.extend([p('Today\'s order','XSub'),p('<b>1.</b> Triage submissions closing first. <b>2.</b> Confirm direct or partner route. <b>3.</b> Check candidate and clearance readiness. <b>4.</b> Prepare a short, evidence-based response.')])
    section(s,'01 / Opportunities','Immediate Illoura actions')
    for r in ict:
        why,action=ict_notes[r['source_id']];card(s,r,why,action,'DUAL' if r['source_id']=='LH-07981' else 'ILLOURA',BLUE if r['source_id']=='LH-07981' else MINT)
    section(s,'02 / Approachable suppliers','People and routes suited to ICT conversations')
    wanted={'ABN:90008615745','ABN:55648966456','ABN:17649341613','ABN:31160925413','ABN:75288172749','ABN:19001011427','ABN:69001002731'}
    rows=[]
    for key in wanted:
        r=routes.get(key)
        if not r:continue
        route=clean(r['preferred_value']); route=href(r['preferred_value'],'Official contact form') if r['preferred_kind']=='contact_form' else route
        rows.append((clean(r['company']),clean(r['person'] or r['role']),route,href(r['source_url'],'Evidence')))
    table(s,['Company','Approachable contact','Preferred route','Proof'],rows,[135,140,170,62])
    callout(s,'Who is excluded','Directors and unsupported “person of interest” names are excluded. Omni uses its official form because no suitable direct recruitment or partnership person was verified.',RED)
    section(s,'03 / Contact approach','What to say and what to verify')
    table(s,['Before contact','Check'],[
      ('Identity','Exact supplier name/ABN and official domain.'),('Role','Person is still employed and the route is published for business use.'),
      ('Reason','A current role, delivery need or company-published recruitment signal.'),('Capability','Illoura can credibly supply the skill, clearance and location.'),
      ('Message','Ask for routing or relevance; do not claim the buyer asked the supplier to recruit.')],[145,362])
    callout(s,'Example','Hi Caleb - Illoura supports ICT professionals in the government market. We are reviewing several current cloud, cyber and data requirements. Are you the right person to discuss candidate introductions or delivery partnerships, or should I speak with another account contact?',BLUE)
    s.extend([p('Evidence boundary','XSub'),p('Supplier contacts are public business routes. No message has been sent. The full historical supplier register is in the separate contact-directory PDF.')])
    doc=SimpleDocTemplate(str(ICT_OUT),pagesize=A4,rightMargin=40,leftMargin=40,topMargin=42,bottomMargin=54,title='Illoura ICT Action Pack',author='Illoura Services')
    doc.build(s,onFirstPage=footer('ICT action pack'),onLaterPages=footer('ICT action pack'))

def make_health():
    s=[p('ILLOURA SERVICES / HEALTHCARE ACTIONS','XSmall'),Spacer(1,18),p('Healthcare<br/>action pack','XTitle'),
       p('Healthcare-related procurement opportunities and workforce signals, kept separate from the Illoura ICT pipeline.')]
    callout(s,'Use this pack','Healthcare work may require professional registrations, clinical governance, safeguarding, specialist insurance or sector delivery experience. Verify these before any bid or introduction.')
    s.extend([p('Today\'s order','XSub'),p('<b>1.</b> Triage imminent notices. <b>2.</b> Decide direct delivery versus specialist partner. <b>3.</b> Check registrations and geography. <b>4.</b> Use a healthcare-specific contact route.')])
    section(s,'01 / Opportunities','Healthcare procurement actions')
    for r in health:
        why,action=health_notes[r['source_id']];card(s,r,why,action,'DUAL' if r['source_id'] in ('WS5682184941','PCS-04551') else 'HEALTH',BLUE if r['source_id'] in ('WS5682184941','PCS-04551') else GOLD)
    section(s,'02 / Existing suppliers','Healthcare contracts approaching end')
    rows=[]
    for r in health_contracts:
        rows.append((clean(r['cn_id']),clean(r['supplier']),clean(r['description']),f'AUD ${float(r["current_value"] or 0):,.0f}<br/>{clean(r["current_end_date"])}',href(r['page_url'],'Notice')+' / '+href(r['source_url'],'JSON')))
    table(s,['CN','Supplier','Service','Value / end','Proof'],rows,[52,110,155,86,104])
    callout(s,'Interpretation','An approaching contract end is a research signal only. It does not prove renewal, replacement, vacancy or supplier demand for candidates.',RED)
    section(s,'03 / Approachable companies','Healthcare contact routes')
    rows=[]
    for key in ['ABN:75619995265','ABN:57080629036']:
        r=routes[key]
        rows.append((clean(r['company']),clean(r['person'] or r['role']),clean(r['preferred_value']),clean(r['secondary_value']),href(r['source_url'],'Evidence')))
    table(s,['Company','Contact','Email / route','Phone','Proof'],rows,[130,120,155,75,55])
    callout(s,'Recommended first approach','Brittany Loveday is a published nursing recruitment contact at JPS. Optimal Health uses its official reception mailbox and phone; no unsupported individual is presented as the procurement contact.',BLUE)
    section(s,'04 / Bid and outreach checks','Decision gate')
    table(s,['Check','Question'],[
      ('Eligibility','Does Illoura or the proposed partner hold every required registration and insurance?'),
      ('Workforce','Are qualified people available for the location, roster and start date?'),
      ('Governance','Who owns clinical quality, privacy, safeguarding and incident management?'),
      ('Commercial path','Can Illoura bid directly, or is an approved panel/prime/clinical partner required?'),
      ('Evidence','Is the notice still open, and does every outreach claim have a source?')],[145,362])
    s.append(p('The full supplier contact directory is provided separately. Buyer notice contacts remain in the procurement database and are not mislabelled as supplier contacts.'))
    doc=SimpleDocTemplate(str(HEALTH_OUT),pagesize=A4,rightMargin=40,leftMargin=40,topMargin=42,bottomMargin=54,title='Healthcare Action Pack',author='Illoura Services')
    doc.build(s,onFirstPage=footer('Healthcare action pack'),onLaterPages=footer('Healthcare action pack'))

def fit(text,n):
    text=' '.join(str(text or '').split())
    return text if len(text)<=n else text[:max(1,n-1)]+'...'
def make_contacts():
    page=landscape(A3); pw,ph=page; margin=28
    c=canvas.Canvas(str(CONTACT_OUT),pagesize=page,pageCompression=1)
    c.setTitle('Historical Supplier Contact Directory - 13 October 2026');c.setAuthor('Illoura Services')
    def base(page_no,title='Historical supplier contact directory'):
        c.setFillColor(GREEN);c.setFont('Helvetica-Bold',16);c.drawString(margin,ph-34,title)
        c.setFont('Helvetica',6.8);c.setFillColor(GRAY);c.drawRightString(pw-margin,ph-31,'13 October 2026')
        c.setStrokeColor(LINE);c.line(margin,24,pw-margin,24);c.drawString(margin,11,'ILLOURA | all stored historical supplier identities | reviewed routes are evidence-linked')
        c.drawRightString(pw-margin,11,str(page_no))
    page_no=1;base(page_no,'Historical supplier contact directory')
    c.setFillColor(DARK);c.setFont('Helvetica',10)
    intro=(f'This directory lists all {len(suppliers):,} supplier identities currently stored from contract versions, roll-ups and panel members. '
           f'{len(routes):,} identities have an approachable route in the append-only reviewed layer. Existing contact records were preserved. '
           'Directors are not shown as preferred contacts. An official contact form is used only where no suitable direct email was verified.')
    y=ph-62
    for line in textwrap.wrap(intro,145):c.drawString(margin,y,line);y-=14
    c.setFillColor(GOLD);c.rect(margin,y-62,pw-2*margin,54,fill=1,stroke=0);c.setFillColor(DARK);c.setFont('Helvetica-Bold',10);c.drawString(margin+10,y-26,'Missing means missing')
    c.setFont('Helvetica',9);c.drawString(margin+10,y-43,'No verified route is left blank. The system does not guess domains or emails, reuse buyer contacts, or invent a contact form.')
    y-=88;c.setFont('Helvetica-Bold',11);c.setFillColor(GREEN);c.drawString(margin,y,'Approachable routes currently reviewed')
    y-=18
    for key,r in sorted(routes.items(),key=lambda kv:(kv[1]['company'].casefold(),kv[0])):
        if y<52:c.showPage();page_no+=1;base(page_no,'Reviewed approachable supplier routes');y=ph-60
        who=r['person'] or r['role']; route=r['preferred_value']; extra=(' | '+r['secondary_value']) if r['secondary_value'] else ''
        c.setFillColor(DARK);c.setFont('Helvetica-Bold',7.5);c.drawString(margin,y,fit(r['company'],45))
        c.setFont('Helvetica',7.2);c.drawString(margin+245,y,fit(who,42));c.drawString(margin+500,y,fit(route+extra,70))
        c.setFillColor(GREEN);c.drawString(pw-115,y,'Evidence');c.linkURL(r['source_url'],(pw-118,y-2,pw-margin,y+8),relative=0)
        y-=12
    c.showPage();page_no+=1
    # Alphabetical exhaustive register.
    widths=[27,270,70,125,240,175,228]; xs=[margin]
    for w in widths:xs.append(xs[-1]+w)
    row_h=10.45; rows_per=70
    def directory_header(page_no,start,end):
        base(page_no,f'All supplier identities - rows {start:,} to {end:,}')
        top=ph-52;c.setFillColor(GREEN);c.rect(margin,top-13,sum(widths),13,fill=1,stroke=0)
        c.setFillColor(colors.white);c.setFont('Helvetica-Bold',5.8)
        for i,h in enumerate(['#','Supplier identity','ABN','Published location','Preferred approach route','Contact / purpose','Evidence / status']):
            c.drawString(xs[i]+3,top-9,h)
        return top-13
    for start in range(0,len(suppliers),rows_per):
        chunk=suppliers[start:start+rows_per]; y=directory_header(page_no,start+1,start+len(chunk))
        for offset,supplier in enumerate(chunk):
            idx=start+offset+1;r=routes.get(supplier['supplier_key'])
            if offset%2==1:c.setFillColor(MINT);c.rect(margin,y-row_h,sum(widths),row_h,fill=1,stroke=0)
            loc=json.loads(supplier['location_json'] or '{}')
            location=', '.join(x for x in [loc.get('locality',''),loc.get('region',''),loc.get('country','')] if x)
            if r:
                preferred=r['preferred_value']; secondary=r['secondary_value'] or ''; contact=(r['person']+' - ' if r['person'] else '')+r['role']
                status='Reviewed '+r['preferred_kind'].replace('_',' ');source=r['source_url']
            else:
                preferred='';secondary='';contact='';status='No verified approach route';source=''
            values=[str(idx),supplier['name'],supplier['abn'] or 'No ABN captured',location,
                    preferred+((' | '+secondary) if secondary else ''),contact,status]
            c.setFont('Helvetica',5.7);c.setFillColor(DARK)
            for col,val in enumerate(values):
                c.saveState();clip=c.beginPath();clip.rect(xs[col]+1,y-row_h+1,widths[col]-2,row_h-1);c.clipPath(clip,stroke=0,fill=0)
                c.drawString(xs[col]+3,y-7.2,str(val));c.restoreState()
            if r:
                target=('mailto:'+r['preferred_value']) if r['preferred_kind']=='email' else r['preferred_value']
                if target.startswith(('http','mailto:')):c.linkURL(target,(xs[4],y-row_h,xs[5],y),relative=0)
                c.linkURL(source,(xs[6],y-row_h,xs[7],y),relative=0)
            c.setStrokeColor(LINE);c.line(margin,y-row_h,pw-margin,y-row_h);y-=row_h
        c.showPage();page_no+=1
    c.save()

make_ict();make_health();make_contacts()
for output,needle in [(ICT_OUT,'Immediate Illoura actions'),(HEALTH_OUT,'Healthcare procurement actions'),(CONTACT_OUT,'No verified approach route')]:
    reader=PdfReader(str(output));text='\n'.join((p.extract_text() or '') for p in reader.pages[:8])
    if output==CONTACT_OUT:text+='\n'+(reader.pages[-1].extract_text() or '')
    assert needle.casefold() in text.casefold(),(output,needle)
    assert not reader.is_encrypted
    print(json.dumps({'path':str(output),'pages':len(reader.pages),'bytes':output.stat().st_size}))
