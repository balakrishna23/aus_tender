"""Supplier and healthcare research from retained official registers, without contact enrichment."""
from datetime import timedelta
import re

from sqlalchemy import text

from .forward import TAGS
from .reporting import write_report_files


def build_targets(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        suppliers=[dict(r) for r in conn.execute(text('''SELECT supplier_abn,count(*) AS contracts,count(DISTINCT buyer_key) AS buyers,
            sum(original_value) AS original_awarded_value,min(first_published) AS earliest_award,max(first_published) AS latest_award,
            array_agg(DISTINCT payload->>'category_tag') AS categories
            FROM market.current_analytical_contracts WHERE supplier_abn IS NOT NULL AND first_published>:past AND first_published<=:asof
              AND payload->>'category_tag'=ANY(:tags) GROUP BY supplier_abn ORDER BY sum(original_value) DESC NULLS LAST,supplier_abn'''),
            {'past':as_of-timedelta(days=1095),'asof':as_of,'tags':TAGS}).mappings()]
        entities=[dict(r) for r in conn.execute(text('''SELECT e.*,s.source_url,s.licence,s.retrieved_at FROM market.current_register_entities e
            JOIN market.register_snapshots s ON s.id=e.snapshot_id WHERE NOT e.is_individual''')).mappings()]
    by_abn={};health=[];indigenous=[]
    for entity in entities:
        if entity['abn']:by_abn.setdefault(entity['abn'],[]).append(entity)
        a=entity['attributes']
        healthcare=entity['source']=='acnc' and a.get('advancing_health') or entity['source']=='oric' and bool(re.search(r'health|medical|hospital',a.get('industry') or '',re.I))
        row=dict(organisation=entity['name'],abn=entity['abn'],record_id=entity['record_id'],source=entity['source'],
            status=entity['status'],state=entity['state'],postcode=entity['postcode'],size=a.get('size'),
            source_url=entity['source_url'],licence=entity['licence'],retrieved_at=entity['retrieved_at'],snapshot_id=str(entity['snapshot_id']),
            target_roles='unknown; workforce needs require evidence',eligibility='unknown')
        if healthcare:
            health.append(dict(row,lane='healthcare',why_now='Published health purpose or industry; confirm buying and workforce need',
                               health_basis='ACNC advancing_health purpose' if entity['source']=='acnc' else 'ORIC industry text',
                               acchs_status='not established',staffing_need='unknown',star_rating=None))
        if entity['source']=='oric':
            indigenous.append(dict(row,lane='private',why_now='Registered Indigenous corporation; verify relevance and current trading status',
                                   supply_nation_status='unknown',industry=a.get('industry')))
    profiles=[]
    five_years=as_of.replace(year=as_of.year-5,day=min(as_of.day,28)) if as_of.month==2 and as_of.day==29 else as_of.replace(year=as_of.year-5)
    for supplier in suppliers:
        records=by_abn.get(supplier['supplier_abn'],[])
        legal=next((r for r in records if r['source']=='abn'),None)
        company=next((r for r in records if r['source']=='asic_companies'),None)
        # No fallback to contract labels: they can include individual names.
        name=legal['name'] if legal else company['name'] if company else None
        registration=company['registered_on'] if company else None
        profiles.append(dict(supplier,organisation=name,registered_on=registration,is_young=registration>=five_years if registration else None,
            is_small=None,is_indigenous_oric=any(r['source']=='oric' for r in records),
            lane='private',why_now='Supplier serving our categories; investigate partnership or subcontracting fit',target_roles=supplier['categories'],
            source_snapshots=sorted({str(r['snapshot_id']) for r in records}),
            qualification='unassessed',supply_nation_status='unknown'))
    partners=[r for r in profiles if r['is_young'] is True or r['is_indigenous_oric']]
    return dict(as_of=as_of,status='draft research; no workforce need or eligibility inferred',supplier_profiles=profiles,
        partner_candidates=partners,indigenous_corporations=indigenous,healthcare_accounts=health,
        summary=dict(suppliers=len(profiles),partner_candidates=len(partners),healthcare_source_records=len(health),indigenous_corporations=len(indigenous)),
        limitations=['Supplier profiles cover original awards in our tagged categories over 36 months, not payments or company turnover.',
                     'Young means ASIC registration within five years; small-company size is unknown and is never inferred from few government awards.',
                     'Healthcare entries are source records that may overlap by ABN; a health purpose does not prove staffing need or buyer status.',
                     'ORIC registration is not Supply Nation certification and a health industry description does not establish ACCHS status.',
                     'Aged-care ratings, NDIS registration, hospitals, PHNs and workforce shortages are not supplied by these registers.',
                     'Supplier labels are shown only from organisation register records; no individual names or contact enrichment are used.'])


def write_targets(report,directory):
    for name,key,title in [('supplier_profiles','supplier_profiles','Supplier profiles'),('partner_candidates','partner_candidates','Partner research candidates'),
                           ('healthcare_accounts','healthcare_accounts','Healthcare research directory'),('indigenous_corporations','indigenous_corporations','ORIC corporation research')]:
        fields=['organisation','supplier_abn','contracts','buyers','original_awarded_value','is_young','is_indigenous_oric','why_now'] if key in ['supplier_profiles','partner_candidates'] else ['organisation','abn','source','status','state','postcode','why_now']
        subset={k:v for k,v in report.items() if k not in ['supplier_profiles','partner_candidates','healthcare_accounts','indigenous_corporations']}
        subset[key]=report[key]
        write_report_files(subset,directory,name,report[key],fields,title)
    return report['summary']
