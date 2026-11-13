"""Rebuild local daily evidence after all source stages finish. Never sends email."""
import argparse,json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path

BASE=Path(__file__).resolve().parents[1];PROJECT=Path(r'/opt/aus_tender')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    state={'started_at_utc':datetime.now(timezone.utc).isoformat(),'steps':[],'sent':False}
    def save(status):
        state.update(status=status,updated_at_utc=datetime.now(timezone.utc).isoformat())
        (out/'finalization.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
    stop=time.monotonic()+7200
    while True:
        try:report=json.loads((out/'source_refresh.json').read_text(encoding='utf-8'))
        except (FileNotFoundError,json.JSONDecodeError):report={}
        # The runner writes its eighth result before rebuilding derived views.
        # Its final feed field is written only after that rebuild finishes.
        if len(report.get('results',[]))==8 and 'feed' in report:break
        save('waiting for source sweep')
        if time.monotonic()>stop:save('source wait limit reached');return 1
        time.sleep(20)
    state['source_success']=report.get('success')
    for name,command,cwd,limit in [
        ('intelligence',[sys.executable,'-u','build_intelligence.py'],PROJECT,900),
        ('data_quality',[sys.executable,'-u','audit_data.py'],PROJECT/'local_assistant',300),
        ('hybrid_index',[sys.executable,'-u','build_hybrid_index.py'],PROJECT/'local_assistant',1200),
        ('newsletter_evidence',[sys.executable,'-u',str(BASE/'scripts/prepare_newsletter_evidence.py'),'--out',str(out)],BASE,300),
    ]:
        if name == 'hybrid_index' and (PROJECT/'local_assistant/LOCAL_AI_DISABLED').exists():
            state['steps'].append({'step':name,'exit_code':0,'status':'skipped','reason':'Local AI disabled by user'})
            continue
        if name == 'intelligence' and '; intelligence views rebuilt' in report.get('feed', ''):
            state['steps'].append({'step': name, 'exit_code': 0, 'completed_by': 'source runner'})
            continue
        save('running '+name)
        try:
            with (out/(name+'.log')).open('w',encoding='utf-8') as log:
                result=subprocess.run(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=limit)
            state['steps'].append({'step':name,'exit_code':result.returncode})
        except Exception as exc:state['steps'].append({'step':name,'error':str(exc)})
        print(json.dumps(state['steps'][-1]),flush=True)
    save('ready for editorial review' if all(x.get('exit_code')==0 for x in state['steps']) else 'finished with local build errors')
    return 0


if __name__=='__main__':raise SystemExit(main())
