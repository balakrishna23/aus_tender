"""Immutable quarterly aged-care observations and unconfirmed provider proposals."""
from alembic import op

revision = '022_aged_care'
down_revision = '021_remoteness'


def upgrade():
    op.execute('''
CREATE TABLE market.aged_care_snapshots (
 id uuid PRIMARY KEY, sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 parser_revision text NOT NULL, reference_period text NOT NULL CHECK(reference_period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
 retrieved_at timestamptz NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 provenance jsonb NOT NULL, metrics jsonb NOT NULL,
 UNIQUE(sha256,parser_revision)
);
CREATE TABLE market.aged_care_observations (
 snapshot_id uuid NOT NULL REFERENCES market.aged_care_snapshots(id),
 row_identity text NOT NULL CHECK(length(row_identity)=64),
 key_ordinal integer NOT NULL CHECK(key_ordinal>0),
 content_sha256 text NOT NULL CHECK(length(content_sha256)=64),
 source_row integer NOT NULL CHECK(source_row>=2),
 provider_name text NOT NULL, provider_name_key text NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(snapshot_id,row_identity), UNIQUE(snapshot_id,source_row)
);
CREATE TABLE market.current_aged_care_snapshot (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 snapshot_id uuid UNIQUE NOT NULL REFERENCES market.aged_care_snapshots(id)
);
CREATE FUNCTION market.aged_care_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'Aged-care source evidence is immutable'; END $$;
CREATE TRIGGER aged_care_snapshots_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON market.aged_care_snapshots FOR EACH STATEMENT EXECUTE FUNCTION market.aged_care_immutable();
CREATE TRIGGER aged_care_observations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON market.aged_care_observations FOR EACH STATEMENT EXECUTE FUNCTION market.aged_care_immutable();

CREATE VIEW market.v_aged_care_quality AS
 SELECT s.reference_period,o.snapshot_id,o.row_identity,o.source_row,o.provider_name,
 o.payload->>'Service Name' AS service_name,o.payload->>'Service Suburb' AS service_suburb,
 o.payload->>'Purpose' AS purpose,o.payload->>'State/Territory' AS state,
 o.payload->>'Aged Care Planning Region' AS planning_region,o.payload->>'MMM Code' AS mmm_code,
 o.payload->>'MMM Region' AS mmm_region,o.payload->>'Size' AS size,
 (o.payload->>'Overall Star Rating')::integer AS overall_rating,
 (o.payload->>'Residents'' Experience rating')::integer AS residents_experience_rating,
 (o.payload->>'Compliance rating')::integer AS compliance_rating,
 (o.payload->>'Staffing rating')::integer AS staffing_rating,
 (o.payload->>'Quality Measures rating')::integer AS quality_measures_rating,
 (o.payload->>'[S] Registered Nurse Care Minutes - Target')::numeric AS rn_target,
 (o.payload->>'[S] Registered Nurse Care Minutes - Actual')::numeric AS rn_actual,
 CASE WHEN o.payload->>'[S] Registered Nurse Care Minutes - Target' IS NOT NULL
 AND o.payload->>'[S] Registered Nurse Care Minutes - Actual' IS NOT NULL THEN
 greatest(0,(o.payload->>'[S] Registered Nurse Care Minutes - Target')::numeric
 -(o.payload->>'[S] Registered Nurse Care Minutes - Actual')::numeric) END AS rn_shortfall,
 (o.payload->>'[S] Total Care Minutes - Target')::numeric AS total_target,
 (o.payload->>'[S] Total Care Minutes - Actual')::numeric AS total_actual,
 CASE WHEN o.payload->>'[S] Total Care Minutes - Target' IS NOT NULL
 AND o.payload->>'[S] Total Care Minutes - Actual' IS NOT NULL THEN
 greatest(0,(o.payload->>'[S] Total Care Minutes - Target')::numeric
 -(o.payload->>'[S] Total Care Minutes - Actual')::numeric) END AS total_shortfall,
 o.payload->>'[C] Decision type' AS decision_type,
 (o.payload->>'[C] Date Decision Applied')::date AS decision_applied,
 (o.payload->>'[C] Date Decision Ends')::date AS decision_ends,
 (o.payload->>'[C] Date Audit Closed')::date AS audit_closed,
 s.provenance->>'publication_page' AS publication_page,
 'Published quality evidence; no buying intent or staffing demand inferred'::text AS evidence_scope
 FROM market.aged_care_observations o JOIN market.current_aged_care_snapshot c ON c.snapshot_id=o.snapshot_id
 JOIN market.aged_care_snapshots s ON s.id=o.snapshot_id;

CREATE VIEW market.v_aged_care_provider_proposals AS
 SELECT o.snapshot_id,o.row_identity,o.provider_name,r.source AS register_source,
 r.snapshot_id AS register_snapshot_id,r.record_id,r.abn,r.name AS register_name,
 CASE WHEN o.provider_name=r.name THEN 'exact_name' ELSE 'normalised_name' END AS match_basis,
 'proposal_only; human verification required'::text AS review_status,
 count(*) OVER(PARTITION BY o.snapshot_id,o.row_identity) AS candidate_records
 FROM market.aged_care_observations o JOIN market.current_aged_care_snapshot c ON c.snapshot_id=o.snapshot_id
 JOIN market.current_register_entities r ON NOT r.is_individual
 AND (o.provider_name=r.name OR o.provider_name_key=r.name_key);
''')


def downgrade():
    raise RuntimeError('Retain immutable aged-care evidence; use a forward migration')
