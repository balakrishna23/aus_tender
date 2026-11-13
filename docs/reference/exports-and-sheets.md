# Reference: exports, Google Sheets and dashboard

Publish database views as workbooks, Google Sheets and a local operations dashboard.

Files covered:

- `export_views.py`
- `export_to_gsheets.py`
- `gsheets_sync.py`
- `publish_sheets.py`
- `sheets_writer.py`
- `dashboard.py`
- `templates/company_profile.yaml`
- `templates/outreach_email.txt`
- Generated `dashboard/` output contract (directory absent in this worktree).

Exports are snapshots of stored evidence. Source collection, intelligence rebuild and remote publication have separate success states. Sheet publishing can expose private working notes and contact data, so credentials, folder IDs, the registry and generated outputs belong outside committed documentation. Dependency and environment-variable names are listed in [configuration.md](configuration.md).

| Module/artifact | Behavior and interface |
|---|---|
| `export_views.py` | `python export_views.py` writes dated `exports/illoura_views_YYYYMMDD.xlsx` with review views, TODAY, Tracker and prime briefs. Formats headers and source-linked values and preserves notes as text rather than executable formulas. |
| `export_to_gsheets.py` | Manual raw-source export to a selected spreadsheet, one worksheet per source. Arguments select spreadsheet, credential file/content, sources and write behavior. Reads schemas through `DatabaseManager`; separate from the Shared Drive registry workflow. |
| `gsheets_sync.py` | `GoogleSheetsSync.sync_spider()` and `sync_releases()` support optional automatic raw-tab/feed refresh. Reads source columns excluding internal content hash, normalizes standing-offer JSON and truncates oversized cells. Failure returns/logs sync failure for the collector runner. |
| `publish_sheets.py` | Creates or reuses one spreadsheet per configured dataset in a Shared Drive folder. `databases/sheets_registry.json` preserves IDs across refreshes; deleted sheets can be recreated and blank folder sheets can be claimed. `--only` selects datasets; `--links` reads registered links. Raw views use recent/current filters while retaining underlying history. Missing optional engine-view capacity can be skipped; other publication failures propagate. |
| `publish_sheets.py` Tracker flow | Pull human edits before rebuilding/replacing views; failed reads prevent destructive overwrites. Engine tabs include Tracker, prime briefs, TODAY, renewals, MMR, exemption history, plans, open ICT, estimated rates and agency routes. Tracker has validated dropdowns and stable action IDs. Access/share events are audited. |
| `sheets_writer.py` | Writes literal values, stages all replacement worksheets before one publish operation, checks Tracker conflicts, and cleans staging on failure. Temporarily lifts hiding filters before pasting and restores a suitable range filter; resizes refreshed grids. A failed staging chunk leaves live sheets available. |
| `dashboard.py` | Reads run JSON plus `activity.db`, `releases.db`, `outreach_agent.db`; emits self-contained `dashboard/index.html`, without a web server or external assets. `--open` opens the generated page. Collection/agent callers rebuild it best-effort after runs. |
| `dashboard/` | Generated output, absent from this checkout. No checked-in dashboard modules were found; actual generated layout/content is unverified. |
| `templates/company_profile.yaml` | Approved company context used by the legacy outreach writer. Keys include `company`, `abn`, `website`, `summary`, `what_we_supply`, `roles`, `clearances`, `locations`, `working_arrangements`, `availability`, `proof_points`, and `signature` (`name`, `title`, `phone`, `email`). Values are omitted. |
| `templates/outreach_email.txt` | Draft template for personalized manual outreach. Supplies wording/placeholders, not authorization to send. |

For a collection-only run use `--no-sync` and `GSHEETS_AUTO_SYNC=False`; `--no-sync` also suppresses registry-driven publishing in the runner. Running the export and publisher scripts directly can still write remote Sheets. Imported Sheets used by the assistant have a separate read-only path in [local-assistant.md](local-assistant.md).
