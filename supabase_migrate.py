#!/usr/bin/env python3
"""Copy every project database and data file into the Supabase Postgres database.

Databases: each SQLite file becomes one Postgres schema (intelligence.db -> schema intelligence),
with every table, its primary key, its indexes and (where the SQL translates) its views.
SQLite's own full-text index tables are skipped; Postgres searches the source rows directly.
The local assistant's retrieval index (runtime/hybrid.db) goes to schema rag_index, with its
embeddings as pgvector vectors.

Files: every data file (exports, reports, inputs, PDFs, logs, business documents) is stored in
files.documents with its bytes, its extracted text and, for JSON, the parsed value. Each CSV and
Excel sheet also becomes a table of its own in schema files, so it can be queried like the rest.

Never copied: .env, the Google service-account key, the archive's secrets folder, source code,
backups/ (older copies of these same databases) and tmp/ (PDF page previews).

Re-running replaces each table, so this can run nightly. A table is swapped inside one
transaction, so readers see the old copy until the new one is complete.

Needs SUPABASE_DB_URL in .env: a session pooler connection string. The project also hosts the
recruitment website, so this logs in as tender_loader, a role that can create its own schemas
but has no rights on the website's tables (public, drizzle, auth, storage).

Usage:
  python supabase_migrate.py --dry-run            # what would be copied, no connection needed
  python supabase_migrate.py                      # everything
  python supabase_migrate.py --only intelligence aus_tender
  python supabase_migrate.py --skip-files
"""
import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from project_paths import PROJECT_DIR, database_directory

WORK_DIR = PROJECT_DIR.parent
ARCHIVE = WORK_DIR / 'aus_tender_archive_20261006'
ONEDRIVE = Path.home() / 'OneDrive' / 'Desktop' / 'work' / 'Austender Automation'

# (sqlite file, schema). The live databases are added in database_sources().
EXTRA_DATABASES = [
    (PROJECT_DIR / 'local_assistant' / 'runtime' / 'hybrid.db', 'rag_index'),
    *[(path, 'archive_' + path.stem) for path in sorted((ARCHIVE / 'old_data' / 'databases_live').glob('*.db'))],
    (ARCHIVE / 'old_data' / 'buy_ict.db', 'archive_buy_ict_old'),
]
# Embedding columns stored as packed float32, converted to pgvector.
VECTOR_COLUMNS = {('rag_index', 'embeddings', 'vector')}

FILE_FOLDERS = [
    PROJECT_DIR / 'exports', PROJECT_DIR / 'output', PROJECT_DIR / 'inputs', PROJECT_DIR / 'templates',
    PROJECT_DIR / 'docs', PROJECT_DIR / 'config', PROJECT_DIR / 'dashboard', PROJECT_DIR / 'logs',
    PROJECT_DIR / 'local_assistant' / 'inputs', PROJECT_DIR / 'local_assistant' / 'reports',
    ARCHIVE / 'page_dumps', ARCHIVE / 'old_data',
]
SINGLE_FILES = [
    PROJECT_DIR / 'README.md', PROJECT_DIR / 'databases' / 'sheets_registry.json',
    WORK_DIR / 'aus_tender_HANDOFF_20261019.md', WORK_DIR / 'aus_tender_setup_prompt.md',
    WORK_DIR / 'aus_tender_setup_prompt_public.md',
    ONEDRIVE / 'capability statement.pdf', ONEDRIVE / 'Illoura — Final Prompt Pack.pdf',
    ONEDRIVE / 'ILLOURA_AI_REVENUE_ENGINE_Practical_Build_Document.docx',
    ONEDRIVE / 'ipp-companies-active-mmr-contracts-0.xlsx', ONEDRIVE / 'HANDOFF.md',
]
SKIP_DIRS = {'backups', '__pycache__', 'secrets_revoke_me', 'databases_live'}
SKIP_SUFFIXES = {'.py', '.pyc', '.db', '.db-shm', '.db-wal', '.pid', '.tmp', '.partial'}
TEXT_SUFFIXES = {'.csv', '.json', '.txt', '.md', '.log', '.html', '.yaml', '.yml', '.tsv', '.xml'}
TABLE_SUFFIXES = {'.csv', '.xlsx'}
MAX_IDENTIFIER = 63


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_DIR / '.env')
    except ImportError:
        pass


def database_sources():
    live = [(path, schema_name(path.stem)) for path in sorted(database_directory().glob('*.db'))]
    return live + [(path, schema) for path, schema in EXTRA_DATABASES if path.exists()]


def schema_name(text):
    return identifier(text)


def identifier(text, used=None):
    """Lower-case snake case, at most 63 characters, unique within `used`."""
    name = re.sub(r'[^0-9a-zA-Z]+', '_', text).strip('_').lower() or 'column'
    if name[0].isdigit():
        name = '_' + name
    name = name[:MAX_IDENTIFIER]
    if used is not None:
        base, n = name, 2
        while name in used:
            suffix = f'_{n}'
            name, n = base[:MAX_IDENTIFIER - len(suffix)] + suffix, n + 1
        used.add(name)
    return name


# ---------------------------------------------------------------------------------- databases

def open_sqlite(path):
    conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    conn.text_factory = lambda b: b.decode('utf-8', 'replace')
    return conn


def user_tables(conn):
    """Ordinary tables, without SQLite internals and the shadow tables of full-text indexes."""
    rows = conn.execute("select name, sql from sqlite_master where type = 'table' and name not like 'sqlite_%'").fetchall()
    virtual = [name for name, sql in rows if (sql or '').upper().startswith('CREATE VIRTUAL')]
    shadow = {name for name, _ in rows for v in virtual if name.startswith(v + '_')}
    return [name for name, _ in rows if name not in shadow and name not in virtual], virtual


def column_types(conn, table, schema):
    """Postgres type per column, from the values actually stored (SQLite does not enforce types)."""
    columns = [(row[1], row[2], row[5]) for row in conn.execute(f'pragma table_info("{table}")')]
    if not columns:
        return []
    parts = ', '.join(f"""sum(typeof("{c}") = 'text'), sum(typeof("{c}") = 'real'), sum(typeof("{c}") = 'blob'),
                          sum(typeof("{c}") = 'integer')""" for c, _, _ in columns)
    counts = conn.execute(f'select {parts} from "{table}"').fetchone()
    result = []
    for i, (name, declared, pk) in enumerate(columns):
        text, real, blob, integer = (counts[i * 4 + k] or 0 for k in range(4))
        if (schema, table, name) in VECTOR_COLUMNS:
            kind = 'vector'
        elif blob and not (text or real or integer):
            kind = 'bytea'
        elif text or blob:
            kind = 'text'
        elif real:
            kind = 'double precision'
        elif integer:
            kind = 'bigint'
        else:
            kind = declared_type(declared)
        result.append((name, kind, pk))
    return result


def declared_type(declared):
    declared = (declared or '').upper()
    if 'INT' in declared:
        return 'bigint'
    if any(word in declared for word in ('REAL', 'FLOA', 'DOUB', 'NUMERIC', 'DECIMAL')):
        return 'double precision'
    if 'BLOB' in declared:
        return 'bytea'
    return 'text'


def convert(value, kind):
    if value is None:
        return None
    if kind == 'text':
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        return str(value).replace('\x00', '')
    if kind == 'vector':
        floats = struct.unpack(f'<{len(value) // 4}f', value)
        return '[' + ','.join(repr(f) for f in floats) + ']'
    return value


def indexes(conn, table):
    """(name, unique, columns) for indexes created with CREATE INDEX on plain columns."""
    found = []
    for _, name, unique, origin, _ in conn.execute(f'pragma index_list("{table}")'):
        if origin != 'c':
            continue
        columns = [row[2] for row in conn.execute(f'pragma index_info("{name}")')]
        if columns and all(columns):
            found.append((name, bool(unique), columns))
    return found


def plan_database(path, schema):
    with closing(open_sqlite(path)) as conn:
        tables, virtual = user_tables(conn)
        plan = []
        for table in tables:
            rows = conn.execute(f'select count(*) from "{table}"').fetchone()[0]
            plan.append((table, rows, column_types(conn, table, schema), indexes(conn, table)))
        views = conn.execute("select name, sql from sqlite_master where type = 'view'").fetchall()
    return plan, virtual, views


def copy_database(pg, path, schema, plan, views):
    from psycopg import sql
    with pg.cursor() as cur:
        cur.execute(sql.SQL('create schema if not exists {}').format(sql.Identifier(schema)))
    pg.commit()
    with closing(open_sqlite(path)) as conn:
        for table, rows, columns, table_indexes in plan:
            target = identifier(table)
            names = identifier_map([c for c, _, _ in columns])
            started = time.time()
            with pg.transaction(), pg.cursor() as cur:
                qualified = sql.Identifier(schema, target)
                cur.execute(sql.SQL('drop table if exists {} cascade').format(qualified))
                definition = sql.SQL(', ').join(
                    sql.SQL('{} {}').format(sql.Identifier(names[c]), sql.SQL(pg_type(kind, conn, table, c)))
                    for c, kind, _ in columns)
                cur.execute(sql.SQL('create table {} ({})').format(qualified, definition))
                column_list = sql.SQL(', ').join(sql.Identifier(names[c]) for c, _, _ in columns)
                select = 'select ' + ', '.join(f'"{c}"' for c, _, _ in columns) + f' from "{table}"'
                kinds = [kind for _, kind, _ in columns]
                with cur.copy(sql.SQL('copy {} ({}) from stdin').format(qualified, column_list)) as copy:
                    for done, row in enumerate(conn.execute(select), 1):
                        copy.write_row([convert(v, k) for v, k in zip(row, kinds)])
                        if done % 200_000 == 0:
                            print(f'    {table}: {done:,}/{rows:,}', flush=True)
                key = [names[c] for c, _, pk in sorted(columns, key=lambda c: c[2]) if pk]
                if key:
                    constraint(cur, sql.SQL('alter table {} add primary key ({})').format(
                        qualified, sql.SQL(', ').join(map(sql.Identifier, key))), f'{schema}.{target} primary key')
                for name, unique, index_columns in table_indexes:
                    statement = sql.SQL('create {}index {} on {} ({})').format(
                        sql.SQL('unique ' if unique else ''), sql.Identifier(identifier(name)), qualified,
                        sql.SQL(', ').join(sql.Identifier(names[c]) for c in index_columns if c in names))
                    constraint(cur, statement, f'{schema}.{identifier(name)}')
            print(f'  {schema}.{target}: {rows:,} rows in {time.time() - started:.0f}s', flush=True)
    for name, view_sql in views:
        with pg.cursor() as cur:
            try:
                with pg.transaction():
                    cur.execute(sql.SQL('set local search_path to {}, public').format(sql.Identifier(schema)))
                    cur.execute(sql.SQL('drop view if exists {} cascade').format(sql.Identifier(schema, identifier(name))))
                    cur.execute(view_sql)
                print(f'  {schema}.{name}: view created')
            except Exception as error:
                print(f'  {schema}.{name}: view skipped ({str(error).splitlines()[0]})')


def identifier_map(columns):
    used = set()
    return {c: identifier(c, used) for c in columns}


def pg_type(kind, conn, table, column):
    if kind != 'vector':
        return kind
    blob = conn.execute(f'select "{column}" from "{table}" where "{column}" is not null limit 1').fetchone()
    return f'extensions.vector({len(blob[0]) // 4})' if blob else 'text'


def constraint(cur, statement, label):
    """Primary keys and unique indexes SQLite accepted can fail in Postgres (NULLs in a key); keep going."""
    try:
        with cur.connection.transaction():
            cur.execute(statement)
    except Exception as error:
        print(f'    {label} not created: {str(error).splitlines()[0]}')


# -------------------------------------------------------------------------------------- files

def file_sources():
    found = []
    for folder in FILE_FOLDERS:
        if folder.is_dir():
            for path in sorted(folder.rglob('*')):
                if path.is_file() and not SKIP_DIRS & set(path.relative_to(folder).parts[:-1]) \
                        and path.suffix.lower() not in SKIP_SUFFIXES:
                    found.append(path)
    found += [path for path in SINGLE_FILES if path.is_file()]
    return found


def display_path(path):
    for root, label in ((PROJECT_DIR, 'aus_tender'), (WORK_DIR, 'work'), (ONEDRIVE, 'onedrive')):
        try:
            return f'{label}/{path.relative_to(root).as_posix()}'
        except ValueError:
            continue
    return path.as_posix()


def extract_text(path, data):
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return data.decode('utf-8', 'replace').replace('\x00', '')
    if suffix == '.pdf':
        from pypdf import PdfReader
        try:
            return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)
        except Exception as error:
            return f'[text not extracted: {error}]'
    if suffix == '.docx':
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read('word/document.xml').decode('utf-8', 'replace')
        return re.sub(r'<[^>]+>', ' ', xml.replace('</w:p>', '\n')).replace('  ', ' ')
    return None


def sheet_frames(path):
    """{sheet name: DataFrame of strings} for a CSV or Excel file."""
    import pandas as pd
    if path.suffix.lower() == '.csv':
        return {'': pd.read_csv(path, dtype=str, keep_default_na=False, encoding_errors='replace')}
    return {name: frame.fillna('') for name, frame in pd.read_excel(path, sheet_name=None, dtype=str).items()}


def copy_files(pg, paths):
    from psycopg import sql
    from psycopg.types.json import Jsonb
    with pg.transaction(), pg.cursor() as cur:
        cur.execute('create schema if not exists files')
        cur.execute("""create table if not exists files.documents (
            path text primary key, folder text, extension text, size_bytes bigint, modified_at timestamptz,
            sha256 text, text_content text, json_content jsonb, tables text[], content bytea,
            copied_at timestamptz default now())""")
    used = set()
    for path in paths:
        data = path.read_bytes()
        label = display_path(path)
        tables = []
        if path.suffix.lower() in TABLE_SUFFIXES:
            try:
                for sheet, frame in sheet_frames(path).items():
                    stem = label.split('/', 1)[1].rsplit('.', 1)[0] + (f'_{sheet}' if sheet else '')
                    name = identifier(stem, used)
                    load_frame(pg, name, frame)
                    tables.append(f'files.{name}')
            except Exception as error:
                print(f'  {label}: not loaded as a table ({str(error).splitlines()[0]})')
        parsed = None
        if path.suffix.lower() == '.json':
            try:
                parsed = Jsonb(json.loads(data.decode('utf-8', 'replace').replace('\\u0000', '')))
            except ValueError:
                pass
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        with pg.transaction(), pg.cursor() as cur:
            cur.execute("""insert into files.documents
                (path, folder, extension, size_bytes, modified_at, sha256, text_content, json_content, tables, content)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (path) do update set folder = excluded.folder, extension = excluded.extension,
                  size_bytes = excluded.size_bytes, modified_at = excluded.modified_at, sha256 = excluded.sha256,
                  text_content = excluded.text_content, json_content = excluded.json_content,
                  tables = excluded.tables, content = excluded.content, copied_at = now()""",
                (label, label.rsplit('/', 1)[0], path.suffix.lower().lstrip('.'), len(data), modified,
                 hashlib.sha256(data).hexdigest(), extract_text(path, data), parsed, tables, data))
        print(f'  {label}' + (f' -> {", ".join(tables)}' if tables else ''), flush=True)


def load_frame(pg, name, frame):
    from psycopg import sql
    names = identifier_map([str(c) for c in frame.columns])
    qualified = sql.Identifier('files', name)
    with pg.transaction(), pg.cursor() as cur:
        cur.execute(sql.SQL('drop table if exists {} cascade').format(qualified))
        cur.execute(sql.SQL('create table {} ({})').format(qualified, sql.SQL(', ').join(
            sql.SQL('{} text').format(sql.Identifier(names[str(c)])) for c in frame.columns)))
        with cur.copy(sql.SQL('copy {} from stdin').format(qualified)) as copy:
            for row in frame.itertuples(index=False):
                copy.write_row([str(v).replace('\x00', '') for v in row])


# --------------------------------------------------------------------------------------- main

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true', help='Show what would be copied; no connection')
    parser.add_argument('--only', nargs='+', metavar='SCHEMA', help='Copy only these database schemas')
    parser.add_argument('--skip-files', action='store_true', help='Copy databases only')
    parser.add_argument('--skip-databases', action='store_true', help='Copy files only')
    args = parser.parse_args(argv)
    load_env()

    sources = [(p, s) for p, s in database_sources() if not args.only or s in args.only]
    pg = None
    if not args.dry_run:
        url = os.environ.get('SUPABASE_DB_URL')
        if not url:
            print('SUPABASE_DB_URL is not set in .env (Supabase dashboard > Connect > Session pooler).')
            return 1
        import psycopg
        pg = psycopg.connect(url, autocommit=True)
        with pg.cursor() as cur:
            cur.execute("select 1 from pg_extension where extname = 'vector'")
            if not cur.fetchone():
                cur.execute('create extension vector with schema extensions')

    total_rows = 0
    if not args.skip_databases:
        for path, schema in sources:
            plan, virtual, views = plan_database(path, schema)
            rows = sum(r for _, r, _, _ in plan)
            total_rows += rows
            print(f'{schema} <- {display_path(path)} ({path.stat().st_size / 1e6:,.0f} MB): '
                  f'{len(plan)} tables, {rows:,} rows, {len(views)} views'
                  + (f'; full-text indexes skipped: {", ".join(virtual)}' if virtual else ''), flush=True)
            if args.dry_run:
                for table, count, columns, _ in plan:
                    print(f'  {table}: {count:,} rows, ' + ', '.join(f'{c} {k}' for c, k, _ in columns)[:300])
            else:
                copy_database(pg, path, schema, plan, views)

    if not args.skip_files and not args.only:
        paths = file_sources()
        size = sum(p.stat().st_size for p in paths)
        print(f'files: {len(paths)} files, {size / 1e6:,.0f} MB', flush=True)
        if args.dry_run:
            for path in paths:
                print(f'  {display_path(path)}')
        else:
            copy_files(pg, paths)

    print(f'Done: {total_rows:,} database rows' + (' (dry run, nothing copied)' if args.dry_run else ''))
    if pg:
        pg.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
