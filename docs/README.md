# Documentation: start here

Illoura IT Revenue Intelligence collects public Australian government procurement data,
turns it into intelligence, sends the team a daily brief at 08:15 Sydney and serves the data
to Claude Desktop through a read-only API. This page lists every document and who it is for.

## New to the project

| Read | For |
|---|---|
| [architecture.md](architecture.md) | What the system does, where each part runs, the morning timeline, safety rules |
| [graduate-guide.md](graduate-guide.md) | Detailed graduate introduction to the technologies, every subsystem, data flow and reliability techniques |
| [market_intelligence.md](market_intelligence.md) | New isolated PostgreSQL federal-buyer research layer, evidence rules, imports, review workflow and tests |
| [project-audit-20261115.md](project-audit-20261115.md) | Verified 15 November audit results, deployed corrections and remaining limits |
| [SETUP_AND_OPERATIONS.md](SETUP_AND_OPERATIONS.md) | Setting up a Windows PC, getting data, running the collectors locally |
| [../README.md](../README.md) | Collector commands and options |
| [reference/README.md](reference/README.md) | Every file in the repository, grouped by area, with what it does |

## Running and fixing the live system

| Read | For |
|---|---|
| [runbook.md](runbook.md) | The alarm emailed or the brief did not arrive: what to check and what to run |
| [cloud_operations.md](cloud_operations.md) | The AWS server: every operator command, deploys, publication, retention, recovery, monitoring |
| [bdm_monitor.md](bdm_monitor.md) | The BuyICT alert monitor and BDM results on the team PC |
| [assistant_architecture.md](assistant_architecture.md) | The local assistant (local AI is disabled) |

## Reference by area

| Page | Covers |
|---|---|
| [reference/collectors.md](reference/collectors.md) | The 8 sources, `run_spiders.py`, statuses and failure signals |
| [reference/databases.md](reference/databases.md) | Every SQLite database and table |
| [reference/intelligence.md](reference/intelligence.md) | Intelligence, releases, classification, briefs and lists |
| [reference/exports-and-sheets.md](reference/exports-and-sheets.md) | Views, Google Sheets, dashboard |
| [reference/bdm-and-outreach.md](reference/bdm-and-outreach.md) | BDM monitor and outreach tooling (no autonomous outreach) |
| [reference/local-assistant.md](reference/local-assistant.md) | `local_assistant/` |
| [reference/cloud-and-daily-brief.md](reference/cloud-and-daily-brief.md) | `deploy/`, `routine/`, systemd units, the daily brief end to end |
| [reference/api-and-claude.md](reference/api-and-claude.md) | `api/`: endpoints, keys, the Claude Desktop bridge |
| [reference/tests.md](reference/tests.md) | Every test file and how to run the suites |
| [reference/configuration.md](reference/configuration.md) | Config files, requirements, environment variable names, private files by path |

## Records and history

| Read | For |
|---|---|
| [history.md](history.md) | Engineering history, newest first |
| [workstreams/](workstreams/) | Change records: 01 retention, 02 audited commits, 03 deploy CLI, 06 review fixes, 07 daily brief, 08 reliability, 09 audit fixes |
| [workstreams/progress.md](workstreams/progress.md) | Step-by-step log shared by Codex and Claude |
| [spec_mapping.md](spec_mapping.md) | How the original Tender Feed was mapped to the Illoura spec (7 October) |
| [HANDOFF.md](HANDOFF.md) | Historical handoff of 7 October (superseded; kept for context) |

## What is not in this repository

Private material lives in the team's private workspace on the team PC, never here: email
addresses, API keys, the Gmail token, the server SSH key, AWS account details, business
inputs (bench, relationships), sent-newsletter records and the current handoff page. A copy
of these docs is kept in that workspace under `project-docs/`.
