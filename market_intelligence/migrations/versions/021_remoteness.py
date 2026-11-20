"""ABS provenance and derived evidence; original workbooks remain on immutable disk."""
from alembic import op

revision = '021_remoteness'
down_revision = '020_extended_demand'


def upgrade():
    op.execute('''
        CREATE TABLE market.remoteness_snapshots (
            id uuid PRIMARY KEY,
            fingerprint text NOT NULL UNIQUE,
            edition_chain text NOT NULL,
            parser_revision text NOT NULL,
            provenance jsonb NOT NULL,
            retrieved_at timestamptz NOT NULL,
            imported_at timestamptz NOT NULL DEFAULT now(),
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE TABLE market.remoteness_rows (
            snapshot_id uuid NOT NULL REFERENCES market.remoteness_snapshots(id),
            layer text NOT NULL CHECK(layer IN ('SA1','SA4','LGA','POA')),
            code text NOT NULL,
            payload jsonb NOT NULL,
            PRIMARY KEY(snapshot_id,layer,code)
        );
    ''')


def downgrade():
    raise RuntimeError('Retain immutable source evidence; use a forward migration')
