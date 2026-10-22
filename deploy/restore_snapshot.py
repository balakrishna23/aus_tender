#!/usr/bin/env python3
"""Restore a data snapshot (deploy/package_snapshot.py) into this checkout.

Copies databases/, inputs/, local_assistant/inputs/, exports/, dashboard/, the assistant's search index,
the capability statements and business_docs/ out of the zip. Code files in the zip are ignored: the
checkout's code (git) is what runs. Existing databases are kept unless --force is given.

  python deploy/restore_snapshot.py ../aus_tender_public_data_YYYYMMDD.zip   # public, from GitHub Releases
  python deploy/restore_snapshot.py ../aus_tender_snapshot_YYYYMMDD.zip --force   # full, shared privately
"""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA_PREFIXES = ('databases/', 'inputs/', 'local_assistant/inputs/', 'exports/', 'dashboard/', 'business_docs/',
                 'local_assistant/runtime/hybrid.db', 'templates/capability_statement')


def restore(zip_path, project=PROJECT, force=False):
    restored, kept = [], []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir() or '/' not in info.filename:
                continue
            rel = info.filename.split('/', 1)[1]
            if '..' in Path(rel).parts or not rel.startswith(DATA_PREFIXES):
                continue
            target = project / rel
            if target.exists() and target.suffix == '.db' and not force:
                kept.append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            restored.append(rel)
    return restored, kept


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('zip', type=Path)
    parser.add_argument('--force', action='store_true', help='Overwrite databases that already exist here')
    args = parser.parse_args(argv)
    restored, kept = restore(args.zip, force=args.force)
    print(f'Restored {len(restored)} files into {PROJECT}')
    if kept:
        print(f'Kept {len(kept)} existing databases (use --force to overwrite): ' + ', '.join(kept), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
