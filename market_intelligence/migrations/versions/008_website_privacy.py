"""Remove contact strings embedded in website fields in earlier derived records."""
from alembic import op

revision='008_website_privacy'
down_revision='007_query_indexes'


def upgrade():
    op.execute(r'''
CREATE TABLE market.privacy_redactions (
 migration text PRIMARY KEY,affected_records integer NOT NULL,applied_at timestamptz NOT NULL DEFAULT now(),
 scope text NOT NULL
);
WITH changed AS (
 UPDATE market.observations SET payload=jsonb_set(jsonb_set(payload,'{website}','""'::jsonb,false),'{Website Address}','""'::jsonb,false),
 issues=issues||'["legacy_website_contact_omitted"]'::jsonb
 WHERE COALESCE(payload->>'website','') ~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+'
 OR COALESCE(payload->>'Website Address','') ~ '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+'
 RETURNING 1
)
INSERT INTO market.privacy_redactions(migration,affected_records,scope)
 SELECT '008_website_privacy',count(*),'Derived website fields only; restricted original source files and checksums retained' FROM changed;
''')


def downgrade():
    raise RuntimeError('Redacted contact data must not be restored to derived query records')
