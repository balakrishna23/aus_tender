#!/bin/bash
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 --set=market_password="$MARKET_DB_PASSWORD" <<'SQL'
CREATE ROLE illoura_market LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'market_password';
ALTER DATABASE illoura_market OWNER TO illoura_market;
REVOKE ALL ON DATABASE illoura_market FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
