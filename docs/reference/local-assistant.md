# Reference: local assistant

Provide bounded local evidence queries and separate supplier/Sheets research helpers while local AI remains disabled.

Files covered:

- `local_assistant/.gitignore`
- `local_assistant/LOCAL_AI_DISABLED`
- `local_assistant/Modelfile`
- `local_assistant/README.md`
- `local_assistant/start.ps1`
- `local_assistant/index.html`
- `local_assistant/assistant.py`
- `local_assistant/planning.py`
- `local_assistant/reasoning.py`
- `local_assistant/hybrid.py`
- `local_assistant/build_hybrid_index.py`
- `local_assistant/public_research.py`
- `local_assistant/verification.py`
- `local_assistant/sheet_data.py`
- `local_assistant/sheet_sync_service.py`
- `local_assistant/sync_sheet_data.py`
- `local_assistant/company_coverage.py`
- `local_assistant/buyict_sellers.py`
- `local_assistant/contact_discovery.py`
- `local_assistant/route_review.py`
- `local_assistant/sync_approachable_routes.py`
- `local_assistant/website_leads.py`
- `local_assistant/check_public_sources.py`
- `local_assistant/audit_data.py`
- `local_assistant/snapshot_databases.py`
- `local_assistant/build_action_pack.py`
- `local_assistant/build_split_pdfs.py`
- `local_assistant/write_refinement_report.py`
- `local_assistant/benchmark.py`
- `local_assistant/evaluate.py`
- `local_assistant/evaluate_model.py`
- `local_assistant/evaluate_hybrid.py`
- `local_assistant/evaluation_cases.py`
- `local_assistant/heldout_cases.py`
- `local_assistant/second_holdout.py`
- `local_assistant/export_question_dataset.py`
- `docs/tools/build_ai_guide.py`
- `docs/Illoura_AI_Assistant_User_Guide.pdf`

`LOCAL_AI_DISABLED` states that local AI is disabled and routine runs must not start Ollama or download models. `start.ps1` refuses startup when that marker exists; finalization skips the hybrid index. This is not a universal runtime guard in every Python entry point: model/evaluation helpers remain executable code and are not routine steps to activate. Historical instructions in the local README describe the earlier pilot. See [assistant architecture](../assistant_architecture.md) for the detailed design. Unit tests and smoke checks have their canonical coverage on [tests.md](tests.md), including every remaining file in this directory.

| Module/artifact | Responsibility and boundaries |
|---|---|
| `assistant.py` | CLI questions or loopback HTTP UI, default port 8765. Read-only bounded database queries, evidence IDs and current date/status handling. Browser requests require trusted host/session token. Optional planning/model calls exist; `--facts-only` disables model use for a CLI question. `ILLOURA_PILOT_DB_DIR` overrides database selection; source defaults can fall back to a separate existing checkout. |
| `index.html` | Local question/evidence interface with query filters, optional semantic controls, public fact checks and explicit Sheets import/context controls. No buyer outreach UI. |
| `planning.py` | Rule-based multi-task plans plus validation of optional model JSON. Explicit organisation, date/value/role and exclusion constraints override model guesses. |
| `reasoning.py` | Retrieves and formats evidence across supported views. Counts/arithmetic use the matching data set rather than displayed samples. Optional hybrid selection operates after SQL constraints; unknown readiness/contact claims stay unknown. |
| `hybrid.py` | Local lexical/semantic fusion with packed vectors and reciprocal-rank scoring. Validates model/cache/text identity and refuses stale/missing coverage; facts remain in current SQL. Optional calls use local model service. |
| `build_hybrid_index.py` | Incremental local description/reviewed-finding embeddings into `runtime/hybrid.db`, plus manifest. Writes no source databases; requires local embedding runtime. Do not run while local AI is disabled. |
| `public_research.py` | Reads private reviewed public-source findings by path, preserving per-finding date, period, scope and proof limits. Does not turn an old review into a new claim. |
| `verification.py` | Bounded official AusTender API checks of selected contracts; compares exact identity/value/date/supplier fields, records hash/time/differences and rejects cross-host redirects. Verifies contract facts, never hiring/renewal intent. |
| `sheet_data.py` | Bounded read-only queries over imported Sheet rows, exact workbook/tab/row selection and literal full-text terms. Returns row-level provenance and staleness; unknown scope never broadens. Generic arbitrary financial totals are unsupported. |
| `sheet_sync_service.py` | Starts one background read-only import only after explicit UI action; reports job status and prevents concurrent imports. |
| `sync_sheet_data.py` | Reads only registered spreadsheets using read-only scope; imports formatted values into versioned `sheet_imports.db`. Complete tab reads become active; failed reads retain previous snapshots. Original numbering/duplicate headers survive. No Sheet editing or source-database writes. |
| `company_coverage.py` | Builds all-history supplier identity/location inventory and separately reviewed contact routes in `company_contacts.db`. Agency contacts are never silently labelled supplier contacts; company address provenance is retained. |
| `buyict_sellers.py` | Robots-aware public seller catalogue/profile traversal; stores company identity, marketplaces and website only. Does not collect named people from the platform. |
| `contact_discovery.py` | Resumable company-website research queue and review candidates. Starts from identity-linked sites, checks robots/private destinations/redirect scope, follows observed links and distinguishes business routes from individuals. Does not guess addresses or send messages. |
| `route_review.py` | Exports a dated review workbook and imports explicitly approved company routes. Proposes functional mailboxes/business lines rather than named individuals; appends reviewed routes with identity/proof information. |
| `sync_approachable_routes.py` | Appends approved route input into the supplier store without replacing existing contacts. Private source path only; no publication or email. |
| `website_leads.py` | Searches identity-backed website gaps using Wikidata, related seller evidence and optional Brave search (`BRAVE_API_KEY`). Verifies ABN evidence on candidate sites and logs each supplier's outcome; a lead is not a reviewed contact. |
| `check_public_sources.py` | Curated official URL checks with robots/redirect protections and checked-time/hash provenance. Separate manual research process, not general chat web search. |
| `audit_data.py` | Reads procurement/identity views and writes aggregate `reports/data-quality.json`; does not export personal contact details or create the optional `data_quality.db`. |
| `snapshot_databases.py` | Recovery snapshot after refresh; no retention deletion. Has operational data effects and is not a unit test. |
| `build_action_pack.py` | Generates a private action-pack PDF from stored opportunities and source-linked evidence using ReportLab. Requires current data; execution/layout are unverified here. |
| `build_split_pdfs.py` | Builds separate ICT/health/contact PDFs from stored data; includes supporting fetch/render helpers. Private generated outputs; execution/layout are unverified here. |
| `write_refinement_report.py` | Reads actual run/evaluation artifacts and emits a standalone HTML review report. Does not establish fresh model performance by itself. |
| `benchmark.py` | Convenience entry point for the local planning/model benchmark. Not a no-model smoke test. |
| `evaluate.py` | Plan-fidelity and semantic retrieval checks against current local data; writes evaluation artifacts. |
| `evaluate_model.py` | Actual local model planning plus held-out retrieval checks; requires runtime/data. |
| `evaluate_hybrid.py` | Real embedding contrasts and stored-data constraint checks; requires local embedding runtime/data. |
| `evaluation_cases.py` | Reviewed expected query requirements used in evaluations; not business facts. |
| `heldout_cases.py` | Unseen paraphrases for planning/retrieval evaluation. |
| `second_holdout.py` | Additional unseen entities/constraints/unsupported facets for evaluation. |
| `export_question_dataset.py` | Routing examples for human review; does not train model weights. |
| `Modelfile` | Earlier planning-model profile, deterministic parameters and examples. Prompt customization, not fine-tuned weights. Retained as historical configuration; no model download is authorized. |
| `start.ps1` | Marker-guarded Windows launcher. Its historical path starts a hidden portable model service if absent, sets model/runtime limits, then launches the assistant. Marker currently blocks that path. |
| `LOCAL_AI_DISABLED` | Checked-in stop marker; no Ollama startup/model downloads for routine newsletter runs. |
| `README.md` | Historical pilot usage/limits; read alongside the current disable marker. |
| `.gitignore` | Excludes local runtime, model/data/report artifacts according to its patterns. |
| `docs/tools/build_ai_guide.py` | Builds the PDF user guide with ReportLab layout and architecture drawing helpers. |
| `docs/Illoura_AI_Assistant_User_Guide.pdf` | Existing generated guide; content/layout was not inspected in this static code review and is unverified. |

Optional official verification and supplier/Sheets research can make network calls even though the core retrieval path is local. No helper was executed in this documentation pass. Private input values, imported cells, personal routes, model files and generated reports are intentionally omitted.
