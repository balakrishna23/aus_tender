"""ABS boundary references with explicit edition and native GDA2020 coordinates."""
from alembic import op
revision='017_geography'
down_revision='016_forward_identity'


def upgrade():
    op.execute('''
CREATE TABLE market.geography_snapshots (
 id uuid PRIMARY KEY,layer text NOT NULL CHECK(layer IN ('SA4','LGA','POA')),edition text NOT NULL,
 sha256 text NOT NULL,source_url text NOT NULL,download_url text NOT NULL,licence text NOT NULL,
 licence_url text NOT NULL,retrieved_at timestamptz NOT NULL,crs_wkt text NOT NULL,
 row_count integer NOT NULL CHECK(row_count>0),imported_at timestamptz NOT NULL DEFAULT now(),UNIQUE(layer,sha256)
);
CREATE TABLE market.geography_features (
 snapshot_id uuid NOT NULL REFERENCES market.geography_snapshots(id),code text NOT NULL,name text NOT NULL,
 state_code text,properties jsonb NOT NULL,geometry jsonb,PRIMARY KEY(snapshot_id,code)
);
CREATE TABLE market.current_geography_snapshots (
 layer text PRIMARY KEY,snapshot_id uuid NOT NULL REFERENCES market.geography_snapshots(id)
);
CREATE VIEW market.current_geography AS SELECT f.*,s.layer,s.edition,s.source_url,s.licence,s.retrieved_at,s.crs_wkt
 FROM market.geography_features f JOIN market.current_geography_snapshots c ON c.snapshot_id=f.snapshot_id
 JOIN market.geography_snapshots s ON s.id=f.snapshot_id;
''')


def downgrade():raise RuntimeError('Geography snapshot history must be retained')
