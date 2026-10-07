"""Relevance, role family and real status (Illoura spec 3.1 and 3.3), driven by config/.

Classification order: category code first, then include keywords, then exclude keywords, which
override a match. Every result says why (relevance_reason), so nothing is a black box.
"""
import re
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import yaml

from project_paths import PROJECT_DIR

CANBERRA = ZoneInfo('Australia/Sydney')
CONFIG_DIR = PROJECT_DIR / 'config'


@lru_cache(maxsize=None)
def config(name):
    with open(CONFIG_DIR / f'{name}.yaml', encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def _pattern(words):
    """Whole-word, case-insensitive match for any of `words` (short upper-case words like AI or BA
    stay case-sensitive, so 'ai' inside ordinary text does not count)."""
    exact = [w for w in words if len(w) <= 3 and w.isupper()]
    loose = [w for w in words if w not in exact]
    parts = []
    if loose:
        parts.append('(?i:' + '|'.join(re.escape(w) for w in sorted(loose, key=len, reverse=True)) + ')')
    if exact:
        parts.append('|'.join(re.escape(w) for w in exact))
    return re.compile(r'(?<![\w.])(?:' + '|'.join(parts) + r')(?![\w])')


@lru_cache(maxsize=None)
def _patterns():
    categories = config('categories')
    return {'strong': _pattern(categories['strong_keywords']), 'weak': _pattern(categories['weak_keywords']),
            'exclude': _pattern(categories['exclude_keywords']),
            'category': _pattern(categories['ict_category_names']),
            'roles': {name: _pattern(words) for name, words in categories['role_families'].items()}}


def family(code):
    """UNSPSC code -> family name, longest configured prefix first."""
    code = re.sub(r'\D', '', str(code or ''))
    families = config('categories')['families']
    return next((families[p] for p in sorted(families, key=len, reverse=True) if code.startswith(p)), None)


def classify(title='', description='', category='', code=None, source_is_ict=False):
    """{'vertical', 'is_relevant', 'needs_review', 'relevance_reason', 'category_family', 'role_families'}.

    Rules first: category code, category name, then title keywords; an exclude keyword in the
    title or category overrides a match. `description` is only used when there is no title.
    needs_review marks unclear titles (one weak keyword) for AI or a person to decide."""
    p = _patterns()
    title = title or description or ''
    code = code or (re.match(r'\s*(\d{8})', category or '') or [None, None])[1]
    group = family(code)
    found = sorted(name for name, pattern in p['roles'].items() if pattern.search(title))
    role_hits = [f for f in found if f in config('categories')['relevant_role_families']]
    weak = {m.group(0).lower() for m in p['weak'].finditer(title)}
    strong = p['strong'].search(title)
    unclear = False
    if group:
        relevant, reason = True, f'category code {code} ({group})'
    elif source_is_ict:
        relevant, reason = True, 'ICT marketplace (BuyICT)'
    elif category and p['category'].search(category):
        relevant, reason = True, f'category "{category}"'
    elif strong:
        relevant, reason = True, f'keyword "{strong.group(0)}"'
    elif role_hits:
        relevant, reason = True, f'role family ({", ".join(role_hits)})'
    elif len(weak) >= 2:
        relevant, reason = True, f'keywords "{", ".join(sorted(weak))}"'
    elif weak:
        relevant, unclear, reason = False, True, f'unclear: only "{next(iter(weak))}" (for AI or a person)'
    else:
        relevant, reason = False, 'no ICT code, category, keyword or role'
    blocked = p['exclude'].search(' '.join(filter(None, (title, category))))
    if (relevant or unclear) and blocked and not source_is_ict:
        relevant, unclear, reason = False, False, f'excluded by keyword "{blocked.group(0)}" ({reason})'
    return {'vertical': config('categories')['vertical'], 'is_relevant': int(relevant),
            'needs_review': int(unclear), 'relevance_reason': reason, 'category_family': group,
            'role_families': ', '.join(found) or None}


CLOSE_FORMATS = ('%d-%b-%Y %I:%M %p', '%d-%b-%Y %I:%M%p', '%d-%b-%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S',
                 '%Y-%m-%d', '%d/%m/%Y %I:%M %p', '%d/%m/%Y', '%A, %d %B %Y %I:%M %p', '%a, %d %B %Y %I:%M %p',
                 '%A, %d %B %Y', '%d %B %Y %I:%M %p', '%d %B %Y', '%d %b %Y', '%d-%b-%Y %H:%M')


def closing_time(text):
    """A closing date/time as shown by a portal -> aware Canberra datetime (None if unreadable).
    A date without a time closes at the end of that day."""
    raw = re.sub(r'[^\x20-\x7e]', ' ', str(text or ''))  # stray dashes such as "2026 – 11:59pm"
    raw = re.sub(r',?\s*\(?(AEST|AEDT|ACT Local Time|Canberra time)\)?.*$', '', raw, flags=re.I)
    raw = re.sub(r'\s+-\s+', ' ', raw)
    raw = raw.replace('.', '').upper().replace('AM', ' AM').replace('PM', ' PM')
    raw = ' '.join(raw.split())
    for fmt in CLOSE_FORMATS:
        for candidate in (raw, raw.title()):
            try:
                moment = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            if '%H' not in fmt and '%I' not in fmt:
                moment = moment.replace(hour=23, minute=59)
            return moment.replace(tzinfo=CANBERRA)
    return None


def effective_status(status_source, close_text, now=None):
    """Spec 3.1: Closed once the closing time (Canberra) has passed, whatever the portal says."""
    now = now or datetime.now(CANBERRA)
    closes = closing_time(close_text)
    if closes and closes < now:
        return 'Closed'
    source = (status_source or '').strip().lower()
    if source and any(word in source for word in ('closed', 'awarded', 'cancel', 'complete', 'withdrawn')):
        return 'Closed'
    return 'Open' if closes or source in ('open', 'published', 'current', 'approaching market') else (
        status_source or 'Unknown')


def contact_safety(status_effective, opted_out=False):
    """Spec section 8: live procurement -> formal channel only; otherwise general contact."""
    if opted_out:
        return 'NO_CONTACT'
    return 'FORMAL_CHANNEL_ONLY' if status_effective == 'Open' else 'SAFE_GENERAL'
