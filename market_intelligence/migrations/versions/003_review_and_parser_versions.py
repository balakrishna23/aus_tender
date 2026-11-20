"""Explicit review decisions, combined identity evidence and parser lineage."""
from alembic import op

revision = '003_review_parser_versions'
down_revision = '002_match_generations'


def upgrade():
    op.execute('''
ALTER TABLE market.snapshots ADD COLUMN parser_revision text NOT NULL DEFAULT '1';
ALTER TABLE market.snapshots DROP CONSTRAINT snapshots_source_sha256_key;
ALTER TABLE market.snapshots ADD UNIQUE(source,sha256,parser_revision);
ALTER TABLE market.buyer_match_proposals DROP CONSTRAINT buyer_match_proposals_match_status_check;
ALTER TABLE market.buyer_match_proposals DROP CONSTRAINT buyer_match_proposals_check;
ALTER TABLE market.buyer_match_proposals ADD CHECK(match_status IN ('exact_abn','exact_abn_name','exact_name','ambiguous','conflict','unmatched'));
ALTER TABLE market.buyer_match_proposals ADD CHECK((match_status IN ('exact_abn','exact_abn_name','exact_name'))=(organisation_id IS NOT NULL));
ALTER TABLE market.review_decisions ADD COLUMN decision text NOT NULL DEFAULT 'approved' CHECK(decision IN ('approved','rejected'));
ALTER TABLE market.review_decisions ALTER COLUMN organisation_id DROP NOT NULL;
ALTER TABLE market.review_decisions ADD CHECK((decision='approved')=(organisation_id IS NOT NULL));
ALTER TABLE market.review_decisions ADD COLUMN identity_fingerprint text;
ALTER TABLE market.review_decisions ADD COLUMN reviewed_snapshots jsonb NOT NULL DEFAULT '[]';
UPDATE market.review_decisions d SET identity_fingerprint=md5(o.organisation_id||'|'||o.name_key||'|'||COALESCE(o.abn,''))
 FROM market.organisations o WHERE o.organisation_id=d.organisation_id;
CREATE TABLE market.review_events (
 id uuid PRIMARY KEY,buyer_key text NOT NULL,decision text NOT NULL,organisation_id text,
 reviewer text NOT NULL,evidence_url text NOT NULL,notes text NOT NULL,reviewed_at timestamptz NOT NULL DEFAULT now(),
 previous_decision jsonb,identity_fingerprint text,reviewed_snapshots jsonb NOT NULL
);
CREATE VIEW market.buyer_resolution AS
 SELECT p.*,d.decision AS human_decision,
 p.snapshots<>COALESCE((SELECT jsonb_agg(snapshot_id::text ORDER BY snapshot_id::text) FROM market.current_snapshots),'[]'::jsonb) AS register_context_stale,
 CASE WHEN d.decision='rejected' THEN 'rejected'
      WHEN d.decision='approved' AND d.identity_fingerprint=md5(o.organisation_id||'|'||o.name_key||'|'||COALESCE(o.abn,'')) THEN 'approved'
      WHEN d.decision='approved' THEN 'needs_recheck'
      ELSE 'unreviewed' END AS review_status,
 CASE WHEN d.decision='approved' AND d.identity_fingerprint=md5(o.organisation_id||'|'||o.name_key||'|'||COALESCE(o.abn,''))
      THEN d.organisation_id ELSE NULL END AS confirmed_organisation_id
 FROM market.current_buyer_matches p LEFT JOIN market.review_decisions d ON d.buyer_key=p.buyer_key
 LEFT JOIN market.organisations o ON o.organisation_id=d.organisation_id;
''')


def downgrade():
    raise RuntimeError('Review history and parser lineage cannot be discarded; restore a validated backup')
