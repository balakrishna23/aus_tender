# Illoura procurement intelligence project explained for graduates

This guide explains how the Illoura project turns public Australian government procurement information into a daily internal email, searchable data and business development working views. It assumes you can read basic Python but have not operated a production data system. Read the first six sections in order, then use the later sections as references while following the code.

The central engineering idea is a controlled data pipeline. Collectors obtain evidence, databases preserve it, deterministic transformations interpret it, validation gates decide whether it is usable, and separate delivery components publish it. The deployed morning brief uses rules and templates. A language model does not decide what to send.

The implementation described here was inspected on 15 November 2026. [The audit report](project-audit-20261115.md) separates verified live behavior, tests and remaining limitations. [The reference index](reference/README.md) provides the existing file-by-file reference for the repository.

## 1 The business problem and the outputs

Government procurement information is spread across portals. A business must distinguish an invitation to submit a response from an award already made, identify the closing deadline, understand buyer requirements, and decide whether it has the access and capability to respond. Reading hundreds of pages manually every morning is slow and makes changes easy to miss.

Illoura is an IT labour-hire business. The system helps its team find relevant requests and research purchasing patterns. It does not establish that a candidate is available, that a seller has been invited, or that a buyer wants a particular partner. Those facts require separate confirmation.

There are three main outputs. The server prepares a daily internal brief of current BuyICT requests. A read-only API makes published procurement data available to Claude Desktop. Local tools provide spreadsheets, monitoring, qualification cards and action tracking for the team. These outputs share source data but have different jobs and permissions.

A simple example is an agency publishing a request for a software developer. The collector stores the public request and its source URL. Classification identifies relevant ICT work. The morning selector may choose it if it is open and closes within the configured window. A fresh check confirms the public page is still open. The email includes the official link and factual requirements. A person then checks invitation access, candidate availability and the complete pack before deciding whether to respond.

The same agency awarding a contract is a different event. The contract may help research incumbent suppliers and possible future renewal windows, but it is not an open vacancy. The project keeps those meanings separate.

## 2 The basic vocabulary

| Term | Meaning in this project |
|---|---|
| Procurement | A public organisation buying goods or services through a defined process. |
| ATM | Approach to Market, an AusTender opportunity notice. |
| RFQ | Request for Quotation, used for many BuyICT requests. |
| RFI | Request for Information, which does not itself establish an award or hiring commitment. |
| CN | Contract Notice, describing a contract or amendment. |
| SON | Standing Offer Notice, often associated with a panel arrangement. |
| Panel | A purchasing arrangement involving eligible suppliers; membership alone does not prove access to every opportunity. |
| ABN | Australian Business Number, useful as an organisation identity anchor. |
| OCDS | Open Contracting Data Standard, the structured release format used by the official contract API. |
| Spider | A Scrapy component that discovers pages, extracts records and follows links. |
| Pipeline | Ordered processing stages, such as cleaning an item and storing it. |
| Upsert | Insert a new row or update the existing row identified by its stable key. |
| Watermark | A saved checkpoint describing how far a collection has successfully progressed. |
| Provenance | The source URL, timestamps and other evidence explaining where a fact came from. |
| Generation | A completed immutable publication of selected databases for readers. |
| Idempotency | Repeating an operation without creating unwanted additional effects. |
| BDM | Business development management, including qualification and follow-up work. |
| MCP | Model Context Protocol, used to expose bounded tools to an assistant client. |

Names such as MMR and RAP refer to specific procurement and organisational evidence used by the project. Imported lists and reviewed public commitments are evidence inputs. A matching name or historical commitment is not proof of a particular contract obligation, partner opportunity or current willingness to engage.

## 3 The complete journey of the data

The main route is public portal to collector, collector to working SQLite databases, working databases to derived intelligence, completed run to a published generation, and published generation to the API. The email route uses the completed evidence run, a selected shortlist, fresh public checks and a separate sender.

```text
Public APIs and portal pages
             |
       Eight collectors
             |
     Source SQLite databases
             |
   Cleaning and identity handling
             |
 Releases and intelligence builds
             |
   Quality and evidence checks
          /              \
 Immutable publication   BuyICT selection and fresh checks
          |                         |
 Read only API and MCP    Email composition and layout checks
          |                         |
    Claude Desktop       Authorized internal Gmail send
```

Each arrow represents a contract between components. For example, collection must report every expected source. The intelligence build must have a completion timestamp. The sender must receive the same content hashes that were approved during automated review. A component should not quietly manufacture missing facts to satisfy the next component.

The system also has a control route. A scheduler starts work, locks prevent overlapping operations, deadlines stop hung processes, health checks summarize evidence, and an independent AWS watcher detects both reported failures and silence. These controls do not produce procurement information themselves; they make the information pipeline operable.

## 4 Where the project runs

The production server is an Ubuntu virtual machine on AWS Lightsail in the selected Region, Sydney `ap-southeast-2`. It has two virtual CPUs, 8 GB of memory and a 160 GB disk allocation. It runs collection, evidence generation, publication, email preparation, the scheduled send and the read-only API. The team's Windows PC is used for development, local business tools and administration.

Lightsail is a managed way to obtain a virtual machine with networking and snapshots. The operating system still needs application processes, updates, storage management, permissions and monitoring. Moving a script onto a cloud server does not automatically give the script scheduling or failure recovery; the project implements those responsibilities separately.

GitHub stores the private source repository and runs automated checks. Source code, documentation and tests belong there. Credentials, mailbox addresses, private business inputs, collected database files and personal working material are excluded. A `.gitignore` rule reduces accidental additions; it does not encrypt a file or remove material from earlier Git history.

An external AWS Lambda watcher runs outside the virtual machine. This separation matters: if the server stops, a health process on that server cannot report its own silence. The watcher requests public readiness, emits a CloudWatch metric, and an alarm uses SNS email notifications. A separate Lightsail alarm monitors infrastructure status. Alert subscription confirmation is part of completing that route.

The local assistant and the Claude Desktop connector are distinct. Claude Desktop can use the server's read-only tools. The optional local model system exists in the repository, but local AI is disabled and routine jobs must not launch Ollama or download models.

## 5 Technologies and why they fit their roles

| Technology | Where it is used | What a graduate should understand |
|---|---|---|
| Python 3.11 | Collectors, transformations, API helpers, operations and tests | A shared language makes it easier to exchange data and reuse validation functions. |
| Scrapy | Most source crawlers | Provides requests, responses, selectors, retries, duplicate handling, statistics and item pipelines. |
| Requests | Direct public HTTP and API helpers | Appropriate for request and response workflows that do not require a browser. |
| Playwright and Chromium | JavaScript-dependent procurement pages and email layout checks | A browser executes page JavaScript and exposes the resulting DOM. It costs more memory than an HTTP request. |
| scrapy-playwright | ACT browser integration | Connects browser-backed requests to Scrapy's normal processing model. |
| undetected-playwright | Shared browser initialization | NSW and ACT use the authorized initializer before navigation; robots and site terms still apply. |
| SQLite | Source, intelligence, workflow and activity databases | An embedded relational database stored in a file; no separate database server is required. |
| SQL | Filtering, joining, grouping, counts and calculations | Calculations operate on matching records rather than on a sample shown to a user. |
| pandas and openpyxl | Tabular transformations and Excel exports | Useful for arranging data and producing workbooks; the databases remain distinct from the workbook views. |
| YAML | Classification, scoring, aliases and configuration | A readable configuration format lets rules change without embedding every value in Python. |
| FastAPI and Pydantic | Read-only HTTP API and input validation | Define routes and structured request models, including limits on lengths and returned rows. |
| Uvicorn | API process | The application server that serves the FastAPI application. |
| MCP | Server tools and the Claude Desktop bridge | Gives an assistant named tools with structured inputs and outputs. |
| nginx and TLS | Public HTTPS entry point | nginx accepts network traffic, applies transport limits and proxies to the local application. |
| systemd | Linux services and timers | Starts jobs, tracks process outcomes, enforces limits and schedules work. |
| Gmail API and OAuth | Internal brief delivery | OAuth grants scoped access without putting a mailbox password in source code. |
| gspread and Google authentication libraries | Optional Google Sheets workflows | Read or update registered sheets using separately configured credentials. |
| Paramiko | Administrator deployment connection | Uses pinned SSH identity and fixed remote actions to stage and install changes. |
| Git and GitHub Actions | Version control and CI | Preserve reviewable changes and run repeatable checks on Windows and Linux. |

The verified core dependency set is in `requirements-tested.txt`; deployment dependencies are in `requirements-deploy.txt`; the API has `api/requirements.txt`. Pinning tested versions helps reproduce an environment. It does not make an old version permanently safe or eliminate the need to test upgrades.

## 6 How the eight procurement collectors differ

### The official contract API

`austender_api.py` consumes official OCDS releases. It stores immutable release versions separately from rolled-up contracts. A release is an event or version; the rolled-up contract represents the current interpretation of those versions. Keeping both allows a reader to investigate an amendment instead of seeing only the newest value.

The collector uses incremental windows and saved state. An overlap rereads a small recent period so delayed releases are less likely to be missed. The watermark advances only after the window is valid and complete. A valid empty release list can advance a checkpoint. An HTTP 200 response with an unexpected JSON envelope cannot: successful transport does not prove successful extraction.

Historical and active-contract modes are different workloads. Historical ingestion can use resumable windows, while active-contract retrieval focuses on relevant future end dates. Neither should be confused with a new opportunity feed.

### Federal contract notice pages

`spiders/aus_contract.py` adds public page fields not represented in the same way by the API, such as some panel, supplier and contact details. API notice identifiers help discover the pages that need to be visited. Stored records support incremental selection rather than rereading all history every morning.

Some API notices have no public page yet. A pending-notice queue preserves them for later checks. After the configured retry period, the record remains as a tombstone rather than vanishing without explanation. A canary reads a known published notice to distinguish a quiet or unpublished-only day from a parser that has stopped understanding every page.

### Federal open tenders

`spiders/aus_tender.py` walks the current ATM listing and visits details. It also revisits previously open notices that leave the current list. A notice disappearing may mean closure, withdrawal or a changed public route; the system should obtain enough evidence before updating its state.

Listing coverage checks compare extracted identities with the listing's count evidence. A changing total gets a bounded fresh walk. A genuinely empty listing must have structural or explicit zero-result evidence. Returning no items because selectors broke must fail visibly.

### Planned procurements

`spiders/planned_procurement.py` visits agency procurement plans and extracts their rows. Some rows inherit contact information from the plan rather than containing their own contact box. The parser preserves that relationship.

Plans can correctly contain no rows. The 15 November failure showed why this matters: the live site's empty-plan wording differed from the accepted wording, and the collector incorrectly classified many empty plans as unexplained. The corrected parser recognizes the observed variants. Structural extraction, explicit emptiness and pagination evidence are different signals and should be tracked separately.

### Standing offers

`spiders/standing_offer.py` follows relevant standing-offer links and refreshes current offers when due. Previously closed or expired material remains historical evidence. A successful run may save zero rows when no offers need a refresh; that is different from failing to extract rows that were expected.

Suppliers listed on a panel and agencies able to use it are distinct fields. A historical panel record cannot establish that a supplier has current access to an unrelated RFQ.

### BuyICT

`spiders/buy_ict.py` uses the anonymous public ServiceNow portal calls observed behind BuyICT pages. The portal exposes bootstrap values and widget instances. The collector obtains the relevant identifiers, walks public listing pages and retrieves detail data for new or still-changing records.

Portal transport details are fragile interfaces. The parser now accepts either JavaScript quote style for bootstrap values, binds the detail widget to its own instance, normalizes field labels and handles formatted integer totals. It can recover a known RFQ identity from the same stable system ID when appropriate. It must not attach one request's details to a different request.

Coverage concerns pages as well as rows. Reported totals may include records that are not publicly visible, so public item count alone is not a sufficient completeness test. Missing totals require explicit pagination evidence. Historical IDs cannot prove that a newly added page was visited.

### ACT tenders

`spiders/act_tender.py` uses Scrapy with Playwright for the listing tables. It extracts public list fields without assuming that challenged detail pages can be visited. Expected table structure, explicit empty lists, enabled forward links and page totals help distinguish successful collection from an incomplete walk.

The shared browser initializer is installed through `playwright_page_init_callback`. This callback runs before navigation, so every initial ACT request receives the same initialization behavior. A failed initializer must not be recorded as successful.

### NSW tenders

`nsw_tender_fetch.py` uses asynchronous Playwright directly. It collects open opportunities and schemes, refreshes a bounded number of stale details, and gathers contract-award and standing-offer notices. Schemes are not ordinary tenders, so count reconciliation includes them without publishing them as equivalent opportunities.

Open opportunities are marked closed only after a complete listing walk. The enabled next link is discovered across direction links, rather than assuming the first direction link is forward. Notice history is bounded to five pages per notice type in the runner. If more pages remain, the source is explicitly partial. That is a known coverage boundary, not a claim that all notice history was collected.

## 7 The collector framework step by step

`run_spiders.py` is the batch runner. It starts sources, applies per-source timeouts and retries, gathers statistics, builds the final feed and views, and writes a report. Its overall exit status tells the coordinator whether the batch was clean. The per-source report explains which source had a problem and whether records were saved.

For Scrapy sources, a request becomes a response, a parser extracts an item, and configured pipelines process the item in priority order. `DataCleaningPipeline` normalizes values. `DatabasePipeline` validates identity, compares stored evidence and saves accepted records. Statistics describe new, updated, unchanged, missing and failed records.

`duplicate_filter.py` combines request fingerprints with database-aware checks. A fingerprint identifies an equivalent request. Persistent skipping saves work for already-final records, but mutable details need explicit refresh behavior. Deduplication becomes a defect if it prevents a changed open tender from being revisited.

`middlewares.py` adds spider metadata and rate-limit backoff. The settings use robots obedience, a per-domain concurrency limit, download delay, request timeout and AutoThrottle. AutoThrottle adjusts pacing from observed response behavior. A 429 response asks the client to slow down; immediate aggressive retries would usually make the outage worse.

Scrapy uses Twisted's event-driven networking. Instead of one thread sitting idle for every pending response, an event loop schedules work when a network operation is ready. The configured `AsyncioSelectorReactor` lets this cooperate with Python's asyncio ecosystem. NSW's standalone Playwright code uses `async def` and `await` directly. An `await` yields while an operation is pending; it does not mean that every source runs in parallel. The batch runner still controls source order and resource use, while the routine runs collection and supplemental research concurrently.

HTML extraction uses selectors. CSS selectors describe elements by tag, class or attributes; XPath can describe relationships and text. The parsers need the meaning of a field, so they should tolerate harmless nested spans or extra classes while rejecting a page with no recognizable identity. JavaScript-backed portals can expose JSON through browser-observed calls. JSON is structured data, but its shape still needs validation: a successful response containing an error object is not a valid listing.

The browser initializer in `spiders/browser_support.py` is shared by NSW and ACT. Direct HTTP/API sources keep their native clients. Browser initialization does not change the requirement to respect robots rules, authorized scope and rate limits.

Field-loss checks protect against partial parser breakage. If many checked records lose key fields, the source fails. A field already absent at the same stored identity is counted as a known omission. This prevents old source omissions from falsely becoming a new parser failure while keeping newly missing fields visible.

## 8 How data is stored and identified

SQLite stores related records in tables. A table has columns with defined meanings; a row represents one entity or observation. A primary key or stable business key identifies the row. The project generally identifies source records by source-specific IDs and URLs, with special handling for portals that expose both a public ID and an internal system ID.

This pipeline combines extraction, loading and transformation. Some transformations happen before storage, such as removing markup or normalizing a date. Others happen after source loading, such as joining contracts to reviewed organisations and building renewal windows. Retaining source evidence alongside derived interpretations lets an engineer correct a classification rule and rebuild the views without pretending that the underlying portal published a different fact.

`database_manager.py` creates source schemas, adds missing columns, normalizes fields and upserts accepted items. A content hash helps distinguish changed data from identical data. A hash is a compact fingerprint: the same normalized input should produce the same hash. It does not explain what changed, and it is not encryption.

The working source tables contain current saved representations and can be updated through controlled collection. The official API's version table is append-only evidence. Published generations are immutable reader snapshots. These are three different kinds of storage, and the term immutable should not be applied indiscriminately to every file.

| Store | Main role | Examples |
|---|---|---|
| Source databases | Saved portal/API facts and crawl state | `buy_ict.db`, `aus_tender.db`, `contract_api.db` |
| Derived databases | Rebuildable interpretations of source data | `intelligence.db`, `releases.db` |
| Human workflow databases | Decisions and edits that must survive rebuilds | `actions.db`, `bdm_monitor.db` |
| Research and evidence databases | Reviewed findings and source snapshots | `research_sources.db`, `rap_evidence.db`, `organisations.db` |
| Operational databases | Activity and historical run events | `activity.db` |
| Published generation | Allowlisted data exposed to readers | `/srv/illoura-data/generation-*` |

An upsert must not replace the wrong entity when a URL changes or is reused. The identity checks therefore matter as much as the SQL statement. Human notes also need a different lifecycle from generated rows: rebuilding an opportunity score must not erase a person's qualification decision.

`project_paths.py` provides consistent paths. `AUSTENDER_DB_DIR` allows an isolated database directory for tests or live canaries. This is how a developer can exercise a parser without mixing its output into the production working database.

## 9 Incremental collection and completeness

Incremental collection asks what needs to be checked now rather than downloading all history. A source may use a modified-date watermark, a current open list, a due-for-refresh age or a durable queue of unpublished notices. The correct strategy depends on how that source changes.

Overlapping a previous time window is useful because records can appear late or be amended. Upserts and content hashes make rereading tolerable. The checkpoint is a promise about completed work; advancing it before completion risks permanently skipping records after a crash.

Completeness needs several signals: process completion, pagination evidence, expected source identities, successful persistence and successful downstream rebuilding. A crawler can exit normally after a parser starts returning no rows. A database can still contain yesterday's rows when today's walk failed. A newly generated report can claim success while containing contradictory fields. Validation should examine the evidence behind the summary.

The audit added a regression for that last case. A clean-looking `success: true` flag no longer bypasses the required eight named sources or a failed feed marker. The same feed validation is enforced at collection coordination, composition, publication and sending. Checking several boundaries helps prevent a damaged intermediate artifact from being promoted into a trusted result.

## 10 Releases classification and intelligence

`build_releases.py` produces a normalized opportunity feed with source keys, dates and change tracking. It excludes awards and standing offers because an award or supplier arrangement is not an invitation to respond. Its persistent feed-start state helps make repeat runs predictable.

`classify.py` reads configuration to interpret category families, positive and negative terms, role families and effective status. Strong terms can establish relevance; weak wording may remain unclear. Exclusion terms can override a positive match. The actual closing time can make an apparently open portal row effectively closed.

This is rules-based classification. A graduate should be able to explain why a record matched by looking at its category, terms and reasons. It is different from training a statistical model on labelled examples. The tradeoff is that the rules are inspectable but require maintenance when business language changes.

`build_intelligence.py` combines source data into opportunities, renewal windows, hot plans, panel memberships, contract/RFQ links, exemption-use history, organisation identities and other derived tables. The build records metadata so readers can tell which transformation produced the views.

A renewal candidate is a contract with an end date in a configured window. It is a research signal. It does not prove that the agency will renew, go back to market or engage a different supplier. Likewise, an estimated daily rate calculated from contract value and business days is a calculation with assumptions, not a quoted candidate rate.

Organisation matching uses cleaned names, aliases, ABNs and review records. A suggested name match remains a suggestion until the evidence is sufficient. Joining two companies incorrectly can make every downstream total and recommendation misleading, so identity provenance is a core data-quality concern.

## 11 Priorities actions and human decisions

`today_list.py` turns evidence into proposed actions with owners, due dates and configured scores. `scoring.yaml` contains weights and thresholds. A score combines signals; it is not a probability of winning a tender unless the system has explicitly been designed and calibrated for that purpose, which this rules-based priority score has not.

The current draft configuration assigns 20 points each to attractiveness, Illoura fit and access, 15 each to timing and delivery readiness, and 10 to commercial potential. Each component is represented on a zero-to-one scale. `score(parts)` multiplies each component by its weight, sums the contributions and rounds the result. The configured labels begin at 85 for ACT NOW, 70 for INVESTIGATE and 50 for WATCH; lower scores are IGNORE. These are draft business rules, not a trained forecast.

For an illustrative calculation, components of 0.8, 0.7, 0.5, 1.0, 0.5 and 0.7 contribute 16, 14, 10, 15, 7.5 and 7 points. The total 69.5 rounds to 70, so the configured label is INVESTIGATE. Those illustrative component values do not describe a real opportunity. Missing bench evidence uses neutral readiness rather than pretending that suitable candidates are available. The TODAY selection also favours a mix of action types, so it is not simply the first fifteen rows after sorting one score column.

`action_tracker.py` preserves human status, notes, stages and follow-up dates while reconciling new machine evidence. Closed, declined and snoozed items need distinct treatment. A due follow-up may return to an active view without deleting the earlier decision history.

`prime_briefs.py` produces evidence-linked summaries for prime or partner research. Facts should retain their supporting source and qualifications. The system must not invent an available candidate, a partner contact, an invitation, a participation commitment or a rate because a template has an empty field.

The BDM workflow maintains versioned qualification cards. Access, capability, readiness and decision are separate questions. For example, a technically relevant RFQ can still be unqualified because invitation access is unknown. Explicit unknown values are more useful than a confident but unsupported green status.

## 12 The morning automatic workflow

The enabled `illoura-daily-brief.timer` starts the production coordinator at 06:15 Australia/Sydney. It uses a named time zone, so the local schedule follows daylight saving. On 16 November 2026, this is 00:45 India time. The intended send at 08:15 Sydney is 02:45 India time on that date. Those India conversions are date-specific; Sydney's offset changes seasonally.

`deploy/illoura_daily_brief.py` runs collection and publication, prepares the brief, waits for the send time, then invokes the sender. `deploy/illoura_cloud_collect.py` coordinates collection, confirmed-send reconciliation, backups, the restore drill and publication. `routine/run_once.py` runs the sources and supplemental research and requires essential evidence stages.

The standalone cloud collection timer is disabled because the daily-brief coordinator already owns the morning collection. Two independent schedulers writing the same stores would add overlap and confusing state. The separate health and certificate-renewal timers remain enabled.

Collection retries wait 5, 20, 40 and 60 minutes before further attempts. Composition has one retry after five minutes. Retry starts must remain within the 09:30 Sydney limit, and the absolute morning cutoff is noon. The send is not blindly retried, because delivery may have occurred before a connection failed.

Stage limits prevent a slow portal from consuming the entire morning indefinitely. The routine has per-source, source-stage, research and evidence budgets. The coordinator also applies an overall collection deadline and reserves time for composition and sending. A timeout terminates the relevant process tree, including separately started descendants on Linux.

## 13 Why one source gap can be tolerated

The brief currently depends on BuyICT. Other sources enrich the wider project but do not supply the brief's selected requests. A named problem in an unrelated source can therefore be reported as a gap while the internal brief proceeds, provided the whole collection is complete enough to assess and mandatory derived stages passed.

This is controlled degradation. It is not a general permission to ignore errors. BuyICT failure, a missing source report, a cut-short whole run, an unexplained failure or failed feed/views still stops the workflow. The coordinator must explicitly approve the exact gaps, and later components check that approval.

NSW's bounded notice history is an example. The opportunity listing can be complete while notice history extends beyond five pages. The run records NSW as partial and the coverage note names the gap. Readers can use current verified BuyICT requests while understanding that NSW history is incomplete.

## 14 How the daily brief is selected and composed

The selector reads open relevant BuyICT opportunities from `intelligence.db`. It prefers labour-hire IDs, considers requests closing more than 18 hours ahead and within 21 days, and selects at most eight. Professional-services requests can fill the configured fallback when fewer than three labour-hire requests are available. These are implemented selection rules, not a personalised capability model.

`refresh_daily_shortlist.py` obtains a fresh public copy for each selected request. The coordinator drops a listing if the live check fails or the record is no longer open with enough time remaining. If more than half of the live checks fail, composition stops rather than approving a misleading email.

`compose_checked_brief.py` combines those checks with factual editorial notes. It adds closing times, location, work arrangements, clearance, contract term and question deadlines when available. Restricted-seller wording receives an access warning. Public access to a listing does not mean the business can submit to it or read secure attachments.

`render_newsletter_preview.py` produces HTML, plain text and review artifacts. Source-derived text is escaped for HTML. `check_newsletter_layout.py` renders desktop and mobile widths and checks layout conditions such as overflow. Content hashes bind the reviewed artifacts to the exact files later sent.

Automated review is labelled as automated. There is no hidden claim that a person read and approved every morning's selection. A dry run composes a copy in a separate directory and never sends; it is a useful end-to-end test of selection, public checks and layout, but it cannot prove real email delivery.

## 15 Email authentication delivery and duplicate prevention

OAuth credentials authorize the Gmail API. The sender checks the authenticated profile against the configured sender. Sender and recipient settings are read from private configuration rather than hard-coded into source. The recipient is the authorized internal mailbox, not buyers or partners.

Before sending, the helper checks the source report, approved gaps, layout results, review authorization, edition date, recipient and content hashes. It checks for a sent copy and refuses an unresolved earlier attempt. It writes `send_attempt.json` before the network POST. After Gmail returns a message, it confirms the `SENT` label and writes `send_receipt.json`.

The difficult case is a network failure after Gmail accepted the email. Repeating the POST may send a duplicate. Deleting the attempt file would discard the evidence needed to make a safe decision. Instead, reconciliation searches Gmail Sent and records a confirmed message. A verified-not-found attempt needs an explicit operator decision before it is treated as undelivered.

Locks protect both a run and the cross-run send path. The coordinator checks other runs for the same edition. This is a practical duplicate-prevention design, not a mathematical guarantee of exactly-once delivery across an external mail service and a local filesystem.

The audit corrected matching of existing Sent messages. Sender and recipient headers are parsed into complete addresses, compared without case sensitivity, and the message must carry the `SENT` label. A display name or an address containing the configured address as a substring cannot establish delivery. Tests use fake Gmail sessions and make no network sends.

The OAuth application remains in pilot/testing under the owner's earlier decision. Its authorization may expire around 19 November 2026 at 20:46 India time. Publishing the OAuth app or reauthorizing is a separate owner-controlled action; a passing test today does not remove that future dependency.

## 16 Publication generations backups and recovery

Readers should not query databases while an intelligence rebuild is replacing tables. Publication creates a completed generation, validates it and changes the `current` pointer. A reader pins one generation for a request, so its metadata and rows refer to the same snapshot.

`deploy/illoura_cloud_ops.py` performs publication, backup, restore-drill, rollback and retention operations. SQLite's backup mechanism creates consistent database copies. Manifests record files, checksums, table information and source-run evidence. SHA-256 verifies byte identity; SQLite integrity checks verify internal database structure. Neither check proves the business meaning of every row.

An atomic pointer change is a small filesystem operation that makes the complete new generation visible without exposing a half-copied directory. The previous generation remains available for controlled rollback. Immutable generation files reduce accidental reader-side modification.

Backups and publications have different purposes. A publication is the allowlisted reader dataset. An operations backup also preserves evidence required to recover the workflow, including send attempts and receipts. Restoring data without those mail artifacts could make the system forget a send and risk duplication.

A restore drill verifies a backup in an isolated location rather than overwriting production. The normal coordinator performs one before publication. Automatic Lightsail snapshots provide an additional whole-machine recovery layer. Application-level backups and infrastructure snapshots complement each other; neither replaces a tested recovery procedure.

Retention protects current and previous generations, a configured number of recent older generations, active readers and recent completed backups. It rejects unsafe roots and escaping links. Run reports are delivery evidence and currently remain an unresolved retention item. Do not remove an uncertain mail attempt or receipt as a storage cleanup shortcut.

## 17 How the API and Claude Desktop work

`api/app.py` exposes a bounded read-only HTTP API. FastAPI handles routing, Pydantic validates inputs, and Uvicorn runs the service. nginx provides HTTPS and proxies to the local process. The public hostname and credentials stay in private configuration.

`/healthz` means the process can answer. `/readyz` means recent operational evidence says the service is ready. An API can answer catalog requests while readiness returns attention because the morning brief was missed. These two endpoints intentionally answer different questions.

Authenticated clients can read a catalog, inspect schema, submit bounded SQL or search selected text columns. The dataset/table allowlist excludes private working stores such as mail and workflow tracking databases. Read-only SQLite mode, `query_only`, an SQL authorizer and disabled extensions provide multiple barriers against writes or file access.

Queries have a three-second execution deadline, a maximum returned row count and bounds on SQL, strings and response size. A result cap does not mean the entire dataset has only that many records. To count contracts, use an aggregate query rather than counting the returned sample.

```sql
SELECT COUNT(*) AS matching_requests
FROM opportunities
WHERE source = ? AND status_effective = ?;
```

The query belongs to the `intelligence` dataset, with parameters such as `BuyICT` and `Open`. Parameters keep values separate from SQL syntax. The example counts the snapshot's matching rows; it does not perform a fresh portal check or establish seller access.

API keys are stored as SHA-256 digests in a private server file and can be disabled independently. Requests are rate-limited, failed authentication is throttled and query-string authentication is rejected. HTTPS protects the connection; the key still needs private storage on the client.

The MCP interface exposes four tools: list datasets, describe a dataset, query a dataset and search records. `api/claude_bridge.py` provides a local stdio bridge for Claude Desktop using private credentials. Procurement text is untrusted data, not instructions that can grant new tool permissions. Responses should cite official source links and disclose snapshot freshness.

## 18 Google Sheets Excel dashboard and BDM tools

Google Sheets and Excel make working views accessible to people who do not write SQL. `publish_sheets.py` manages registered destinations, raw views and derived engine views such as the tracker through `_engine_views` and `publish_all`. `export_views.py` produces local workbook outputs, and `listings_pipeline.py` creates normalized listing/contact summaries.

Spreadsheet synchronization must preserve human edits. The tracker workflow reads editable decisions before refreshing generated evidence. If the read fails, writing a replacement could erase changes, so the process should stop that write. Stable action IDs connect old decisions to newly generated rows.

`dashboard/` provides operational and working summaries. It is a convenience view rather than proof that collection succeeded; authoritative source reports and receipts remain separate artifacts.

`bdm_monitor.py` observes BuyICT revisions. The first successful observation silently establishes a baseline, and later changes create durable events. `bdm_local_runner.py` supervises refresh and local recording. `bdm_workbench.py` serves a loopback working interface. `bdm_workflow.py` stores qualification, follow-ups and edit history. Its internal alert features have their own configuration gates and are not automatically enabled by running the cloud morning brief.

Legacy `outreach.py` and `outreach_agent.py` contain separate outreach tooling. Their presence in the repository does not authorize outreach. No buyer or partner outreach was enabled during this audit; the existing authorized automatic mail is the internal daily brief.

## 19 The optional local assistant and research system

The local assistant uses bounded evidence retrieval over project stores. `planning.py` interprets requested tasks and constraints. `reasoning.py` retrieves and formats facts. `sheet_data.py` searches imported spreadsheet snapshots with workbook, tab and row provenance. `verification.py` can check selected contract facts through the official API.

Optional hybrid retrieval combines lexical matching with semantic vectors and reciprocal-rank fusion. Lexical retrieval finds matching words; embeddings represent semantic similarity; rank fusion combines result ordering. SQL constraints still define the valid population. Similarity is useful for finding potentially relevant text, not for proving a contract value or creating a missing fact.

The cache and model identity must match the indexed text. Otherwise a semantic result may refer to stale material. The project keeps current factual computation in SQL. A local model can help interpret or phrase evidence when explicitly enabled, but it cannot establish invitation access, current candidate availability or a buyer's private intent.

`LOCAL_AI_DISABLED` is the current operational instruction. The scheduled routine skips hybrid indexing. Historical model, benchmark and evaluation helpers remain in source and should not be mistaken for components running in production today.

Public research helpers collect allowlisted official sources, BuyICT news and reviewed company evidence. Website contact discovery records candidates for human review. It does not guess an address or grant permission to contact someone. RAP reviews retain the reviewed date, commitment scope and source evidence so an old statement does not silently become a current promise.

## 20 Monitoring and automatic recovery

`deploy/illoura_cloud_health.py` checks publication age, collection age, latest run state, the daily receipt, coordinator progress, disk, retention, Gmail authorization and certificate validity. A fresh publication alone cannot conceal a newly failed collection. Source diagnostics identify failed and missing sources without exposing raw private logs.

Health evidence is written to a server file and exposed by authenticated operations. Public readiness rejects failed or stale evidence. The external watcher runs every five minutes; CloudWatch evaluates aggregated periods, so an alert is not an instantaneous push for every failed request. Missing metric data is configured to alarm as well.

The health timer runs every 15 minutes. Certificate renewal has its own timer. Resource limits constrain memory and CPU usage. The API runs under its own service identity, while privileged operations perform publication and administration. Private token and key files have restrictive modes.

The failed daily-brief service visible during this audit reflects the earlier missed 15 November edition. Keeping that failure visible is more honest than resetting the status to green. The daily timer is still active and scheduled for the next morning. Actual recovery requires observing the next collection and confirmed send.

Recovery is bounded. Collection and composition can retry transient problems. Unrelated named source gaps can be tolerated. An uncertain email cannot be blindly retried. An expired credential may require the owner's action. A lost virtual machine needs a restore path. These are different failure categories and need different responses.

## 21 Testing and what each level establishes

Unit tests isolate functions with controlled inputs. Parser fixtures reproduce HTML or JSON variants without depending on a live portal. Temporary databases test persistence and identity. Fake clocks exercise deadline and time-zone boundaries. Mock Gmail sessions let tests inspect send gates without delivering messages.

Integration tests exercise several components together. The local crawl fixture uses Scrapy, middleware, pipelines and SQLite. API framework tests exercise real request handling against isolated data. Linux staging tests cover code paths that Windows cannot establish, including file locks and system-specific behavior.

Live canaries visit the actual affected source using isolated databases before a collector deployment. A full no-send rehearsal exercises all sources, evidence, publication and composition in the deployed environment. A real scheduled receipt is still required to establish unattended delivery.

GitHub Actions runs the main suite and local-assistant tests on Ubuntu and Windows. A local unpushed commit has not automatically received those remote CI results. The audit's separate staged server results are recorded as their own evidence.

Regression tests preserve a discovered bug as a failing example, then verify its correction. In this audit, contradictory source reports and ambiguous Sent headers failed before the fix. This is stronger evidence than writing a test that simply repeats the final implementation's logic.

Passing tests show that the checked examples behave as intended in the checked environment. They do not prove every portal layout, every network interruption, a power failure at every write boundary or all future dependency versions. Good operation combines testing with evidence, monitoring and recoverability.

## 22 Deployment version control and safe changes

The operator CLI is `deploy/aws/illoura_aws.py`. Its fixed actions stage allowed files, normalize line endings, verify staged hashes and run server tests. Collector and routine deployment preserve replaced files; API deployment checks process health and can roll back an unhealthy replacement. The operations installer is less transactional: it stages and tests code, installs the fixed files and verifies timer states, but does not provide the API installer's automatic rollback. Deployment does not enable unrelated timers as a side effect.

SSH host pinning checks that the administrator is connecting to the known server. The firewall restricts administrator SSH to the current single address and also permits the Lightsail browser-connect service alias. Changing a home network address can break administrator SSH without breaking the public HTTPS API.

The sequence for a parser change is reproduce, edit, run focused tests, live-test the affected collector into isolated databases, stage server tests, deploy, then rehearse the full workflow. For a sender change, fake transport tests must prove safety before a no-send preview is run. Testing the live sender by mailing arbitrary extra editions would undermine duplicate controls.

Commits make changes reviewable and recoverable locally. Pushing publishes them to the remote repository; merging changes the shared branch. Those are separate actions with separate approval rules. Never include private configuration or broad `git add --all` in an operational cleanup.

## 23 Security and privacy boundaries

Credentials live in private configuration and restricted server files. Documentation uses paths and key names, not credential values, mailbox addresses or server addresses. A key's digest can authenticate a presented secret without storing the secret itself in the key registry. The client still needs its actual secret, so the client's private access file must be protected.

Source HTML and JSON are untrusted inputs. HTML escaping prevents source text from becoming markup in a generated email. Parameterized SQL separates values from syntax. API allowlists and an authorizer restrict which database objects and functions a query can use. MCP instructions tell clients to treat retrieved procurement content as evidence.

Permissions separate responsibilities. The collector can write working data, the API reads publications, and privileged operations manage publication and deployment. A read-only API should never provide an indirect route to sending email or changing a qualification card.

Public data is not a blanket authorization for every use. The project follows robots and site terms, avoids LinkedIn scraping, keeps personal/contact research separate and does not autonomously contact buyers or partners. The internal daily brief is the authorized recurring message.

## 24 Practical examples for a new engineer

To investigate a missing request, start with the source identity and official URL. Check the source report, then the source row, then the normalized opportunity and classification reasons. Check whether the request is outside the selection window, already closed, not relevant, beyond the eight-item limit or dropped by the fresh check. Do not start by changing the email template.

To investigate a zero-row collection, ask whether no records were due, the site explicitly reported zero, all extracted rows were unchanged, persistence failed, or extraction failed. Those outcomes can all produce no new rows but imply very different health states.

To investigate a missed email, read the current run phase, source gaps, mandatory evidence results, layout result, review hashes, attempt file and receipt. A readable receipt with `sent: true` is evidence; the existence of an empty file is not. If an attempt is unresolved, use reconciliation instead of deleting it and trying again.

To investigate a wrong company total, inspect the identity mapping and whether sources overlap. The API contract store and the contract page store describe overlapping contracts, so summing both totals double-counts. Use the intended canonical dataset and query the full matching population.

For local parser experiments, use `AUSTENDER_DB_DIR` and disable Sheets synchronization. Use test fixtures first, then the affected live source in an isolated directory. Never experiment by editing the published generation or a private send receipt.

## 25 A learning path through the code

First follow `run_spiders.py`, one straightforward source spider and `pipelines.py`. Trace one item into `database_manager.py`. Then read `classify.py`, `build_releases.py` and one intelligence builder to see how saved facts become derived views.

Next follow `routine/run_once.py` and its source gate. Read `deploy/illoura_cloud_collect.py` for backup and publication boundaries, then `deploy/illoura_daily_brief.py` for selection, composition and send timing. Read `send_gmail.py` with its tests open beside it.

Finally inspect `api/app.py`, the health module, systemd units and the deployment CLI. These explain why a production data service needs more than a scraper. As exercises, explain a valid empty day, a partial NSW day, a BuyICT outage, a Gmail timeout after acceptance and a database publication interrupted before the pointer switch. For each, identify the durable evidence and the safe next action.

## 26 Module map and further reading

| Area | Key code | Detailed reference |
|---|---|---|
| Collection orchestration | `run_spiders.py`, `settings.py`, `middlewares.py`, `duplicate_filter.py`, `pipelines.py` | [Collectors](reference/collectors.md) |
| Eight source adapters | `austender_api.py`, `spiders/*.py`, `nsw_tender_fetch.py` | [Collectors](reference/collectors.md) |
| Data storage and backup | `database_manager.py`, `project_paths.py`, `backup_databases.py`, `db_query_tool.py` | [Databases](reference/databases.md) |
| Transformations and priorities | `build_intelligence.py`, `build_releases.py`, `classify.py`, `today_list.py`, `prime_briefs.py`, `mmr_list.py`, `state_contracts.py`, `action_tracker.py` | [Intelligence](reference/intelligence.md) |
| Workbooks and Sheets | `publish_sheets.py`, `build_intelligence.py`, `export_views.py`, `listings_pipeline.py`, `dashboard.py` | [Exports and Sheets](reference/exports-and-sheets.md) |
| Daily routine | `routine/run_once.py`, `routine/scripts/`, `deploy/illoura_daily_brief.py` | [Cloud and daily brief](reference/cloud-and-daily-brief.md) |
| Publication and health | `deploy/illoura_cloud_ops.py`, `deploy/illoura_cloud_health.py`, `deploy/illoura_cloud_collect.py` | [Cloud operations](cloud_operations.md) |
| AWS installation and operation | `deploy/aws/`, `deploy/*.service`, `deploy/*.timer`, `deploy/system/` | [Cloud and daily brief](reference/cloud-and-daily-brief.md) |
| API and client bridge | `api/app.py`, `api/claude_bridge.py`, `api/install-api.sh`, `api/enable-https.sh` | [API and Claude](reference/api-and-claude.md) |
| BDM workflow | `bdm_monitor.py`, `bdm_local_runner.py`, `bdm_workflow.py`, `bdm_workbench.py` | [BDM and outreach](reference/bdm-and-outreach.md) |
| Optional research and local AI | `local_assistant/` | [Local assistant](reference/local-assistant.md) |
| Regression and integration tests | `tests/`, `local_assistant/test_*.py`, `api/test_security.py` | [Tests](reference/tests.md) |
| Configuration and private boundaries | `config/*.yaml`, requirements files, environment names | [Configuration](reference/configuration.md) |
| Operational history | `docs/workstreams/`, `docs/history.md` | [Progress](workstreams/progress.md) |

The detailed references enumerate the remaining utilities and configuration files within each area, including legacy and optional components. Historical statements in those references describe their original review context; use the current audit report and private handoff for the latest runtime state. Implementation facts in this guide can be followed directly to the named code and its regression tests.
