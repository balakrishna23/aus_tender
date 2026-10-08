"""Create a recovery snapshot after the completed refresh; no retention deletes."""
import sqlite3,json
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

source_root=Path(r'C:\work\aus_tender\databases').resolve()
backup_root=Path(r'C:\work\aus_tender\backups').resolve()
snapshot=backup_root/('pilot-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-UTC'))
snapshot.mkdir(parents=True,exist_ok=False)
records=[]
for source in source_root.glob('*.db'):
    target=(snapshot/source.name).resolve()
    if not target.is_relative_to(backup_root) or not source.resolve().is_relative_to(source_root):
        raise ValueError('Snapshot path escaped the specified database/backup folders')
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            checked=dst.execute('PRAGMA quick_check').fetchone()[0]
            if checked!='ok':raise ValueError(source.name+': snapshot integrity check failed')
    records.append({'database':source.name,'bytes':target.stat().st_size,'quick_check':'ok'})
    print(source.name+': copied and checked',flush=True)
manifest={'snapshot_directory':str(snapshot),'created_at_utc':datetime.now(timezone.utc).isoformat(),'databases':records}
(snapshot/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
reports=Path(__file__).parent/'reports';reports.mkdir(exist_ok=True)
(reports/'snapshot.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print('Recovery snapshot complete:',snapshot)
