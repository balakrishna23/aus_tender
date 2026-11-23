"""Publisher checks measure checks and editions, never age alone."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

MANUAL = ('abn', 'asic_companies', 'asic_business_names', 'acnc', 'agor', 'government_directory', 'oric')
STATUSES = {'latest_available', 'new_edition_pending', 'check_overdue', 'blocked', 'failed', 'manual_refresh_needed'}


@dataclass(frozen=True)
class Source:
    key: str
    cadence_days: int
    adapter: str
    automation: str
    snapshot_table: str
    pointer_table: str
    edition_column: str
    db_source: str = ''


SOURCES = {s.key: s for s in [
    *[Source(k, 30, 'inbox', 'manual', 'snapshots' if k in ('agor', 'government_directory') else 'register_snapshots',
             'current_snapshots' if k in ('agor', 'government_directory') else 'current_register_snapshots', 'effective_date', k)
      for k in MANUAL],
    *[Source(k, 7 if k in ('jsa_shortages', 'jsa_projections') else 1, 'jsa', 'automatic',
             'demand_snapshots', 'current_demand_snapshots', 'period_end', k)
      for k in ('jsa_ivi', 'jsa_projections', 'jsa_shortages', 'jsa_reos', 'jsa_tnv')],
    Source('aps_employment', 7, 'aps', 'automatic', 'aps_snapshots', 'current_aps_snapshot', 'as_at'),
    Source('aged_care_star_ratings', 7, 'health', 'automatic', 'aged_care_snapshots', 'current_aged_care_snapshot', 'reference_period'),
    Source('rap_directory', 30, 'rap', 'automatic', 'research_snapshots', 'current_research_snapshots', 'observed_at', 'rap_directory'),
    Source('wa_contracts', 7, 'wa', 'automatic', 'research_snapshots', 'current_research_snapshots', 'observed_at', 'state_contracts'),
    Source('procurement', 1, 'monitor', 'monitor', 'procurement_snapshots', 'current_procurement_snapshot', 'imported_at'),
    Source('tender_feed', 1, 'monitor', 'monitor', 'forward_snapshots', 'current_forward_snapshot', 'imported_at'),
]}


def imported(conn, source):
    # Identifiers come only from the fixed registry, never CLI input.
    s = SOURCES[source]
    query = f'SELECT s.id,s.sha256,s.imported_at,s.{s.edition_column} AS edition FROM market.{s.snapshot_table} s JOIN market.{s.pointer_table} c ON c.snapshot_id=s.id'
    if s.db_source:
        query += ' WHERE c.source=:source'
    row = conn.execute(text(query), {'source': s.db_source}).mappings().first()
    return dict(row) if row else None


def manual_baseline(source, current):
    if SOURCES[source].automation != 'manual' or not current or not current.get('imported_at'):
        return None
    return dict(source=source, checked_at=current['imported_at'], last_success_at=current['imported_at'],
                status='latest_available', details=dict(reason='existing_verified_manual_import',
                    imported_edition=str(current['edition']), snapshot_id=str(current['id'])))


def due(source, state, now):
    if not state or state['status'] in {'failed', 'blocked', 'new_edition_pending', 'check_overdue'}:
        return True
    basis = state.get('last_success_at') if SOURCES[source].automation == 'manual' else state['checked_at']
    return basis is None or now >= basis + timedelta(days=SOURCES[source].cadence_days)


def report(db, now=None):
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        states = {r['source']: dict(r) for r in conn.execute(text('SELECT * FROM market.research_freshness')).mappings()}
        rows = []
        for key, source in SOURCES.items():
            state = states.get(key)
            current = imported(conn, key)
            state = state or manual_baseline(key, current)
            status = state['status'] if state else 'check_overdue'
            if due(key, state, now) and status == 'latest_available':
                status = 'check_overdue'
            if (source.automation == 'manual' and status not in {'failed', 'blocked', 'new_edition_pending'}
                    and (not state or due(key, state, now))):
                status = 'manual_refresh_needed'
            rows.append(dict(source=key, status=status, automation=source.automation,
                             cadence_days=source.cadence_days, checked_at=state['checked_at'] if state else None,
                             imported_edition=str(current['edition']) if current else None,
                             details=state['details'] if state else {}))
    return dict(informational_only=True, sources=rows)
