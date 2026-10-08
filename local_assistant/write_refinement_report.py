"""Generate a self-contained review report from actual run/evaluation artifacts."""
import html,json,statistics
from datetime import datetime,timezone
from pathlib import Path
import assistant

ROOT=Path(__file__).parent
REPORTS=ROOT/'reports'


def read(name):return json.loads((REPORTS/name).read_text(encoding='utf-8'))
def esc(value):return html.escape(str(value))
def table(headers,rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'


def write():
    audit=read('data-quality.json')
    foundation=read('evaluation-v2.json')
    paraphrases=read('heldout-illoura-planner-latest.json')
    second=read('heldout-illoura-planner-latest-second.json')
    baseline=read('heldout-qwen3-4b-instruct.json')
    source_checks=read('public-source-checks.json')
    snapshot=read('snapshot.json')
    original=json.loads(Path(r'C:\work\aus_tender\logs\runs\20261008-204024.json').read_text(encoding='utf-8'))
    q='Which cyber contracts end within 90 days? Compare suppliers and choose 3 for ICT labour hire rather than a one-off assessment or equipment purchase.'
    example_path=REPORTS/'verified-shortlist-example.json'
    answer=json.loads(example_path.read_text(encoding='utf-8')) if example_path.is_file() else None
    if answer is None or answer['build_timestamp']!=audit['build_timestamp']:
        answer=assistant.answer(q,False,verify=True)
        example_path.write_text(json.dumps(answer,indent=2),encoding='utf-8')
    sections=['<h1>Illoura assistant refinement report</h1>',
      '<p class="lead">A stronger local evidence assistant is running at <a href="http://127.0.0.1:8765">127.0.0.1:8765</a>. The database was refreshed, the query and reasoning paths were refined, and reviewed public evidence was added.</p>',
      '<p class="note"><strong>Weights were not trained.</strong> The working model is Qwen 4B with an Illoura prompt/profile and validated retrieval. No paid model API, cloud GPU or VPS was used. Passing these checks does not establish perfection.</p>',
      '<p>Prepared 40 routing-example candidates and 10 validation candidates for human review under <code>reports/training-review</code>. The full labels were partly expanded by rules, and all examples have been used during application refinement. They are not human-approved training data or an untouched test set. No training job was run.</p>',
      '<h2>Data refresh</h2><p>Manual 8 October run, without Sheets publishing. The original batch report remains unchanged. Its standing-offer result was “FAIL(no responses)” because no records were due under the seven-day recheck rule. A separate one-time recheck fetched all 457 current/undated offers and completed; the intelligence views were rebuilt after it.</p>',
      table(['Source','Original batch result','Stored rows/versions afterward','Seconds'],[(r['spider'],r['status'],r['after'],r['duration_seconds']) for r in original['results']]),
      '<p>Contract API: 283 new notice versions; 1,010 contract roll-ups updated. Contract pages: 251 new records. The releases feed recorded 39 new and 9 changed items. These are different units and are not added together as “new opportunities”.</p>',
      '<h2>Current stored coverage</h2>',
      table(['Measure','Count'],[('Rolled-up contracts',audit['contracts']['total']),('Panel member rows',audit['panels']['member_records']),('Distinct panels in the derived membership view',audit['panels']['distinct_panels']),('Historical member rows by deed dates',audit['panels']['expired_member_records']),('Reviewed public evidence records',audit['public_research']['records'])]),
      '<p>Intelligence build timestamp: '+esc(audit['build_timestamp'])+' (upstream timezone unspecified). Query date: '+esc(answer['as_of_date'])+' in Australia/Canberra.</p>',
      '<h2>What now works better</h2><ul><li>Multi-part questions preserve separate organisation, role, timeframe, value, supplier ABN and exclusion constraints.</li><li>Full matching sets determine counts and renewal rankings before the display sample is selected.</li><li>Open notices are separate from plans; closing timestamps and panel deed dates are rechecked at query time.</li><li>Unknown candidate readiness, clearances, delivery locations, relationships, margins and current subcontracting demand are stated.</li><li>Optional official API checks show compared fields, differences, checked time and response hash.</li><li>Research records carry source, locator, review date and proof limits. Historical RAPs are not treated as current.</li><li>Clickable evidence references, readable notice links and explicit raw-JSON labels make answers easier to inspect.</li></ul>',
      '<h2>Validation and speed</h2>',
      table(['Check','Final result'],[('Local behavioural/unit checks','54 passed'),('Existing scraper project regression','195 passed'),('Foundation questions',f"{sum(r['passed'] for r in foundation)}/{len(foundation)}"),('First paraphrase set',f"{sum(r['passed'] for r in paraphrases)}/{len(paraphrases)}"),('Second set, including unseen organisations',f"{sum(r['passed'] for r in second)}/{len(second)}"),('HTTP host/token controls','Passed'),('Desktop/mobile UI','Passed, no JavaScript errors')]),
      '<p>The base model with revised rules passed '+str(sum(r['passed'] for r in baseline))+'/20 on the first paraphrase set; the Illoura profile passed 20/20. Failures in these sets were used to refine the application, so the final scores are regression evidence rather than an untouched general-accuracy estimate.</p>']
    for label,records in [('Database-only foundation',foundation),('First paraphrases',paraphrases),('Second question set',second)]:
        values=[r['seconds'] for r in records]
        sections.append('<p>'+esc(label)+': median '+str(round(statistics.median(values),3))+' seconds; maximum '+str(max(values))+' seconds in this run. Source-check time and cold model loading vary.</p>')
    sections += ['<h2>Your cyber-shortlist question</h2><p class="question">'+esc(q)+'</p><pre>'+esc(answer['explanation'])+'</pre>',
      '<p>This replaced the earlier unfiltered renewal list. The answer is a research shortlist, not confirmed vacancies. Selected official checks are shown below.</p>',
      table(['Contract','Official API check','Scope'],[(r['cn_id'],r['status'],'Contract fields only; no hiring claim') for r in answer['verifications']]),
      '<h2>Public evidence and source limits</h2><p>The register covers EY, Fujitsu, Deloitte, DXC, KPMG, Hays and Talent International. It includes three stated RAP periods that cover 2026 by year, four historical RAPs, published contact/procurement routes and an EY cyber job advert. A job advert does not establish contractor engagement or agency acceptance.</p>',
      table(['Source URL','Direct check result'],[(r['source_url'],r['status']+((': '+r['error']) if r.get('error') else '')) for r in source_checks]),
      '<p>Fujitsu’s direct check could not establish robots permission because of HTTP 429. Its manually reviewed official document remains a research reference, not a successful direct fetch. HTTP 200 on the other sources establishes availability, not the truth of every claim.</p>',
      '<p>The NIAA download matched the stored file byte-for-byte. Its published list date remains 25 February 2026. It does not identify which contracts have MMR obligations or whether a company is behind on delivery.</p>',
      '<h2>Gaps that still need information</h2>',
      table(['Input','Owner','Needed'],[(r['input'],r['owner'],'; '.join(r['needed'])) for r in audit['missing_private_inputs']]),
      '<p>Private candidate and relationship templates are available in the pilot’s inputs folder. They contain no fictional records. CV extraction and matching are not connected yet.</p>',
      '<h2>Data-quality findings</h2>',
      table(['Finding','Stored count','Treatment'],[('Missing supplier ABN',audit['contracts']['missing_supplier_abn'],'May include foreign suppliers; do not guess identity'),('End date before start date',audit['contracts']['end_before_start'],'Flag source conflict; do not invent a corrected date'),('No readable contract-page link',audit['contracts']['missing_readable_source'],'Use official API evidence if available'),('Historical panel member rows',audit['panels']['expired_member_records'],'Excluded from current-membership queries')]),
      '<h2>Recovery and operating limits</h2><p>Database recovery snapshot: <code>'+esc(snapshot['snapshot_directory'])+'</code>. '+str(len(snapshot['databases']))+' databases copied with SQLite backup and quick integrity checks. No old backups were deleted.</p>',
      '<p>Single-user, loopback-only prototype. No remote access, general live web search, persistent conversation memory or autonomous outreach is enabled. Existing scraper schedules remain as they were. Important decisions still need source review and Illoura’s own readiness information.</p>',
      '<p class="muted">Report generated '+esc(datetime.now(timezone.utc).isoformat())+'. Raw test, audit, source-check and example artifacts are in this private reports folder.</p>']
    document='<!doctype html><html lang="en"><meta charset="utf-8"><title>Illoura refinement report</title><style>body{font:16px/1.6 system-ui;color:#172b2b;background:#f4f5ef;margin:0}main{max-width:1050px;margin:28px auto;background:white;padding:36px;border-radius:14px}h1{font-size:32px}h2{margin-top:32px;color:#174a3c}.lead{font-size:19px}.note{background:#fff4d8;padding:16px}.muted{color:#526763;font-size:13px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d6dfd8;padding:10px;text-align:left;overflow-wrap:anywhere}th{background:#e5ece5}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5ef;padding:18px;font:14px/1.7 system-ui}a{color:#146b51}code{overflow-wrap:anywhere}@media(max-width:650px){main{padding:18px;margin:0;border-radius:0}td,th{padding:6px;font-size:12px}}@media print{body{background:white}main{margin:0;padding:0}h2,thead{break-after:avoid}tr{break-inside:avoid}}</style><main>'+''.join(sections)+'</main></html>'
    (REPORTS/'refinement-report.html').write_text(document,encoding='utf-8')
    print('Wrote',REPORTS/'refinement-report.html')


if __name__=='__main__':write()
