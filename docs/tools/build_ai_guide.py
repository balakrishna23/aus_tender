from pathlib import Path
from xml.sax.saxutils import escape
import json
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether,Flowable
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader
import pypdfium2 as pdfium
from PIL import Image,ImageOps,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output'/'pdf'/'Illoura_AI_Assistant_User_Guide.pdf'
OUT.parent.mkdir(parents=True,exist_ok=True)
TMP=ROOT/'tmp'/'pdfs'/'ai_guide_preview';TMP.mkdir(parents=True,exist_ok=True)
GREEN=colors.HexColor('#174A3C');INK=colors.HexColor('#17312D');GRAY=colors.HexColor('#526763')
PALE=colors.HexColor('#EAF1EC');GOLD=colors.HexColor('#FFF3DA');LINE=colors.HexColor('#D4DFD7')
W=A4[0]-88
styles=getSampleStyleSheet()
styles.add(ParagraphStyle(name='DiagramAI',fontName='Helvetica',fontSize=8.3,leading=11,textColor=INK))
for name,font,size,leading,color in [('Body','Helvetica',10.2,14.3,INK),('TitleAI','Helvetica-Bold',28,32,GREEN),
 ('HeadAI','Helvetica-Bold',20,24,GREEN),('SubAI','Helvetica-Bold',12,16,GREEN),
 ('SmallAI','Helvetica',8.6,12,GRAY),('CellAI','Helvetica',9.1,12.8,INK),('THAI','Helvetica-Bold',9.2,13,colors.white)]:
    styles.add(ParagraphStyle(name=name,fontName=font,fontSize=size,leading=leading,textColor=color,
        spaceAfter=7 if name in ('Body','SubAI') else 0,keepWithNext=name in ('HeadAI','SubAI')))
story=[]
def p(text,style='Body'):return Paragraph(text,styles[style])
def body(text):story.append(p(text))
def sub(text):story.extend([Spacer(1,9),p(text,'SubAI')])
def callout(title,text,color=PALE):
    t=Table([[p(title,'SubAI')],[p(text)]],colWidths=[W-24])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),color),('BOX',(0,0),(-1,-1),0.5,LINE),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,0),10),
        ('BOTTOMPADDING',(0,-1),(-1,-1),8)]))
    story.extend([Spacer(1,8),t,Spacer(1,10)])
def table(headers,rows,widths):
    data=[[p(escape(h),'THAI') for h in headers]]+[[p(c,'CellAI') for c in row] for row in rows]
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),GREEN),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,PALE]),('LINEBELOW',(0,0),(-1,-1),0.4,LINE),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.extend([t,Spacer(1,9)])
def page(kicker,title):
    if story:story.append(PageBreak())
    story.extend([p(kicker.upper(),'SmallAI'),Spacer(1,10),p(title,'HeadAI'),Spacer(1,15)])

story.extend([p('ILLOURA SERVICES  /  LOCAL PILOT 2.3','SmallAI'),Spacer(1,17),
              p('Using the Illoura<br/>AI assistant','TitleAI'),Spacer(1,12)])
body('A practical guide to asking questions, inspecting evidence and understanding the limits. Prepared 8 October 2026 (India time).')
callout('What the assistant is for','Research stored government procurement evidence, shortlist relevant work and prepare a sourced approach. Its answers do not establish current hiring, a warm introduction or candidate availability.')
sub('Start a search')
for text in [
 '<b>1. Open the pilot.</b> On the laptop hosting it, open <link href="http://127.0.0.1:8765" color="#146B51">http://127.0.0.1:8765</link>. Refresh the page to see the latest controls.',
 '<b>2. Ask one clear question.</b> Include the company or agency, work type, relative day window and exclusions you need.',
 '<b>3. Select the search controls.</b> Use hybrid retrieval for related meanings; use the official API option when checking selected contract details.',
 '<b>4. Click Ask assistant.</b> Inspect the displayed filters, query date, retrieval mode and evidence before acting.']:body(text)
sub('The search controls')
table(['Control','When to use it'],[
 ('Use the local model for unfamiliar wording','Keep enabled. Qwen helps interpret unfamiliar names or wording when rules need help. Clear requests can skip the model.'),
 ('Check up to 3 contracts against the official API','Enable for a contract shortlist you are reviewing. Requires internet and adds waiting time. Checks contract fields, not hiring.'),
 ('Include related meanings in search','Enable to combine keyword and semantic retrieval. Disable to use the earlier structured query and keyword ranking.'),
 ('Include imported Google Sheets context','Adds matching imported company or selected-contract rows. Use Sync Google Sheets to refresh the local Sheet snapshot.')],[W*0.39,W*0.61])
body('The optional <b>Search topic</b> field supplies a short meaning to look for, such as <b>cloud migration</b>. Otherwise a supported role in your question can provide the topic.')

page('02 / Ask useful questions','Ready-to-use prompts')
body('Use relative windows such as <b>within 90 days</b> or <b>between 30 and 90 days</b>. Exact company names, contract IDs and supplier ABNs help resolve identity.')
table(['Goal','Question to paste'],[
 ('Understand the data','What data do we have?'),
 ('Investigate a supplier','What contracts does DXC hold?'),
 ('Build a staffing shortlist','Which cyber contracts end within 90 days? Choose 3 for ICT labour hire, excluding equipment and assessments.'),
 ('Search related meanings','Show contracts related to cloud migration ending within 90 days above $100k, excluding equipment and assessments.'),
 ('Find current notices','Show open cyber opportunities at Defence.'),
 ('Check panels','Which current panels include Hays?'),
 ('Compare primes','Compare DXC and EY using renewals, panels and MMR evidence.'),
 ('Inspect a specific contract','Show CN4228169 and check it against the official API live.'),
 ('Research commitments','Show RAP evidence. Set Search topic to Indigenous supplier procurement.'),
 ('Read a Sheet row','Show Google Sheets row 12 from tab "Tracker" in workbook intelligence.')],[W*0.30,W*0.70])
callout('An alternative hybrid search','Question: <b>Show contracts ending within 90 days above $100k, excluding equipment and assessments.</b><br/>Search topic: <b>cloud migration</b><br/>Enable Include related meanings in search, then ask.')
body('A specific topic searches the eligible records for relevance. If you also specify a separate positive role in the question, its keyword constraint remains in SQL. When the topic is derived from a role, discovery can include related meanings alongside exact role matches.')
body('Ask complete questions each time. Follow-ups such as "What about them?" are unreliable because conversational memory is not connected.')

page('03 / Read the evidence','Understand an answer')
body('Start with the answer, then inspect its evidence cards. Open the human-readable notice for context. The JSON link is the official machine-readable source.')
table(['Field','What it means'],[
 ('CN ID, buyer, supplier and ABN','The contract identifier, purchasing agency and recorded supplier identity. An ABN identifies a legal entity; a name match is not verified group consolidation.'),
 ('Description and category family','The stored work description and our category grouping. A labour-hire category alone does not prove an individual contractor requirement.'),
 ('Current value and end date','Latest rolled-up stored contract fields. The value is not remaining spend or an available staffing budget. End date is not confirmed renewal intent.'),
 ('Days until end','Calculated from the displayed Canberra query date. It can differ from the date in India, especially near midnight.'),
 ('Staffing fit','A wording-based signal. Explicit role wording is stronger than a general services description, but availability and buyer demand remain unconfirmed.'),
 ('Contact safety','A stored routing classification. SAFE_GENERAL is not consent, a warm relationship or a guarantee that no related procurement is live.'),
 ('Retrieval method','Keyword, semantic, keyword + semantic, or exact role keyword explains why the result entered the shortlist.'),
 ('Cosine, BM25 and fusion scores','Relevance and ranking signals. A cosine of 0.6441 is not 64.41% factual accuracy or a probability of hiring.'),
 ('Official API check','Only an explicitly displayed check establishes that selected fields were fetched and compared. A source link alone is not verification.')],[W*0.34,W*0.66])
callout('Worked example: CN4228169','Stored evidence: Austrade / Peppertree Cloud Consulting; Azure Migration Project Services; AUD $684,899.20; end date 30 October 2026. Semantic search connects this description to cloud migration. It does not establish that the supplier needs contractors now.',GOLD)
body('A renewals view monitors approaching recorded end dates. Always distinguish <b>published facts</b>, <b>derived relevance signals</b> and <b>missing information</b>.')

page('04 / Strengths and measured results','Where it performs best')
table(['Strongest use','Why it helps'],[
 ('Exact procurement research','Date windows, values, contract IDs, ABNs and exclusions are handled by parameterized SQL and application calculations.'),
 ('Company and agency research','Stored contracts, renewal windows, panels and reviewed findings can be combined in a supported multi-part answer.'),
 ('Discovering differently worded work','Local embeddings can connect cloud migration with Azure Migration Project Services, while exact eligibility filters remain in place.'),
 ('Staffing investigation','Explicit staffing or role wording keeps priority over a semantically similar general service in a staffing shortlist.'),
 ('Evidence inspection','Clickable references, readable notice links, timestamps and optional official checks make selected claims reviewable.')],[W*0.35,W*0.65])
sub('What the checks demonstrate')
table(['Check','Observed result / interpretation'],[
 ('Assistant unit checks','83 passed. Covers query behaviour, constraints, ranking, index mismatch, Sheet snapshots and failed-read preservation.'),
 ('Question regression suite','90 of 90 passed. These cases were used during refinement; this is not an independent accuracy percentage.'),
 ('Semantic contrast examples','4 of 5 paraphrased positives found; 5 of 5 unrelated negatives rejected. A tiny synthetic demonstration, not a representative benchmark.'),
 ('Known semantic miss','Controlling privileged user access did not retrieve Identity and Access Management Engineer at the current relevance threshold.'),
 ('Interface checks','Desktop/mobile rendering, hybrid controls, exact Sheet row lookup and HTTP checks passed.')],[W*0.35,W*0.65])
body('Selected warm stored-data hybrid searches took about <b>0.2-0.5 seconds</b> locally. This is not a response-time guarantee. Model loading, unfamiliar wording, query size and official API requests can take longer.')
sub('Current semantic index coverage')
body('<b>5,840 unique chunks</b> cover 14,882 renewal records, 203 relevant current opportunity titles and 12 reviewed research records at the indexed build. Coverage is a snapshot and changes after refreshes.')
body('Qwen interprets supported requests; facts and arithmetic come from code and databases. All-MiniLM provides local embeddings. No paid model API is used. The model weights have not been trained on Illoura data.')

page('05 / Limits and Google Sheets','What it cannot establish yet')
table(['Limit','Practical consequence'],[
 ('Current hiring or subcontracting demand','A contract award, end date, panel membership or RAP policy is not proof of an active staffing requirement.'),
 ('Candidates and warm access','CV matching, availability, verified clearances, work rights and relationship registers are not connected.'),
 ('Incomplete or stale evidence','The scrapers are manually run. Missing records do not prove absence of work. Bad source dates and incomplete supplier identities can affect answers.'),
 ('Semantic scope and interpretation','Full historical contracts, full RAP PDFs and CVs are not indexed. Similarity can miss paraphrases or retrieve broad services. Review the source.'),
 ('Commercial or legal conclusions','Estimated rates are not candidate pay. Stored MMR counts do not identify every obligated contract or prove unmet obligations.'),
 ('Broader assistant features','General live web search, conversation memory, arbitrary calendar-date/location filters and remote/VPS access are not connected.')],[W*0.34,W*0.66])
sub('Does it answer questions from Google Sheets?')
body('<b>Yes, through an imported local snapshot.</b> The first read-only import covered 10 registered project spreadsheets, 26 tabs and 51,637 data rows. Click <b>Sync Google Sheets</b> after edits. The bot searches the imported cells and attaches workbook, tab, row link and last-read UTC.')
body('It retains the procurement databases separately. The context checkbox adds matching Sheet rows to company or selected-contract answers. An explicit Sheets question also works. The assistant does not edit or publish Sheet cells; sync is manual.')
callout('Example','Ask: <b>Show Google Sheets notes from tab "Tracker" in workbook intelligence.</b><br/>Or: <b>Show Google Sheets row 12 from tab "Tracker" in workbook intelligence.</b><br/>Read the sync timestamp before relying on a recent edit.',GOLD)
body('Sheet rows currently use literal full-text search, not embeddings. Arbitrary Sheet sums/date calculations and edit-history questions are not implemented. An owner name does not prove who added a note. Failed reads preserve and label the previous snapshot; old raw snapshots remain private.')
sub('Keep results dependable')
body('Check filters, the Canberra query date and Sheet sync time. Rebuild the semantic index after a data refresh or research edit. Missing embeddings trigger a labelled keyword fallback. Review evidence and drafts before acting; the pilot sends no messages.')

class Architecture(Flowable):
    def __init__(self):Flowable.__init__(self);self.width=W;self.height=570
    def draw(self):
        c=self.canv
        def box(x,y,w,h,title,text,fill=PALE):
            c.setFillColor(fill);c.setStrokeColor(LINE);c.roundRect(x,y,w,h,7,fill=1,stroke=1)
            content=p('<b>'+title+'</b><br/>'+text,'DiagramAI');cw,ch=content.wrap(w-16,h-12)
            assert ch<=h-12,(title,ch,h)
            content.drawOn(c,x+8,y+h-8-ch)
        def arrow(x1,y1,x2,y2):
            c.setStrokeColor(GREEN);c.setFillColor(GREEN);c.setLineWidth(1.1);c.line(x1,y1,x2,y2)
            import math
            angle=math.atan2(y2-y1,x2-x1);size=5
            path=c.beginPath();path.moveTo(x2,y2)
            path.lineTo(x2-size*math.cos(angle-.5),y2-size*math.sin(angle-.5))
            path.lineTo(x2-size*math.cos(angle+.5),y2-size*math.sin(angle+.5));path.close();c.drawPath(path,fill=1,stroke=0)
        gap=12;col=(W-2*gap)/3
        c.setFillColor(GRAY);c.setFont('Helvetica-Bold',8);c.drawString(0,557,'DATA PREPARATION / MANUAL REFRESH')
        box(0,502,col,45,'Procurement sources','AusTender, BuyICT, ACT, NSW and NIAA')
        box(col+gap,502,col,45,'Registered Google Sheets','10 project workbooks; human edits remain in Sheets')
        box(2*(col+gap),502,col,45,'Public company sources','Selected official pages and reviewed RAP findings')
        box(0,426,col,60,'Scrapers + build','Manual collectors retain sources and build derived intelligence')
        box(col+gap,426,col,60,'Read-only Sheets import','Sync button / CLI; GET requests; readonly credential scope')
        box(2*(col+gap),426,col,60,'Manual source review','Source, finding, locator, review date and proof limits')
        box(0,334,col,74,'Procurement SQLite','Source databases + intelligence.db: contracts, renewals, panels, opportunities, MMR, actions')
        box(col+gap,334,col,74,'Sheet snapshot SQLite','sheet_imports.db: formatted cells, versions, active tabs, row provenance + FTS index')
        box(2*(col+gap),334,col,74,'Private research register','Reviewed findings and source-check metadata. Full RAP PDFs and CVs not indexed.')
        for i in range(3):
            x=i*(col+gap)+col/2;arrow(x,502,x,486);arrow(x,426,x,408)
        left=(W-22)/2;right=left+22
        box(0,242,left,70,'Private semantic index','All-MiniLM CPU embeddings + SQLite vector cache. Incremental builder; relevance only.')
        box(right,242,left,70,'Rules + local Qwen planner','Ollama :11434; validated task/entity JSON; explicit constraints survive. No generated SQL/facts.')
        arrow(col/2,334,col/2,312)
        # Reviewed findings also feed the cache.
        c.setStrokeColor(GREEN);c.line(W-col/2,334,W-col/2,323);c.line(W-col/2,323,left/2,323);arrow(left/2,323,left/2,312)
        box(0,132,303,86,'Read-only query coordinator','Fresh SQL filters + Sheet FTS rows + keyword/semantic fusion. Staffing priorities and exact constraints. Missing-index keyword fallback. Evidence formatter.')
        box(325,132,W-325,86,'Optional official API check','Up to 3 selected contracts: compare fields, timestamp, differences and response hash. Not hiring proof.',GOLD)
        arrow(left/2,242,left/2,218);arrow(right+left/2,242,275,218)
        arrow(col+gap+col/2,334,col+gap+col/2,218)
        # Procurement facts bypass the embedding cache.
        c.setStrokeColor(GREEN);c.line(0,371,-11,371);c.line(-11,371,-11,175);arrow(-11,175,0,175)
        c.saveState();c.translate(-17,250);c.rotate(90);c.setFont('Helvetica',7.5);c.drawString(0,0,'fresh database facts');c.restoreState()
        arrow(303,175,325,175)
        box(0,29,W,67,'Browser interface - localhost :8765','Question + topic + search controls -> planner and queries. Answer returns with source links, filters, query date, Sheet row references, sync times and missing information.')
        arrow(140,132,140,96);arrow(415,132,415,96)
        c.setStrokeColor(GREEN);c.line(W,62,W+11,62);c.line(W+11,62,W+11,278);arrow(W+11,278,W,278)
        c.saveState();c.translate(W+18,180);c.rotate(90);c.setFont('Helvetica',7.5);c.drawString(0,0,'question');c.restoreState()
        c.setFont('Helvetica',8);c.setFillColor(GRAY);c.drawString(0,8,'Data and model files stay local. No autonomous outreach, Sheet writes or VPS deployment.')

page('06 / Full architecture','From sources to an answer')
body('The chat searches local evidence. Scraper refresh, Sheets sync, research review and embedding-index rebuild are separate operations; Google Sheets is imported with read-only access.')
story.append(Architecture())
body('The wider project can separately publish database views to Sheets and import Tracker edits. Those manual workflows are separate from the chat and its read-only Sheets import.')
story.append(p('Basis: local assistant 2.3 code, tests, hybrid and Sheets manifests checked on 8 October 2026. This guide describes the current pilot.','SmallAI'))

def furniture(canvas,doc):
    canvas.saveState();canvas.setStrokeColor(LINE);canvas.line(44,44,A4[0]-44,44)
    canvas.setFont('Helvetica',8);canvas.setFillColor(GRAY)
    canvas.drawString(44,29,'ILLOURA  |  AI assistant user guide  |  8 October 2026')
    canvas.drawRightString(A4[0]-44,29,str(doc.page));canvas.restoreState()

doc=SimpleDocTemplate(str(OUT),pagesize=A4,rightMargin=44,leftMargin=44,topMargin=44,bottomMargin=58,
                      title='Illoura AI Assistant - User Guide',author='Illoura Services')
doc.build(story,onFirstPage=furniture,onLaterPages=furniture)
reader=PdfReader(str(OUT));assert len(reader.pages)==6, len(reader.pages)
texts=[page.extract_text() for page in reader.pages]
assert all(len(text)>300 for text in texts)
assert 'Yes, through an imported local snapshot' in texts[4]
document=pdfium.PdfDocument(str(OUT));tiles=[]
for i in range(len(document)):
    image=document[i].render(scale=1.4).to_pil().convert('RGB')
    image.save(TMP/f'page-{i+1}.png')
    image.thumbnail((420,595));tile=Image.new('RGB',(440,625),'#DCE3DF')
    tile.paste(image,((440-image.width)//2,10));ImageDraw.Draw(tile).text((12,606),f'Page {i+1}',fill='black');tiles.append(tile)
sheet=Image.new('RGB',(440*3,625*2),'#DCE3DF')
for i,tile in enumerate(tiles):sheet.paste(tile,((i%3)*440,(i//3)*625))
sheet.save(TMP/'contact-sheet.png')
print(f'Created {OUT} ({len(reader.pages)} pages, {OUT.stat().st_size:,} bytes).')
print('Rendered previews:',TMP)
