"""Senior role holders published by the Australian Government Directory (spec Phase 2b item 11).

"Use it to map who heads ICT, people/workforce, procurement and digital in each agency. Store only what the
directory publishes, and record the date it was last checked." Only roles whose title or unit matches one of
those functions, and agency heads, are kept: the role title, its unit and the holder's published name. Phone
numbers, emails, postal and street addresses, salutations and honorifics are never read into the database.
Roles attach to an already imported Directory snapshot of the same file (see ingest.import_snapshot), so the
identity snapshot that buyer matching depends on is not replaced.
"""
import json
import re

from defusedxml import ElementTree as ET
from sqlalchemy import text

from .ingest import file_hash, verified_resource
from .parsers import SourceError

PARSER_REVISION = '1'
ROLE_TYPES = ('directory_role', 'single_executive_role')
BODY_TYPES = {'organisation': 'body', 'other_entities': 'body', 'board': 'body', 'portfolio': 'portfolio',
              'directory_sub_structure': 'unit'}
PATTERNS = {
    'ict': re.compile(r'\b(?:ict|information (?:and communications? )?technology|chief information(?: security)? officer'
                      r'|cio|ciso|chief technology officer|cto|cyber(?: ?security)?|information security'
                      r'|technology (?:services|operations|division|branch|group|delivery|and digital|solutions)'
                      r'|it (?:services|operations|security|infrastructure|branch)|infrastructure services'
                      r'|enterprise architecture|service desk|information services|information management)\b', re.I),
    'digital': re.compile(r'\b(?:digital|chief data officer|data (?:and|&) analytics|analytics'
                          r'|data (?:division|branch|services|strategy|management|governance|platforms?|and insights|office))\b',
                          re.I),
    'workforce': re.compile(r'\b(?:chief people officer|people (?:and (?:culture|capability|services|change)|branch|division'
                            r'|group|services|management|strategy|capability|operations|experience|partnering)'
                            r'|workforce|human resources?|hr|human capital|recruitment|talent|employee relations'
                            r'|workplace relations)\b|^people$', re.I),
    'procurement': re.compile(r'\b(?:procurement|contracts?(?: management)?|purchasing|sourcing|supply chain'
                              r'|chief procurement officer|chief commercial officer'
                              r'|commercial (?:services|management|division|branch|group|operations|and procurement))\b', re.I),
}
HEAD = re.compile(r'^(?:acting )?(?:secretary|chief executive(?: officer)?|ceo|director[- ]general|commissioner'
                  r'|chief commissioner|administrator|agency head|head of agency|managing director|chief executive and)\b',
                  re.I)


def functions(role_title, unit_title, role_type, belongs_kind):
    """Which spec functions a role covers, from its own title and its unit's title; [] means not stored."""
    found = {name for name, pattern in PATTERNS.items()
             if pattern.search(role_title or '') or pattern.search(unit_title or '')}
    if role_type == 'single_executive_role' or (belongs_kind == 'body' and HEAD.search(role_title or '')):
        found.add('agency_head')
    return sorted(found)


def _text(item, tag):
    return (item.findtext(tag) or '').strip()


def parse_directory_roles(path):
    """Role holders for the four spec functions and agency heads; returns (roles, metrics)."""
    people, places, raw_roles = {}, {}, []
    with open(path, 'rb') as stream:
        depth = 0
        for event, item in ET.iterparse(stream, events=('start', 'end')):
            if event == 'start':
                depth += 1
                if depth == 1 and item.tag != 'response':
                    raise SourceError('Directory root structure changed')
                continue
            depth -= 1
            if item.tag != 'item':
                continue
            kind = _text(item, 'type')
            content_id = _text(item, 'content_id')
            if kind == 'person':
                # Published name only; salutation, honorific and personal title are not kept.
                name = ' '.join(x for x in (_text(item, 'first_name'), _text(item, 'last_name')) if x)
                if content_id and name:
                    people[content_id] = name
            elif kind in BODY_TYPES:
                places[content_id] = (_text(item, 'unique_record_id'), _text(item, 'title'), BODY_TYPES[kind])
            elif kind in ROLE_TYPES:
                raw_roles.append(dict(role_type=kind, role_record_id=_text(item, 'unique_record_id'),
                                      role_title=_text(item, 'title'), contact=_text(item, 'contact'),
                                      belongs=_text(item, 'role_belongs_to'),
                                      acting=_text(item, 'is_acting_current').upper() == 'TRUE',
                                      importance=_text(item, 'importance'), updated=_text(item, 'updated')))
            item.clear()
    if not places or not raw_roles:
        raise SourceError('Directory export has no units or roles')
    roles, metrics = [], dict(roles_read=len(raw_roles), people_read=len(people), kept=0, not_in_scope=0,
                              without_holder=0, unit_not_found=0, invalid_record_id=0, holder_contact_like_dropped=0)
    seen = set()
    for r in raw_roles:
        if not re.fullmatch(r'[A-Z]-[0-9]+', r['role_record_id']) or not r['role_title']:
            metrics['invalid_record_id'] += 1
            continue
        if r['role_record_id'] in seen:
            raise SourceError('Duplicate Directory role ID')
        seen.add(r['role_record_id'])
        place = places.get(r['belongs'])
        if r['belongs'] and not place:
            metrics['unit_not_found'] += 1
        found = functions(r['role_title'], place[1] if place else '', r['role_type'], place[2] if place else None)
        if not found:
            metrics['not_in_scope'] += 1
            continue
        holder = people.get(r['contact'])
        if holder and ('@' in holder or re.search(r'[0-9]{3}', holder)):
            # Some Directory "person" entries are switchboards or mailboxes, not names (549 of ~26k in the
            # 19 Nov file). Keep the role, never the contact-like text.
            metrics['holder_contact_like_dropped'] += 1
            holder = None
        if not holder:
            metrics['without_holder'] += 1
        roles.append(dict(role_record_id=r['role_record_id'], role_type=r['role_type'], role_title=r['role_title'],
                          functions=found, belongs_to_publisher_id=place[0] if place and place[0] else None,
                          holder_name=holder or None, is_acting=r['acting'],
                          importance=int(r['importance']) if r['importance'].isdigit() else None,
                          publisher_updated=r['updated'] or None))
    metrics['kept'] = len(roles)
    metrics['by_function'] = {f: sum(f in r['functions'] for r in roles) for f in [*PATTERNS, 'agency_head']}
    if not roles:
        raise SourceError('No senior role holders recognised; refusing an empty import')
    return roles, metrics


def import_directory_roles(engine, manifest):
    """Attach role holders to the imported Directory snapshot of the same file. Idempotent per parser revision."""
    path, metadata = verified_resource(manifest, 'government_directory')
    with engine.connect() as conn:
        snapshot_id = conn.execute(text('''SELECT id FROM market.snapshots WHERE source='government_directory'
            AND sha256=:sha ORDER BY imported_at DESC LIMIT 1'''), dict(sha=metadata['sha256'])).scalar()
    if not snapshot_id:
        raise SourceError('Import this Directory file first (import --source government_directory)')
    roles, metrics = parse_directory_roles(path)
    if file_hash(path) != metadata['sha256']:
        raise SourceError('Resource changed while parsing')
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'market:directory_roles'})
        if conn.execute(text('''SELECT 1 FROM market.directory_role_imports WHERE snapshot_id=:s AND parser_revision=:r'''),
                        dict(s=snapshot_id, r=PARSER_REVISION)).scalar():
            return dict(status='unchanged', snapshot_id=str(snapshot_id), roles=None)
        conn.execute(text('''INSERT INTO market.directory_role_imports(snapshot_id,parser_revision,row_count,metrics)
            VALUES(:s,:r,:n,CAST(:m AS jsonb))'''), dict(s=snapshot_id, r=PARSER_REVISION, n=len(roles), m=json.dumps(metrics)))
        conn.execute(text('''INSERT INTO market.directory_roles(snapshot_id,parser_revision,role_record_id,role_type,role_title,
            functions,belongs_to_publisher_id,holder_name,is_acting,importance,publisher_updated)
            VALUES(:s,:r,:role_record_id,:role_type,:role_title,:functions,:belongs_to_publisher_id,:holder_name,:is_acting,
                   :importance,:publisher_updated)'''),
            [dict(role, s=snapshot_id, r=PARSER_REVISION) for role in roles])
    return dict(status='complete', snapshot_id=str(snapshot_id), roles=len(roles), metrics=metrics)
