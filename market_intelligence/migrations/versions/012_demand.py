"""Versioned JSA observations. Dataset and classification stay separate."""
from alembic import op

revision = '012_demand'
down_revision = '011_aps_headcount'


def upgrade():
    op.execute('''
CREATE TABLE market.demand_snapshots (
 id uuid PRIMARY KEY, source text NOT NULL, sha256 text NOT NULL, parser_revision text NOT NULL,
 source_url text NOT NULL, download_url text NOT NULL, licence text NOT NULL, licence_url text NOT NULL,
 retrieved_at timestamptz NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 period_end date NOT NULL, row_count integer NOT NULL CHECK(row_count>0), metrics jsonb NOT NULL,
 UNIQUE(source,sha256,parser_revision)
);
CREATE TABLE market.demand_observations (
 snapshot_id uuid NOT NULL REFERENCES market.demand_snapshots(id), observation_key text NOT NULL,
 classification text NOT NULL, occupation_code text, occupation_title text NOT NULL,
 region text NOT NULL, period date NOT NULL, measure text NOT NULL,
 value numeric, rating text, payload jsonb NOT NULL,
 PRIMARY KEY(snapshot_id,observation_key)
);
CREATE INDEX demand_lookup ON market.demand_observations(snapshot_id,occupation_code,region,period);
CREATE TABLE market.current_demand_snapshots (
 source text PRIMARY KEY, snapshot_id uuid NOT NULL REFERENCES market.demand_snapshots(id)
);
CREATE VIEW market.current_demand AS
 SELECT o.*,s.source,s.source_url,s.download_url,s.licence,s.retrieved_at,s.sha256
 FROM market.demand_observations o JOIN market.current_demand_snapshots c ON c.snapshot_id=o.snapshot_id
 JOIN market.demand_snapshots s ON s.id=o.snapshot_id;
''')


def downgrade():
    raise RuntimeError('Demand history must be retained')
