"""Own-site RAP documents and rule-based findings. Retain document people as personal."""
from alembic import op

revision = '026_rap_documents'
down_revision = '025_spec_views'


def upgrade():
    op.execute('''
 CREATE TABLE market.rap_document_fetches (
 id uuid PRIMARY KEY, record_key text, organisation text, url text, final_url text,
 status text CHECK(status IN ('downloaded','robots_disallowed','blocked_host','not_pdf','too_large','http_error','error','duplicate')),
 http_status int, bytes bigint, sha256 text, robots_allowed bool, found_via text,
 fetched_at timestamptz DEFAULT now(), detail text);
 CREATE INDEX rap_fetch_record ON market.rap_document_fetches(record_key,fetched_at);
 CREATE TABLE market.rap_documents (
 sha256 text PRIMARY KEY, record_key text, organisation text, source_url text, pages int, characters int,
 rap_type_detected text, period_detected text, organisation_found bool,
 match_status text CHECK(match_status IN ('matched','type_mismatch','period_mismatch','organisation_not_found','unreadable')),
 parser_revision text, extracted_at timestamptz);
 CREATE INDEX rap_document_record ON market.rap_documents(record_key,extracted_at);
 CREATE TABLE market.rap_commitments (
 id bigserial PRIMARY KEY, document_sha256 text REFERENCES market.rap_documents(sha256), record_key text,
 category text CHECK(category IN ('procurement','employment','supplier_relationships','supply_nation','procurement_strategy','other')),
 deliverable text, target_kind text CHECK(target_kind IN ('percent','money','count','none')),
 target_value numeric, target_unit text, timeline text, responsibility text, page int, quote text);
 CREATE INDEX rap_commitment_document ON market.rap_commitments(document_sha256);
 CREATE TABLE market.rap_people (
 id bigserial PRIMARY KEY, document_sha256 text REFERENCES market.rap_documents(sha256), record_key text,
 context text CHECK(context IN ('contact','ceo','sponsor','champion','ambassador','working_group','generic_contact')),
 name text, title text, email text, phone text, page int, quote text, is_personal bool NOT NULL DEFAULT true);
 CREATE INDEX rap_person_document ON market.rap_people(document_sha256);
 CREATE VIEW market.current_rap_document_findings AS
 WITH latest AS (
 SELECT DISTINCT ON (record_key) * FROM market.rap_documents WHERE match_status='matched'
 -- A landing page can yield two matching plans (e.g. 2023-2026 and 2026-2029): prefer the later plan.
 ORDER BY record_key,(SELECT max(y[1]::int) FROM regexp_matches(coalesce(period_detected,''),'((?:19|20)[0-9]{2})','g') AS y) DESC NULLS LAST,
 extracted_at DESC NULLS LAST,sha256 DESC
 ), findings AS (
 SELECT d.record_key,d.organisation,d.sha256 AS document_sha256,d.source_url,
 coalesce(c.has_procurement_target,false) AS has_procurement_target,
 c.procurement_spend_percent,c.procurement_spend_money,c.supplier_relationships_target,
 coalesce(c.employment_target,false) AS employment_target,c.employment_target_percent,c.employment_target_count,
 coalesce(c.supply_nation_membership,false) AS supply_nation_membership,
 coalesce(c.procurement_strategy,false) AS procurement_strategy,
 p.name AS contact_name,p.title AS contact_title,p.email AS contact_email,p.phone AS contact_phone,
 ceo.name AS ceo_name,coalesce(c.certified_required,false) AS certified_required
 FROM latest d LEFT JOIN LATERAL (
 SELECT bool_or(category='procurement' AND (target_kind<>'none' OR deliverable ~* '(spend|percent|count)')) AS has_procurement_target,
 max(target_value) FILTER(WHERE category='procurement' AND target_kind='percent') AS procurement_spend_percent,
 max(target_value) FILTER(WHERE category='procurement' AND target_kind='money') AS procurement_spend_money,
 max(target_value) FILTER(WHERE category='supplier_relationships' AND target_kind='count') AS supplier_relationships_target,
 bool_or(category='employment' AND target_kind<>'none') AS employment_target,
 max(target_value) FILTER(WHERE category='employment' AND target_kind='percent') AS employment_target_percent,
 max(target_value) FILTER(WHERE category='employment' AND target_kind='count') AS employment_target_count,
 bool_or(category='supply_nation' AND deliverable ~* '(member|membership)') AS supply_nation_membership,
 bool_or(category='procurement_strategy' OR deliverable ~* 'procurement strategy') AS procurement_strategy,
 bool_or(deliverable ~* '(require|must|only).{0,80}Supply Nation.{0,20}certif') AS certified_required
 FROM market.rap_commitments WHERE document_sha256=d.sha256
 ) c ON true
 LEFT JOIN LATERAL (SELECT * FROM market.rap_people WHERE document_sha256=d.sha256
 AND context IN ('contact','generic_contact') ORDER BY (context='contact') DESC,id LIMIT 1) p ON true
 LEFT JOIN LATERAL (SELECT name FROM market.rap_people WHERE document_sha256=d.sha256 AND context='ceo' ORDER BY id LIMIT 1) ceo ON true
 ) SELECT record_key,organisation,document_sha256,source_url,has_procurement_target,
 procurement_spend_percent,procurement_spend_money,supplier_relationships_target,employment_target,
 employment_target_percent,employment_target_count,supply_nation_membership,procurement_strategy,
 contact_name,contact_title,contact_email,contact_phone,ceo_name,
 jsonb_build_object('has_procurement_target',jsonb_build_object('value',has_procurement_target),
 'employment_target',jsonb_build_object('value',employment_target),
 'requires_supply_nation_certified',jsonb_build_object('value',CASE WHEN certified_required THEN 'yes' ELSE 'unknown' END)) AS fields
 FROM findings;
 DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='illoura_market_reader') THEN
 GRANT SELECT ON market.rap_document_fetches,market.rap_documents,market.rap_commitments,
 market.rap_people,market.current_rap_document_findings TO illoura_market_reader;
 END IF; END $$;
 ''')


def downgrade():
    raise RuntimeError('Retain document history; use a forward migration')
