"""Refresh existing official research sources for the private newsletter pilot.

Uses existing robots-aware collectors. No mail or Google Sheets writes.
"""
import importlib.util
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PROJECT = Path(r'/opt/aus_tender')
OUT = BASE / 'reports' / 'newsletter_trial_20261024'


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUT)
    OUT = parser.parse_args().out.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    def record(name, **fields):
        results.append(dict(step=name, checked_at_utc=datetime.now(timezone.utc).isoformat(), **fields))
        (OUT / 'research_refresh.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(results[-1]), flush=True)

    for name, filename in [('official_sources', 'collect_official_research.py'), ('buyict_news', 'collect_buyict_news.py')]:
        try:
            spec = importlib.util.spec_from_file_location(name, BASE / 'scripts' / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.OUT = OUT
            module.main()
            record(name, status='completed')
        except Exception as exc:
            record(name, status='failed', error=str(exc)[:500])

    for name, args, limit in [
        ('curated_source_checks', ['local_assistant/check_public_sources.py'], 1200),
        ('wa_contracts', ['state_contracts.py'], 1200),
    ]:
        try:
            with (OUT / (name + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.run([sys.executable, '-u', *args], cwd=PROJECT,
                                         stdout=log, stderr=subprocess.STDOUT, timeout=limit)
            record(name, status='completed' if process.returncode == 0 else 'failed', exit_code=process.returncode)
        except Exception as exc:
            record(name, status='failed', error=str(exc)[:500])


if __name__ == '__main__':
    main()
