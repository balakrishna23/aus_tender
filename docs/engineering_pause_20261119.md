# Engineering pause: 19 November 2026

User instruction: **stop for now and document everything**. Engineering was stopped at
17:19:48 UTC (22:49:48 IST); final read-only state check was 17:20:27 UTC.
Do not resume deployment, imports or paid model calls until the user resumes the work.

## Exact state at the pause

| Component | State |
|---|---|
| Repository | `C:/work/aus_tender`, branch `feature/federal-intelligence-20261119` |
| Last committed baseline | `1589d8f`; this continuation's code is **uncommitted**, preserved in the working tree |
| Production PostgreSQL | **018_research_views**, verified after stopping the deployment |
| Disposable test PostgreSQL | **020_extended_demand**, 129 market tests passed with no skips |
| Active research runtime | `federal-automatic-refresh-20261119-v1`, unchanged by this continuation |
| New candidate runtime | `federal-engineering-finish-20261119-v4`, staged and tested, **not activated** |
| Candidate bundle SHA-256 | `1d474822c2fae608ca4171d1bf48d51559553a20b57d462b9994ca67e199de39` |
| Metabase | Installed and running, pinned OSS 0.65.6 image, loopback listener only |
| Local browser tunnel | Tested successfully, then stopped at the user's request |
| Paid RAP model calls | **None**; provider configuration and approved budget are missing |
| New source imports | REOS and TNV parsed locally; **not imported into production** |
| CRM export | Implemented and unit tested; **real production export has not run** |
| Backup/restore worker | Stopped during isolated market restore; no pg_dump/pg_restore process remains |
| Existing collection/mail jobs | Existing schedule/configuration preserved; no additional send or new timer |
| GitHub | No push or merge; no commit of this continuation |

The full engineering specification is **not finished**. Missing external permissions and
business inputs are separate from source integrations that still need engineering.

## Previously completed, still active

The earlier committed implementation remains intact: versioned government/business
registers, federal procurement snapshots, buyer identity proposals and review packets,
APS headcount, JSA IVI/shortages/projections, Tender Feed forward evidence, retained RAP
and WA history, supplier/healthcare research directories, panel evidence/calendar,
business-input CSV templates/imports, ABS SA4/LGA/POA boundaries, changes/readiness
reports, five analytical SQL views, and a dated research package for the newsletter.

The existing morning job runs a bounded optional research refresh after collection and
before RFQ composition. It refreshes collector-derived facts and reports, without
approving identities or acquiring every publisher's latest release. Failures preserve the
previous dated package and cannot cancel RFQs. The active runtime still uses the old
build, not the candidate described below.

Previously loaded evidence includes APS 102 current agencies / 198,529 staff; JSA
41,886 IVI observations, 311 shortage ratings and 1,422 employment baseline/projection
observations; 19,267 retained Tender Feed records; RAP directory 1,406 and 26 retained
reviewed findings; 16,751 historical WA rows. Those are prior verified import totals,
not a fresh recount at this pause. Full-active ABN coverage is not claimed.

## New source changes, not yet deployed

### RAP extraction

`market_intelligence/rap_extract.py` adds prepare/run/queue/review commands.

- Retained PDFs are checked against recorded SHA-256, bounded by file/page/text limits,
  and reduced to procurement-related pages. Email and Australian phone patterns are
  redacted. Copyright and document/source provenance remain attached.
- Prepared **30 real PDFs**. Four earlier candidates failed preparation with `SourceError`;
  the manifest records their document hashes. They need diagnosis/OCR/manual handling;
  no full-library extraction or accuracy target is claimed.
- A configurable HTTPS OpenAI-compatible Chat Completions endpoint uses an explicit
  model, API key, input/output prices, per-document budget and batch budget. There is no
  default model or assumed free pricing. Example keys are in `rap-model.env.example`.
- Eight structured fields require exact quotes on supplied PDF pages. Missing findings
  remain null with zero confidence. Citation presence checks do not verify interpretation.
- Migration `019_rap_extraction.py` stores runs, document/page/config identities, token
  usage, known/unknown cost, pricing, provider request identifiers, errors and review history.
- Repeating unchanged evidence/model/endpoint does not issue another request, including
  after a failed or uncertain paid call. Budget/price changes do not bypass that protection.
- Invalid model JSON still retains reported usage/cost. Unknown usage is never zero cost;
  the batch stops after a failed/uncertain request. Budgets are conservative request
  estimates, not a provider-enforced billing quota.
- A named reviewer must approve the matching document hash. Only approved findings for
  that exact RAP document can enter the report. Certification/membership remain distinct.

No live provider request, extraction quality sample, page-image review or production
approval happened. Thirty prepared documents are not thirty completed extractions.

### CRM export

`market_intelligence/crm.py` and `crm-export` create contact-free CSV/JSON/HTML plus a
checksum manifest, capped per lane (default 20). Federal and RAP scores retain their
different bases. Historical state and health-purpose directory records remain unscored.

Stable source keys reconcile imported relationship/activity data; active/past clients
are marked, and do-not-pursue entries are excluded. No CRM login, external write,
contact enrichment or outreach is implemented. CSV is the implemented interchange path.
Actual production generation and reconciliation against real Illoura inputs are pending.

The candidate `refresh.py` adds CRM export to the existing derived refresh. That new
stage is not active; production remains on the earlier runtime.

### Additional workforce sources

`demand.py` adds parsers/import commands for:

- **REOS October 2026:** 364 observations from six selected monthly indicators and
  their supported strata over the latest 13 months. Preserve proportions, denominators,
  percent strings, suppression and low-sample flags. Many government organisations and
  employers with fewer than five employees fall outside survey scope. This is not the
  whole workbook or a role-specific measure.
- **TNV May 2026:** 4,236 regional/occupation observations, retaining their overlapping
  aggregation levels. Do not sum totals across levels or add TNV to IVI. The publisher
  recommends annual comparisons. The original XLSX has invalid fractional
  `baseColWidth="8.43"`; only that cosmetic attribute is normalized in memory, with the
  original file retained and hashed. No source cell values are changed.

The actual retained workbooks parse successfully locally. Migration
`020_extended_demand_views.py` extends `v_demand_signals`; reports gain separate REOS
and TNV outputs. Production import, repeat-import verification, report generation and
Metabase queries over these new sources did **not** execute before the pause.

### Source acquisition findings

The official October 2026 aged-care ratings XLSX downloaded successfully (1,736,264
bytes). Its `Star Ratings` sheet has 2,602 source data rows and 15 columns. It was
inspected, **not integrated or imported**. The Department's [copyright terms](https://www.health.gov.au/using-our-websites/copyright)
restrict commercial reuse; do not assume the GEN site's CC BY licence covers an
externally hosted Department file. Permission/reuse review is required for business use.

The GEN directory landing page downloaded, but no actual provider-directory workbook
was acquired. The NDIS bulk endpoint returned HTTPError again; no NDIS rows or coverage
are claimed. `sources.yaml` records the distinction between parsed, imported and blocked.

## Metabase installed and verified

- Docker was installed on the existing VM; no AWS resource, public port or firewall rule
  was created. AWS project remains Free plan ACTIVE in the selected Sydney Region.
- Image: `metabase/metabase@sha256:21a0c25c12a9d6334c9efb3d74db9973707baaf543ae6abb43bd6d42818e01af`.
- Current container: `illoura-metabase-secure`; non-root UID/GID 2000, dropped
  capabilities, no-new-privileges, 1.5 CPU / 1,800 MB container limit, JVM 1,024 MB heap.
- Host networking, with the application itself bound to `127.0.0.1:3000` only. Separate
  PostgreSQL application database `illoura_metabase`; research connection uses
  `illoura_market_reader`. The reader has **zero mutation privileges** over market tables,
  verified after pausing, plus default read-only transactions and a 30-second SQL timeout.
- Private credentials, encryption key, session signing key and provision state are under
  `/home/ubuntu/illoura-market-data/metabase/`, owner-only. Never print or commit them.
- Dashboard ID 2, database ID 2, saved cards 40–44. Authenticated queries completed:
  agency spend 100 rows, supplier profiles 100, healthcare directory 100, demand 100,
  panel calendar 93. These saved queries intentionally limit displayed results to 100.
- Unauthenticated dashboard access was rejected. Local SSH tunnel health and login
  worked. Headless browser rendered the dashboard shell; screenshot captured while its
  title still said `0/5 loaded`. Do not claim completed visual review of loaded tables.
- No SMTP configuration, subscriptions, notifications, public sharing or paid AI enabled.
- Two earlier unsuccessful containers (`illoura-metabase`, `illoura-metabase-runtime`)
  are stopped and retained. Do not delete them or recovery artifacts without authorization.

Reproducible source helpers:

| File | Purpose / verification |
|---|---|
| `deploy/illoura_metabase.py` | Bootstrap/login, read-only connection, saved questions/dashboard; exercised successfully |
| `deploy/illoura_research_tunnel.py` | Pinned SSH local-only tunnel; exercised successfully, stopped at pause |
| `deploy/illoura_research_backup.py` | Dumps market + Metabase and optional isolated restore; stopped during first market restore, full helper verification pending |
| `deploy/illoura_metabase_runtime.py` | Generalized repeatable runtime/role setup; lint passed, **not executed or included in staged v4** |

The installed runtime was provisioned with retained private one-off scripts. The generalized
runtime helper preserves an existing container's environment; applying changed environment
settings to an existing container requires an explicit, planned recreation.

## Validation and interrupted deployment

Candidate v4 staged tests all passed: **129 market PostgreSQL**, **131 cloud operations**,
**44 routine** tests, no skips. Latest local market run: 129 tests, 39 environment-dependent
skips, no failures. Ruff unused/undefined-name checks passed for the market package and
new deployment helpers. No new full-repository Windows regression was completed in this
continuation; the prior 849-test result belongs to the earlier deployed refresh baseline.

The deployment worker then created a 428,405,832-byte market dump and began restoring it
to `illoura_market_restore_20261119t171723`. At the user's stop, the deployment parent,
backup wrapper and pg_restore processes were terminated. Final process check found no
pg_dump/pg_restore. The dump is retained, but its final checksum/full restore verification
was not recorded; the isolated restore is **partial/unverified**. No database was deleted.

Directory:
`/home/ubuntu/illoura-market-data/backups/engineering-before-20261119T171605Z`.
Metabase backup/restore, production migrations 019/020, REOS/TNV production imports,
real CRM export, runtime activation and final newsletter rehearsal had not executed.
The last worker JSON still says `running` because termination bypassed its finalizer;
**engineering-paused-20261119.json and engineering-pause-final-state-20261119.json supersede it**.

The earlier schema-018 verified full backup/restore remains the last fully validated
recovery baseline. Do not replace it with the interrupted backup's unverified status.

## Resume order, only after the user resumes

1. Read this note and private `HANDOFF.md`; inspect Git status and the final pause evidence.
2. Review preserved source changes, particularly the generalized runtime/backup helpers.
   Do not rerun the stopped deployment worker blindly or treat its stale JSON as active.
3. Inspect the partial restore database/dump without deleting either. Finish a new complete
   market + Metabase recovery set and actual restore drill, including recovery keys privately.
4. Stage a new immutable source bundle including the final runtime helper. Rerun meaningful
   PostgreSQL/operations/routine checks for changed code; run real new-source parser/import
   tests on the disposable database and verify repeated imports are unchanged.
5. Once checks/backup pass, migrate production 019/020, import REOS/TNV, generate actual
   CRM/RAP/demand/readiness reports, verify their content and recheck Metabase's new view.
6. Activate the validated research build, exercise bounded refresh and no-send newsletter
   rehearsal, inspect final package/layouts, record hashes and preserve timer/send state.
7. Configure RAP endpoint/key/model/pricing/budgets privately before any paid calls. Run a
   bounded real sample, review page evidence and accuracy, then approve only verified fields.
8. Continue source integrations still unbuilt: other state/territory disclosures and
   directories, permitted named healthcare feeds, grants, budgets, annual reports/insourcing,
   ABS remoteness/crosswalks and automatic publisher acquisition. A generic loader or a
   directory of links is not completion of those source-specific feeds.

Remaining acceptance inputs include reviewed buyer mappings (last measured reviewed
value coverage 0%, machine proposals 39.4% across all recent federal original value),
panel relevance/verified portal facts, Illoura eligibility evidence, aggregate bench and
occupation mappings, relationships/activity, invitations/outcomes and verified lineage.
Do not invent these or convert missing inputs to approvals, zero demand or eligibility.

## Private evidence and recovery pointers

Workspace: the private local workspace named in the private `HANDOFF.md`.
This continuation's private evidence is `reports/engineering_finish_20261119/`:
runtime/install/provision reports, staged deployment log, pause/final state, 30 prepared
PDF packages and manifest, and `metabase_dashboard.png`. The preserved source snapshot
and hash manifest are in the same directory when the documentation step completes.

Private local login: `~/.illoura/research-browser/login.private.json`. It contains real
login details; never put its contents in reports/chat/Git. Restart access only after resume:
`uv run --with paramiko python C:/work/aus_tender/deploy/illoura_research_tunnel.py`.
Then use the private login URL, which points to localhost port 3030. No tunnel remains now.

SSH uses `~/.illoura/aws-trial/known_hosts` and `admin.pem`, with strict pinned-host checks.
Private helper `tmp/market_remote_continuation.py` can fetch evidence; it refuses to replace
staged workers/builds. Use new version names when resuming. Never print server addresses,
mailbox addresses, environment values, database URLs, session/setup tokens or passwords.
