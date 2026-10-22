#!/usr/bin/env python3
"""Build a data snapshot zip: what a fresh clone needs that git does not hold.

Full (team) snapshot, ../aus_tender_snapshot_<date>.zip, under aus_tender/:
  the committed code (git archive HEAD), a consistent copy of every database (SQLite backup API,
  safe while a spider writes), the Sheets registry, hand-reviewed inputs, exports, the dashboard,
  the assistant's search index and, with --docs, a folder of business documents.
  Private: it holds personal contact details and Illoura's internal plans.

Public snapshot (--public), ../aus_tender_public_data_<date>.zip:
  only the public-source databases (AusTender contracts, notices, tenders, panels, plans, BuyICT, WA
  state contracts, the NIAA MMR list), with personal details removed: contact name/email/phone columns
  are blanked, contact fields inside the stored page/API JSON are dropped, and any email address or
  Australian phone number left in free text is redacted. Column layouts are unchanged, so the scrapers
  and `build_intelligence.py` run on it as they do on the full data. Not included: engine views
  (rebuilt locally), supplier contacts, actions and plans, logs, NSW and ACT (licence terms not cleared
  for republishing; the spiders fetch them), inputs, exports and business documents.

Never inside either: .env, the Google service-account key, backups/, logs/, tmp/, output/, the Ollama
runtime, and sheet_imports.db (a copy of our own Google Sheets).

  python deploy/package_snapshot.py
  python deploy/package_snapshot.py --note HANDOFF.md --docs "C:/path/to/business documents"
  python deploy/package_snapshot.py --public
Restore on another PC with deploy/restore_snapshot.py.
"""
import argparse
import io
import json
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from project_paths import database_directory  # noqa: E402

ROOT = 'aus_tender'
SKIP_DATABASES = {'sheet_imports.db'}
DATA_DIRS = ['inputs', 'local_assistant/inputs', 'exports', 'dashboard']
DATA_FILES = ['templates/capability_statement.pdf', 'templates/capability_statement_original.pdf',
              'local_assistant/runtime/hybrid.db']
DOC_SUFFIXES = {'.pdf', '.docx', '.xlsx'}
SECRET_MARKERS = ('.env', 'service_account', 'dazzling-tiger', '.pem', '.key')

PUBLIC_DATABASES = {'contract_api.db', 'aus_contract.db', 'aus_tender.db', 'standing_offer.db',
                    'planned_procurement.db', 'buy_ict.db', 'state_contracts.db', 'organisations.db'}
PERSONAL_COLUMNS = re.compile(r'contact_name|contact_email|contact_phone|contact_mobile|email_address|buyer_contact')
JSON_COLUMNS = {'raw_json', 'all_details', 'main_details'}
# Review decisions and notes are Illoura's own work, not public data.
INTERNAL_COLUMNS = {('mmr_matches', 'review'), ('mmr_matches', 'reviewed_at'), ('mmr_matches', 'note')}
# Identifiers, links and keys are never redacted: a digit run in an ID can look like a phone number.
ID_LIKE = re.compile(r'(^|_)(id|ids|url|urls|link|hash|abn|key|uri|ocid)$|reference', re.I)
CONTACT_KEY = re.compile(r'contact', re.I)
KEEP_KEY = re.compile(r'branch|division|postcode|web address', re.I)
EMAIL = re.compile(r'[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)+')
PHONE = re.compile(r'(?<![\d/])(?:\(0[2-478]\)\s?\d{4}[\s\-]?\d{4}|(?:\+61[\s\-]?|0)[2-478](?:[\s\-]?\d){8})(?![\d/])')
PUBLIC_NOTICE = """# Public data snapshot

Public-source data collected by https://github.com/balakrishna23/aus_tender, with personal details removed.
Restore it into a clone with `python deploy/restore_snapshot.py <this zip>`, then rebuild the engine views
with `python build_intelligence.py`. See docs/SETUP_AND_OPERATIONS.md.

## Sources and licences
- AusTender (contract notices via the official OCDS API, approaches to market, standing offers, annual
  procurement plans): Department of Finance, CC BY 3.0 AU. Credit: AusTender.
- BuyICT opportunities: Digital Transformation Agency. Company-level fields only; personal information removed.
- WA contract awards: Tenders WA open data, Department of Treasury and Finance WA, CC BY 4.0.
- MMR list: National Indigenous Australians Agency (NIAA), IPP companies with active MMR contracts.

## Removed
Contact names, emails and phone numbers (columns blanked, JSON contact fields dropped, emails and
Australian phone numbers in free text redacted); supplier contact research; Illoura's engine outputs,
actions and review decisions; NSW and ACT data (run their spiders to collect them).
Data is as published by the sources at the snapshot date and may since have been amended.
"""


def is_secret(name):
    lower = name.lower()
    return any(m in lower for m in SECRET_MARKERS) and not lower.endswith('.env.example')


def snapshot_db(source, destination):
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def redact(text):
    if not isinstance(text, str):
        return text
    return PHONE.sub('[phone removed]', EMAIL.sub('[email removed]', text))


def scrub_value(value):
    """JSON value without contact keys (branch/division/postcode kept); free text redacted."""
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items() if not (CONTACT_KEY.search(k) and not KEEP_KEY.search(k))}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    return redact(value)


def scrub_json(text):
    if not isinstance(text, str) or not text.strip():
        return text
    try:
        parsed = json.loads(text)
    except ValueError:
        return redact(text)
    return json.dumps(scrub_value(parsed), ensure_ascii=False, separators=(',', ':'))


def scrub_database(path):
    """Remove personal and internal data in place; table and column layout stay the same."""
    with closing(sqlite3.connect(path)) as conn:
        conn.create_function('redact', 1, redact, deterministic=True)
        conn.create_function('scrub_json', 1, scrub_json, deterministic=True)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                            "AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            unique = {info[2] for index in conn.execute(f'PRAGMA index_list("{table}")').fetchall() if index[2]
                      for info in conn.execute(f'PRAGMA index_info("{index[1]}")').fetchall()}
            for _, column, kind, _, _, pk in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
                if PERSONAL_COLUMNS.fullmatch(column) or (table, column) in INTERNAL_COLUMNS:
                    sql = f'UPDATE "{table}" SET "{column}" = NULL'
                elif column in JSON_COLUMNS:
                    sql = f'UPDATE "{table}" SET "{column}" = scrub_json("{column}") WHERE "{column}" IS NOT NULL'
                elif pk or column in unique or ID_LIKE.search(column):
                    continue
                elif kind.upper() in ('TEXT', '') or 'CHAR' in kind.upper():
                    sql = (f'UPDATE "{table}" SET "{column}" = redact("{column}") '
                           f'WHERE "{column}" IS NOT NULL AND redact("{column}") IS NOT "{column}"')
                else:
                    continue
                conn.execute(sql)
        conn.commit()
        conn.execute('VACUUM')


def build(out, note=None, docs=None, project=PROJECT, databases=None, public=False):
    databases = databases or database_directory()
    if public and docs:
        raise SystemExit('Business documents are never part of a public snapshot')
    names = []
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z, \
            tempfile.TemporaryDirectory() as staging:
        def add(path, arcname):
            if is_secret(arcname):
                raise SystemExit(f'Refusing to pack a secret: {arcname}')
            z.write(path, f'{ROOT}/{arcname}')
            names.append(arcname)

        if public:
            z.writestr(f'{ROOT}/DATA_README.md', PUBLIC_NOTICE)
            names.append('DATA_README.md')
        if note:
            add(Path(note), 'HANDOFF.md')
        archive = subprocess.run(['git', '-C', str(project), 'archive', '--format=tar', 'HEAD'],
                                 capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    z.writestr(f'{ROOT}/{member.name}', tar.extractfile(member).read())
                    names.append(member.name)
        if not public:
            for folder in DATA_DIRS:
                for path in sorted((project / folder).rglob('*')):
                    if path.is_file() and '__pycache__' not in path.parts:
                        add(path, path.relative_to(project).as_posix())
            for rel in DATA_FILES:
                if (project / rel).is_file():
                    add(project / rel, rel)
            for path in sorted(databases.glob('sheets_registry*.json')):
                add(path, f'databases/{path.name}')
        for source in sorted(databases.glob('*.db')):
            if source.name in SKIP_DATABASES or (public and source.name not in PUBLIC_DATABASES):
                continue
            copy = Path(staging) / source.name
            snapshot_db(source, copy)
            if public:
                scrub_database(copy)
            add(copy, f'databases/{source.name}')
            copy.unlink()
            print(f'packed {source.name}', flush=True)
        if docs:
            for path in sorted(Path(docs).iterdir()):
                if path.is_file() and path.suffix.lower() in DOC_SUFFIXES:
                    add(path, f'business_docs/{path.name}')
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
    if bad:
        raise SystemExit(f'Zip check failed at {bad}')
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--public', action='store_true', help='Public-source data only, personal details removed')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--note', help='A handoff note to put at the top of the zip as HANDOFF.md')
    parser.add_argument('--docs', help='A folder of business documents to add under business_docs/')
    args = parser.parse_args(argv)
    kind = 'public_data' if args.public else 'snapshot'
    out = args.out or PROJECT.parent / f'aus_tender_{kind}_{date.today():%Y%m%d}.zip'
    names = build(out, args.note, args.docs, public=args.public)
    print(f'{len(names)} files, {out.stat().st_size / 1e6:,.0f} MB: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
