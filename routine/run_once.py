"""Run the source and evidence phases on AWS; requires reviewed editorial before mail."""
import json,os,signal,sqlite3,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
PROJECT=Path('/opt/aus_tender');WORK=Path('/opt/illoura-routine');PY=PROJECT/'.venv/bin/python'
SPIDER_TIMEOUT_SECONDS = 2700  # Bound one source attempt to 45 minutes.
RESEARCH_TIMEOUT_SECONDS = 3600  # Supplemental research gets at most one hour.
FINALIZATION_TIMEOUT_SECONDS = 2700  # Reserve 45 minutes for mandatory evidence.
SOURCE_TIMEOUT_SECONDS = 3600  # Sequential sources must leave time for evidence and a morning retry.
ROUTINE_BUDGET_SECONDS = 6300  # Sources/research plus evidence fit in 105 minutes.


SOURCES = {'contract_api', 'aus_contract', 'aus_tender', 'planned_procurement', 'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender'}
BRIEF_SOURCES = {'buy_ict'}  # The daily brief is built from BuyICT only.


def source_gate(report, exit_code):
    """(may continue, sources with a problem).

    A clean report passes. A report with failed sources passes only when all eight sources
    reported, the collection as a whole was not cut short, the runner itself says the batch
    failed (success is false, not missing), nothing else failed (the feed and view rebuild)
    and no source the brief depends on is among them: one unrelated site must not cancel the
    day. A single unrelated source that timed out is such a problem. Anything unclear stays closed.
    """
    if not isinstance(report, dict):
        return False, []
    results = report.get('results', [])
    feed = report.get('feed')
    if (not isinstance(results, list) or len(results) != 8
            or not all(isinstance(r, dict) for r in results)
            or {r.get('spider') for r in results} != SOURCES
            or not isinstance(feed, str) or not feed.strip() or 'FAILED' in feed):
        return False, []
    gaps = sorted(r['spider'] for r in results if r.get('status') != 'OK' or r.get('crawl_failed') or r.get('errors'))
    if not gaps:
        return exit_code == 0 and report.get('success') is True, []
    if exit_code not in (0, 1) or report.get('success') is not False or BRIEF_SOURCES & set(gaps):
        return False, []
    return True, gaps


def bounded_process(command, timeout, **kwargs):
    process = subprocess.Popen(command, start_new_session=os.name != 'nt', **kwargs)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        raise


def kill_tree(process):
    if os.name == 'nt':
        process.kill()
    else:
        # run_spiders starts new sessions: include those descendant groups too.
        parents = {}
        for entry in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = entry.read_text().rsplit(')', 1)[1].split()
                parents[int(entry.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
        descendants = {process.pid}
        while True:
            found = {pid for pid, parent in parents.items() if parent in descendants}
            if found <= descendants:
                break
            descendants.update(found)
        groups = {process.pid}
        for pid in descendants:
            try:
                groups.add(os.getpgid(pid))
            except ProcessLookupError:
                pass
        groups.discard(os.getpgrp())
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait()


def read_report(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}  # A killed writer can leave a partial report; preserve the failure gate.


def routine_deadline(env, monotonic, wall):
    """Monotonic deadline: the routine budget, shortened by the coordinator's morning deadline.

    The morning deadline is an epoch time in ILLOURA_MORNING_DEADLINE; an unreadable value
    must not cancel the morning, so it falls back to the normal budget.
    """
    deadline = monotonic + ROUTINE_BUDGET_SECONDS
    try:
        morning = float(env.get('ILLOURA_MORNING_DEADLINE') or '')
    except ValueError:
        return deadline
    if morning != morning or morning in (float('inf'), float('-inf')):
        return deadline
    return min(deadline, monotonic + morning - wall)


def main():
    import fcntl
    deadline = routine_deadline(os.environ, time.monotonic(), time.time())
    os.environ.update(GSHEETS_AUTO_SYNC='False',AUSTENDER_DB_DIR=str(PROJECT/'databases'),PLAYWRIGHT_BROWSERS_PATH=str(PROJECT/'.browsers'),PYTHONDONTWRITEBYTECODE='1')
    with (WORK/'routine.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        now=datetime.now(timezone.utc)
        out=WORK/'reports'/('daily_sweep_'+now.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%Y%m%d_%H%M%S'))
        out.mkdir(parents=True)
        baseline={}
        with sqlite3.connect((PROJECT/'databases/contract_api.db').as_uri()+'?mode=ro',uri=True) as db:
            for table in ['contracts','contract_versions']:baseline[table]=db.execute('SELECT count(*) FROM '+table).fetchone()[0]
        (out/'baseline.json').write_text(json.dumps(baseline,indent=2))
        with sqlite3.connect((PROJECT/'databases/buy_ict.db').as_uri()+'?mode=ro',uri=True) as db:ids=[r[0] for r in db.execute('SELECT rfq_id FROM buy_ict_tenders')]
        (out/'baseline_buyict_ids.json').write_text(json.dumps(ids))
        state={'run_dir':str(out),'execution_host':'AWS Sydney Lightsail','started_at_utc':now.isoformat(),'authorization':'Source/evidence collection only; newsletter requires a separately reviewed send','status':'running_sources','sent':False}
        def save():
            (out/'cloud_run.json').write_text(json.dumps(state,indent=2));(WORK/'latest_run.json').write_text(json.dumps(state,indent=2))
        save();print(json.dumps(state),flush=True)
        def run(name,args,cwd,limit):
            with (out/(name+'.console.log')).open('w') as log:
                remaining = min(limit, deadline - time.monotonic())
                try:
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, 0)
                    return bounded_process([str(PY),'-u',*args], remaining, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
                except subprocess.TimeoutExpired:
                    print(name + ' deadline exceeded', file=log)
                    return 124
        with ThreadPoolExecutor(max_workers=2) as pool:
            source=pool.submit(run,'source',['run_spiders.py','--timeout',str(SPIDER_TIMEOUT_SECONDS),'--retries','1','--no-sync','--report',str(out/'source_refresh.json')],PROJECT,SOURCE_TIMEOUT_SECONDS)
            research=pool.submit(run,'research',[str(WORK/'scripts/newsletter_research_sweep.py'),'--out',str(out)],WORK,RESEARCH_TIMEOUT_SECONDS)
            state['source_exit_code']=source.result();state['research_exit_code']=research.result()
        report=read_report(out/'source_refresh.json')
        proceed,gaps=source_gate(report,state['source_exit_code'])
        if gaps:state['source_gaps']=gaps
        if not proceed:
            state['status']='source_failure';save();raise SystemExit(1)
        state['status']='building_evidence';save()
        state['finalization_exit_code']=run('finalization',[str(WORK/'scripts/finalize_daily_sweep.py'),'--out',str(out)],WORK,FINALIZATION_TIMEOUT_SECONDS)
        final=read_report(out/'finalization.json')
        ok={x['step'] for x in final.get('steps', []) if x.get('exit_code')==0}
        state['status']='ready_for_editorial_review' if state['finalization_exit_code']==0 and {'intelligence','data_quality','newsletter_evidence'}<=ok else 'evidence_failure'
        save();print(json.dumps(state),flush=True)
        raise SystemExit(0 if state['status']=='ready_for_editorial_review' else 1)


if __name__ == '__main__':
    main()
