"""Show measured coverage and missing human inputs without declaring the full spec complete."""
from datetime import timedelta
from sqlalchemy import text


def readiness(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        matches=[dict(r) for r in conn.execute(text('SELECT match_status,review_status,count(*) AS identities FROM market.buyer_resolution GROUP BY 1,2')).mappings()]
        profile=[r[0] for r in conn.execute(text('SELECT key FROM market.illoura_profile WHERE value IS NULL ORDER BY key'))]
        panels=conn.execute(text('SELECT count(*) AS candidates,count(*) FILTER(WHERE marked_relevant IS TRUE) AS marked,count(*) FILTER(WHERE last_verified_at IS NOT NULL) AS legacy_verified FROM market.panel_watchlist')).mappings().one()
        evidence=conn.execute(text('SELECT count(*) FROM market.panel_evidence')).scalar()
        inputs={r.source:r.rows for r in conn.execute(text('''SELECT s.source,s.row_count AS rows FROM market.current_research_snapshots c
            JOIN market.research_snapshots s ON s.id=c.snapshot_id'''))}
        # Resolve identity once per buyer, rather than joining the resolution view to
        # every contract. Exact sums and approval predicates are unchanged.
        coverage=conn.execute(text('''WITH totals AS MATERIALIZED (
            SELECT buyer_key,sum(original_value) AS original_value
            FROM market.current_analytical_contracts
            WHERE first_published>:past AND first_published<=:asof GROUP BY buyer_key)
            SELECT sum(c.original_value) AS value,
            sum(c.original_value) FILTER(WHERE r.confirmed_organisation_id IS NOT NULL AND r.review_status='approved' AND NOT r.register_context_stale) AS reviewed_value,
            sum(c.original_value) FILTER(WHERE r.organisation_id IS NOT NULL AND r.review_status NOT IN ('rejected','needs_recheck') AND NOT r.register_context_stale) AS proposed_value
            FROM totals c LEFT JOIN market.buyer_resolution r USING(buyer_key)'''),{'asof':as_of,'past':as_of-timedelta(days=365)}).mappings().one()
        # Count the grouping keys instead of computing unused top-supplier JSON and
        # correlated panel/register evidence for every analytical row.
        count_queries={
            'v_agency_spend':'''SELECT count(*) FROM (SELECT buyer_key,buyer,payload->>'category_tag',
                extract(year FROM first_published) FROM market.current_analytical_contracts GROUP BY 1,2,3,4) g''',
            'v_supplier_profile':'''SELECT count(DISTINCT supplier_abn) FROM market.current_analytical_contracts
                WHERE supplier_abn IS NOT NULL'''}
        for name in ['v_healthcare_accounts','v_demand_signals','v_panel_calendar']:
            count_queries[name]='SELECT count(*) FROM market.'+name
        views={name:conn.execute(text(query)).scalar() for name,query in count_queries.items()}
    total=coverage['value']
    return dict(as_of=as_of,status='full specification not yet accepted',buyer_identities=matches,profile_fields_missing=profile,
        panels=dict(panels,verified_evidence=evidence),input_rows=inputs,analytical_view_rows=views,
        recent_original_award_value=total,reviewed_buyer_value_coverage=float((coverage['reviewed_value'] or 0)/total) if total else None,
        proposed_buyer_value_coverage=float((coverage['proposed_value'] or 0)/total) if total else None,
        business_inputs_missing=[name for name in ['bench','role_map','relationships','invitations','outcomes','lineage'] if not inputs.get(name)],
        acceptance_gaps=['Government buyer approvals and unresolved source identity conflicts require evidence review.',
                         'Panel candidate marking, portal facts and Illoura eligibility evidence are incomplete.',
                         'RAP matching/extraction does not yet meet the full-directory 90% match and spot-check targets.',
                         'State coverage is retained WA history; other states and current disclosure sources remain incomplete.',
                         'Healthcare registers and quarterly Star Ratings provide identity and quality evidence; comprehensive provider coverage, buying intent and workforce need remain unestablished.',
                         'Budget, grants, insourcing and annual-report feeds require source-specific acquisition and parsers.',
                         'National business register scope remains procurement ABNs; the full-active-ABN target is not claimed.',
                         'This command neither fabricates approvals nor changes schedules or sends mail.'])
