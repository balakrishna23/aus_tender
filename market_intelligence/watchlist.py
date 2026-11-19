"""Panel watchlist (spec phase 2c) and Illoura's own profile.

Seeding fills only facts from data (names, notice dates, spend, contract counts). Everything people
decide (relevance, Illoura status, intake dates, notes) is entered by a named person, never guessed,
and every change is logged with who and when.
"""
import csv
from datetime import date, timedelta
import json
from pathlib import Path

from sqlalchemy import text

from .buyict import load_arrangements
from .levers import OUR_TAGS

STATUSES = ('unknown', 'member', 'applied', 'not_applied', 'not_eligible')
EXPORT_COLUMNS = ['son_id', 'panel_name', 'owner_agency', 'portal', 'notice_expiry', 'spend_12m', 'contracts_36m',
                  'categories', 'marked_relevant', 'illoura_status', 'notes', 'source_url']


def seed_watchlist(engine, as_of, rules_path=None):
    if not isinstance(as_of, date):
        raise ValueError('Explicit as-of date required')
    buyict = load_arrangements(rules_path) if rules_path else load_arrangements()
    portal = {a['son_id']: 'BuyICT' for a in buyict['arrangements']}
    d12, d36 = as_of - timedelta(days=365), as_of - timedelta(days=1095)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('market:watchlist'))"))
        candidates = [dict(r) for r in conn.execute(text('''
            WITH linked AS (
              SELECT DISTINCT l.son_id, c.cn_id, c.original_value, c.first_published, c.payload->>'category_tag' AS tag
              FROM market.current_analytical_contracts c
              JOIN market.procurement_panel_links l ON l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id
              WHERE c.first_published > :d36 AND c.first_published <= :asof
                AND (c.payload->>'category_tag' = ANY(:tags) OR l.son_id = ANY(:buyict)))
            SELECT k.son_id, count(DISTINCT k.cn_id) AS contracts_36m,
                   sum(k.original_value) FILTER (WHERE k.first_published > :d12) AS spend_12m,
                   jsonb_agg(DISTINCT k.tag) FILTER (WHERE k.tag = ANY(:tags)) AS categories,
                   p.title, p.owner_agency, p.start_date, p.current_expiry, p.source_url
            FROM linked k
            LEFT JOIN LATERAL (SELECT title, owner_agency, start_date, current_expiry, source_url
                               FROM market.procurement_panels pp JOIN market.current_procurement_snapshot cs ON cs.snapshot_id=pp.snapshot_id
                               WHERE pp.son_id=k.son_id ORDER BY pp.observation_id DESC LIMIT 1) p ON true
            GROUP BY k.son_id, p.title, p.owner_agency, p.start_date, p.current_expiry, p.source_url'''),
            dict(d12=d12, d36=d36, asof=as_of, tags=OUR_TAGS, buyict=list(portal))).mappings()]
        names = {a['son_id']: a['name'] for a in buyict['arrangements']}
        inserted = updated = 0
        for c in candidates:
            title = c['title'] or names.get(c['son_id']) or c['son_id']
            result = conn.execute(text('''
                INSERT INTO market.panel_watchlist(son_id,panel_name,owner_agency,portal,categories,start_date,current_expiry,
                    spend_through_panel_12m,contracts_36m,source_url,seeded_from)
                VALUES(:son_id,:title,:owner,:portal,CAST(:categories AS jsonb),:start,:expiry,:spend,:contracts,:url,:seeded)
                ON CONFLICT (son_id) DO UPDATE SET spend_through_panel_12m=EXCLUDED.spend_through_panel_12m,
                    contracts_36m=EXCLUDED.contracts_36m, categories=EXCLUDED.categories
                RETURNING (xmax = 0) AS inserted'''),
                dict(son_id=c['son_id'], title=title, owner=c['owner_agency'], portal=portal.get(c['son_id']),
                     categories=json.dumps(c['categories'] or []), start=c['start_date'], expiry=c['current_expiry'],
                     spend=c['spend_12m'], contracts=c['contracts_36m'], url=c['source_url'],
                     seeded='buyict_rules' if c['son_id'] in portal else 'contracts_in_our_categories_36m')).scalar()
            inserted += bool(result)
            updated += not result
    return {'candidates': len(candidates), 'inserted': inserted, 'refreshed': updated}


def export_watchlist(engine, output):
    with engine.connect() as conn:
        rows = conn.execute(text('''SELECT son_id, panel_name, owner_agency, portal, current_expiry AS notice_expiry,
            spend_through_panel_12m AS spend_12m, contracts_36m, categories, marked_relevant, illoura_status, notes, source_url
            FROM market.panel_watchlist ORDER BY spend_through_panel_12m DESC NULLS LAST, contracts_36m DESC, son_id''')).mappings().all()
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for r in rows:
            row = dict(r)
            row['categories'] = ', '.join(r['categories'] or [])
            row['marked_relevant'] = '' if r['marked_relevant'] is None else ('yes' if r['marked_relevant'] else 'no')
            writer.writerow(row)
    return {'panels': len(rows), 'csv': str(output)}


def _relevant(value):
    value = (value or '').strip().lower()
    if value in ('', 'unknown'):
        return None
    if value in ('yes', 'y', 'true', '1'):
        return True
    if value in ('no', 'n', 'false', '0'):
        return False
    raise ValueError('marked_relevant must be yes, no or blank')


def mark_watchlist(engine, csv_path, changed_by):
    """Apply a reviewed export: only marked_relevant, illoura_status and notes are read from it."""
    if not (changed_by or '').strip():
        raise ValueError('A reviewer name is required')
    with open(csv_path, newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    changes = 0
    with engine.begin() as conn:
        for row in rows:
            current = conn.execute(text('SELECT marked_relevant, illoura_status, notes FROM market.panel_watchlist WHERE son_id=:s'),
                                   {'s': row['son_id']}).mappings().first()
            if not current:
                raise ValueError('Unknown panel in review file: ' + row['son_id'])
            status = (row.get('illoura_status') or 'unknown').strip() or 'unknown'
            if status not in STATUSES:
                raise ValueError('illoura_status must be one of ' + ', '.join(STATUSES))
            new = dict(marked_relevant=_relevant(row.get('marked_relevant')), illoura_status=status, notes=(row.get('notes') or '').strip())
            for field, value in new.items():
                if value != current[field]:
                    conn.execute(text('INSERT INTO market.watchlist_events(son_id,field,old_value,new_value,changed_by) VALUES(:s,:f,:o,:n,:b)'),
                                 dict(s=row['son_id'], f=field, o=None if current[field] is None else str(current[field]),
                                      n=None if value is None else str(value), b=changed_by))
                    changes += 1
            conn.execute(text('''UPDATE market.panel_watchlist SET marked_relevant=:marked_relevant, illoura_status=:illoura_status,
                notes=:notes, updated_by=:b, updated_at=now() WHERE son_id=:s'''), dict(new, s=row['son_id'], b=changed_by))
    return {'rows': len(rows), 'changes': changes}


def set_profile(engine, key, value, evidence_url, changed_by):
    if not (changed_by or '').strip():
        raise ValueError('A person must be named for every profile change')
    if evidence_url and not evidence_url.startswith('https://'):
        raise ValueError('Evidence must be an https link')
    with engine.begin() as conn:
        old = conn.execute(text('SELECT value FROM market.illoura_profile WHERE key=:k FOR UPDATE'), {'k': key}).first()
        if old is None:
            raise ValueError('Unknown profile key: ' + key)
        conn.execute(text('''UPDATE market.illoura_profile SET value=:v, evidence_url=:e, updated_by=:b, updated_at=now() WHERE key=:k'''),
                     dict(k=key, v=value, e=evidence_url, b=changed_by))
        conn.execute(text('''INSERT INTO market.profile_events(key,old_value,new_value,evidence_url,changed_by)
            VALUES(:k,:o,:v,:e,:b)'''), dict(k=key, o=old[0], v=value, e=evidence_url, b=changed_by))
    return {'key': key, 'value': value}


def show_profile(engine):
    with engine.connect() as conn:
        return {'profile': [dict(r) for r in conn.execute(text(
            'SELECT key, value, evidence_url, updated_by, updated_at, note FROM market.illoura_profile ORDER BY key')).mappings()]}


def category_summary(engine):
    """How contracts in the current snapshot are tagged; the spec asks to watch the 'other' share."""
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text('''
            SELECT coalesce(payload->>'category_tag','untagged') AS tag, payload->>'category_family' AS family,
                   count(*) AS contracts, sum(original_value) AS value
            FROM market.current_analytical_contracts GROUP BY 1,2 ORDER BY 3 DESC''')).mappings()]
    ours = [r for r in rows if r['family'] in ('labour hire and recruitment', 'ICT services')]
    other = sum(float(r['value'] or 0) for r in ours if r['tag'] == 'other')
    total = sum(float(r['value'] or 0) for r in ours)
    return {'by_tag_and_family': rows, 'other_share_of_our_family_value': round(other / total, 4) if total else None}
