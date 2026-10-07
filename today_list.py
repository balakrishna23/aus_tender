"""TODAY list, first draft (spec engine 7 and section 7): signals -> owned, dated actions, scored.

Rules only. Each signal type maps to an action template; every row carries its evidence, contact
safety and the assumptions behind its score. Two dimensions need Illoura's own data and are not
scored yet: delivery readiness (needs the bench) and warm access (needs relationships). They count
as neutral (half marks) and say so, rather than being guessed.
"""
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

import classify

NEUTRAL = 0.5
# The offer in the spec's worked examples: cleared BAs, data and cyber people, delivery roles.
ILLOURA_ROLES = {'analysis', 'AI/data', 'cyber', 'delivery'}
TIMING = [((0, 30), 0.6), ((31, 60), 1.0), ((61, 90), 0.9), ((91, 180), 0.7), ((181, 365), 0.4)]


def _value_score(value):
    """$100k -> ~0.33, $1m -> ~0.67, $10m+ -> 1."""
    return max(0.0, min(1.0, (math.log10(value) - 4) / 3)) if value and value > 0 else 0.2


def _timing(days):
    return next((score for (lo, hi), score in TIMING if lo <= days <= hi), 0.2)


def _friday(today, weeks=0):
    return today + timedelta(days=(4 - today.weekday()) % 7 + 7 * weeks)


def score(parts):
    weights = classify.config('scoring')['weights']
    total = round(sum(weights[k] * parts[k] for k in weights))
    thresholds = classify.config('scoring')['thresholds']
    label = next((name for name, floor in sorted(thresholds.items(), key=lambda kv: -kv[1]) if total >= floor),
                 'IGNORE')
    return total, label


def _action(kind, organisation, why, person, offer, owner, safety, evidence, parts, assumptions, today,
            subject=None):
    """`subject` keeps the action's identity across runs (default: the organisation; a plan or a live
    item when one organisation has several)."""
    total, label = score(parts)
    due = _friday(today) if label == 'ACT NOW' else _friday(today, 1)
    return dict(priority=label, score=total, organisation=organisation, action_type=kind, why_now=why,
                person_and_route=person, offer=offer, owner=owner, due=due.isoformat(), contact_safety=safety,
                evidence=evidence, score_parts=', '.join(f'{k} {v:.1f}' for k, v in parts.items()),
                assumptions=assumptions, desired_outcome=None, status='proposed', subject=subject or organisation)


# Contracts a Tier-2 partner can staff (not hardware or licences).
TIER2_FAMILIES = {'labour hire and recruitment', 'ICT services'}


def prime_actions(renewals, primes, today):
    """Staffing or IT-services renewals in the next 6 months held by a NIAA-listed prime ->
    Tier-2 offer to the prime (Lane). One action per company or company group, whatever number of
    ABNs it uses; MMR counts are the group's (DXC: 5 + 1 = 6)."""
    company = {}
    for p in primes:
        company.setdefault(p['abn'], p)
    by_company = defaultdict(list)
    for r in renewals:
        prime = company.get(re.sub(r'\D', '', r['supplier_abn'] or ''))
        if prime and r['days_left'] <= 180 and r['category_family'] in TIER2_FAMILIES:
            by_company[prime.get('group_name') or prime['organisation']].append((prime, r))
    out = []
    for organisation, pairs in by_company.items():
        prime, items = pairs[0][0], [r for _, r in pairs]
        mmr = prime.get('group_mmr_contracts') or prime['mmr_contracts'] or 1
        # The hook: the renewal best placed in time (31-60 days is ideal), then the largest.
        hook = max(items, key=lambda r: (_timing(r['days_left']), r['current_value'] or 0))
        roles = {f for r in items for f in (classify.classify(r['description'] or '')['role_families'] or '').split(', ') if f}
        staffing = any(r['category_family'] == 'labour hire and recruitment' for r in items)
        parts = dict(attractiveness=_value_score(sum(r['current_value'] or 0 for r in items)),
                     illoura_fit=0.8 if roles & ILLOURA_ROLES or staffing else NEUTRAL,
                     # MMR obligations give the prime a policy reason to talk to Illoura; more
                     # obligations, a stronger reason.
                     access=min(1.0, 0.6 + 0.05 * mmr),
                     timing=_timing(hook['days_left']), delivery_readiness=NEUTRAL,
                     commercial_potential=min(1.0, 0.3 + 0.1 * len(items)))
        why = (f"{mmr} active MMR contract(s) (NIAA list as at {prime['list_date']}); "
               f"{len(items)} staffing/IT-services contract(s) ending within 6 months. Hook: {hook['buyer']} "
               f"{hook['description']!r} ends {hook['current_end_date']} ({hook['days_left']} days)")
        out.append(_action(
            'Tier-2 offer to prime', organisation, why,
            'Social impact or Indigenous procurement lead; warm path not known yet',
            'Illoura contractors for the renewal; every placement counts toward their MMR target', 'Lane',
            'SAFE_GENERAL', hook['page_url'] or hook['api_url'], parts,
            'Delivery readiness neutral (no bench data); warm access not scored (no relationships data); '
            'MMR status of each contract unconfirmed', today))
    return out


def agency_actions(renewals, exemption_use, today):
    """Agencies that already buy directly from Indigenous SMEs with ICT/labour-hire work renewing ->
    capability introduction (Sirish), to the contract notice's procurement contact."""
    cutoff = (today - timedelta(days=730)).isoformat()
    item16 = defaultdict(int)
    for r in exemption_use:
        if r['category_family'] and (r['first_published'] or '') >= cutoff:
            item16[r['buyer']] += 1
    by_buyer = defaultdict(list)
    for r in renewals:
        if item16.get(r['buyer'], 0) >= 3 and 31 <= r['days_left'] <= 365 and r['contact_email']:
            by_buyer[r['buyer']].append(r)
    out = []
    for buyer, items in by_buyer.items():
        items.sort(key=lambda r: (r['days_left'], -(r['current_value'] or 0)))
        hook = items[0]
        parts = dict(attractiveness=_value_score(sum(r['current_value'] or 0 for r in items)),
                     illoura_fit=0.8 if hook['category_family'] == 'labour hire and recruitment' else NEUTRAL,
                     access=min(1.0, 0.5 + 0.05 * item16[buyer]), timing=_timing(hook['days_left']),
                     delivery_readiness=NEUTRAL, commercial_potential=min(1.0, 0.3 + 0.05 * len(items)))
        why = (f"{item16[buyer]} ICT/labour-hire contracts bought directly from Indigenous SMEs (Exemption 16) "
               f"in 2 years; {len(items)} ICT/labour-hire contract(s) ending within a year, first "
               f"{hook['description']!r} ({hook['supplier']}) ends {hook['current_end_date']}")
        out.append(_action(
            'Capability introduction to agency', buyer, why,
            f"{hook['contact_name'] or 'Procurement contact'} <{hook['contact_email']}> (contract notice)",
            'Capability statement; Indigenous SME pathway (Exemption 16)', 'Sirish', 'SAFE_GENERAL',
            hook['page_url'] or hook['api_url'], parts,
            'Delivery readiness neutral (no bench data); never imply an award can change', today))
    return out


def plan_actions(hot_list, today):
    """Planned ICT procurement with a published contact -> register interest (Sirish)."""
    out = []
    for p in hot_list:
        start = date.fromisoformat(p['expected_from'])
        days = (start - today).days
        parts = dict(attractiveness=NEUTRAL, illoura_fit=0.8 if set((p['role_families'] or '').split(', ')) & ILLOURA_ROLES
                     else NEUTRAL, access=0.8, timing=1.0 if days <= 90 else 0.7, delivery_readiness=NEUTRAL,
                     commercial_potential=NEUTRAL)
        out.append(_action(
            'Register interest in planned procurement', p['buyer'],
            f"Procurement plan: {p['title']!r} expected {p['expected_from']} to {p['expected_to']}",
            f"{p['contact_name'] or 'Listed contact'} <{p['contact_email'] or p['contact_phone']}> (procurement plan)",
            'Ask to be notified when it is released', 'Sirish', 'SAFE_GENERAL', p['url'], parts,
            'Value unknown until released; delivery readiness neutral (no bench data)', today, subject=p['url']))
    return out


def live_actions(opportunities, today):
    """Live ICT roles and tenders: formal channel only; the recruiter checks the bench."""
    now = datetime.now(classify.CANBERRA)
    out = []
    for o in opportunities:
        if o['status_effective'] != 'Open' or not o['is_relevant'] or not o['closes_at']:
            continue
        closes = datetime.fromisoformat(o['closes_at'])
        days = (closes - now).days
        if days < 2:
            continue  # too late to respond well
        roles = set((o['role_families'] or '').split(', '))
        parts = dict(attractiveness=NEUTRAL, illoura_fit=0.9 if roles & ILLOURA_ROLES else 0.4, access=0.3,
                     timing=0.9 if days <= 14 else 0.6, delivery_readiness=NEUTRAL, commercial_potential=NEUTRAL)
        out.append(_action(
            'Respond through the formal channel', o['buyer'],
            f"Live {o['source']} {o['kind']}: {o['title']!r} ({o['source_id']}) closes {o['closes']}",
            'None while live: questions through the formal Q&A only', 'Check bench; respond only if a strong match',
            'Recruiter', 'FORMAL_CHANNEL_ONLY', o['url'], parts,
            'No informal contact while live (probity); delivery readiness neutral (no bench data)', today,
            subject=o['url'] or o['source_id']))
    return out


def build(renewals, primes, exemption_use, hot_list, opportunities, today):
    """All proposed actions, ranked, and the TODAY selection (5-15, varied action types)."""
    actions = (prime_actions(renewals, primes, today) + agency_actions(renewals, exemption_use, today)
               + plan_actions(hot_list, today) + live_actions(opportunities, today))
    actions.sort(key=lambda a: -a['score'])
    return actions, select_actions(actions)


def select_actions(actions):
    """Select a varied, ranked list from the current machine proposals."""
    limits = classify.config('scoring')['today_list']
    cap = max(2, limits['max_items'] // 3)
    eligible = [a for a in actions if a['priority'] != 'IGNORE']
    # The best action of each type first (so the list is never all one kind), then by score.
    picked, per_type = [], defaultdict(int)
    for a in [next(x for x in eligible if x['action_type'] == t) for t in dict.fromkeys(x['action_type'] for x in eligible)]:
        picked.append(a)
        per_type[a['action_type']] += 1
    for a in eligible:
        if len(picked) >= limits['max_items']:
            break
        if a in picked or per_type[a['action_type']] >= cap:
            continue
        picked.append(a)
        per_type[a['action_type']] += 1
    picked.sort(key=lambda a: -a['score'])
    for rank, a in enumerate(picked, 1):
        a['rank'] = rank
    return picked
