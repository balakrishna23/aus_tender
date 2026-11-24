# Research refresh handoff — 23 November 2026

Implementation only: no git commands, network calls, deployments, timer changes,
real sends, send-state changes, or model calls were made while implementing this.

Local validation used a cached Python 3.11 environment because uv's cache lock
was inaccessible. Its versions match `requirements-tested.txt`,
`requirements-market.txt`, `requirements-market-test.txt` and
`requirements-deploy.txt` exactly. Targeted tests passed. Final full discovery:
1,070 tests, 1,000 passed, 69 skipped, one unrelated error in
`test_routine_layout.MobileCoverageLayout.test_long_source_url_does_not_expand_mobile_viewport`:
Playwright could not create a Windows subprocess pipe (`WinError 5`). Optional
PostgreSQL and real POSIX process/lock tests still require the server gate.

## Design and files

- `market_intelligence/freshness.py`: fixed source registry, check intervals,
  transport permissions, current-snapshot queries, and informational reporting.
  Monthly manual registers use last successful import, not the daily inbox scan,
  for their next due date. Old publication dates do not imply staleness.
- `market_intelligence/research_refresh.py`: separate source attempts, immutable
  raw directories, verified manifests, idempotent existing importers, current
  pointer verification, acquired-run replay after a crash, private JSON reports.
  A PostgreSQL session lock protects concurrent application invocations.
- `market_intelligence/acquire_jsa.py`: IVI ANZSCO4 states/territories, May
  projections, four-digit unit-group shortage list, plus existing REOS/TNV.
  Download filenames retain importer selectors. Six-digit ANZSCO/OSCA is excluded.
- `market_intelligence/acquire_aps.py`: newest **June** release from the listing;
  December releases are observed in the listing but never selected for import.
- `market_intelligence/acquire_health.py`: bounded pagination (20 pages), newest
  slug month/year, all pagination evidence retained. The collection page supplied
  to the existing importer is the actual page containing the chosen edition.
- `market_intelligence/acquire_wa.py`: bounded official-host CKAN/CSV acquisition,
  existing WA row/header/normalisation helpers, contact-free research export.
  Only WA accepts the known ordinary HTTP 200 HTML robots fallback as an absent
  robots file, recording `robots_absent_html_served` in every such run. HTML
  challenges, 202 and WAF-action headers stop collection. Other clients continue
  rejecting HTML robots responses.
- `market_intelligence/acquire_common.py`: shared WAF status/header guard.
- `market_intelligence/migrations/versions/023_research_freshness.py`: revision
  `023_research_freshness`, parent `022_aged_care`; append-only received/started/
  acquired/finished events, current state, `market.v_research_freshness`.
- `market_intelligence/__main__.py`: three new commands and migration label 023.
- `deploy/illoura_research_refresh.py`: root wrapper using the exact morning
  `/var/lib/illoura-market-refresh/refresh.lock`, nonblocking flock, blackout
  checks before and after locking (05:30 inclusive to 09:30 exclusive Sydney),
  ubuntu application execution, nice/ionice, process-group timeout termination,
  atomic mode-0600 summary under `/var/lib/illoura-research-refresh/`.
- `deploy/illoura-research-refresh.service` and `.timer`: daily 10:15 Sydney,
  up to ten minutes random delay, no persistent catch-up, 2,700-second systemd
  limit with control-group termination. No timer is enabled by installation.
- `deploy/illoura_cloud_health.py`: bounded, allowlisted informational summary
  outside the failing `checks` map. Freshness cannot change overall health or send.
- `deploy/aws/illoura_aws.py`: `research-refresh install|enable|disable|status`.
  Installation hashes staged files and runs wrapper tests before installing.
- New tests: `test_market_freshness.py`, `test_market_freshness_database.py`,
  `test_market_refresh_discovery.py`, `test_cloud_research_refresh.py`; existing
  health and deployment CLI tests extended. All fixtures are synthetic.

Daily procurement and forward imports remain the morning helper's responsibility;
this job monitors their existing pointers. RAP is directory-only and uses the
existing reviewer/verifier/import chain. RAP commitments and paid extraction are
outside the automatic job.

## CLI

With the private database URL already in the process environment:

```sh
python -m market_intelligence migrate
python -m market_intelligence freshness-report
python -m market_intelligence inbox-status --data-root /home/ubuntu/illoura-market-data
python -m market_intelligence research-refresh --data-root /home/ubuntu/illoura-market-data
python -m market_intelligence research-refresh --data-root /home/ubuntu/illoura-market-data --source jsa_ivi --force
```

For a private environment file, use the application module entry point instead:

```sh
python -m market_intelligence.research_refresh \
  --environment /home/ubuntu/illoura-market-data/database.env \
  --data-root /home/ubuntu/illoura-market-data --source jsa_ivi --force
```

`--force` bypasses check intervals/hash skipping, but does not bypass robots,
licences, parser validation, inbox processed hashes, or promotion guards.
Application invocation completion is independent of individual source failures;
inspect the source states and report, not just the process exit code. It produces
research freshness reports; the existing morning job rebuilds the brief package.

## Manual inbox

Create private ubuntu-owned directories at `ROOT/inbox/<source>/`. A person must
download the release using a browser, finish transferring it outside the inbox,
and then atomically rename the completed file into the inbox. Automated HTTP/API
requests to data.gov.au are never made, including discovery or licence checks.

| Source | File contract |
| --- | --- |
| abn | One complete set of ZIP parts plus `READY` described below |
| asic_companies | ZIP |
| asic_business_names | ZIP |
| acnc | CSV |
| agor | CSV; retain publisher date `YYYY-MM-DD` in filename |
| government_directory | XML with `response` root |
| oric | CSV; retain publisher date such as `20 November 2026` in filename |

Preserve the publisher filename, using letters, digits, spaces, periods,
underscores, parentheses and hyphens. Limits: 1 GB/file, 4 GB/source inbox,
100 files, bounded ZIP expansion. Non-ABN sources accept one unprocessed release
at a time. Unsupported files, symlinks, partial releases, HTML, unsafe ZIP members
and parser changes fail that source without promoting it.

For multipart ABN, `READY` is a JSON object mapping every unprocessed part filename
to its SHA-256. A person writes it only after the whole release is uploaded:

```json
{"ABN-part-01.zip":"<64 lowercase hexadecimal characters>","ABN-part-02.zip":"<64 lowercase hexadecimal characters>"}
```

The refresher copies completed inputs into a new private raw run, retains dataset
page/licence from `sources.yaml`, checksums and byte counts, and uses first recorded
inbox observation as receipt time (not an old publisher timestamp or preserved
filesystem mtime). Successful hashes are marked processed in append-only events.
No inbox files are deleted or moved. Manage retained inbox capacity separately;
failed inputs remain available for inspection and retry.

## Claude: server validation and installation

These steps are instructions, not actions already performed. Work outside the
Sydney blackout. Stage the reviewed source tree without credentials or private
raw data at `ROOT/builds/research-refresh-20261123`; do not replace the existing
build or config until isolated live validation and server staging tests pass.

1. Use the existing approved private transfer mechanism to place this tree on the
   server, including `sources.yaml`, `state_contracts.py`, `classify.py`,
   `project_paths.py` and `config/`, which the reused WA helpers require. As ubuntu
   in that staged tree, create a separate test interpreter with all test pins:

   ```sh
   python3.11 -m venv /home/ubuntu/illoura-market-data/builds/research-refresh-20261123/.test-venv
   .test-venv/bin/python -m pip install -r requirements-tested.txt -r requirements-market.txt -r requirements-market-test.txt -r requirements-deploy.txt
   ```

   Use `.test-venv/bin/python` for the full staging suite and isolated live adapter
   validation below. The commands below spell out `ROOT/venv/bin/python` for the
   production interpreter; substitute the test interpreter during those gates.
2. Set `ILLOURA_MARKET_TEST_DATABASE_URL` privately to the dedicated
   `illoura_market_test` database. Never print it or place it in arguments. Existing
   integration tests require exactly that database name. Run:

   ```sh
   .test-venv/bin/python -m unittest discover -s tests
   ```

3. Prepare a separate private live-validation environment file pointing to an
   **isolated validation database** and migrate that database to 023. The following
   executes migration without shell-sourcing or printing the URL; run from the
   staged tree as ubuntu, with the environment path set privately:

   ```sh
   /home/ubuntu/illoura-market-data/venv/bin/python - <<'PY'
   import os
   from pathlib import Path
   from market_intelligence.db import engine, migrate
   for line in Path(os.environ['RESEARCH_ENV_FILE']).read_text().splitlines():
       if line.startswith('ILLOURA_MARKET_DATABASE_URL='):
           os.environ['ILLOURA_MARKET_DATABASE_URL'] = line.split('=', 1)[1]
   db = engine()
   try:
       migrate(db)
   finally:
       db.dispose()
   PY
   ```

   Retain separate validation raw/inbox/reports under
   `ROOT/validation/research-refresh-20261123`. Seed the validation database with
   the required current procurement/register snapshots using the project's
   existing verified importers before testing scoped registers and RAP. Run each
   adapter against the live publisher **after reading robots and applicable
   terms**, never against production during this gate:

   ```sh
   /home/ubuntu/illoura-market-data/venv/bin/python -m market_intelligence.research_refresh \
     --environment "$RESEARCH_ENV_FILE" \
     --data-root /home/ubuntu/illoura-market-data/validation/research-refresh-20261123 \
     --source jsa_ivi --force
   ```

   Repeat per adapter using the validation checklist below. Review row counts,
   period coverage, retained evidence and pointer changes; exercise a second
   unchanged download and a rejected manifest. Rerun the server staging suite
   after live validation, before production promotion.

4. After those gates, ensure the production interpreter has the existing market
   pins **and** the two dependencies imported by the reused WA collector helpers:

   ```sh
   /home/ubuntu/illoura-market-data/venv/bin/python -m pip install -r requirements-market.txt 'requests==2.32.3' 'PyYAML==6.0.2'
   ```

   These match `requirements-tested.txt`; no new versions are introduced.
   Migrate production using the same snippet with the private
   production `database.env` path. Preserve the existing root-owned 0600
   `/etc/illoura-market/refresh.json` fields `data_root`, `build`, `tools`, changing
   only `build` to the validated staged build path. Keep its existing `tools` path.
   This config is shared with the morning helper, so validate that helper against
   the new build before changing the path. Use a private atomic config replacement.

   Alternative (added 25 November 2026): leave the shared file alone and give the
   refresher its own `/etc/illoura-market/research-refresh.json` with the same three
   fields, root-owned mode 0600, naming the new build. The wrapper uses that file when
   it exists, so the morning helper keeps its proven build (the new build's morning
   refresh measured about 400 seconds against the 210-second morning limit).
5. As root, create the inbox directories and reviewer environment file. Set only
   `ILLOURA_RAP_DIRECTORY_REVIEWER_IDS=<provided directory reviewer UUID>` in
   `/etc/illoura-market/research-refresh.env`, owner root, mode 0600. It is loaded
   by systemd and inherited by the ubuntu child. No RAP model/provider key is needed.

   ```sh
   sudo install -d -o ubuntu -g ubuntu -m 0700 /home/ubuntu/illoura-market-data/inbox
   for source in abn asic_companies asic_business_names acnc agor government_directory oric; do
     sudo install -d -o ubuntu -g ubuntu -m 0700 "/home/ubuntu/illoura-market-data/inbox/$source"
   done
   sudo install -d -o root -g root -m 0700 /var/lib/illoura-market-refresh /var/lib/illoura-research-refresh
   ```

6. From the reviewed admin tree, install the tested root wrapper and units with
   the existing pinned-host deployment CLI. Deploy updated health through its
   existing staging flow as well:

   ```sh
   python deploy/aws/illoura_aws.py research-refresh install --yes
   python deploy/aws/illoura_aws.py deploy-ops --yes
   python deploy/aws/illoura_aws.py research-refresh status
   ```

   Installation does not enable the new timer. On the server, inspect the units:

   ```sh
   sudo systemd-analyze verify /etc/systemd/system/illoura-research-refresh.service /etc/systemd/system/illoura-research-refresh.timer
   systemd-analyze calendar '*-*-* 10:15:00 Australia/Sydney'
   sudo systemctl start illoura-research-refresh.service
   sudo cat /var/lib/illoura-research-refresh/status.json
   ```

   The manual service start must be outside the blackout, using the shared lock.
   Verify blocked/manual sources do not change overall health, and no email or
   send-state operation occurs. Query `market.v_research_freshness` as the existing
   Metabase reader; existing default SELECT grants should cover the new view.
7. Only once reviewed, use the separately approved scheduling action:

   ```sh
   python deploy/aws/illoura_aws.py research-refresh enable --yes
   python deploy/aws/illoura_aws.py research-refresh status
   ```

   To stop future runs: `research-refresh disable --yes`. Disabling the timer does
   not interrupt a service already running. To stop that service deliberately,
   use `sudo systemctl stop illoura-research-refresh.service`; its entire control
   group is terminated. Do not change the daily brief timer or send state.

## Live adapter checklist

| Adapter | Claude must validate in isolation |
| --- | --- |
| JSA IVI | All six links seen; only ANZSCO4 states/territories selected; `4 digit 3 month average` parser, latest month, occupation/region coverage, exact filename selector, CC BY evidence. |
| JSA projections | May 2025–May 2035 filename; `Table_6 Occupation Unit Group`, baseline and future units/years, baseline determines edition; licence evidence. |
| JSA shortages | 2026 four-digit unit-group file; reject the separate six-digit ANZSCO/OSCA product and report-figure workbooks; classifications/ratings coverage. |
| JSA REOS/TNV | Existing contracts, workbook periods and suppression; current live filenames; no denominator/aggregation changes. |
| APS | Listing selects newest June page regardless of December entries; Table 2/13/88, current ICT family labels, headcount/portfolio coverage and licence statements. |
| Health | Follow all bounded collection pages, find October 2026 when absent on page one, compare slug months, replay retained selected collection page and workbook copyright, parser coverage and prior-quarter guard. If October is not linked anywhere, stop/review the publisher listing; do not invent a download link. |
| RAP | Private reviewer UUID configured; whole directory coverage by RAP type, privacy quarantine, independent verifier, exact retained collection and current register context. No PDFs or paid calls. |
| WA | Ordinary 200 HTML robots fallback recorded per run; genuine robots denial and WAF 202/action/HTML challenges stop. Official-host redirects only; catalogue counts within bounds, CC BY evidence, all selected yearly CSVs, 2023–24 newest when still current, header/row counts and contact-free normalisation. |
| Manual registers | No network requests; actual publisher filenames/types, receipt hashes/bytes/licences; complete ABN parts, procurement-scoped ABN/ASIC coverage, ORIC/AGOR publisher dates, directory XML. Failed/partial files cannot move current pointers. |
| Procurement/forward | Monitor only; morning helper remains the importer; inspect current snapshot imports without invoking another collector or send. |

Exercise lock contention with the morning helper, both DST blackout boundaries,
forced timeout/process-group cleanup, crash between import and state update,
unchanged hashes, a same-edition correction, and failed promotion in staging.
These are covered by offline boundary tests, but real fcntl/process/systemd and
PostgreSQL permissions still need the Linux validation gate.
