# Reference index: every file in the repository

> Describes the code in this repository as of 13 November 2026 (written from the code by Codex,
> reviewed by Claude). Some of it is built but not yet deployed: what is live on the server is
> recorded in [../workstreams/09-audit-fixes.md](../workstreams/09-audit-fixes.md). Start with
> [../README.md](../README.md) for the overview pages.

Find the canonical reference page for every file in this checkout.

Files covered:

- `docs/assistant_architecture.md`
- `docs/bdm_monitor.md`
- `docs/cloud_operations.md`
- `docs/HANDOFF.md`
- `docs/history.md`
- `docs/reference/api-and-claude.md`
- `docs/reference/bdm-and-outreach.md`
- `docs/reference/cloud-and-daily-brief.md`
- `docs/reference/collectors.md`
- `docs/reference/configuration.md`
- `docs/reference/databases.md`
- `docs/reference/exports-and-sheets.md`
- `docs/reference/intelligence.md`
- `docs/reference/local-assistant.md`
- `docs/reference/tests.md`
- `docs/SETUP_AND_OPERATIONS.md`
- `docs/spec_mapping.md`
- `docs/workstreams/01-retention.md`
- `docs/workstreams/02-audited-commits.md`
- `docs/workstreams/03-deploy-cli.md`
- `docs/workstreams/06-review-fixes.md`
- `docs/workstreams/07-daily-brief.md`
- `docs/workstreams/08-reliability.md`
- `docs/workstreams/09-audit-fixes.md`
- `docs/workstreams/progress.md`
- `README.md`
- `docs/reference/README.md`

These pages describe checked-in code as read on 13 November 2026. They do not establish current server state or contain private data. No network, collectors, servers, tests or version-control commands were run. Each Python file has one canonical owner in this index; links and mentions elsewhere are cross-references. Existing overview/operations/history documents are contextual material owned by this index, not rewritten here. Source paths are relative to the repository root.

| Reference page | Scope |
|---|---|
| [Collectors](collectors.md) | Eight sources, incremental coverage, pipeline and failure reports. |
| [Databases](databases.md) | Code-defined schemas, writers/readers, backups and migration. |
| [Intelligence](intelligence.md) | Releases, classification, derived views, MMR and action tracking. |
| [Exports and Sheets](exports-and-sheets.md) | Workbooks, raw/derived Sheets, literal publication and dashboard. |
| [BDM and outreach](bdm-and-outreach.md) | Monitor, qualification/workbench, internal alerts and legacy send code. |
| [Local assistant](local-assistant.md) | Disabled local AI, evidence queries, supplier/Sheets research and evaluations. |
| [Cloud and daily brief](cloud-and-daily-brief.md) | Deployment, schedules, routine scripts, send/recovery and external watch. |
| [API and Claude](api-and-claude.md) | HTTP/MCP, authentication, query limits and client bridge. |
| [Tests](tests.md) | Every test/smoke file, Windows commands and Linux-only checks. |
| [Configuration](configuration.md) | Every config YAML/requirements file, environment names and private paths. |

`databases/` and generated `dashboard/` are absent from this worktree. Their documented contracts come from code. `.git` is worktree metadata and is excluded from the project-file table. Private inputs, databases, environment contents and root credential JSON are not read or indexed by value. There were no private data files to enumerate in the visible checkout inventory. No repository source file remains unplaced.

| Every repository file | Canonical reference page |
|---|---|
| `.env.example` | [configuration.md](configuration.md) |
| `.gitattributes` | [configuration.md](configuration.md) |
| `.github/workflows/tests.yml` | [tests.md](tests.md) |
| `.gitignore` | [configuration.md](configuration.md) |
| `action_tracker.py` | [intelligence.md](intelligence.md) |
| `activity_log.py` | [intelligence.md](intelligence.md) |
| `api/app.py` | [api-and-claude.md](api-and-claude.md) |
| `api/claude_bridge.py` | [api-and-claude.md](api-and-claude.md) |
| `api/enable-https.sh` | [api-and-claude.md](api-and-claude.md) |
| `api/install-api.sh` | [api-and-claude.md](api-and-claude.md) |
| `api/requirements.txt` | [configuration.md](configuration.md) |
| `api/test_security.py` | [tests.md](tests.md) |
| `austender_api.py` | [collectors.md](collectors.md) |
| `backup_databases.py` | [databases.md](databases.md) |
| `bdm_authorize_gmail.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `bdm_local_runner.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `bdm_monitor.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `bdm_workbench.html` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `bdm_workbench.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `bdm_workflow.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `build_intelligence.py` | [intelligence.md](intelligence.md) |
| `build_releases.py` | [intelligence.md](intelligence.md) |
| `classify.py` | [intelligence.md](intelligence.md) |
| `config/bdm_monitor.example.yaml` | [configuration.md](configuration.md) |
| `config/categories.yaml` | [configuration.md](configuration.md) |
| `config/opt_outs.yaml` | [configuration.md](configuration.md) |
| `config/organisation_aliases.yaml` | [configuration.md](configuration.md) |
| `config/panels.yaml` | [configuration.md](configuration.md) |
| `config/procurement_rules.yaml` | [configuration.md](configuration.md) |
| `config/scoring.yaml` | [configuration.md](configuration.md) |
| `config/seed_contacts.yaml` | [configuration.md](configuration.md) |
| `dashboard.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `database_manager.py` | [databases.md](databases.md) |
| `db_query_tool.py` | [databases.md](databases.md) |
| `deploy/austender-scraper.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/austender-scraper.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/aws/cloud-init.sh` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/aws/illoura-watch.yaml` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/aws/illoura_aws.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/check_vps.sh` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-buyict.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-buyict.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-cloud-collect.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-cloud-collect.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-cloud-health.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-cloud-health.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-daily-brief.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-daily-brief.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-planning.service` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura-planning.timer` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura_cloud_collect.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura_cloud_health.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura_cloud_ops.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura_daily_brief.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/illoura_external_watch.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/package_for_vps.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/package_snapshot.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/register_windows_task.ps1` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/restore_snapshot.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/routine_mail_env.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/run-bdm-preview.ps1` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/setup_vps.sh` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/system/apt-daily-upgrade-illoura.conf` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `deploy/system/needrestart-illoura.conf` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `docs/assistant_architecture.md` | [README.md](README.md) |
| `docs/bdm_monitor.md` | [README.md](README.md) |
| `docs/cloud_operations.md` | [README.md](README.md) |
| `docs/HANDOFF.md` | [README.md](README.md) |
| `docs/history.md` | [README.md](README.md) |
| `docs/Illoura_AI_Assistant_User_Guide.pdf` | [local-assistant.md](local-assistant.md) |
| `docs/reference/api-and-claude.md` | [README.md](README.md) |
| `docs/reference/bdm-and-outreach.md` | [README.md](README.md) |
| `docs/reference/cloud-and-daily-brief.md` | [README.md](README.md) |
| `docs/reference/collectors.md` | [README.md](README.md) |
| `docs/reference/configuration.md` | [README.md](README.md) |
| `docs/reference/databases.md` | [README.md](README.md) |
| `docs/reference/exports-and-sheets.md` | [README.md](README.md) |
| `docs/reference/intelligence.md` | [README.md](README.md) |
| `docs/reference/local-assistant.md` | [README.md](README.md) |
| `docs/reference/tests.md` | [README.md](README.md) |
| `docs/SETUP_AND_OPERATIONS.md` | [README.md](README.md) |
| `docs/spec_mapping.md` | [README.md](README.md) |
| `docs/tools/build_ai_guide.py` | [local-assistant.md](local-assistant.md) |
| `docs/tools/requirements.txt` | [configuration.md](configuration.md) |
| `docs/workstreams/01-retention.md` | [README.md](README.md) |
| `docs/workstreams/02-audited-commits.md` | [README.md](README.md) |
| `docs/workstreams/03-deploy-cli.md` | [README.md](README.md) |
| `docs/workstreams/06-review-fixes.md` | [README.md](README.md) |
| `docs/workstreams/07-daily-brief.md` | [README.md](README.md) |
| `docs/workstreams/08-reliability.md` | [README.md](README.md) |
| `docs/workstreams/09-audit-fixes.md` | [README.md](README.md) |
| `docs/workstreams/progress.md` | [README.md](README.md) |
| `duplicate_filter.py` | [collectors.md](collectors.md) |
| `evaluate_outreach.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `export_to_gsheets.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `export_views.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `gsheets_sync.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `listings_pipeline.py` | [intelligence.md](intelligence.md) |
| `local_assistant/.gitignore` | [local-assistant.md](local-assistant.md) |
| `local_assistant/assistant.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/audit_data.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/benchmark.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/build_action_pack.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/build_hybrid_index.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/build_split_pdfs.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/buyict_sellers.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/check_public_sources.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/company_coverage.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/contact_discovery.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/evaluate.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/evaluate_hybrid.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/evaluate_model.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/evaluation_cases.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/export_question_dataset.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/heldout_cases.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/http_smoke.py` | [tests.md](tests.md) |
| `local_assistant/hybrid.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/hybrid_ui_smoke.py` | [tests.md](tests.md) |
| `local_assistant/index.html` | [local-assistant.md](local-assistant.md) |
| `local_assistant/LOCAL_AI_DISABLED` | [local-assistant.md](local-assistant.md) |
| `local_assistant/Modelfile` | [local-assistant.md](local-assistant.md) |
| `local_assistant/planning.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/public_research.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/README.md` | [local-assistant.md](local-assistant.md) |
| `local_assistant/reasoning.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/report_smoke.py` | [tests.md](tests.md) |
| `local_assistant/route_review.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/second_holdout.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/sheet_data.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/sheet_sync_service.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/sheets_sync_smoke.py` | [tests.md](tests.md) |
| `local_assistant/sheets_ui_smoke.py` | [tests.md](tests.md) |
| `local_assistant/snapshot_databases.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/start.ps1` | [local-assistant.md](local-assistant.md) |
| `local_assistant/sync_approachable_routes.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/sync_sheet_data.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/test_assistant.py` | [tests.md](tests.md) |
| `local_assistant/test_buyict_sellers.py` | [tests.md](tests.md) |
| `local_assistant/test_company_coverage.py` | [tests.md](tests.md) |
| `local_assistant/test_contact_discovery.py` | [tests.md](tests.md) |
| `local_assistant/test_hybrid.py` | [tests.md](tests.md) |
| `local_assistant/test_reasoning.py` | [tests.md](tests.md) |
| `local_assistant/test_refinements.py` | [tests.md](tests.md) |
| `local_assistant/test_route_review.py` | [tests.md](tests.md) |
| `local_assistant/test_sheet_data.py` | [tests.md](tests.md) |
| `local_assistant/test_verification.py` | [tests.md](tests.md) |
| `local_assistant/test_website_leads.py` | [tests.md](tests.md) |
| `local_assistant/ui_smoke.py` | [tests.md](tests.md) |
| `local_assistant/verification.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/website_leads.py` | [local-assistant.md](local-assistant.md) |
| `local_assistant/write_refinement_report.py` | [local-assistant.md](local-assistant.md) |
| `middlewares.py` | [collectors.md](collectors.md) |
| `mmr_list.py` | [intelligence.md](intelligence.md) |
| `nsw_tender_fetch.py` | [collectors.md](collectors.md) |
| `outreach.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `outreach_agent.py` | [bdm-and-outreach.md](bdm-and-outreach.md) |
| `pipelines.py` | [collectors.md](collectors.md) |
| `prime_briefs.py` | [intelligence.md](intelligence.md) |
| `project_paths.py` | [databases.md](databases.md) |
| `publish_sheets.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `README.md` | [README.md](README.md) |
| `requirements-bdm.txt` | [configuration.md](configuration.md) |
| `requirements-deploy.txt` | [configuration.md](configuration.md) |
| `requirements-tested.txt` | [configuration.md](configuration.md) |
| `requirements.txt` | [configuration.md](configuration.md) |
| `routine/legacy/complete-cloud-review.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/legacy/send-reviewed-cloud-brief.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/README.md` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/run_once.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/build_rap_partner_register.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/check_newsletter_layout.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/collect_buyict_news.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/collect_official_research.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/compose_checked_brief.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/export_five_buyict_requirements.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/finalize_daily_sweep.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/mail_config.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/newsletter_research_sweep.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/prepare_newsletter_evidence.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/refresh_daily_shortlist.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/render_newsletter_preview.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/send_gmail.py` | [cloud-and-daily-brief.md](cloud-and-daily-brief.md) |
| `routine/scripts/test_sender.py` | [tests.md](tests.md) |
| `run_spiders.py` | [collectors.md](collectors.md) |
| `scrapy.cfg` | [collectors.md](collectors.md) |
| `settings.py` | [collectors.md](collectors.md) |
| `sheets_writer.py` | [exports-and-sheets.md](exports-and-sheets.md) |
| `spiders/__init__.py` | [collectors.md](collectors.md) |
| `spiders/act_tender.py` | [collectors.md](collectors.md) |
| `spiders/aus_contract.py` | [collectors.md](collectors.md) |
| `spiders/aus_tender.py` | [collectors.md](collectors.md) |
| `spiders/austender_common.py` | [collectors.md](collectors.md) |
| `spiders/buy_ict.py` | [collectors.md](collectors.md) |
| `spiders/planned_procurement.py` | [collectors.md](collectors.md) |
| `spiders/standing_offer.py` | [collectors.md](collectors.md) |
| `state_contracts.py` | [intelligence.md](intelligence.md) |
| `supabase_migrate.py` | [databases.md](databases.md) |
| `templates/company_profile.yaml` | [exports-and-sheets.md](exports-and-sheets.md) |
| `templates/outreach_email.txt` | [exports-and-sheets.md](exports-and-sheets.md) |
| `tests/fixtures/ocds_CN3695219.json` | [tests.md](tests.md) |
| `tests/test_action_tracker.py` | [tests.md](tests.md) |
| `tests/test_activity_log.py` | [tests.md](tests.md) |
| `tests/test_audit_fixes.py` | [tests.md](tests.md) |
| `tests/test_austender_api.py` | [tests.md](tests.md) |
| `tests/test_aws_cli.py` | [tests.md](tests.md) |
| `tests/test_backup_databases.py` | [tests.md](tests.md) |
| `tests/test_bdm_local_runner.py` | [tests.md](tests.md) |
| `tests/test_bdm_monitor.py` | [tests.md](tests.md) |
| `tests/test_bdm_workflow.py` | [tests.md](tests.md) |
| `tests/test_build_intelligence.py` | [tests.md](tests.md) |
| `tests/test_classify.py` | [tests.md](tests.md) |
| `tests/test_cloud_collect.py` | [tests.md](tests.md) |
| `tests/test_cloud_daily_brief.py` | [tests.md](tests.md) |
| `tests/test_cloud_health.py` | [tests.md](tests.md) |
| `tests/test_cloud_ops.py` | [tests.md](tests.md) |
| `tests/test_cloud_retention.py` | [tests.md](tests.md) |
| `tests/test_collectors_pending.py` | [tests.md](tests.md) |
| `tests/test_collectors_silent_output.py` | [tests.md](tests.md) |
| `tests/test_collectors_unpublished.py` | [tests.md](tests.md) |
| `tests/test_collectors_wave6.py` | [tests.md](tests.md) |
| `tests/test_compliant_routes.py` | [tests.md](tests.md) |
| `tests/test_crawl_integration.py` | [tests.md](tests.md) |
| `tests/test_data_quality.py` | [tests.md](tests.md) |
| `tests/test_data_quality_alerts.py` | [tests.md](tests.md) |
| `tests/test_export_views.py` | [tests.md](tests.md) |
| `tests/test_full_details.py` | [tests.md](tests.md) |
| `tests/test_incremental.py` | [tests.md](tests.md) |
| `tests/test_listings_pipeline.py` | [tests.md](tests.md) |
| `tests/test_mmr_list.py` | [tests.md](tests.md) |
| `tests/test_outreach.py` | [tests.md](tests.md) |
| `tests/test_outreach_agent.py` | [tests.md](tests.md) |
| `tests/test_planned_procurement.py` | [tests.md](tests.md) |
| `tests/test_prime_briefs.py` | [tests.md](tests.md) |
| `tests/test_public_research.py` | [tests.md](tests.md) |
| `tests/test_publish_engine_views.py` | [tests.md](tests.md) |
| `tests/test_publish_sheets.py` | [tests.md](tests.md) |
| `tests/test_releases.py` | [tests.md](tests.md) |
| `tests/test_routine_coverage.py` | [tests.md](tests.md) |
| `tests/test_routine_mail_config.py` | [tests.md](tests.md) |
| `tests/test_routine_mail_env.py` | [tests.md](tests.md) |
| `tests/test_routine_run_once.py` | [tests.md](tests.md) |
| `tests/test_routine_sender.py` | [tests.md](tests.md) |
| `tests/test_snapshot.py` | [tests.md](tests.md) |
| `tests/test_state_contracts.py` | [tests.md](tests.md) |
| `tests/test_today_list.py` | [tests.md](tests.md) |
| `tests/test_workflow.py` | [tests.md](tests.md) |
| `today_list.py` | [intelligence.md](intelligence.md) |
| `docs/reference/README.md` | [README.md](README.md) |

Unverified items: live portal coverage; deployed database presence, extra tables and row counts; writer/full schema of optional `data_quality.db`; current reviewed-input availability and data quality; generated dashboard/PDF layouts and existing guide content; current scheduled-task/timer/server state and delivery; private `ACCESS_GUIDE` location and installed Claude configuration; actual test/Linux/browser/OAuth/Sheets results and installed runtime/dependency availability. Each relevant page states its own limit. The source paths and module ownership were checked statically.
