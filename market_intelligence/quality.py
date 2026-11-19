"""Quality and evidence coverage, with organisation types and uncertainty visible."""
from sqlalchemy import text
from datetime import date,timedelta


def report(engine,as_of=None):
    as_of=as_of or date.today()
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        result={}
        result['publisher_records_by_kind']=[dict(r) for r in conn.execute(text('''SELECT kind,count(*) AS records,
            count(abn) AS with_valid_abn FROM market.organisations GROUP BY kind ORDER BY kind''')).mappings()]
        result['register_identity_conflicts']=conn.execute(text('SELECT count(*) FROM market.register_identity_conflicts')).scalar()
        result['shared_abn_groups']=conn.execute(text('''SELECT count(*) FROM(SELECT abn FROM market.organisations
            WHERE kind='body' AND abn IS NOT NULL GROUP BY abn HAVING count(*)>1) x''')).scalar()
        result['shared_abn_note']='Government bodies may share an ABN; these groups are not automatically merged.'
        resolved_units=conn.execute(text('SELECT count(*) FROM market.agency_unit_map')).scalar()
        result['directory_unit_links']={
            'resolved_to_body':resolved_units,
            'without_resolved_body':conn.execute(text("SELECT count(*) FROM market.organisations WHERE kind='unit'")).scalar()-resolved_units,
            'units_with_unresolved_direct_parent':conn.execute(text("SELECT count(*) FROM market.directory_edges WHERE child_kind='unit' AND parent_id IS NULL")).scalar()}
        result['current_buyer_proposals']=[dict(r) for r in conn.execute(text('''SELECT match_status,review_status,count(*) AS buyers
            FROM market.buyer_resolution GROUP BY match_status,review_status ORDER BY match_status,review_status''')).mappings()]
        result['procurement']={
            'stored_contracts':conn.execute(text('SELECT count(*) FROM market.current_contracts')).scalar(),
            'analytical_contracts':conn.execute(text('SELECT count(*) FROM market.current_analytical_contracts')).scalar(),
            'source_exported_at':conn.execute(text('SELECT p.exported_at FROM market.procurement_snapshots p JOIN market.current_procurement_snapshot c ON p.id=c.snapshot_id')).scalar()}
        result['recent_evidence_coverage']=dict(conn.execute(text('''SELECT count(*) AS relevant_awards,
            count(*) FILTER(WHERE r.match_status IN ('exact_abn','exact_abn_name','exact_name')
                AND r.review_status NOT IN ('rejected','needs_recheck') AND NOT r.register_context_stale) AS awards_with_unique_identity_proposal,
            count(*) FILTER(WHERE r.review_status='approved') AS awards_with_human_confirmed_identity,
            count(*) FILTER(WHERE EXISTS(SELECT 1 FROM market.procurement_panel_links l WHERE l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id)) AS awards_with_panel_link,
            sum(c.original_value) AS original_awarded_value,
            sum(c.original_value) FILTER(WHERE r.match_status IN ('exact_abn','exact_abn_name','exact_name')
                AND r.review_status NOT IN ('rejected','needs_recheck') AND NOT r.register_context_stale) AS original_value_with_identity_proposal
            FROM market.current_analytical_contracts c LEFT JOIN market.buyer_resolution r ON r.buyer_key=c.buyer_key
            WHERE c.category_family IN ('ICT services','labour hire and recruitment') AND c.first_published>:past AND c.first_published<=:asof'''),
            {'past':as_of-timedelta(days=365),'asof':as_of}).mappings().one())
        result['recent_evidence_coverage']['as_of']=as_of
        result['recent_evidence_coverage']['note']='Proposal coverage is not reviewed matching accuracy. Original award values are not annual expenditure.'
        result['privacy']={
            'email_patterns_in_register_payloads':conn.execute(text("SELECT count(*) FROM market.observations WHERE payload::text ~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]+'")).scalar(),
            'contact_fields_in_register_payloads':conn.execute(text("SELECT count(*) FROM market.observations WHERE payload ?| ARRAY['email','phone_number','first_name','last_name','contact']")).scalar()}
        result['current_sources']=[dict(r) for r in conn.execute(text('''SELECT s.source,s.effective_date,s.effective_date_basis,
            s.row_count,s.parser_revision,s.sha256 FROM market.current_snapshots c JOIN market.snapshots s ON s.id=c.snapshot_id ORDER BY s.source''')).mappings()]
    return result
