"""Run existing collection/evidence, then recoverable atomic publication. No send."""
import json
from pathlib import Path
import subprocess
import sys
from illoura_cloud_ops import atomic_json,exclusive,utc,backup_operations,publish,restore_drill

WORK=Path('/opt/illoura-routine');OPS=Path('/opt/illoura-ops')

def run(command):subprocess.run(command,check=True)

if __name__=='__main__':
    state={'started_at_utc':utc(),'status':'running','mail_sending_enabled':False}
    with exclusive(WORK/'coordinator.lock'):
        atomic_json(WORK/'coordinator_status.json',state)
        try:
            run(['runuser','-u','scraper','--','/opt/aus_tender/.venv/bin/python',str(WORK/'run_once.py')])
            # Hold all publication-side locks continuously across backup and publish.
            # Only this coordinator is a supported cloud write entry point. A source
            # CLI invoked independently by an administrator is outside that contract;
            # publication still checks intelligence against the completed evidence run.
            with exclusive(WORK/'operations.lock'),exclusive(WORK/'routine.lock'),exclusive(Path('/opt/aus_tender/logs/.run.lock')):
                backup=backup_operations(Path('/opt/aus_tender'),WORK,Path('/var/backups/illoura'))
                recovery=restore_drill(backup)
                if recovery['unresolved_send_runs']:raise RuntimeError('Unresolved sends require reconciliation')
                completed=json.loads((WORK/'latest_run.json').read_text())
                publication=publish(Path('/opt/aus_tender/databases'),Path('/srv/illoura-data'),Path(completed['run_dir']),json.loads((OPS/'policy.json').read_text()),'illoura-api',backups=Path('/var/backups/illoura'))
                atomic_json(WORK/'publication.json',{'status':'published','generation_id':publication['generation_id'],'snapshot_finished_utc':publication['snapshot_finished_utc'],'retention':publication['retention'],'retention_attention':publication['retention_attention']})
            phase = 'completed' if publication['retention']['status']=='ok' else 'completed_with_retention_attention'
            state.update(status=phase,finished_at_utc=utc(),editorial_review_required=True,published=True)
        except BaseException as error:
            state.update(status='failed',finished_at_utc=utc(),error_type=type(error).__name__)
            raise
        finally:atomic_json(WORK/'coordinator_status.json',state)
    print(json.dumps(state))
