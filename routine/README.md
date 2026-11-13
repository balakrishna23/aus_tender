# Server newsletter routine

These are the audited server copies, retaining the Linux installation paths
`/opt/illoura-routine` and `/opt/aus_tender`. This import is repository work only;
server is the source of truth until the deploy-routine command exists.

The timer enters through `deploy/illoura_daily_brief.py`, which calls
`deploy/illoura_cloud_collect.py` for collection. That collector invokes
`run_once.py`; the daily brief then invokes shortlist refresh, composition,
preview/layout checks and the reviewed sender. Collection alone never sends.

| File | Purpose |
| --- | --- |
| `run_once.py` | Locks the run, captures baselines, runs procurement and supplemental research, gates evidence completion. |
| `scripts/newsletter_research_sweep.py` | Runs official research, BuyICT news, curated checks and WA contracts. |
| `scripts/collect_official_research.py` | Collects allowlisted research with robots checks and provenance. |
| `scripts/collect_buyict_news.py` | Collects public BuyICT news. |
| `scripts/finalize_daily_sweep.py` | Rebuilds intelligence, quality reports and newsletter evidence. |
| `scripts/prepare_newsletter_evidence.py` | Extracts candidates for editorial review. |
| `scripts/refresh_daily_shortlist.py` | Rechecks selected public BuyICT listings. |
| `scripts/compose_checked_brief.py` | Composes checked editorial content and procurement/supplemental coverage. |
| `scripts/render_newsletter_preview.py` | Produces HTML, text and unsent EML previews. |
| `scripts/check_newsletter_layout.py` | Checks desktop/mobile layout using browser rendering. |
| `scripts/send_gmail.py` | Validates source, layout and content review, reconciles Sent, records attempts before sending. |
| `scripts/mail_config.py` | Loads and validates private mail settings. |
| `scripts/test_sender.py` | Preserved server mock sender checks; manual, outside the timer. |
| `scripts/build_rap_partner_register.py` | Manual RAP evidence register builder, outside the timer. |
| `scripts/export_five_buyict_requirements.py` | Manual requirements export, outside the timer. |
| `legacy/complete-cloud-review.py` | Historical one-off review record; never run by the timer. |
| `legacy/send-reviewed-cloud-brief.py` | Historical one-off reviewed send; never run by the timer. |

Before deploying, provision private `KEY=VALUE` settings in
`/etc/illoura-routine/mail.env`: `ILLOURA_MAIL_SENDER` and
`ILLOURA_MAIL_RECIPIENT`. Composition also needs `ILLOURA_MAIL_LINK_ID` for the
legacy outbound request metadata. Environment variables override file values;
`ILLOURA_MAIL_ENV` overrides the file path for tests. Missing or malformed
addresses fail closed. The sender is normalized to lower case. Message-ID uses
the configured sender domain. Keep this file and Gmail credentials outside source.

Supplemental failures are read from this run's `research_refresh.json`, individual
curated JSON manifests if staged, and JSON records in `curated_source_checks.log`.
The coverage note names failed sources separately from the eight mandatory
procurement collectors. Missing wrapper status is explicitly reported. Retained
research files outside the current run are not used to claim current coverage.

Isolated regression tests (Google transport mocked; no real databases or network):

```powershell
$env:GSHEETS_AUTO_SYNC='False'
python -m unittest discover -s tests -p "test_routine_*.py"
python -m unittest discover -s tests
```

The `run_once.py` execution tests skip on Windows because it requires `fcntl`.
