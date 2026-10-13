# Illoura evidence assistant — local pilot 2.3

The pilot reads `databases/` beside the repository's `local_assistant/` folder when
that directory contains `intelligence.db`, otherwise the existing `C:\work\aus_tender\databases`.
Set `ILLOURA_PILOT_DB_DIR` to override the database directory.
It combines bounded database queries with a local Qwen planning model. Facts,
dates, counts, filters and arithmetic are formatted by application code. It has
no paid model calls and sends no outreach. VPS work is deferred.

Saved documentation: [user guide PDF](../docs/Illoura_AI_Assistant_User_Guide.pdf)
and [complete architecture with component explanations](../docs/assistant_architecture.md).

## Run

### Files included in GitHub

GitHub contains the assistant source, browser interface, model profile, tests and
support scripts. It does not contain databases, private inputs, generated reports,
Ollama binaries or model weights. A checkout alone therefore does not include a
working data store or an installed model.

Use Python 3.11 and the repository's dependencies. On Windows, `tzdata` is needed
for Canberra dates. Browser smoke checks additionally need Playwright and Edge.
The 83 unit tests use fixtures and do not need real databases, Ollama or internet.
The question evaluations and audit scripts do need the local databases; model
evaluations also need Ollama and the model profile.

For the portable Windows launcher, restore the Ollama executable under
`runtime/ollama/ollama.exe`, then pull `qwen3:4b-instruct` and create the profile
using the commands below. Alternatively, run an existing local Ollama server at
`127.0.0.1:11434`, create the profile with `ollama create illoura-planner -f Modelfile`,
and launch `python assistant.py` from this folder. Model downloads stay local.
Keep the source and operational data private. The repository remains private;
private inputs and generated artifacts remain excluded even there.

From this directory in PowerShell:

```powershell
.\start.ps1
```

Open `http://127.0.0.1:8765`. Keep the terminal running. Ctrl+C stops the chat
server. The portable Ollama server runs hidden on loopback port 11434 and unloads
an idle model after five minutes. No Windows startup task is installed, and
existing scraper schedules have not been enabled.

If rebuilding the planning profile:

```powershell
$env:OLLAMA_HOST = '127.0.0.1:11434'
.\runtime\ollama\ollama.exe pull qwen3:4b-instruct
.\runtime\ollama\ollama.exe create illoura-planner -f .\Modelfile
```

The app uses `illoura-planner:latest`. The base model remains available for comparison.

## What was refined

### Google Sheets import (2.3)

**Sync Google Sheets** reads the ten project-registered spreadsheets using the
existing service account with `spreadsheets.readonly` scope. Imported data goes to
`databases/sheet_imports.db`; source scraper databases and Sheet cells are unchanged.
First import: 26 tabs, 51,637 nonempty data rows, no failed reads. This is a snapshot,
not continuous monitoring. CLI alternative: `python sync_sheet_data.py`, optionally
`--only intelligence`. The UI runs one background import job at a time.

Ask `What Google Sheets data do we have?`, `Show Google Sheets rows for DXC`,
`Show Google Sheets notes from tab "Tracker" in workbook intelligence`, or
`Show Google Sheets row 12 from tab "Tracker" in workbook intelligence`.
Use `containing "CN4228169"` or `containing "a phrase"` for a literal row search.
Workbook selectors use registered keys, not display titles. Unknown selectors
produce no rows and never broaden to all sources. The **Include imported Google
Sheets context** checkbox adds matching selected-contract/company Sheet rows to
ordinary procurement answers. An explicit Sheets question works regardless of
that context checkbox. Evidence includes workbook, tab, row link and last-read UTC.

Raw formatted cells and historical snapshots are retained. Only complete tab reads
become active; failed reads preserve the previous snapshot and are labelled. Empty
rows retain numbering, repeated headers keep separate columns, and unchanged tabs
reuse their stored snapshot. Queries use a consistent read-only SQLite transaction
and bounded full-text search. Notes remain data, not instructions.

Limitations: first row is treated as headers; values are formatted display values,
not typed financial measures or formula expressions. Sheet-row lookups do not
enforce generic monetary/date filters or calculate arbitrary totals. Procurement
SQL still handles those filters. Sheet content is not independently verified;
owner names do not establish edit history. Per-tab downloads are not an atomic
snapshot across Google Sheets. Sync before relying on a recent edit; snapshots
older than one hour are flagged. New spreadsheets must first be registered. No
Sheet-row embedding index, Sheet editing, public sharing or scheduled sync is enabled.

### Hybrid retrieval (2.2)

The UI option **Include related meanings in search** combines existing exact role
matches, BM25 keyword ranking and local `all-minilm:22m` embeddings. The 46 MB
embedding model runs on CPU through Ollama. SQLite stores normalized 384-dimensional
vectors; NumPy computes cosine similarity only over fresh SQL-eligible records.
Reciprocal rank fusion combines lexical and semantic rankings. Similarity is not
confidence, hiring proof or a suitability score. Generic short descriptions have a
stricter relevance gate. Existing explicit role matches remain eligible when the
topic comes from the role; an explicit topic narrows relevance instead and retains
any separate positive role keyword constraint from the question.
Staffing shortlists retain explicit staffing/role wording as their first priority,
then hybrid relevance and end date; a semantically close general service should
not outrank an explicit staffing role just because of its similarity score.

Enable the option and put an optional short topic such as `cloud migration` in the
topic field. Alternatively: `Show contracts about "breach investigation" ending
within 90 days`, or `Contracts related to cloud migration ending within 90 days`.
Without a supplied topic, supported roles provide the search topic.
Dates, value bounds, supplier ABNs, organisations, statuses and exclusions stay in
SQL; semantic search never relaxes those constraints. Other views keep structured
retrieval. Disable the option for the previous query/ranking behaviour.

Initial coverage is the renewal view (up to one year), relevant open/planned
opportunity titles and reviewed research findings. Full historical contracts,
full RAP PDF passages and CVs are not indexed. The private index is under
`runtime/hybrid.db`, and its manifest is under `reports/hybrid-index.json`.

With Ollama running, from this folder:

```powershell
$env:OLLAMA_HOST = '127.0.0.1:11434'
.\runtime\ollama\ollama.exe pull all-minilm:22m
python build_hybrid_index.py
python evaluate_hybrid.py
```

Run the builder after each manual scraper/intelligence rebuild or research edit.
Only new or changed text chunks are embedded; the query always gets dates and facts
from the current source databases. If eligible chunks are missing, the index model
differs, or Ollama is unavailable, the answer explicitly falls back to keyword
retrieval. The index retains resumable batches; it never alters source databases.
Set `ILLOURA_HYBRID_INDEX` to use another private cache path. Model changes require
a new cache. The launcher permits Qwen plus the small CPU embedding model to stay
loaded together, with one inference request at a time.

- Multi-part questions can combine contracts, renewals, opportunities, panels,
  MMR evidence, exemption history, estimated rates, contact routes, comparisons,
  TODAY actions, reviewed RAP sources and human-review drafts.
- Company names and role filters remain separate. Cyber searches match work
  descriptions rather than a supplier name containing “cyber”.
- Renewal windows, explicit day intervals, role exclusions, equipment/licence/
  SaaS/assessment exclusions, supplier ABNs and dollar-value bounds are preserved.
  Month windows mean 30 days per month and are labelled as such.
- Full matching sets determine counts and renewal ranking before a bounded
  evidence sample is displayed. “Open” queries do not include plans. Closing
  timestamps are checked at query time; optional closing windows use Canberra time.
- Expired panel memberships are labelled and removed from “current” queries.
- Candidate, clearance, delivery-location, relationship and commercial gaps are
  stated instead of inferred. A public email is not a warm introduction.
- Optional official API checks compare up to three selected contract records.
  Results include checked time, compared fields, differences, source URL and
  response hash. They verify contract fields, not hiring or renewal intention.
- Selected official company research is stored privately in
  `inputs/public_research.json`, with source, locator, reviewed date and proof limit.
  Historical RAP periods remain historical. There is no general web-search API in
  the chat. Direct source checks are a separate manual process.
- Evidence references are clickable within the answer. Human-readable notice
  links and raw JSON links have distinct labels. Nested evidence renders fully.
- Read-only SQLite connections, parameterized queries, query deadlines, loopback
  binding, host validation, a browser session token and no question-content logging.
- A database-build change during an answer causes a retry, preventing silent mixing
  of two completed intelligence builds.

## Model and hardware

Checked on 8 October 2026: Dell G15 5511; i5-11260H, 6 cores/12 threads;
15.7 GiB usable RAM; RTX 3050 Laptop, 4 GiB VRAM; NVIDIA driver 610.74.
PyTorch is CPU-only (`2.6.0+cpu`); no CUDA training stack is installed.

Ollama v0.40.1 is portable under `runtime/ollama`. The official release archive
was verified against its SHA-256 digest. Model files stay under `runtime/models`.
The base is `qwen3:4b-instruct`, Q4_K_M, about 2.5 GB. Context is capped at 4096
tokens, with one Qwen model plus an optional CPU embedding model and one parallel inference request. Earlier placement
was approximately 67% GPU / 33% CPU, using about 3.5 GB runtime model memory.
Cold loading can take tens of seconds; common queries skip inference entirely.

**The weights have not been fine-tuned.** `Modelfile` adds Illoura instructions,
examples and deterministic inference settings to the existing model. That is
prompt/profile customization, not LoRA or a newly trained language model. Actual
weight training needs a suitable training checkpoint, training software, a reviewed
dataset and a measured memory budget. The current inference file and tests do not
establish that 4B training will fit this laptop. No cloud GPU or paid API was used.

`export_question_dataset.py` prepares 40 routing-example candidates and 10 validation
candidates in `reports/training-review`. Their full labels partly come from rules
and require human review. All examples have already been used during refinement;
future weight training needs a new untouched test set and real user failures.

The application uses rules for clear requests and a validated model fallback for
unfamiliar names/wording. The model never generates SQL or contract facts. Earlier
free-form explanations misrouted questions and invented date interpretations, so
free-form factual generation remains disabled.

## Validate

```powershell
python -m unittest discover -s . -p 'test_*.py'
python evaluate.py
python evaluate_model.py illoura-planner:latest
python evaluate_model.py illoura-planner:latest second
python audit_data.py
python http_smoke.py
python ui_smoke.py
```

`evaluate.py` checks 50 labelled questions against real stored data without network
or model calls. The two model evaluations contain 20 questions each. Their initial
failures were used to refine this version, so final scores are regression results,
not an independent untouched accuracy estimate. The first paraphrase set passed
17/20 using the revised rules and base model, and 20/20 with the custom profile.
Final local unit checks, pipeline regression and current latency measurements are
recorded in `reports/`. Passing these cases does not imply perfection.

`benchmark.py` runs the current first 20-question model benchmark. Reports, runtime files and private inputs are
ignored. Keep the pilot private and do not push research, CVs or business evidence
to the public scraper repository.

## Supplier contact routes

Contract notices carry agency contacts only, so supplier contacts come from each
company's own website, and every route needs a person's approval. All of it lives in
`databases/company_contacts.db`:

```powershell
python company_coverage.py                       # every supplier identity from contract history
python buyict_sellers.py --profiles 200          # BuyICT catalogue + next 200 profiles (ABN -> website)
python contact_discovery.py --crawl-known-sites --limit 500   # published contacts on those websites
python route_review.py                           # exports/supplier_route_review_<date>.xlsx
python route_review.py --import <reviewed .xlsx> # approved rows -> inputs/approachable_company_routes.json -> DB
```

- **Websites:** a company's website comes from its BuyICT seller profile (published against
  its ABN) or from `inputs/supplier_websites.json` (hand-reviewed, which wins). Only
  company-level fields are taken from BuyICT. Its terms of use limit personal information
  seen there to procurement through BuyICT.
- **What the crawl does:**
  - obeys robots.txt and waits at least 1 s between pages of a site
  - reads at most 5 pages, contact pages first
  - follows a redirect only to the same brand's own domain (data3.com.au -> data3.com)
- **What it extracts:**
  - email and tel links on any page
  - plain-text emails, Australian landlines and 1300/1800 numbers, from the home and contact/office pages only
- **The review sheet:**
  - proposes only functional mailboxes and Australian business lines
  - counts named individuals but never proposes them
- **Resuming:** the crawl skips suppliers already attempted, so runs with `--limit` continue
  from where the last one stopped.

## Data refresh and remaining gaps

The manual 8 October run refreshed the contract API, notice pages, tender notices,
plans, BuyICT, ACT and NSW, without Sheets publishing. The standard standing-offer
run found nothing due under its seven-day rule and was labelled “FAIL(no responses)”.
A separate one-time recheck fetched all 457 current/undated stored offers; the
intelligence views were rebuilt afterward. The original batch report is preserved.
Panel discovery uses links in contract notices and is not an exhaustive panel directory.

The NIAA MMR list remains dated 25 February 2026. A direct download matched the
stored file byte-for-byte; its list date was not changed to today's date. Supplier
matches still require review, and the list does not prove which contracts have MMRs.

The stored audit found 29,153 contracts without a supplier ABN, 18 with an end date
before their start, and 82 without a readable notice link. Missing ABNs may be
foreign suppliers. The application flags date conflicts and does not guess a fix.

Candidate CV matching, conversational memory, general live web search and remote
access are not connected. Private candidate and relationship templates are provided
under `inputs/`; they contain no fictional records. Clearance, availability and
permission must come from Illoura/candidates, not public procurement data. Estimated
day rates describe linked historical contracts, not candidate pay or assured margins.

RAP policies and public supplier routes support research and a possible general
introduction. They do not establish a current staffing package, unmet MMR obligation
or an existing relationship. Direct source checks report availability separately
from claim verification; a successful HTTP fetch alone does not prove hiring.

The upstream build/retrieval timestamps lack timezone offsets. The UI labels that
limitation; query dates use `Australia/Canberra`. Original source data is retained.
Nothing was sent, published to Sheets, deployed to a VPS or committed publicly.
