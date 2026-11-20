"""Page-cited RAP extraction, exact usage/cost and named review history."""
from alembic import op
revision='019_rap_extraction'
down_revision='018_research_views'


def upgrade():
    op.execute('''
CREATE TABLE market.rap_extraction_runs (
 id uuid PRIMARY KEY,document_sha256 text NOT NULL,pages_sha256 text NOT NULL,provider text NOT NULL,model text NOT NULL,
 config_sha256 text NOT NULL,prompt_version text NOT NULL,status text NOT NULL
 CHECK(status IN ('running','needs_review','approved','rejected','failed')),
 organisation text NOT NULL,source_url text NOT NULL,licence text NOT NULL,pages jsonb NOT NULL,
 fields jsonb,input_tokens integer CHECK(input_tokens>=0),output_tokens integer CHECK(output_tokens>=0),cost_usd numeric CHECK(cost_usd>=0),
 cost_status text NOT NULL CHECK(cost_status IN ('not_requested','known','unknown')),
 pricing jsonb NOT NULL,provenance jsonb NOT NULL,provider_request_id text,started_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,error_code text,
 UNIQUE(document_sha256,pages_sha256,provider,model,config_sha256,prompt_version),
 CHECK(document_sha256 ~ '^[0-9a-f]{64}$' AND pages_sha256 ~ '^[0-9a-f]{64}$'),
 CHECK((cost_status='known' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cost_usd IS NOT NULL)
       OR (cost_status IN ('unknown','not_requested') AND cost_usd IS NULL))
);
CREATE TABLE market.rap_extraction_reviews (
 id bigserial PRIMARY KEY,run_id uuid NOT NULL REFERENCES market.rap_extraction_runs(id),
 decision text NOT NULL CHECK(decision IN ('approved','rejected')),reviewer uuid NOT NULL,
 document_sha256 text NOT NULL CHECK(document_sha256 ~ '^[0-9a-f]{64}$'),notes text NOT NULL CHECK(notes=''),reviewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE VIEW market.current_approved_rap_extractions AS
 SELECT DISTINCT ON(r.document_sha256) r.* FROM market.rap_extraction_runs r
 JOIN LATERAL (SELECT decision,document_sha256 FROM market.rap_extraction_reviews
               WHERE run_id=r.id ORDER BY reviewed_at DESC,id DESC LIMIT 1) approval
 ON approval.decision='approved' AND approval.document_sha256=r.document_sha256
 WHERE r.status='approved' AND r.prompt_version='rap-procurement-2'
 AND r.provenance->'privacy_review'->>'contains_personal_data'='false'
 ORDER BY r.document_sha256,r.finished_at DESC,r.id;
''')


def downgrade():raise RuntimeError('RAP extraction and cost history must be retained')
