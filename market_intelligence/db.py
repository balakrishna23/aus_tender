"""PostgreSQL connections and Alembic migrations; no SQLite fallback."""
import os
from pathlib import Path

from sqlalchemy import create_engine


def engine(url=None):
    value = url or os.environ.get('ILLOURA_MARKET_DATABASE_URL')
    if not value:
        raise ValueError('Set ILLOURA_MARKET_DATABASE_URL in the private environment')
    result = create_engine(value, hide_parameters=True, pool_pre_ping=True)
    if result.dialect.name != 'postgresql':
        result.dispose()
        raise ValueError('The market intelligence database must be PostgreSQL')
    return result


def migrate(db_engine, revision='head'):
    from alembic import command
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).parent/'migrations'))
    with db_engine.begin() as connection:
        connection.exec_driver_sql('SELECT pg_advisory_xact_lock(191126001)')
        cfg.attributes['connection'] = connection
        command.upgrade(cfg, revision)
