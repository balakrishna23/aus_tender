"""Verified panel details separate from unverified notice dates."""
from alembic import op

revision='013_panel_evidence'
down_revision='012_demand'


def upgrade():
    op.execute('''
CREATE TABLE market.panel_evidence (
 son_id text PRIMARY KEY REFERENCES market.panel_watchlist(son_id), facts jsonb NOT NULL,
 source_url text NOT NULL, verified_at timestamptz NOT NULL, verified_by text NOT NULL,
 content_sha256 text NOT NULL CHECK(length(content_sha256)=64), recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE market.panel_evidence_events (
 id bigserial PRIMARY KEY, son_id text NOT NULL REFERENCES market.panel_watchlist(son_id),
 old_evidence jsonb, new_evidence jsonb NOT NULL, changed_by text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
);
''')


def downgrade():raise RuntimeError('Panel evidence history must be retained')
