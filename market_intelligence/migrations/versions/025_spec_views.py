"""Generation-backed, contact-free analytical views; retain source and generation history."""
from alembic import op

revision = '025_spec_views'
down_revision = '024_national_registers'

VIEW_FIELDS = {
    'account_score': [('rank_in_lane','integer'),('overall_rank','integer'),('score','numeric'),('score_coverage_pct','numeric'),('already_engaged','boolean'),('new_target','boolean'),('components','jsonb'),('penalties','jsonb'),('blockers','jsonb')],
    'buyict_buyers': [('relevant_value_36m','numeric'),('relevant_contracts_36m','integer'),('value_12m','numeric'),('contracts_12m','integer'),('invitation_basis','text')],
    'buyict_gaps': [('relevant_value_36m','numeric'),('relevant_contracts_36m','integer'),('invitation_basis','text')],
    'ipp_gap': [('portfolio','text'),('value_ratio','numeric'),('volume_ratio','numeric'),('gap','numeric')],
    'ipp_levers': [('lever','text'),('contracts','integer'),('value','numeric')],
    'prime_targets': [('active_high_value_contracts','integer'),('value','numeric')],
    'partner_candidates': [('contracts','integer'),('original_awarded_value','numeric'),('is_young','boolean')],
    'indigenous_partners': [('state','text'),('industry','text')],
    'rap_targets': [('rap_type','text'),('period_status','text'),('period_end','date'),('blocked_until_certified','boolean')],
    'panel_access': [('son_id','text'),('illoura_status','text'),('eligibility_status','text'),('spend_through_panel_12m','numeric')],
    'forward_pipeline': [('date','date'),('signal','text'),('source','text'),('category_tag','text')],
    'demand_vs_bench': [('state','text'),('role_family','text'),('ads_3m_average','numeric'),('bench_count','integer'),('period','date')],
    'lookalikes': [('similarity','numeric'),('similarity_coverage_pct','numeric'),('matched_client_key','text')],
    'whats_changed': [('source','text'),('record_key','text'),('event','text')],
}

RATE_SQL = """
CREATE VIEW market.v_rate_benchmarks AS
 WITH candidates AS (
 SELECT c.*,c.payload->>'category_tag' AS role_category,
 c.payload->>'supplier_state' AS state,(c.current_end_date-c.start_date+1) AS duration_days
 FROM market.current_analytical_contracts c
 WHERE c.payload->>'category_tag' IN ('ict_labour','recruitment','labour_hire_general','healthcare_staffing','nursing_agency')
 AND c.start_date>=DATE '1990-01-01' AND c.current_end_date>=DATE '1990-01-01'
 AND c.current_end_date<DATE '2100-01-01' AND c.start_date<DATE '2100-01-01'
 AND c.current_end_date>c.start_date AND c.current_end_date-c.start_date BETWEEN 20 AND 1826
 AND c.current_value>0
 AND c.current_value/(c.current_end_date-c.start_date+1) BETWEEN 100 AND 2500

 AND NOT (coalesce(c.payload->>'title','') || ' ' || coalesce(c.payload->>'description',''))
 ~* '(multiple|team|[2-9][[:space:]]*(resources|contractors|fte))'
 )
 SELECT 'federal'::text AS lane,buyer_key,buyer AS account,buyer AS organisation,
 role_category,state,'Supplier state; delivery/agency state unavailable'::text AS state_basis,count(*)::integer AS sample_size,
 avg(current_value/duration_days)::numeric AS estimated_calendar_day_rate,
 avg(current_value/duration_days*365.25)::numeric AS estimated_annual_rate,
 percentile_cont(0.5) WITHIN GROUP(ORDER BY current_value/duration_days) AS median_estimated_calendar_day_rate,
 min(source_url) AS source_url,
 'Estimate: value divided by inclusive calendar duration; not a working-day quote. Labour tags and rate/duration bounds are single-resource heuristics; headcount is unavailable. Bounds truncate the sample; rates may include several people.'::text AS estimate_basis,
 'Review estimated rates against scope, GST, leave and working days before pricing'::text AS why_now,
 role_category AS target_roles,
 jsonb_build_object('heuristic_single_resource',true,'minimum_duration_days',21,'maximum_duration_days',1827,'value_basis','current full contract value','date_basis','recorded start and current end; amendments may distort estimates') AS details
 FROM candidates GROUP BY buyer_key,buyer,role_category,state
"""


def upgrade():
    op.execute('''CREATE TABLE market.view_generations (
 id uuid PRIMARY KEY,as_of date NOT NULL,generated_at timestamptz NOT NULL DEFAULT now(),
 status text,code_bundle text,config_sha256 text,metrics jsonb);
 CREATE TABLE market.current_view_generation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 generation_id uuid NOT NULL REFERENCES market.view_generations(id));
 CREATE TABLE market.view_rows (
 generation_id uuid NOT NULL REFERENCES market.view_generations(id),view_name text NOT NULL,
 position integer NOT NULL CHECK(position>0),row jsonb NOT NULL,
 PRIMARY KEY(generation_id,view_name,position));
 CREATE INDEX view_rows_lookup ON market.view_rows(view_name,generation_id);
 ''')
    for name, fields in VIEW_FIELDS.items():
        columns = ["r.row->>'lane' AS lane", 'r.position AS rank']
        columns += ["r.row->>'"+f+"' AS "+f for f in ('account_key','account','organisation','abn','organisation_id','buyer_key','why_now','source_url')]
        columns += ["r.row->'target_roles' AS target_roles", "r.row->'evidence' AS evidence"]
        for field, kind in fields:
            columns.append(("r.row->'"+field+"'" if kind=='jsonb' else "(r.row->>'"+field+"')::"+kind)+' AS '+field)
        columns += ['r.row AS details','g.as_of','g.id AS generation_id','g.generated_at']
        op.execute('CREATE VIEW market.v_'+name+' AS SELECT '+','.join(columns)+
                   " FROM market.view_rows r JOIN market.current_view_generation c USING(generation_id) JOIN market.view_generations g ON g.id=c.generation_id WHERE r.view_name='"+name+"'")
    op.execute(RATE_SQL)
    # Existing deployments grant owner default SELECT privileges; also cover migrations
    # executed by a different owner, and test databases without the deployment reader.
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='illoura_market_reader') THEN
 GRANT SELECT ON ALL TABLES IN SCHEMA market TO illoura_market_reader;
 END IF; END $$""")


def downgrade():
    raise RuntimeError('Retain generation history; use a forward view fix')
