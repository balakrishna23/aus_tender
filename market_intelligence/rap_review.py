"""Conservative directory-only review and independent replay verification. No PDF authority."""
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from sqlalchemy import text

from .acquire_common import AcquisitionError
from .acquire_rap import DIRECTORY, TYPES, load_collection
from .parsers import EMAIL_PATTERN, SourceError, name_key
from .rap_extract import PHONE, digest, privacy_check, reviewer_id
from .research import SCHEMAS, period_range

POLICY = 'rap-directory-privacy-2'
HOSTS = {'reconciliation.my.salesforce.com', 'recaus.my.salesforce.com', 'www.reconciliation.org.au'}
# Evidence tokens, not an allowlist for every word in a name. Brands can accompany
# organisational evidence; an unexplained personal-looking name still needs a register match.
WORDS = set('community health services service association '
            'australian australia '
            'indigenous aboriginal torres strait islander corporation council education training '
            'housing disability aged care children youth family families support development '
            'research institute foundation centre center arts cultural employment business '
            'industry technology technologies group resources infrastructure transport energy '
            'environment environmental water land conservation social justice legal medical '
            'hospital university college school public department '
            'bank banking law lawyers lawyer partners consulting consultancy engineering engineers '
            'construction mining insurance financial finance capital property shire city authority '
            'commission agency office network alliance collective cooperative club league museum '
            'gallery library trust fund solutions systems holdings chamber commerce building '
            'communications communication media broadcasting telecommunications digital software '
            'logistics manufacturing agriculture agricultural farming food retail hospitality tourism '
            'sports sport football cricket rugby basketball theatre theater orchestra music '
            'charity charitable church mission ministry union federation society partnership '
            'enterprise enterprises industries investments investment advisory advisers advisors '
            'accounting accountants audit architecture architects design recruitment workforce '
            'employment disability rehabilitation childcare early learning kindergarten preschool '
            'pharmacy pharmaceutical dental veterinary ambulance rescue emergency police defence '
            'municipal municipality government state federal department bureau board committee '
            'electricity electrical solar gas petroleum oil coal steel cement industrial '
            'scientific science innovation sustainability waste recycling airport airlines aviation '
            'rail railway road roads port ports shipping marine maritime automotive motors '
            'distribution maintenance cleaning facilities security telecommunications publishing '
            'association associations cooperative cooperatives union unions councils schools '
            'universities hospitals banks libraries museums foundations'.split())
LEGAL_SUFFIXES = frozenset('pty ltd limited inc incorporated co company corp corporation plc llc llp'.split())
HONORIFIC = re.compile(r'\b(?:mr|mrs|ms|miss|dr|prof|professor|sir|dame)\.?\s', re.I)
POLITICAL = re.compile(r'\b(?:MP|MHR|MLA|MLC|senator|honourable)\b', re.I)
INITIALS = re.compile(r'\b[A-Za-z]\.|\b[A-Z](?:\s+[A-Z])?\s+[A-Z][a-z]')


def legal_name_key(value):
    """Normalise punctuation, an initial 'the', and terminal legal-form tokens only."""
    tokens = name_key(value).split()
    if tokens[:1] == ['the']:
        tokens.pop(0)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return ' '.join(tokens)


def phone_path(path):
    """Phone-shaped segments are unsafe; decimal substrings in opaque IDs are not phones."""
    for segment in path.split('/'):
        if re.search(r'(?<!\w)\+?\d[\d() .-]{6,}\d(?!\w)', segment):
            return True
        for match in PHONE.finditer(segment):
            before, after = segment[:match.start()], segment[match.end():]
            if (not before or not (before[-1].isalnum() or before[-1]=='_')) and (
                    not after or not (after[0].isalnum() or after[0]=='_')):
                return True
    return False


def safe_plan_url(value):
    if not isinstance(value, str):
        raise SourceError('invalid_plan_url')
    if not value:
        return
    try:
        parts = urlsplit(value)
        decoded = value
        for _ in range(8):
            if re.search(r'%(?![0-9a-fA-F]{2})', decoded):
                raise ValueError()
            next_value = unquote(decoded, errors='strict')
            if decoded == next_value:
                break
            decoded = next_value
        else:
            raise ValueError()
        if (parts.scheme != 'https' or parts.hostname not in HOSTS or parts.port not in (None, 443)
                or parts.username or parts.password or parts.query or parts.fragment
                or any(c.isspace() or ord(c) < 32 for c in decoded) or '\\' in decoded
                or '?' in decoded or '#' in decoded or EMAIL_PATTERN.search(decoded)
                or phone_path(urlsplit(decoded).path)
                or any(segment in ('.', '..') for segment in urlsplit(decoded).path.split('/'))):
            raise ValueError()
    except (ValueError, TypeError):
        raise SourceError('invalid_plan_url') from None


def load_context(engine):
    """One consistent DB observation; no external lookups, no negative-match inference."""
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        snapshots = [dict(r) for r in conn.execute(text('''
            SELECT source,snapshot_id::text AS snapshot_id FROM market.current_register_snapshots
            UNION ALL SELECT source,snapshot_id::text FROM market.current_snapshots ORDER BY source''')).mappings()]
        entities = [dict(r) for r in conn.execute(text('''
            SELECT e.source,e.record_id,e.abn,e.name,e.is_individual,e.entity_type
            FROM market.current_register_entities e
            UNION ALL
            SELECT e.source,e.record_id,e.abn,n.name,e.is_individual,e.entity_type
            FROM market.current_register_entities e JOIN market.register_names n
              ON n.snapshot_id=e.snapshot_id AND n.record_id=e.record_id
            UNION ALL
            SELECT source,publisher_id,abn,name,false,kind FROM market.organisation_observations
            WHERE kind IN ('body','unit')''')).mappings()]
        baseline = [dict(r) for r in conn.execute(text("SELECT snapshot_id::text,payload,provenance FROM market.current_research WHERE source='rap_directory'")).mappings()]
    entities.sort(key=lambda row: json.dumps(row,sort_keys=True))
    return dict(snapshots=snapshots, entities=entities, baseline=baseline)


def identity_index(context):
    exact, normalised = defaultdict(list), defaultdict(list)
    individual_abns = {e['abn'] for e in context['entities'] if e.get('abn') and is_individual(e)}
    for entity in context['entities']:
        if entity.get('name'):
            checked = dict(entity,is_individual=is_individual(entity) or entity.get('abn') in individual_abns)
            exact[name_key(entity['name'])].append(checked)
            key = legal_name_key(entity['name'])
            if key:
                normalised[key].append(checked)
    return dict(exact=exact, normalised=normalised)


def is_individual(entity):
    return bool(entity['is_individual'] or re.search(r'^IND$|individual|sole\s*(?:trader|proprietor)', entity.get('entity_type') or '', re.I))


def identity_assessment(organisation, index):
    exact = index.get('exact', {}).get(name_key(organisation), [])
    normalised = index.get('normalised', {}).get(legal_name_key(organisation), [])
    # Include normalised candidates even with an exact hit so a suffix collision or
    # an individual entity cannot be hidden by a more convenient exact spelling.
    matches = normalised or exact
    basis = 'register_exact' if exact else 'register_legal_normalised'
    if matches:
        if any(is_individual(e) for e in matches):
            return 'individual_register_match', basis
        identities = {('abn', e['abn']) if e.get('abn') else (e['source'], e['record_id']) for e in matches}
        if len(identities) != 1:
            return 'ambiguous_register_match', basis
        return None, basis
    tokens = name_key(organisation).split()
    # Public office descriptions can still identify an individual representative.
    if HONORIFIC.search(organisation) or POLITICAL.search(organisation):
        return 'person_or_representative_pattern', None
    if not tokens or all(t in LEGAL_SUFFIXES or t=='the' for t in tokens):
        return 'insufficient_organisational_evidence', None
    if len(tokens) >= 2 and set(tokens) & LEGAL_SUFFIXES:
        return None, 'legal_form'
    if set(tokens) & WORDS:
        return None, 'organisation_vocabulary'
    if any(re.fullmatch(r'[A-Z]{2,12}', token) for token in re.findall(r"[\w.]+", organisation)):
        return None, 'uppercase_acronym'
    meaningful = [t for t in tokens if t not in ('and', 'the')]
    if INITIALS.search(organisation) or 1 <= len(meaningful) <= 3:
        return 'unregistered_name_or_person_pattern', None
    return 'insufficient_organisational_evidence', None


def identity_reason(organisation, index):
    return identity_assessment(organisation, index)[0]


def partition(bundle, context):
    index = identity_index(context)
    records = bundle['records']
    keys = Counter(r.get('record_key') for r in records)
    names = Counter(name_key(r.get('organisation')) for r in records)
    cleared, decisions = [], []
    for number, row in enumerate(records):
        reasons = []
        if set(row) != SCHEMAS['rap_directory']:
            reasons.append('unsupported_fields')
        try:
            for field in ('organisation', 'period_as_published'):
                privacy_check(row.get(field))
            if not row['organisation'].strip():
                raise SourceError('empty')
        except (SourceError, KeyError, TypeError):
            reasons.append('privacy_check_failed')
        if row.get('rap_type') not in TYPES:
            reasons.append('invalid_rap_type')
        try:
            safe_plan_url(row.get('document_url'))
        except SourceError:
            reasons.append('invalid_plan_url')
        reason, match_basis = identity_assessment(row.get('organisation', ''), index)
        if reason:
            reasons.append(reason)
        if keys[row.get('record_key')] > 1:
            reasons.append('duplicate_record_key')
        if names[name_key(row.get('organisation'))] > 1:
            reasons.append('duplicate_or_conflicting_organisation')
        valid_period = period_range(row.get('period_as_published', '')) != (None, None)
        if not reasons:
            cleared.append(row)
        # Quarantine records stay in the private retained collection, not in another names file.
        decisions.append(dict(index=number, record_sha256=digest(row), reasons=sorted(reasons),
            identity_basis=match_basis, cleared=not reasons, period_valid=valid_period,
            missing_plan_link=not row.get('document_url')))
    return cleared, decisions


def coverage(bundle, cleared, context):
    reasons = []
    if bundle['provenance'].get('complete') is not True:
        reasons.append('incomplete_collection')
    if not cleared:
        reasons.append('empty_cleared_subset')
    if len(cleared) < .5 * len(bundle['records']):
        reasons.append('low_cleared_fraction')
    baseline = context['baseline']
    if baseline:
        old = Counter(r['payload']['rap_type'] for r in baseline)
        old_raw = baseline[0]['provenance'].get('type_counts', dict(old))
        comparisons = [('collection', bundle['provenance']['type_counts'], old_raw)]
        if baseline[0]['provenance'].get('privacy_policy'):
            comparisons.append(('cleared', Counter(r['rap_type'] for r in cleared), old))
        for label, counts, previous in comparisons:
            if sum(counts.values()) < .9 * sum(previous.values()):
                reasons.append(label + '_overall_coverage_loss')
            if any(counts.get(t, 0) < .75 * previous.get(t, 0) for t in TYPES):
                reasons.append(label + '_type_coverage_loss')
    return sorted(set(reasons))


def evaluate(collection, context):
    try:
        bundle = load_collection(collection)
        observed = datetime.fromisoformat(bundle['observed_at'])
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise AcquisitionError('Invalid collection observation time')
        date.fromisoformat(bundle['provenance']['collection_date'])
    except (KeyError, TypeError, ValueError):
        raise AcquisitionError('Invalid retained collection structure') from None
    cleared, decisions = partition(bundle, context)
    reasons = coverage(bundle, cleared, context)
    counts = dict(input=len(bundle['records']), cleared=len(cleared), quarantined=len(decisions)-len(cleared),
                  malformed_periods=sum(not d['period_valid'] for d in decisions),
                  cleared_malformed_periods=sum(d['cleared'] and not d['period_valid'] for d in decisions))
    bundle['records'] = cleared
    bundle['provenance']['privacy_policy'] = POLICY
    bundle['provenance']['privacy_partition'] = counts
    audit = dict(policy_version=POLICY, register_snapshot_ids=context['snapshots'],
        register_context_sha256=digest(context['entities']),
        baseline_snapshot_ids=sorted({r['snapshot_id'] for r in context['baseline']}),
        counts=counts, decisions=decisions, promotion_block_reasons=reasons,
        payload_sha256=digest({'records': cleared, 'provenance': bundle['provenance']}))
    return bundle, audit


def audit_path(bundle_path):
    return Path(str(bundle_path) + '.audit.json')


def review(engine, collection, destination, account):
    account = reviewer_id(account, 'rap_directory')
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or audit_path(path).exists():
        raise SourceError('Review destination already exists')
    stage = 'review'
    try:
        bundle, audit = evaluate(collection, load_context(engine))
        audit['reviewer_id'] = account
        audit['review_pass'] = 'passed'
        # Reload retained evidence AND current DB independently. Changes fail closed.
        stage = 'verifier'
        checked, second = evaluate(collection, load_context(engine))
        second.update(reviewer_id=account, review_pass='passed')
        if audit != second or bundle != checked:
            raise SourceError('Independent RAP verification differs')
    except Exception as exc:
        reason = 'collection_invalid' if isinstance(exc, AcquisitionError) else 'review_error'
        # Do not copy exception text, partial row decisions or counts into a failed audit.
        # Those might be private or could falsely imply that review completed.
        audit = dict(policy_version=POLICY,reviewer_id=account,failure_stage=stage,
                     review_pass='error' if stage=='review' else 'passed',
                     verifier_pass='not_run' if stage=='review' else 'error',
                     promotion_block_reasons=[reason])
        with audit_path(path).open('x',encoding='utf-8') as stream:
            json.dump(audit,stream,indent=2)
        return dict(status='blocked',reasons=[reason])
    audit['verifier_pass'] = 'passed'
    if audit['promotion_block_reasons']:
        audit['review_pass'] = 'blocked'
        audit['verifier_pass'] = 'blocked'
    with audit_path(path).open('x', encoding='utf-8') as stream:
        json.dump(audit, stream, indent=2)
    if audit['promotion_block_reasons']:
        return dict(status='blocked', counts=audit['counts'], reasons=audit['promotion_block_reasons'])
    bundle['privacy_review'] = dict(reviewer_id=account, contains_personal_data=False, payload_sha256=audit['payload_sha256'])
    with path.open('x', encoding='utf-8') as stream:
        json.dump(bundle, stream, ensure_ascii=False, indent=2)
    return dict(status='reviewed', counts=audit['counts'])


def verify(engine, collection, bundle_path):
    supplied = json.loads(Path(bundle_path).read_text(encoding='utf-8'))
    audit = json.loads(audit_path(bundle_path).read_text(encoding='utf-8'))
    account = reviewer_id(supplied['privacy_review']['reviewer_id'], 'rap_directory')
    context = load_context(engine)
    current = context['baseline']
    # Re-verifying an already imported identical bundle uses its original baseline.
    # Any other current snapshot/context change still invalidates the audit.
    if current:
        provenance = dict(current[0]['provenance'])
        clearance = provenance.pop('rap_privacy_review', None)
        if (provenance == supplied['provenance'] and clearance == supplied['privacy_review']
                and sorted((r['payload'] for r in current),key=lambda r:r['record_key'])
                == sorted(supplied['records'],key=lambda r:r['record_key'])):
            ids = audit['baseline_snapshot_ids']
            if ids:
                with engine.connect() as conn:
                    context['baseline'] = [dict(r) for r in conn.execute(text('''
                        SELECT r.snapshot_id::text,r.payload,s.provenance FROM market.research_records r
                        JOIN market.research_snapshots s ON s.id=r.snapshot_id
                        WHERE s.source='rap_directory' AND s.id=ANY(CAST(:ids AS uuid[]))'''),{'ids':ids}).mappings()]
                if sorted({r['snapshot_id'] for r in context['baseline']}) != sorted(ids):
                    raise SourceError('RAP audit baseline is unavailable')
            else:
                context['baseline'] = []
    expected, checks = evaluate(collection, context)
    checks.update(reviewer_id=account, review_pass='passed', verifier_pass='passed')
    expected['privacy_review'] = dict(reviewer_id=account, contains_personal_data=False, payload_sha256=checks['payload_sha256'])
    if checks['promotion_block_reasons'] or expected != supplied or checks != audit:
        raise SourceError('RAP verifier rejected evidence, partition or payload')
    return dict(status='verified', counts=checks['counts'], payload_sha256=checks['payload_sha256'])
