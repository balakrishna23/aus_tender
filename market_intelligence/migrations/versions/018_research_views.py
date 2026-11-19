"""Read-only analytical views for the data actually loaded; no invented eligibility or demand."""
from alembic import op
revision='018_research_views'
down_revision='017_geography'


def upgrade():
    op.execute('''
CREATE VIEW market.v_agency_spend AS
 WITH facts AS (
 SELECT c.*,c.payload->>'category_tag' AS category,
        EXISTS(SELECT 1 FROM market.procurement_panel_links l WHERE l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id) AS has_panel
 FROM market.current_analytical_contracts c),
 supplier_totals AS (
 SELECT buyer_key,category,extract(year FROM first_published)::int AS award_year,supplier_abn,
        count(*) AS contracts,sum(original_value) AS original_award_value
 FROM facts GROUP BY 1,2,3,4),
 ordered AS (SELECT *,row_number() OVER(PARTITION BY buyer_key,category,award_year ORDER BY original_award_value DESC NULLS LAST,supplier_abn) AS position FROM supplier_totals),
 sellers AS (SELECT buyer_key,category,award_year,jsonb_agg(jsonb_build_object('supplier_abn',supplier_abn,'contracts',contracts,
             'original_award_value',original_award_value) ORDER BY position) FILTER(WHERE position<=5) AS top5_suppliers
             FROM ordered GROUP BY 1,2,3)
 SELECT c.buyer_key,c.buyer,c.category,extract(year FROM c.first_published)::int AS award_year,
 count(*) AS contracts,sum(c.original_value) AS original_awarded_value,
 count(c.original_value) AS contracts_with_value,
 sum(c.original_value) FILTER(WHERE has_panel)/nullif(sum(c.original_value),0) AS linked_panel_value_share,
 s.top5_suppliers,'federal'::text AS lane,'Research recorded awards; annual values are not payments'::text AS why_now,
 c.category AS target_roles
 FROM facts c LEFT JOIN sellers s ON s.buyer_key=c.buyer_key AND s.category IS NOT DISTINCT FROM c.category
 AND s.award_year IS NOT DISTINCT FROM extract(year FROM c.first_published)::int
 GROUP BY c.buyer_key,c.buyer,c.category,extract(year FROM c.first_published)::int,s.top5_suppliers;

CREATE VIEW market.v_supplier_profile AS
 WITH totals AS (SELECT supplier_abn,count(*) AS contracts,count(DISTINCT buyer_key) AS agencies_served,
 sum(original_value) AS original_awarded_value,array_agg(DISTINCT payload->>'category_tag') AS categories
 FROM market.current_analytical_contracts WHERE supplier_abn IS NOT NULL GROUP BY supplier_abn),
 entity AS (SELECT DISTINCT ON(abn) abn,name,registered_on,is_individual,source
 FROM market.current_register_entities WHERE abn IS NOT NULL ORDER BY abn,(source='abn') DESC,(source='asic_companies') DESC,record_id)
 SELECT t.*,CASE WHEN NOT e.is_individual THEN e.name END AS organisation,
 EXISTS(SELECT 1 FROM market.current_register_entities r WHERE r.abn=t.supplier_abn AND r.source='oric') AS has_oric_record,
 NULL::boolean AS is_small,'private'::text AS lane,'Research supplier and partner fit; turnover and readiness unknown'::text AS why_now,
 t.categories AS target_roles FROM totals t LEFT JOIN entity e ON e.abn=t.supplier_abn;

CREATE VIEW market.v_healthcare_accounts AS
 SELECT r.source,r.record_id,r.abn,r.name AS organisation,r.status,r.state,r.postcode,r.attributes->>'size' AS size,
 s.source_url,s.licence,s.retrieved_at,r.snapshot_id,
 CASE WHEN r.source='acnc' THEN 'Published advancing-health purpose' ELSE 'Published health-related industry' END AS health_basis,
 'healthcare'::text AS lane,'Verify buying activity and workforce need'::text AS why_now,NULL::text AS target_roles,
 NULL::text AS staffing_need,NULL::text AS acchs_status
 FROM market.current_register_entities r JOIN market.register_snapshots s ON s.id=r.snapshot_id
 WHERE NOT r.is_individual AND ((r.source='acnc' AND r.attributes->>'advancing_health'='true')
 OR (r.source='oric' AND r.attributes->>'industry' ~* '(health|medical|hospital)'));

CREATE VIEW market.v_demand_signals AS
 SELECT * FROM (SELECT d.*,row_number() OVER(PARTITION BY source,classification,occupation_code,occupation_title,region,measure ORDER BY period DESC) AS recency
 FROM market.current_demand d WHERE d.source IN ('jsa_ivi','jsa_shortages')) d WHERE recency=1;

CREATE VIEW market.v_panel_calendar AS
 SELECT p.son_id,p.panel_name,'notice_expiry'::text AS event,p.current_expiry AS event_date,
 'published_notice; intake unknown'::text AS basis,p.source_url
 FROM market.panel_watchlist p WHERE p.current_expiry IS NOT NULL
 UNION ALL
 SELECT p.son_id,p.panel_name,k.field,(e.facts->>k.field)::date,'human_verified',e.source_url
 FROM market.panel_watchlist p JOIN market.panel_evidence e USING(son_id)
 CROSS JOIN (VALUES('current_expiry'),('max_possible_expiry'),('next_refresh_or_intake_date')) AS k(field)
 WHERE e.facts->>k.field IS NOT NULL;
''')


def downgrade():raise RuntimeError('Use a forward analytical-view fix')
