"""Human-verified panel facts, conservative eligibility checks and date reminders."""
import calendar
from datetime import date, datetime, timezone
import json
from pathlib import Path

from sqlalchemy import text

from .resources import https_url
from .reporting import write_report_files

FIELDS={'current_expiry','max_possible_expiry','next_refresh_or_intake_date','open_for_new_suppliers',
        'how_to_join','extension_options','requirements'}
DATE_FIELDS={'current_expiry','max_possible_expiry','next_refresh_or_intake_date'}
PROFILE_KEYS={'abn','supply_nation_status','indigenous_ownership_percent','oric_registered','labour_hire_licences',
              'insurances','disp_status','security_clearances_held','panels_joined','panels_applied','categories_sold'}


def validate_facts(facts):
    if not isinstance(facts,dict) or set(facts)-FIELDS:raise ValueError('Unknown panel fact fields')
    for field in DATE_FIELDS:
        if facts.get(field) is not None:date.fromisoformat(facts[field])
    if facts.get('open_for_new_suppliers') not in (None,'always','scheduled_refresh','closed'):
        raise ValueError('Unexpected panel intake status')
    if facts.get('max_possible_expiry') and facts.get('current_expiry') and facts['max_possible_expiry']<facts['current_expiry']:
        raise ValueError('Maximum expiry precedes current expiry')
    requirements=facts.get('requirements')
    if requirements is not None:
        if not isinstance(requirements,list):raise ValueError('Requirements must be a list; null means unknown')
        for r in requirements:
            if set(r)!={'profile_key','accepted_values','label'} or r['profile_key'] not in PROFILE_KEYS:
                raise ValueError('Unknown profile requirement')
            if not isinstance(r['accepted_values'],list) or not r['accepted_values'] or not all(isinstance(v,str) and v.strip() for v in r['accepted_values']):
                raise ValueError('Requirements need explicit accepted profile values')
    return facts


def record_panel_evidence(engine,path,reviewer):
    if not reviewer or not reviewer.strip():raise ValueError('Named reviewer required')
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(data)!={'son_id','facts','source_url','verified_at','content_sha256'}:
        raise ValueError('Panel evidence fields changed')
    validate_facts(data['facts']);https_url(data['source_url'])
    stamp=datetime.fromisoformat(data['verified_at'])
    if stamp.tzinfo is None or stamp>datetime.now(timezone.utc):raise ValueError('Verification time needs timezone and cannot be future')
    if len(data['content_sha256'])!=64 or any(c not in '0123456789abcdef' for c in data['content_sha256']):
        raise ValueError('Evidence document SHA-256 required')
    with engine.begin() as conn:
        panel=conn.execute(text('SELECT son_id FROM market.panel_watchlist WHERE son_id=:id FOR UPDATE'),{'id':data['son_id']}).first()
        if not panel:raise ValueError('Unknown panel; review the candidate list first')
        old=conn.execute(text('SELECT * FROM market.panel_evidence WHERE son_id=:id'),{'id':data['son_id']}).mappings().first()
        if old and stamp<old['verified_at']:raise ValueError('Evidence older than current verified panel facts')
        new=dict(data,verified_by=reviewer)
        conn.execute(text('''INSERT INTO market.panel_evidence_events(son_id,old_evidence,new_evidence,changed_by)
            VALUES(:id,CAST(:old AS jsonb),CAST(:new AS jsonb),:by)'''),
            dict(id=data['son_id'],old=json.dumps(dict(old),default=str) if old else None,new=json.dumps(new),by=reviewer))
        conn.execute(text('''INSERT INTO market.panel_evidence(son_id,facts,source_url,verified_at,verified_by,content_sha256)
            VALUES(:son_id,CAST(:facts AS jsonb),:source_url,:verified_at,:verified_by,:content_sha256)
            ON CONFLICT(son_id) DO UPDATE SET facts=EXCLUDED.facts,source_url=EXCLUDED.source_url,
            verified_at=EXCLUDED.verified_at,verified_by=EXCLUDED.verified_by,content_sha256=EXCLUDED.content_sha256,recorded_at=now()'''),
            dict(new,facts=json.dumps(data['facts'])))
    return dict(son_id=data['son_id'],status='recorded',reviewer=reviewer)


def eligibility(facts,profile):
    requirements=facts.get('requirements')
    if requirements is None:return dict(status='unknown',gaps=[],unknown=['Panel requirements not verified'])
    gaps=[];unknown=[]
    for r in requirements:
        value=profile.get(r['profile_key'])
        if value is None or not str(value).strip():unknown.append(r['label'])
        elif value.casefold().strip() not in {v.casefold().strip() for v in r['accepted_values']}:gaps.append(r['label'])
    return dict(status='blocked' if gaps else 'unknown' if unknown else 'requirements_met',gaps=gaps,unknown=unknown)


def months_before(value,months):
    ordinal=value.year*12+value.month-1-months
    year,month=divmod(ordinal,12);month+=1
    return date(year,month,min(value.day,calendar.monthrange(year,month)[1]))


def calendar_items(panel,as_of):
    facts=panel.get('facts') or {};events=[]
    # Publisher notice dates remain visible but never establish an intake or application deadline.
    dates=[('notice_expiry',panel.get('current_expiry'),'published_notice; intake unknown')]
    dates += [(field,date.fromisoformat(facts[field]),'human_verified') for field in DATE_FIELDS if facts.get(field)]
    for kind,day,basis in dates:
        if day is None:continue
        day=date.fromisoformat(day) if isinstance(day,str) else day
        if day>=as_of:
            events.append(dict(son_id=panel['son_id'],panel_name=panel['panel_name'],event=kind,date=day,
                               basis=basis,days_away=(day-as_of).days,source_url=panel.get('evidence_url') or panel.get('source_url')))
        if kind in ('current_expiry','max_possible_expiry'):
            for months in (12,6,3):
                reminder=months_before(day,months)
                if reminder>=as_of:
                    events.append(dict(son_id=panel['son_id'],panel_name=panel['panel_name'],event=f'{months}_month_{kind}_reminder',
                                       date=reminder,basis=basis,days_away=(reminder-as_of).days,
                                       source_url=panel.get('evidence_url')))
    return events


def build_panel_access(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        rows=[dict(r) for r in conn.execute(text('''SELECT p.*,e.facts,e.source_url AS evidence_url,e.verified_at,e.verified_by
            FROM market.panel_watchlist p LEFT JOIN market.panel_evidence e USING(son_id) ORDER BY p.spend_through_panel_12m DESC NULLS LAST,p.son_id''')).mappings()]
        profile={r.key:r.value for r in conn.execute(text('SELECT key,value FROM market.illoura_profile'))}
    events=[]
    for panel in rows:
        panel['eligibility']=eligibility(panel['facts'] or {},profile)
        panel['eligibility_status']=panel['eligibility']['status']
        panel['needs_manual_check']=not panel['verified_at'] or (as_of-panel['verified_at'].date()).days>30
        panel['lane']='healthcare' if any(c in ('nursing_agency','healthcare_staffing') for c in panel['categories']) else 'federal' if panel['jurisdiction']=='Commonwealth' else 'state'
        panel['why_now']='Verify panel access and requirements' if panel['needs_manual_check'] else 'Check verified intake and expiry dates'
        events.extend(calendar_items(panel,as_of))
    events.sort(key=lambda r:(r['date'],r['son_id'],r['event']))
    return dict(as_of=as_of,status='internal research; verified facts distinguished from notice dates',panels=rows,calendar=events,
                unknown_profile_keys=[k for k,v in profile.items() if v is None],
                limitations=['A published notice expiry is not an intake date or application deadline.',
                             'Requirements are assessed only against explicit reviewed facts and entered profile values; missing values remain unknown.',
                             'Requirements met does not establish membership, invitation access or acceptance.',
                             'Reminders are calculated at 12, 6 and 3 calendar months before verified expiry dates; no messages are sent.',
                             'Verification older than 30 days requires rechecking.'])


def write_panel_access(report,directory):
    write_report_files(report,directory,'panel_access',report['panels'],
                       ['son_id','panel_name','lane','illoura_status','eligibility_status','needs_manual_check','why_now'],'Panel access and verification')
    return write_report_files(report,directory,'panel_calendar',report['calendar'],
                              ['date','panel_name','event','basis','source_url'],'Panel calendar')
