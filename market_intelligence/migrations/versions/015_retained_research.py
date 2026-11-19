"""Versioned contact-free retained RAP and state-contract evidence."""
from alembic import op
revision='015_retained_research'
down_revision='014_forward'


def upgrade():
    op.execute('''
CREATE TABLE market.research_snapshots (
 id uuid PRIMARY KEY,source text NOT NULL,sha256 text NOT NULL,observed_at timestamptz NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(),provenance jsonb NOT NULL,row_count integer NOT NULL CHECK(row_count>0),
 UNIQUE(source,sha256)
);
CREATE TABLE market.research_records (
 snapshot_id uuid NOT NULL REFERENCES market.research_snapshots(id),record_key text NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(snapshot_id,record_key)
);
CREATE TABLE market.current_research_snapshots (
 source text PRIMARY KEY,snapshot_id uuid NOT NULL REFERENCES market.research_snapshots(id)
);
CREATE VIEW market.current_research AS SELECT s.source,r.*,s.observed_at,s.provenance FROM market.research_records r
 JOIN market.current_research_snapshots c ON c.snapshot_id=r.snapshot_id JOIN market.research_snapshots s ON s.id=r.snapshot_id;
''')


def downgrade():raise RuntimeError('Retained research history must be preserved')
