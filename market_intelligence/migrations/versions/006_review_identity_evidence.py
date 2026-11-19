"""Recheck human decisions when any official identity claim changes."""
from alembic import op

revision='006_review_identity_evidence'
down_revision='005_evidence_quality'


def upgrade():
    op.execute('''
ALTER TABLE market.procurement_snapshots ADD COLUMN parser_revision text NOT NULL DEFAULT '1';
ALTER TABLE market.procurement_snapshots DROP CONSTRAINT procurement_snapshots_sha256_key;
ALTER TABLE market.procurement_snapshots ADD UNIQUE(sha256,parser_revision);
CREATE VIEW market.register_identity_fingerprints AS
 SELECT organisation_id,md5(string_agg(source||'|'||name_key||'|'||COALESCE(abn,''),';' ORDER BY source)) AS fingerprint
 FROM market.organisation_observations WHERE kind='body' GROUP BY organisation_id;
CREATE OR REPLACE VIEW market.buyer_resolution AS
 SELECT p.*,d.decision AS human_decision,
 p.snapshots<>COALESCE((SELECT jsonb_agg(snapshot_id::text ORDER BY snapshot_id::text) FROM market.current_snapshots),'[]'::jsonb) AS register_context_stale,
 CASE WHEN d.decision='rejected' THEN 'rejected'
      WHEN d.decision='approved' AND d.identity_fingerprint=f.fingerprint THEN 'approved'
      WHEN d.decision='approved' THEN 'needs_recheck'
      ELSE 'unreviewed' END AS review_status,
 CASE WHEN d.decision='approved' AND d.identity_fingerprint=f.fingerprint THEN d.organisation_id ELSE NULL END AS confirmed_organisation_id
 FROM market.current_buyer_matches p LEFT JOIN market.review_decisions d ON d.buyer_key=p.buyer_key
 LEFT JOIN market.register_identity_fingerprints f ON f.organisation_id=d.organisation_id;
''')


def downgrade():
    raise RuntimeError('Identity review evidence history must be retained')
