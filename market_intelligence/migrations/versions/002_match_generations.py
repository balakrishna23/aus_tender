"""Keep buyer-match history and atomically activate complete generations."""
from alembic import op

revision = '002_match_generations'
down_revision = '001_federal_spine'


def upgrade():
    op.execute('''
CREATE TABLE market.buyer_match_runs (
 id uuid PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 status text NOT NULL CHECK(status IN ('running','complete','failed')),
 source_sha256 text NOT NULL, source_metadata jsonb NOT NULL, snapshots jsonb NOT NULL,
 metrics jsonb NOT NULL DEFAULT '{}',error_code text
);
ALTER TABLE market.buyer_match_proposals ADD COLUMN run_id uuid REFERENCES market.buyer_match_runs(id);
ALTER TABLE market.buyer_match_proposals ADD COLUMN candidate_evidence jsonb NOT NULL DEFAULT '[]';
INSERT INTO market.buyer_match_runs(id,status,finished_at,source_sha256,source_metadata,snapshots)
 SELECT '00000000-0000-0000-0000-000000000001','complete',now(),'legacy-unversioned','{}','[]'
 WHERE EXISTS(SELECT 1 FROM market.buyer_match_proposals);
UPDATE market.buyer_match_proposals SET run_id='00000000-0000-0000-0000-000000000001';
ALTER TABLE market.buyer_match_proposals ALTER COLUMN run_id SET NOT NULL;
ALTER TABLE market.buyer_match_proposals DROP CONSTRAINT buyer_match_proposals_pkey;
ALTER TABLE market.buyer_match_proposals ADD PRIMARY KEY(run_id,buyer_key);
CREATE TABLE market.current_buyer_match_run (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),run_id uuid NOT NULL REFERENCES market.buyer_match_runs(id)
);
INSERT INTO market.current_buyer_match_run(run_id)
 SELECT id FROM market.buyer_match_runs WHERE id='00000000-0000-0000-0000-000000000001';
CREATE VIEW market.current_buyer_matches AS
 SELECT p.* FROM market.buyer_match_proposals p JOIN market.current_buyer_match_run c ON c.run_id=p.run_id;
''')


def downgrade():
    # History cannot be collapsed without loss; forward migrations are the recovery path.
    raise RuntimeError('Match-generation history is not downgradeable; restore a validated backup instead')
