#!/usr/bin/env python3
"""Score outreach emails and compare two rounds (e.g. before/after a writer change).

Reads the emails the agent logged in the Outreach sheet ('sent' tab). Each email gets
  - automatic checks: promises made, tender details used, agency context used, upcoming
    plan mentioned, personal greeting, length;
  - an AI reviewer's grades (1-5) on personalisation, credibility, clarity, tone and
    likelihood of a reply, with a one-line reason.
The reviewer uses the same AI provider as the writer, so treat its grades as a second
opinion; the automatic checks are the hard evidence.

  python evaluate_outreach.py --rounds 3 --size 3    # last three rounds of 3 emails, oldest first
Writes logs/evals/<time>.json and prints a comparison.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from statistics import mean

import requests
from dotenv import load_dotenv

import outreach_agent as agent
from project_paths import PROJECT_DIR

DETAIL_PATTERNS = {  # tender details an email can show it noticed
    'clearance': r'\b(baseline|nv1|nv2|negative vetting|clearance)\b',
    'seniority': r'\b(aps ?\d|el ?[12]|senior|lead|executive level)\b',
    'location': r'\b(act|nsw|vic|qld|wa|sa|tas|nt|canberra|sydney|melbourne|brisbane)\b',
    'working arrangement': r'\b(hybrid|onsite|on-site|remote)\b',
    'start date': r'\b(commenc\w*|start\w*)\b[^.]{0,40}\b\d{1,2}\b',
    'duration': r'\b\d+[\s-]?(week|month|year)s?\b|\b(six|twelve)[\s-](week|month)s?\b',
    'criteria': r'\b(criteria|criterion|capability|approach)\b',
}
JUDGE_RULES = """You review cold business-development emails from a government contract-personnel
supplier to the published contact on a tender. Grade strictly, 1 (poor) to 5 (excellent):
- personalisation: clearly about THIS tender and agency, not a mail-merge; addresses the reader
  by name or by their team rather than "there"
- credibility: no promises the supplier cannot know and no invented facts. Give 1 or 2 if the
  email says candidates are available, can start or commence, meet or satisfy the requirements,
  will work the hours, or that the company itself holds a clearance
- clarity: short, easy to scan, one clear point
- tone: professional, respectful of the formal tender process, no hype
- reply_likelihood: would a busy procurement officer reply or keep it on file
Reply with JSON only: {"personalisation": n, "credibility": n, "clarity": n, "tone": n,
"reply_likelihood": n, "reason": "one sentence"}"""


def auto_checks(email_row):
    body, subject = email_row['body'], email_row['subject']
    facts = {'reference': email_row['reference'], 'greeting_name': 'there'}
    greeting = re.match(r'\s*Hi ([^,\n]+),', body)
    promises = [what for pattern, what in agent.PROMISE_PATTERNS if re.search(pattern, body, re.I)]
    details = [name for name, pattern in DETAIL_PATTERNS.items() if re.search(pattern, body, re.I)]
    return {
        'words': len(body.split()),
        'promises': promises,
        'details_used': details,
        'agency_context': bool(re.search(r'\b(regularly|frequently|often|routinely|ongoing)\b', body, re.I)),
        'mentions_plan': bool(re.search(r'\b(planned|upcoming|future (procurement|requirement)|later this year)\b',
                                        body, re.I)),
        'greeting': ('generic' if not greeting or greeting.group(1).strip().lower() == 'there' else
                     'team' if greeting.group(1).strip().lower().endswith('team') else 'person'),
        'mentions_reference': str(email_row['reference']).lower() in (subject + body).lower(),
        'checks_passed': not agent.check_draft(subject, body, dict(facts, greeting_name=(greeting.group(1).strip()
                                                if greeting else 'there')), agent.load_profile()),
    }


def judge(email_row):
    key = agent.env('GROQ_API_KEY') or agent.env('groq_api_key')
    payload = {'model': agent.env('OUTREACH_LLM_MODEL', 'openai/gpt-oss-120b'), 'temperature': 0,
               'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': JUDGE_RULES},
                            {'role': 'user', 'content': f"Tender: {email_row['title']} ({email_row['reference']}) "
                                                        f"for {email_row['buyer']}\n\nSubject: {email_row['subject']}"
                                                        f"\n\n{email_row['body']}"}]}
    response = requests.post('https://api.groq.com/openai/v1/chat/completions',
                             headers={'Authorization': f'Bearer {key}'}, json=payload, timeout=90)
    response.raise_for_status()
    return json.loads(response.json()['choices'][0]['message']['content'])


GRADES = ['personalisation', 'credibility', 'clarity', 'tone', 'reply_likelihood']


def summarise(rows):
    return {
        'emails': len(rows),
        'avg words': round(mean(r['auto']['words'] for r in rows)),
        'promises made': sum(len(r['auto']['promises']) for r in rows),
        'avg tender details used': round(mean(len(r['auto']['details_used']) for r in rows), 1),
        'agency context used': sum(r['auto']['agency_context'] for r in rows),
        'plan mentioned': sum(r['auto']['mentions_plan'] for r in rows),
        'greeting (person/team/generic)': '/'.join(str(sum(r['auto']['greeting'] == g for r in rows))
                                                  for g in ('person', 'team', 'generic')),
        'AI-written (not template)': sum(r.get('writer') == 'ai' for r in rows),
        **{f'AI grade: {g}': round(mean(r['judge'].get(g, 0) for r in rows), 2) for g in GRADES},
    }


def main(argv=None):
    load_dotenv(PROJECT_DIR / '.env')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--rounds', type=int, default=2, help='How many rounds to compare (newest last)')
    parser.add_argument('--size', type=int, default=3, help='Emails per round')
    args = parser.parse_args(argv)
    rows = agent.AgentSheet().book.worksheet('sent').get_all_records()[-args.rounds * args.size:]
    scored = []
    for row in rows:
        row = {k: str(v) for k, v in row.items()}
        scored.append(dict(row, auto=auto_checks(row), judge=judge(row)))
    groups = [(f'v{n + 1}', scored[n * args.size:(n + 1) * args.size]) for n in range(len(scored) // args.size)]
    summaries = {name: summarise(group) for name, group in groups}
    round_of = {id(r): name for name, group in groups for r in group}
    width = max(len(k) for k in next(iter(summaries.values())))
    print(f"\n{'':{width}}  " + '  '.join(f'{n:>8}' for n in summaries))
    for key in next(iter(summaries.values())):
        print(f'{key:{width}}  ' + '  '.join(f'{str(s[key]):>8}' for s in summaries.values()))
    print()
    for r in scored:
        print(f"[{round_of.get(id(r))}] {r['reference']}: grades "
              f"{[r['judge'].get(g) for g in GRADES]} | promises {r['auto']['promises'] or 'none'} | "
              f"details {r['auto']['details_used']}\n    reviewer: {r['judge'].get('reason')}")
    out = PROJECT_DIR / 'logs' / 'evals' / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'summary': summaries, 'emails': scored}, indent=1, ensure_ascii=False), encoding='utf-8')
    print(f'\nSaved {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
