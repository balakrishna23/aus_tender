#!/usr/bin/env python3
"""Build ../aus_tender_snapshot_<date>.zip: everything a team member needs that git does not hold.

Inside (under aus_tender/): the committed code (git archive HEAD), a consistent copy of every database
(SQLite backup API, safe while a spider writes), the Sheets registry, hand-reviewed inputs, exports,
the dashboard, the assistant's search index and, with --docs, a folder of business documents.

Never inside: .env, the Google service-account key, backups/, logs/, tmp/, output/, the Ollama runtime,
and sheet_imports.db (a copy of our own Google Sheets that the assistant rebuilds).

  python deploy/package_snapshot.py
  python deploy/package_snapshot.py --note HANDOFF.md --docs "C:/path/to/business documents"
Restore on another PC with deploy/restore_snapshot.py.
"""
import argparse
import io
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


def is_secret(name):
    lower = name.lower()
    return any(m in lower for m in SECRET_MARKERS) and not lower.endswith('.env.example')


def snapshot_db(source, destination):
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True)) as src, \
            closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def build(out, note=None, docs=None, project=PROJECT, databases=None):
    databases = databases or database_directory()
    names = []
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z, \
            tempfile.TemporaryDirectory() as staging:
        def add(path, arcname):
            if is_secret(arcname):
                raise SystemExit(f'Refusing to pack a secret: {arcname}')
            z.write(path, f'{ROOT}/{arcname}')
            names.append(arcname)

        if note:
            add(Path(note), 'HANDOFF.md')
        archive = subprocess.run(['git', '-C', str(project), 'archive', '--format=tar', 'HEAD'],
                                 capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    z.writestr(f'{ROOT}/{member.name}', tar.extractfile(member).read())
                    names.append(member.name)
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
            if source.name in SKIP_DATABASES:
                continue
            copy = Path(staging) / source.name
            snapshot_db(source, copy)
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
    parser.add_argument('--out', type=Path, default=PROJECT.parent / f'aus_tender_snapshot_{date.today():%Y%m%d}.zip')
    parser.add_argument('--note', help='A handoff note to put at the top of the zip as HANDOFF.md')
    parser.add_argument('--docs', help='A folder of business documents to add under business_docs/')
    args = parser.parse_args(argv)
    names = build(args.out, args.note, args.docs)
    print(f'{len(names)} files, {args.out.stat().st_size / 1e6:,.0f} MB: {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
