"""Federal snapshots, observations, source-preserving identities and match proposals."""
from alembic import op

revision = '001_federal_spine'
down_revision = None


def upgrade():
    op.execute('''
CREATE SCHEMA market;
CREATE TABLE market.pipeline_runs (
 id uuid PRIMARY KEY, source text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, status text NOT NULL CHECK(status IN ('running','complete','unchanged','failed')),
 snapshot_id uuid, metrics jsonb NOT NULL DEFAULT '{}', error_code text
);
CREATE TABLE market.snapshots (
 id uuid PRIMARY KEY, source text NOT NULL CHECK(source IN ('agor','government_directory')),
 sha256 text NOT NULL CHECK(length(sha256)=64), retrieved_at timestamptz NOT NULL,
 effective_date date NOT NULL, effective_date_basis text NOT NULL,
 source_url text NOT NULL, licence text NOT NULL, licence_url text NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(), row_count integer NOT NULL CHECK(row_count>0),
 UNIQUE(source,sha256)
);
CREATE TABLE market.observations (
 snapshot_id uuid NOT NULL REFERENCES market.snapshots(id), publisher_id text NOT NULL,
 name text NOT NULL, name_key text NOT NULL, kind text NOT NULL,
 abn text CHECK(abn IS NULL OR abn ~ '^[0-9]{11}$'), payload jsonb NOT NULL, issues jsonb NOT NULL,
 PRIMARY KEY(snapshot_id,publisher_id)
);
CREATE INDEX observations_name_key ON market.observations(name_key);
CREATE INDEX observations_abn ON market.observations(abn);
CREATE TABLE market.current_snapshots (
 source text PRIMARY KEY, snapshot_id uuid UNIQUE NOT NULL REFERENCES market.snapshots(id)
);
CREATE VIEW market.organisation_observations AS
 SELECT 'gov:'||o.publisher_id AS organisation_id, o.publisher_id, o.name, o.name_key,
 o.kind,o.abn,o.payload,o.issues,s.id AS snapshot_id,s.source,s.effective_date,
 s.retrieved_at,s.source_url,s.licence,s.licence_url,
 CASE WHEN s.source='agor' THEN o.payload->>'Portfolio' ELSE NULL END AS portfolio
 FROM market.observations o JOIN market.snapshots s ON s.id=o.snapshot_id
 JOIN market.current_snapshots c ON c.snapshot_id=s.id;
CREATE VIEW market.organisations AS
 SELECT DISTINCT ON (organisation_id) * FROM market.organisation_observations
 ORDER BY organisation_id,effective_date DESC,retrieved_at DESC,source;
CREATE TABLE market.buyer_match_proposals (
 buyer_key text PRIMARY KEY, buyer_name text NOT NULL, buyer_abn text,
 match_status text NOT NULL CHECK(match_status IN ('exact_abn','exact_name','ambiguous','conflict','unmatched')),
 organisation_id text, candidate_ids jsonb NOT NULL, evidence text NOT NULL,
 snapshots jsonb NOT NULL, built_at timestamptz NOT NULL DEFAULT now(),
 CHECK((match_status IN ('exact_abn','exact_name'))=(organisation_id IS NOT NULL))
);
CREATE TABLE market.review_decisions (
 buyer_key text PRIMARY KEY, organisation_id text NOT NULL, reviewer text NOT NULL,
 evidence_url text NOT NULL, reviewed_at timestamptz NOT NULL DEFAULT now(), notes text NOT NULL DEFAULT ''
);
''')


def downgrade():
    # Explicitly data-destructive; used only in an isolated integration-test database.
    op.execute('DROP TABLE market.review_decisions; DROP TABLE market.buyer_match_proposals; '
               'DROP VIEW market.organisations; DROP VIEW market.organisation_observations; DROP TABLE market.current_snapshots; '
               'DROP TABLE market.observations; DROP TABLE market.snapshots; '
               'DROP TABLE market.pipeline_runs; DROP SCHEMA market;')
