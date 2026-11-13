# Reference: configuration, environment variables and private files

Configure dependencies, classification rules and runtime settings without storing private values in documentation.

Files covered:

- `config/categories.yaml`
- `config/scoring.yaml`
- `config/procurement_rules.yaml`
- `config/panels.yaml`
- `config/organisation_aliases.yaml`
- `config/seed_contacts.yaml`
- `config/opt_outs.yaml`
- `config/bdm_monitor.example.yaml`
- `requirements.txt`
- `requirements-tested.txt`
- `requirements-bdm.txt`
- `requirements-deploy.txt`
- `api/requirements.txt`
- `docs/tools/requirements.txt`
- `.env.example` (name and role only; values not inspected)
- `.gitattributes`
- `.gitignore`

Configuration is loaded by source/classification/publication modules and private environment files. This inventory records names and intended roles only; it does not reproduce environment values, contact records or credential filenames. Private means a value can identify a person/account/destination or grant access; a path variable may also point to confidential local storage. Configuration assumptions are not a substitute for verifying current procurement rules.

| File | Keys/purpose |
|---|---|
| `config/categories.yaml` | Relevance taxonomy: `vertical`, `families`, `ict_category_names`, `strong_keywords`, `weak_keywords`, `exclude_keywords`, `role_families`, `relevant_role_families`. Used by classification and derived intelligence; category/code matches precede keywords and exclusions override. |
| `config/scoring.yaml` | `weights` for attractiveness, fit, access, timing, readiness and commercial potential; `thresholds` for INVESTIGATE/WATCH; `today_list` min/max; `renewal_windows`; `stale_after_months`. Used by TODAY/intelligence. |
| `config/procurement_rules.yaml` | `rules`: named rules with jurisdiction, optional value bounds, treatment and optional contract/organisation participation targets. Used to interpret project procurement evidence; not automatically current legal advice. |
| `config/panels.yaml` | `panels`: configured panel identifier → display-name records used for membership context. |
| `config/organisation_aliases.yaml` | `organisations` with optional ABN and aliases; `groups` aggregate related entities. Reviewed identity hints; actual values omitted. |
| `config/seed_contacts.yaml` | `contacts`: organisation-keyed source-backed route records including email, optional phone and source. Private contact values omitted. |
| `config/opt_outs.yaml` | `emails`: suppressed routes used to prevent recommending contact. Values are private and omitted. |
| `config/bdm_monitor.example.yaml` | Monitor template: polling/sending/desktop/workbench toggles; alert age/start; busy retries/delay; sender/recipient/token path; RFQ prefixes/title includes/excludes; digest cap; freshness and healthcheck URL. Private copies hold real values; send defaults off. |
| `requirements.txt` | Core collector/data/Sheets dependencies: dotenv, pandas, Scrapy/Playwright, gspread/dataframe, Google auth, requests, holidays, YAML and openpyxl. Most versions are unconstrained. |
| `requirements-tested.txt` | Pinned core/local verification stack including Scrapy, Twisted, w3lib, parsers and browser libraries. Does not include API, OAuth-helper, PDF-tool or Supabase migration additions. |
| `requirements-bdm.txt` | Adds `google-auth-oauthlib` for manual Gmail OAuth; collection still needs core dependencies. |
| `requirements-deploy.txt` | Pins Paramiko for the administrator-side SSH deployment CLI; not needed by the server runtime. |
| `api/requirements.txt` | Pinned FastAPI, Uvicorn, MCP and httpx for API/Claude bridge and API security tests. |
| `docs/tools/requirements.txt` | ReportLab, pypdf, pypdfium2 and Pillow for PDF guide generation/inspection. |
| `.env.example` | Example runtime-settings filename; copy values only through an approved private setup, never into documentation. Contents were not read here. |
| `.gitattributes` | Repository text/line-ending policy; relevant to LF-normalized deployment hashes. |
| `.gitignore` | Exclusion rules for private data, credentials, outputs and local runtime artifacts; exclusion is not encryption. |

The following names were found through static Python `os.environ`/`os.getenv`, environment-wrapper calls, injected child environments, test overrides and shell/service definitions. Source locations identify reads or writes; tests patch many production names rather than defining a second configuration system. The routine also accepts the dynamic `ILLOURA_MAIL_*` namespace, with three named values consumed by its loaders. No code was imported or executed.

| Environment name | Purpose | Private value? | Read/set locations |
|---|---|---|---|
| `ACTIVITY_DB` | Audit SQLite path override. | No credential by design; path/content may be private | `activity_log.py:74`; `tests/test_activity_log.py:16`; `tests/test_audit_fixes.py:22`; `tests/test_austender_api.py:12`; `tests/test_crawl_integration.py:16`; `tests/test_full_details.py:14`; `tests/test_outreach.py:10`; `tests/test_outreach_agent.py:8`; `tests/test_publish_sheets.py:10`; `tests/test_releases.py:12`; `tests/test_workflow.py:25` |
| `ACTIVITY_RUN_ID` | Batch correlation ID inherited by child processes. | No | `activity_log.py:67`; `activity_log.py:68`; `activity_log.py:69`; `run_spiders.py:363`; `tests/test_activity_log.py:16` |
| `AUSTENDER_DB_DIR` | Source/derived database directory override. | No credential by design; path/content may be private | `bdm_monitor.py:529`; `deploy/illoura-buyict.service:11`; `deploy/illoura-planning.service:11`; `project_paths.py:9`; `routine/run_once.py:8`; `tests/test_action_tracker.py:28`; `tests/test_austender_api.py:29`; `tests/test_backup_databases.py:29`; `tests/test_build_intelligence.py:45`; `tests/test_compliant_routes.py:32`; `tests/test_export_views.py:21`; `tests/test_mmr_list.py:34`; `tests/test_outreach.py:55`; `tests/test_outreach_agent.py:60`; `tests/test_publish_engine_views.py:48`; `tests/test_publish_sheets.py:17`; `tests/test_publish_sheets.py:60`; `tests/test_releases.py:19`; `tests/test_state_contracts.py:40`; `tests/test_workflow.py:67` |
| `BRAVE_API_KEY` | Optional website-research API credential. | Yes | `local_assistant/website_leads.py:189` |
| `DEBIAN_FRONTEND` | Noninteractive package installation mode. | No | `api/install-api.sh:4`; `deploy/aws/cloud-init.sh:4` |
| `GROQ_API_KEY` | Legacy writer/reviewer provider credential. | Yes | `outreach_agent.py:436` |
| `GSHEETS_AUTO_SYNC` | Boolean automatic source-tab synchronization switch. | No | `bdm_monitor.py:529`; `routine/run_once.py:8`; `run_spiders.py:337`; `run_spiders.py:340`; `settings.py:132` |
| `GSHEETS_FOLDER_ID` | Shared Drive publication destination identifier. | Yes | `publish_sheets.py:333`; `run_spiders.py:499`; `run_spiders.py:510`; `run_spiders.py:524`; `tests/test_publish_sheets.py:17`; `tests/test_publish_sheets.py:45`; `tests/test_publish_sheets.py:60` |
| `GSHEETS_SERVICE_ACCOUNT_FILE` | Private credential path; filename/value intentionally omitted. | Yes | `settings.py:141` |
| `GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT` | Inline Google credentials or a path to them; value/path filename omitted. | Yes | `settings.py:140` |
| `GSHEETS_SHARE_ROLE` | Sharing access role. | No | `listings_pipeline.py:518` |
| `GSHEETS_SHARE_WITH` | Publication recipients. | Yes | `listings_pipeline.py:547` |
| `GSHEETS_SPREADSHEET_ID` | Raw-tab destination spreadsheet identifier. | Yes | `settings.py:137` |
| `HEALTHCHECK_URL` | Private dead-man-switch endpoint. | Yes | `run_spiders.py:87` |
| `ILLOURA_AWS_ACCOUNT` | Expected private account identity. | Yes | `deploy/aws/illoura_aws.py:123` |
| `ILLOURA_AWS_INSTANCE` | Private managed-instance selector. | Yes | `deploy/aws/illoura_aws.py:122` |
| `ILLOURA_AWS_PROFILE` | Administrator AWS CLI profile. | No | `deploy/aws/illoura_aws.py:120` |
| `ILLOURA_AWS_REGION` | AWS CLI region. | No | `deploy/aws/illoura_aws.py:121` |
| `ILLOURA_BDM_PYTHON` | Windows preview interpreter override. | No | `deploy/run-bdm-preview.ps1:4` |
| `ILLOURA_DATA_DIR` | API immutable generation pointer path. | No credential by design; path/content may be private | `api/app.py:24` |
| `ILLOURA_GMAIL_SENDER` | Expected private Gmail identity for health validation. | Yes | `deploy/illoura_cloud_health.py:202`; `tests/test_cloud_health.py:200`; `tests/test_cloud_health.py:81`; `tests/test_cloud_health.py:88` |
| `ILLOURA_HEALTH_FILE` | API health status path. | No | `api/app.py:28` |
| `ILLOURA_HYBRID_INDEX` | Earlier private vector-cache path; AI disabled. | No credential by design; path/content may be private | `local_assistant/hybrid.py:19` |
| `ILLOURA_KEYS_FILE` | Private API-key registry path. | Yes | `api/app.py:25` |
| `ILLOURA_MAIL_ENV` | Private routine mail settings file path. | Yes | `routine/scripts/mail_config.py:10`; `tests/test_routine_mail_config.py:20`; `tests/test_routine_sender.py:46` |
| `ILLOURA_MAIL_LINK_ID` | Private transport/link identifier, read without documenting its value. | Yes | `routine/scripts/mail_config.py (dynamic ILLOURA_MAIL_* lookup)` |
| `ILLOURA_MAIL_RECIPIENT` | Internal receiving mailbox. | Yes | `bdm_monitor.py:143`; `routine/scripts/mail_config.py (dynamic ILLOURA_MAIL_* lookup)`; `tests/test_bdm_monitor.py:152`; `tests/test_bdm_monitor.py:169`; `tests/test_routine_mail_config.py:25`; `tests/test_routine_mail_config.py:48`; `tests/test_routine_sender.py:46` |
| `ILLOURA_MAIL_SENDER` | Internal sending mailbox. | Yes | `bdm_monitor.py:142`; `routine/scripts/mail_config.py (dynamic ILLOURA_MAIL_* lookup)`; `tests/test_bdm_monitor.py:152`; `tests/test_bdm_monitor.py:169`; `tests/test_routine_mail_config.py:25`; `tests/test_routine_mail_config.py:32`; `tests/test_routine_mail_config.py:39`; `tests/test_routine_mail_config.py:48`; `tests/test_routine_sender.py:46` |
| `ILLOURA_PILOT_DB_DIR` | Assistant database directory override. | No credential by design; path/content may be private | `local_assistant/assistant.py:19` |
| `ILLOURA_PILOT_MODEL` | Earlier local planner model selector; AI disabled. | No | `local_assistant/assistant.py:20` |
| `ILLOURA_PRIVATE_DIR` | Administrator credentials/host-key/access directory. | Yes | `deploy/aws/illoura_aws.py:124` |
| `ILLOURA_PUBLIC_HOST` | API/MCP host/origin validation setting. | Yes | `api/app.py:26` |
| `ILLOURA_RETENTION_TEST_REPORT` | Optional test-only retention measurement report path. | No | `tests/test_cloud_retention.py:462` |
| `LOCALAPPDATA` | Windows per-user installation-path base; personal path values omitted. | Yes | `deploy/aws/illoura_aws.py:152`; `deploy/run-bdm-preview.ps1:4` |
| `OLLAMA_CONTEXT_LENGTH` | Historical model context limit. | No | `local_assistant/start.ps1:10` |
| `OLLAMA_HOST` | Historical loopback model-service binding; do not start while disabled. | No | `local_assistant/start.ps1:8` |
| `OLLAMA_MAX_LOADED_MODELS` | Historical loaded-model cap. | No credential by design; path/content may be private | `local_assistant/start.ps1:12` |
| `OLLAMA_MODELS` | Historical local model storage path; no downloads. | No credential by design; path/content may be private | `local_assistant/start.ps1:9` |
| `OLLAMA_NUM_PARALLEL` | Historical model concurrency limit. | No | `local_assistant/start.ps1:11` |
| `OUTREACH_ABN` | Approved company identity used in manual drafts. | No | `outreach.py:145` |
| `OUTREACH_ADDRESS` | Private sender/company postal contact detail. | Yes | `outreach.py:161` |
| `OUTREACH_COMPANY` | Draft company display identity. | No | `outreach.py:156` |
| `OUTREACH_COOLDOWN_DAYS` | Minimum repeat-contact interval. | No | `outreach.py:318`; `outreach_agent.py:189` |
| `OUTREACH_DAILY_CAP` | Legacy daily agent cap. | No | `outreach_agent.py:162`; `tests/test_outreach_agent.py:144` |
| `OUTREACH_DELAY_SECONDS` | Pause between legacy messages. | No | `outreach.py:317`; `outreach_agent.py:167`; `outreach_agent.py:168` |
| `OUTREACH_FORWARD_TO` | Private reply forwarding destination. | Yes | `outreach_agent.py:90`; `tests/test_outreach_agent.py:60` |
| `OUTREACH_FROM_EMAIL` | Private sender mailbox. | Yes | `outreach.py:159`; `outreach.py:301`; `outreach_agent.py:81`; `tests/test_outreach_agent.py:60` |
| `OUTREACH_FROM_NAME` | Private sender display name. | Yes | `outreach.py:158`; `outreach.py:305` |
| `OUTREACH_HOLIDAY_STATE` | Holiday calendar selector for sending window. | No | `outreach_agent.py:140` |
| `OUTREACH_KEYWORDS` | Manual people-related opportunity inclusion terms. | No | `outreach.py:69` |
| `OUTREACH_LLM_MODEL` | Legacy draft writer model selector. | No | `outreach_agent.py:447` |
| `OUTREACH_LLM_REASONING` | Legacy writer reasoning option. | No | `outreach_agent.py:451` |
| `OUTREACH_MAX_BOUNCES` | Bounce threshold for agent fail-safe. | No | `outreach_agent.py:836`; `outreach_agent.py:921` |
| `OUTREACH_MAX_PER_RUN` | Manual send cap per invocation. | No | `outreach.py:316` |
| `OUTREACH_MIN_DAYS_TO_CLOSE` | Minimum remaining response window for selection. | No | `outreach_agent.py:193` |
| `OUTREACH_MODE` | Legacy agent mode and dashboard view; does not authorize outreach. | No | `dashboard.py:262`; `outreach_agent.py:77`; `tests/test_outreach_agent.py:123`; `tests/test_outreach_agent.py:60` |
| `OUTREACH_PHONE` | Private sender phone. | Yes | `outreach.py:160` |
| `OUTREACH_REPLY_TO` | Private reply mailbox. | Yes | `outreach.py:307` |
| `OUTREACH_SEND_FROM_HOUR` | Sydney sending-window start. | No | `outreach_agent.py:141` |
| `OUTREACH_SEND_TO_HOUR` | Sydney sending-window end. | No | `outreach_agent.py:141`; `outreach_agent.py:977` |
| `OUTREACH_SPECIALTY` | Company specialty wording used in drafts. | No | `outreach.py:129` |
| `OUTREACH_TEST_RECIPIENT` | Private test-mode destination. | Yes | `outreach_agent.py:627`; `outreach_agent.py:90`; `tests/test_outreach_agent.py:129`; `tests/test_outreach_agent.py:60` |
| `PLAYWRIGHT_BROWSERS_PATH` | Installed browser directory for source/render workers. | No | `deploy/austender-scraper.service:13`; `deploy/illoura_daily_brief.py:201`; `deploy/setup_vps.sh:11`; `routine/run_once.py:8` |
| `PYTHONDONTWRITEBYTECODE` | Disable Python bytecode writes in deployed workers. | No | `api/install-api.sh:50`; `routine/run_once.py:8` |
| `PYTHONUNBUFFERED` | Immediate child log output. | No | `deploy/austender-scraper.service:14`; `deploy/illoura-buyict.service:10`; `deploy/illoura-planning.service:10` |
| `READYZ_URL` | External watcher readiness target configured by CloudFormation. | No | `deploy/aws/illoura-watch.yaml:121` |
| `SHEETS_KEEP_DAYS` | Raw publication recency window. | No | `publish_sheets.py:59` |
| `SMTP_HOST` | Private SMTP connection destination. | Yes | `outreach.py:289` |
| `SMTP_PASSWORD` | SMTP credential. | Yes | `outreach.py:296`; `outreach_agent.py:86` |
| `SMTP_PORT` | SMTP transport port. | No | `outreach.py:289` |
| `SMTP_USER` | Private mailbox login. | Yes | `outreach.py:295`; `outreach.py:296` |
| `SUPABASE_DB_URL` | Private PostgreSQL connection credential. | Yes | `supabase_migrate.py:389` |
| `TZ` | Service timezone. | No | `deploy/austender-scraper.service:12` |
| `gaccount_pwd` | Legacy mailbox credential fallback. | Yes | `outreach_agent.py:86` |
| `groq_api_key` | Legacy lowercase provider credential fallback. | Yes | `outreach_agent.py:436` |

| Private path / selector | Purpose |
|---|---|
| `.env` | Runtime secrets/contact/publication settings loaded by dotenv. Values omitted. |
| `config/*private*` | Any private configuration variants; not inspected. |
| `inputs/`, `local_assistant/inputs/` | Reviewed contact/identity/business research input locations; contents not inspected. |
| `inputs/bdm/monitor.yaml` | Local private monitor settings. |
| `/etc/illoura/bdm_monitor.yaml` | Linux monitor configuration including internal mail/token selectors. |
| `/etc/illoura-routine/mail.env` | Routine sender/recipient/link settings; mode 0640 migration output. |
| `/etc/illoura-routine/gmail-token.json` | Offline Gmail credentials; never publish contents. |
| `/etc/illoura-ops/health.env` | Private expected sending identity used by health. |
| `/etc/illoura-api/keys.json` | API key digest/enabled records. |
| `/etc/illoura-api/environment` | API paths/host settings loaded by systemd. |
| `ILLOURA_PRIVATE_DIR` | Private administrator directory containing pinned `known_hosts`, `admin.pem` and generated API access JSON. |
| `api/claude_bridge.py --credentials PRIVATE_PATH` | Local private API access file; do not embed credentials in Claude command arguments. |
| `databases/sheets_registry.json` | Private publication identifiers used for stable spreadsheet reuse/import. |
| `GSHEETS_SERVICE_ACCOUNT_FILE` / `GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT` | Google key selector/inline credentials. Actual key filename and project identifiers intentionally omitted. |
| BDM authorization `--client` / `--token` paths | Private OAuth client/token files chosen during authorization; actual filenames omitted. |
| Private `ACCESS_GUIDE` | Installation-specific Claude access instructions; referenced by name only. |

`templates/company_profile.yaml` keys and purpose are covered in [exports-and-sheets.md](exports-and-sheets.md). CloudFormation watcher configuration and systemd settings are covered in [cloud-and-daily-brief.md](cloud-and-daily-brief.md). The optional Supabase script imports `psycopg`, and local semantic code imports NumPy; neither is explicitly declared in the checked-in requirement files. Browser/PDF/model runtimes may need separate installed assets; their availability is unverified. No installed-package or private-setting values were checked.
