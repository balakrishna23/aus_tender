"""Versioned procurement facts and explicit panel observations for research reports."""
from alembic import op

revision='004_procurement_facts'
down_revision='003_review_parser_versions'


def upgrade():
    op.execute('''
CREATE TABLE market.procurement_snapshots (
 id uuid PRIMARY KEY,sha256 text NOT NULL UNIQUE,exported_at timestamptz NOT NULL,
 metadata jsonb NOT NULL,imported_at timestamptz NOT NULL DEFAULT now(),
 source_url text NOT NULL DEFAULT 'https://api.tenders.gov.au/ocds',
 licence text NOT NULL DEFAULT 'Creative Commons Attribution 3.0 Australia'
);
CREATE TABLE market.procurement_contracts (
 snapshot_id uuid NOT NULL REFERENCES market.procurement_snapshots(id),cn_id text NOT NULL,
 buyer_key text NOT NULL,buyer text NOT NULL,buyer_abn text,supplier text,supplier_abn text,category_family text,
 original_value numeric,current_value numeric,start_date date,current_end_date date,first_published date,
 source_url text,retrieved_at text,payload jsonb NOT NULL,issues jsonb NOT NULL,
 PRIMARY KEY(snapshot_id,cn_id)
);
CREATE INDEX procurement_buyer ON market.procurement_contracts(snapshot_id,buyer_key);
CREATE INDEX procurement_expiry ON market.procurement_contracts(snapshot_id,current_end_date);
CREATE TABLE market.procurement_panel_links (
 snapshot_id uuid NOT NULL REFERENCES market.procurement_snapshots(id),cn_id text NOT NULL,son_id text NOT NULL,
 source_url text NOT NULL,retrieved_at text,
 PRIMARY KEY(snapshot_id,cn_id,son_id,source_url)
);
CREATE TABLE market.procurement_panels (
 snapshot_id uuid NOT NULL REFERENCES market.procurement_snapshots(id),observation_id integer NOT NULL,
 son_id text NOT NULL,title text NOT NULL,owner_agency text,category text,period_text text,
 start_date date,current_expiry date,source_url text,retrieved_at text,
 date_basis text NOT NULL,PRIMARY KEY(snapshot_id,observation_id)
);
CREATE TABLE market.current_procurement_snapshot (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),snapshot_id uuid NOT NULL REFERENCES market.procurement_snapshots(id)
);
CREATE VIEW market.current_contracts AS SELECT p.* FROM market.procurement_contracts p
 JOIN market.current_procurement_snapshot c ON p.snapshot_id=c.snapshot_id;
''')


def downgrade():
    raise RuntimeError('Procurement snapshot history must be retained; restore a validated backup')
