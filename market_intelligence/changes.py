"""Compare retained snapshots; a first import is a baseline, not a batch of new opportunities."""
from datetime import datetime,time,timezone

from sqlalchemy import text

from .reporting import write_report_files


def build_changes(engine,since,as_of):
    if since>as_of:raise ValueError('Comparison start follows as-of date')
    cutoff=datetime.combine(since,time.min,tzinfo=timezone.utc)
    events=[];baselines=[]
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        for row in conn.execute(text('''SELECT s.* FROM market.research_snapshots s
            JOIN market.current_research_snapshots c ON c.snapshot_id=s.id''')).mappings():
            previous=conn.execute(text('''SELECT id FROM market.research_snapshots WHERE source=:source AND observed_at<=:cutoff
                AND id<>:current ORDER BY observed_at DESC,imported_at DESC LIMIT 1'''),dict(source=row['source'],cutoff=cutoff,current=row['id'])).scalar()
            if not previous:
                baselines.append(dict(source=row['source'],status='no pre-period baseline',snapshot_id=str(row['id'])));continue
            changes=conn.execute(text('''WITH old AS (SELECT record_key,payload FROM market.research_records WHERE snapshot_id=:old),
                new AS (SELECT record_key,payload FROM market.research_records WHERE snapshot_id=:new)
                SELECT coalesce(n.record_key,o.record_key) AS record_key,
                CASE WHEN o.record_key IS NULL THEN 'added' WHEN n.record_key IS NULL THEN 'removed' ELSE 'changed' END AS event,
                coalesce(n.payload->>'organisation',o.payload->>'organisation',n.payload->>'agency',o.payload->>'agency') AS organisation
                FROM old o FULL JOIN new n USING(record_key) WHERE n.payload IS DISTINCT FROM o.payload'''),{'old':previous,'new':row['id']}).mappings()
            events.extend(dict(r,source=row['source']) for r in changes)
        procurement=conn.execute(text('SELECT snapshot_id FROM market.current_procurement_snapshot')).scalar()
        old=conn.execute(text('SELECT id FROM market.procurement_snapshots WHERE imported_at<=:cutoff AND id<>:current ORDER BY imported_at DESC LIMIT 1'),{'cutoff':cutoff,'current':procurement}).scalar()
        if not old:baselines.append(dict(source='federal_contracts',status='no pre-period baseline',snapshot_id=str(procurement)))
        else:
            rows=conn.execute(text('''SELECT n.cn_id AS record_key,n.buyer AS organisation,'contract_terms_changed' AS event
                FROM market.procurement_contracts n JOIN market.procurement_contracts o USING(cn_id)
                WHERE n.snapshot_id=:new AND o.snapshot_id=:old AND
                (n.current_end_date,n.current_value) IS DISTINCT FROM (o.current_end_date,o.current_value)'''),{'new':procurement,'old':old}).mappings()
            events.extend(dict(r,source='federal_contracts') for r in rows)
        panels=[dict(r) for r in conn.execute(text('''SELECT son_id AS record_key,'panel_evidence_changed' AS event,
            changed_at FROM market.panel_evidence_events WHERE changed_at>=:cutoff ORDER BY changed_at'''),{'cutoff':cutoff}).mappings()]
        events.extend(dict(r,source='panel_evidence') for r in panels)
    return dict(as_of=as_of,since=since,status='retained snapshot comparison',changes=events,baseline_gaps=baselines,
        limitations=['A first imported snapshot establishes a baseline; it is not labelled as newly published market activity.',
                     'Research sources compare current records with the most recent retained pre-period baseline.',
                     'Federal contract changes compare recorded current values/end dates only; they are not new retender announcements.',
                     'Removed source observations do not prove an organisation or procurement ceased.'])


def write_changes(report,directory):
    return write_report_files(report,directory,'whats_changed',report['changes'],['source','record_key','organisation','event'],'Changes in retained evidence')
