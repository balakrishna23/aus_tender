"""Append-only refresh evidence and current informational freshness."""
from alembic import op

revision = '023_research_freshness'
down_revision = '022_aged_care'


def upgrade():
    op.execute('''
CREATE TABLE market.research_refresh_attempts (
 id bigserial PRIMARY KEY, run_id uuid NOT NULL, source text NOT NULL,
 phase text NOT NULL CHECK(phase IN ('received','started','acquired','finished')),
 observed_at timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL
);
CREATE INDEX research_refresh_attempt_source ON market.research_refresh_attempts(source,id DESC);
CREATE INDEX research_refresh_attempt_run ON market.research_refresh_attempts(run_id,source,phase);
CREATE FUNCTION market.research_refresh_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'Research refresh attempts are append-only'; END $$;
CREATE TRIGGER research_refresh_attempt_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
 ON market.research_refresh_attempts FOR EACH STATEMENT EXECUTE FUNCTION market.research_refresh_immutable();
CREATE TABLE market.research_freshness (
 source text PRIMARY KEY, checked_at timestamptz NOT NULL,
 last_success_at timestamptz, status text NOT NULL CHECK(status IN
 ('latest_available','new_edition_pending','check_overdue','blocked','failed','manual_refresh_needed')),
 details jsonb NOT NULL
);
CREATE VIEW market.v_research_freshness AS
 SELECT source,CASE
 WHEN status='latest_available' AND details->>'automation'='manual'
  AND COALESCE(last_success_at,checked_at) + (details->>'check_interval_days')::integer * interval '1 day' <= now()
  THEN 'manual_refresh_needed'
 WHEN status='latest_available' AND details->>'automation'<>'manual'
  AND checked_at + (details->>'check_interval_days')::integer * interval '1 day' <= now()
  THEN 'check_overdue'
 ELSE status END AS status,checked_at,last_success_at,details->>'reason' AS reason,
 details->>'available_edition' AS available_edition,details->>'imported_edition' AS imported_edition,
 details->>'sha256' AS sha256 FROM market.research_freshness;
''')


def downgrade():
    raise RuntimeError('Append-only refresh evidence must be retained')
