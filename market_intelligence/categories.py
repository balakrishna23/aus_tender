"""Category tags for contracts from editable rules (rules/categories.json).

The description is used only to choose a tag; it is never returned or stored.
"""
from functools import lru_cache
import json
from pathlib import Path
import re

RULES_PATH = Path(__file__).parent/'rules'/'categories.json'


@lru_cache(maxsize=4)
def load_rules(path=str(RULES_PATH)):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    tags = set(data['tags'])
    if data['default'] not in tags:
        raise ValueError('Default category must be one of the tags')
    patterns = {}
    for name, words in data['keywords'].items():
        exact = [re.escape(w[1:]) for w in words if w.startswith('=')]
        loose = [re.escape(w) for w in words if not w.startswith('=')]
        patterns[name] = (re.compile(r'(?<![A-Za-z0-9])(?:' + '|'.join(exact) + r')(?![A-Za-z0-9])') if exact else None,
                          re.compile(r'(?<![a-z0-9])(?:' + '|'.join(loose) + r')(?![a-z0-9])', re.I) if loose else None)
    for rule in data['rules']:
        if rule['tag'] not in tags:
            raise ValueError('Unknown category tag in rules: ' + rule['tag'])
        when = rule['when']
        if not (when.get('families') or when.get('unspsc_prefixes')):
            raise ValueError('Each rule needs a family or UNSPSC scope')
        if when.get('keywords') and when['keywords'] not in patterns:
            raise ValueError('Unknown keyword list: ' + when['keywords'])
    return data, patterns


def _keyword(patterns, name, text):
    exact, loose = patterns[name]
    return bool((exact and exact.search(text)) or (loose and loose.search(text)))


def tag(unspsc, family, description, path=str(RULES_PATH)):
    """Return (tag, rule label) for one contract; the label says which rule matched."""
    data, patterns = load_rules(path)
    unspsc = (unspsc or '').strip()
    text = description or ''
    for index, rule in enumerate(data['rules'], 1):
        when = rule['when']
        in_scope = (family in when.get('families', [])) or any(unspsc.startswith(p) for p in when.get('unspsc_prefixes', []))
        if not in_scope:
            continue
        if when.get('keywords') and not _keyword(patterns, when['keywords'], text):
            continue
        return rule['tag'], 'rule_%d' % index
    return data['default'], 'default'
