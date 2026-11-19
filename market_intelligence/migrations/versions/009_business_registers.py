"""National business registers for supplier identity checks, kept apart from government bodies.

These tables never feed market.organisations (which treats every observation as a
government body). Individuals' names and street addresses are never stored.
"""
from alembic import op

revision = '009_business_registers'
down_revision = '008_website_privacy'


def upgrade():
    op.execute('''
CREATE TABLE market.register_snapshots (
 id uuid PRIMARY KEY,
 source text NOT NULL CHECK(source IN ('abn','asic_companies','acnc','oric')),
 sha256 text NOT NULL CHECK(length(sha256)=64),
 retrieved_at timestamptz NOT NULL, effective_date date NOT NULL, effective_date_basis text NOT NULL,
 source_url text NOT NULL, licence text NOT NULL, licence_url text NOT NULL,
 parser_revision text NOT NULL,
 scope_kind text NOT NULL CHECK(scope_kind IN ('complete','procurement_abns')),
 scope_sha256 text NOT NULL DEFAULT '' CHECK(scope_sha256='' OR length(scope_sha256)=64),
 scope_count integer CHECK(scope_count IS NULL OR scope_count>0),
 row_count integer NOT NULL CHECK(row_count>0), metrics jsonb NOT NULL DEFAULT '{}',
 imported_at timestamptz NOT NULL DEFAULT now(),
 CHECK((scope_kind='complete')=(scope_sha256='' AND scope_count IS NULL)),
 UNIQUE(source,sha256,parser_revision,scope_sha256)
);
CREATE TABLE market.register_scope (
 snapshot_id uuid NOT NULL REFERENCES market.register_snapshots(id),
 abn text NOT NULL CHECK(abn ~ '^[0-9]{11}$'), PRIMARY KEY(snapshot_id,abn)
);
CREATE TABLE market.register_entities (
 snapshot_id uuid NOT NULL REFERENCES market.register_snapshots(id), record_id text NOT NULL,
 abn text CHECK(abn IS NULL OR abn ~ '^[0-9]{11}$'), acn text CHECK(acn IS NULL OR acn ~ '^[0-9]{9}$'),
 icn text, name text, name_key text, is_individual boolean NOT NULL DEFAULT false,
 status text, status_from date, registered_on date, deregistered_on date,
 entity_type text, state text, postcode text,
 attributes jsonb NOT NULL DEFAULT '{}', issues jsonb NOT NULL DEFAULT '[]',
 PRIMARY KEY(snapshot_id,record_id),
 CHECK(NOT is_individual OR (name IS NULL AND name_key IS NULL))
);
CREATE INDEX register_entities_abn ON market.register_entities(abn);
CREATE TABLE market.register_names (
 snapshot_id uuid NOT NULL, record_id text NOT NULL, name_type text NOT NULL,
 name text NOT NULL, name_key text NOT NULL,
 PRIMARY KEY(snapshot_id,record_id,name_type,name),
 FOREIGN KEY(snapshot_id,record_id) REFERENCES market.register_entities(snapshot_id,record_id)
);
CREATE TABLE market.current_register_snapshots (
 source text PRIMARY KEY, snapshot_id uuid UNIQUE NOT NULL REFERENCES market.register_snapshots(id)
);
CREATE VIEW market.current_register_entities AS
 SELECT s.source, s.effective_date, s.scope_kind, e.*
 FROM market.register_entities e JOIN market.current_register_snapshots c ON c.snapshot_id=e.snapshot_id
 JOIN market.register_snapshots s ON s.id=e.snapshot_id;
''')


def downgrade():
    raise RuntimeError('Register snapshot history must be retained; restore a validated backup')
