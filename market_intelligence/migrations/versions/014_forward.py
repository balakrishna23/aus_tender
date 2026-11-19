"""Contact-free Tender Feed snapshot and next-year research view."""
from alembic import op
revision='014_forward'
down_revision='013_panel_evidence'


def upgrade():
    op.execute('''
CREATE TABLE market.forward_snapshots (
 id uuid PRIMARY KEY, sha256 text UNIQUE NOT NULL, exported_at timestamptz NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL, row_count integer NOT NULL CHECK(row_count>0)
);
CREATE TABLE market.forward_observations (
 snapshot_id uuid NOT NULL REFERENCES market.forward_snapshots(id), source text NOT NULL,source_id text NOT NULL,
 kind text,title text NOT NULL,buyer text,buyer_name_key text,category_tag text NOT NULL,
 expected_from date,expected_to date,closes_at timestamptz,status text,source_url text,retrieved_at text,
 payload jsonb NOT NULL,PRIMARY KEY(snapshot_id,source,source_id)
);
CREATE TABLE market.current_forward_snapshot (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),snapshot_id uuid NOT NULL REFERENCES market.forward_snapshots(id)
);
CREATE VIEW market.current_forward AS SELECT o.*,s.exported_at,s.metadata FROM market.forward_observations o
 JOIN market.current_forward_snapshot c ON c.snapshot_id=o.snapshot_id JOIN market.forward_snapshots s ON s.id=o.snapshot_id;
''')


def downgrade():raise RuntimeError('Forward history must be retained')
