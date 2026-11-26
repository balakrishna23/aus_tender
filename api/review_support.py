"""Standard-library-only review wire contract, shared with the offline importer."""
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

BODY_LIMIT = 8192
INBOX_LIMIT = 20 * 1024 * 1024
COMMON = {'kind', 'target', 'expected', 'evidence_url', 'notes'}
FIELDS = {
    'buyer': COMMON | {'decision', 'organisation_id'},
    'panel': COMMON | {'relevant', 'illoura_status'},
    'profile': COMMON | {'value'},
}
BUYER_COLUMNS = ('buyer_key', 'buyer_name', 'buyer_abn', 'match_status', 'organisation_id',
                 'review_status', 'register_context_stale', 'candidate_ids', 'candidate_evidence', 'evidence', 'snapshots')
PANEL_COLUMNS = ('son_id', 'panel_name', 'owner_agency', 'current_expiry', 'spend_through_panel_12m',
                 'marked_relevant', 'illoura_status', 'notes', 'source_url', 'needs_manual_check')
PROFILE_COLUMNS = ('key', 'value', 'evidence_url', 'note')
COLUMNS = {'buyer': BUYER_COLUMNS, 'panel': PANEL_COLUMNS, 'profile': PROFILE_COLUMNS}
TABLES = {'buyer': 'buyer_resolution', 'panel': 'panel_watchlist', 'profile': 'illoura_profile'}
IDENTIFIERS = {'buyer': 'buyer_key', 'panel': 'son_id', 'profile': 'key'}


def clean(value, limit, empty=False):
    if (not isinstance(value, str) or len(value) > limit or
            (not empty and not value.strip()) or
            any(unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('Invalid text, length or control characters')
    return value


def https(value):
    clean(value, 2048)
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                or any(c.isspace() for c in value) or '\\' in value):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError('Evidence must be an HTTPS URL without credentials') from None
    return value


def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('kind'), str) or data['kind'] not in FIELDS:
        raise ValueError('Choose buyer, panel or profile')
    if set(data) != FIELDS[data['kind']]:
        raise ValueError('Missing or unknown review fields')
    clean(data['target'], 200)
    if not isinstance(data['expected'], str) or not re.fullmatch('[a-f0-9]{64}', data['expected']):
        raise ValueError('Current evidence fingerprint required')
    https(data['evidence_url'])
    clean(data['notes'], 2000, empty=True)
    if data['kind'] == 'buyer':
        if data['decision'] not in ('approved', 'rejected'):
            raise ValueError('Choose approved or rejected')
        if data['decision'] == 'approved':
            clean(data['organisation_id'], 200)
        elif data['organisation_id'] is not None:
            raise ValueError('Rejected buyers cannot select an organisation')
    elif data['kind'] == 'panel':
        if type(data['relevant']) not in (bool, type(None)):
            raise ValueError('Relevance must be yes, no or unknown')
        if data['illoura_status'] not in ('unknown', 'member', 'applied', 'not_applied', 'not_eligible'):
            raise ValueError('Unknown Illoura status')
    else:
        clean(data['value'], 1000, empty=True)
    return data


def canonical(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def packet(kind, row):
    """Only explicit contact-free columns leave this boundary; no register people."""
    result = {k: canonical(row[k]) for k in COLUMNS[kind]}
    if kind == 'buyer':
        for key in ('candidate_ids', 'candidate_evidence', 'snapshots'):
            if isinstance(result[key], str):
                result[key] = json.loads(result[key])
        result['register_context_stale'] = bool(result['register_context_stale'])
        # Candidate evidence in buyer_resolution contains government bodies only.
        result['candidate_evidence'] = [{k: canonical(c.get(k)) for k in
            ('organisation_id', 'name', 'abn', 'source', 'source_url', 'snapshot_id')}
            for c in (result['candidate_evidence'] or [])]
    if kind == 'panel' and result['marked_relevant'] is not None:
        result['marked_relevant'] = bool(result['marked_relevant'])
    if kind == 'panel':
        result['needs_manual_check'] = bool(result['needs_manual_check'])
        if result['spend_through_panel_12m'] is not None:
            result['spend_through_panel_12m'] = str(Decimal(str(result['spend_through_panel_12m'])).normalize())
    return result


def fingerprint(kind, row):
    return hashlib.sha256(json.dumps(packet(kind, row), sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
