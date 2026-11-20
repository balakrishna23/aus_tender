"""APS agency headcount from the APSC Employment Release Tables (spec phase 2)."""
from alembic import op

revision = '011_aps_headcount'
down_revision = '010_watchlist_profile'


def upgrade():
    op.execute('''
-- One row per APSC release file. Figures are as at 30 June of as_at's year; prev_as_at is the comparison year.
CREATE TABLE market.aps_snapshots (
 id uuid PRIMARY KEY, sha256 text NOT NULL, parser_revision text NOT NULL,
 as_at date NOT NULL, prev_as_at date NOT NULL CHECK(prev_as_at < as_at),
 source_url text NOT NULL, download_url text NOT NULL, licence text NOT NULL, licence_url text NOT NULL,
 retrieved_at timestamptz NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 row_count integer NOT NULL CHECK(row_count > 0), metrics jsonb NOT NULL,
 UNIQUE(sha256, parser_revision)
);
-- '.' in the APSC tables means nothing published; it is stored as NULL, never as zero.
CREATE TABLE market.aps_agencies (
 snapshot_id uuid NOT NULL REFERENCES market.aps_snapshots(id), row_number integer NOT NULL,
 agency text NOT NULL, agency_key text NOT NULL, portfolio text CHECK(portfolio IS NOT NULL OR ceased),
 is_department_row boolean NOT NULL, ceased boolean NOT NULL,
 ongoing_prev integer, ongoing integer, non_ongoing_prev integer, non_ongoing integer, total_prev integer, total integer,
 by_location jsonb NOT NULL, by_job_family jsonb NOT NULL,
 organisation_id text, match_basis text NOT NULL CHECK(match_basis IN ('exact_name','department_name','ambiguous','unmatched')),
 issues jsonb NOT NULL,
 PRIMARY KEY(snapshot_id, row_number)
);
CREATE INDEX aps_agencies_organisation ON market.aps_agencies(snapshot_id, organisation_id);
CREATE TABLE market.current_aps_snapshot (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), snapshot_id uuid NOT NULL REFERENCES market.aps_snapshots(id)
);
CREATE VIEW market.current_aps_agencies AS
 SELECT a.*, s.as_at, s.prev_as_at FROM market.aps_agencies a
 JOIN market.current_aps_snapshot c ON c.snapshot_id = a.snapshot_id
 JOIN market.aps_snapshots s ON s.id = a.snapshot_id;
''')


def downgrade():
    raise RuntimeError('APS snapshot history must be retained; use a forward migration')
