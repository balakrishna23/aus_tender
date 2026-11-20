"""Render an internal brief from this run's public checks and reviewed editorial notes."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r'/opt/aus_tender')
from classify import closing_time
from render_newsletter_preview import render
from mail_config import load_mail_config, load_link_id


def supplemental_failures(out):
    """Use this run's wrapper results and per-source curated checks only."""
    failures = set()
    refresh = out / 'research_refresh.json'
    if refresh.exists():
        for item in json.loads(refresh.read_text(encoding='utf-8')):
            if item.get('status') != 'completed':
                failures.add(item['step'])
    else:
        failures.add('research_refresh (status unavailable)')
    # The deployed checker prints one JSON record per source to this run's log.
    # Also accept explicitly staged JSON manifests, without reading retained data.
    checks = []
    for name in ('curated_source_checks.json', 'public-source-checks.json'):
        path = out / name
        if path.exists():
            checks.extend(json.loads(path.read_text(encoding='utf-8')))
    log = out / 'curated_source_checks.log'
    if log.exists():
        for line in log.read_text(encoding='utf-8').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and 'source_url' in item:
                checks.append(item)
    for item in checks:
        if item.get('status') != 'fetched':
            failures.add(item.get('source_url') or item.get('organisation') or 'curated source (unnamed)')
    return sorted(failures)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    out = parser.parse_args().out.resolve()
    if (out/'send_receipt.json').exists():
        raise ValueError('This edition already has a send receipt')
    config = json.loads((out/'editorial_notes.json').read_text(encoding='utf-8'))
    now = datetime.now(timezone.utc)
    india = ZoneInfo('Asia/Kolkata')
    today = now.astimezone(india).date()
    if config['edition_date'] != today.isoformat():
        raise ValueError('Editorial date does not match today in India')
    report = json.loads((out/'source_refresh.json').read_text(encoding='utf-8'))
    final = json.loads((out/'finalization.json').read_text(encoding='utf-8'))
    expected={'contract_api','aus_contract','aus_tender','planned_procurement','standing_offer','buy_ict','act_tender','nsw_tender'}
    rows=report.get('results', [])
    if (not isinstance(rows,list) or len(rows)!=8 or not all(isinstance(row,dict) for row in rows)
            or {row.get('spider') for row in rows}!=expected
            or not isinstance(report.get('feed'),str) or not report['feed'].strip() or 'FAILED' in report['feed']):
        raise ValueError('Source sweep and essential evidence builds must finish first')
    passed = {x['step'] for x in final['steps'] if x.get('exit_code') == 0}
    problems = {x.get('spider') for x in report['results'] if x.get('status') != 'OK' or x.get('crawl_failed') or x.get('errors')}
    # BuyICT is the brief's only source: a problem there, any failure that is not a named source
    # (the feed, a missing success flag) or a run the run script did not approve stops the brief.
    if problems:
        try:
            run_state = json.loads((out/'cloud_run.json').read_text(encoding='utf-8'))
            approved = run_state.get('source_exit_code') in (0, 1) and run_state.get('source_gaps') == sorted(problems, key=str)
        except (OSError, ValueError, AttributeError, TypeError):
            approved = False
        sources_ok = (approved and 'buy_ict' not in problems and report.get('success') is False
                      and isinstance(report.get('feed'), str) and 'FAILED' not in report['feed'])
    else:
        sources_ok = report.get('success') is True
    if len(report['results']) != 8 or 'feed' not in report or not sources_ok or not {'intelligence', 'data_quality', 'newsletter_evidence'} <= passed:
        raise ValueError('Source sweep and essential evidence builds must finish first')

    def deadline(moment):
        return (moment.astimezone(ZoneInfo('Australia/Canberra')).strftime('%d %B, %I:%M %p Canberra')
                + ' / ' + moment.astimezone(india).strftime('%d %B, %I:%M %p IST'))

    due, upcoming = [], []
    for key, notes in config['opportunities'].items():
        record = json.loads((out/'shortlist'/(key+'.json')).read_text(encoding='utf-8'))
        if datetime.fromisoformat(record['checked_at_utc']).astimezone(india).date() != today:
            raise ValueError('Stale public check: '+key)
        data = record['content']
        details = {x.get('label'): x.get('value') for x in data['mainDetails']}
        req = {x.get('label'): x.get('value') for x in data.get('requirements', []) if x.get('label')}
        end = closing_time(details.get('RFQ closing date') or details.get('RFI closing date'))
        if not end or end <= now or not data.get('rfqOpen') or data.get('is_cancelled'):
            raise ValueError('Selected opportunity is no longer open: '+key)
        paragraphs = ['Closes '+deadline(end)+'.']
        fields = [('Location of work', 'Location'), ('Working arrangements', 'Work'),
                  ('Security clearance', 'Clearance'), ('Maximum number of candidates per seller', 'Candidate cap per seller'),
                  ('Initial contract duration', 'Initial term')]
        paragraphs.append('; '.join(label+': '+str(req[field]) for field, label in fields if req.get(field))+'.')
        q = closing_time(details.get('Deadline for asking questions'))
        if q:
            paragraphs.append('Questions have closed; review published answers and the complete pack.' if q <= now
                              else 'Questions close '+deadline(q)+'. Use the formal BuyICT Q&A channel.')
        paragraphs.extend(notes)
        entry = {'heading':details['Buyer']+' | '+data['title']+' | '+key,
                 'paragraphs':paragraphs, 'links':[{'label':key+' official listing','url':record['source_url']}]}
        (due if end.astimezone(india).date() == today else upcoming).append(entry)
    sections = []
    if due:
        sections.append({'title':'Decisions due today', 'items':due})
    if upcoming:
        sections.append({'title':'Prepare the next responses', 'items':upcoming})
    sections.extend(config.get('additional_sections', []))
    gaps = sorted(problems)
    research = json.loads((out/'official_source_collection.json').read_text(encoding='utf-8'))
    research_gaps = sorted({x['organisation'] for x in research if x['status'] != 'collected'})
    supplemental_gaps = supplemental_failures(out)
    baseline = json.loads((out/'baseline.json').read_text(encoding='utf-8'))
    with sqlite3.connect(r'/opt/aus_tender/databases/contract_api.db') as db:
        contracts = db.execute('SELECT count(*) FROM contracts').fetchone()[0]
        versions = db.execute('SELECT count(*) FROM contract_versions').fetchone()[0]
    health = 'All eight procurement source stages completed successfully.' if not gaps else 'Source stages with a problem today: '+', '.join(gaps)+'. Their data is from the last good run; BuyICT, which this brief is built from, completed normally.'
    warnings = sum(x.get('warnings', 0) for x in report['results'])
    if warnings:
        health += f' {warnings} source warning(s) were recorded; see the source gaps below.'
    sections.append({'title':'Coverage and access', 'items':[{'heading':'Checked '+today.strftime('%d %B %Y'), 'paragraphs':[
        health+f" The routine added {contracts-baseline['contracts']:,} contract records and {versions-baseline['contract_versions']:,} notice versions to the AWS database; overlapping historical notices may be included. {len(config['opportunities'])} selected public listings were rechecked today.",
        config.get('coverage_note', '') + (' Additional research pages unavailable: '+', '.join(research_gaps)+'. ' if research_gaps else '')
        + (' Supplemental research failures (separate from the eight procurement collectors): '+', '.join(supplemental_gaps)+'. ' if supplemental_gaps else '')
        +'Invitation access, secure attachments, complete response packs and candidate fit remain unverified. Use formal procurement channels for live opportunities.'], 'links':[]}]})
    sender, recipient = load_mail_config()
    package = {'subject':config['subject'], 'preheader':config['preheader'],
               'edition_label':today.strftime('%A %d %B %Y')+' | Private internal brief',
               'greeting':'Hi Sirish,', 'introduction':config['introduction'], 'sections':sections,
               'footer':'Illoura Services | Private internal brief | '+today.strftime('%d %B %Y')+' | Procurement data attributed to the linked official sources.',
               'from':sender, 'to':recipient}
    (out/'newsletter_editorial.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
    render(package, out)
    request = {'link_id':load_link_id(),'from_address':package['from'],
               'to':package['to'],'subject':package['subject'],'response_fields':['id','thread_id','label_ids','snippet'],
               'payload':{'mime_type':'multipart/alternative','parts':[
                   {'mime_type':kind,'charset':'UTF-8','body':{'content':(out/name).read_text(encoding='utf-8')}}
                   for kind,name in [('text/plain','newsletter_preview.txt'),('text/html','newsletter_preview.html')]]}}
    (out/'outbound_send_request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    review=json.loads((out/'newsletter_review.json').read_text(encoding='utf-8'))
    review.update(status='prepared_for_send', verified_public_listings=len(config['opportunities']),source_gaps=gaps,
                  send_authorization='User requested: run routine and send newsletter')
    (out/'newsletter_review.json').write_text(json.dumps(review,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
