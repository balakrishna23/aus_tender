# Engineering history (newest first)

A sanitized engineering chronology for this public repository. Business content, people's
contact details, sent-newsletter records and private operational notes are kept in the
team's private workspace, not here. For the original 7 October design record see
[HANDOFF.md](HANDOFF.md); for current operations see [cloud_operations.md](cloud_operations.md).

## 12 November 2026: cloud stabilisation (branch `fix/cloud-stabilization-20261112`)

Six agreed workstreams, each with a record in [workstreams/](workstreams/):

1. **Retention** for served snapshot generations, failed staging and operations backups,
   with dry-run, attention reporting and a ten-day disk-budget simulation.
2. **Audited commits** of the accumulated cloud, BDM and data-quality work, followed by a
   privacy scrub: real mailbox addresses and a machine path were moved out of source into
   private configuration before anything was published.
3. **One operations CLI** (`deploy/aws/illoura_aws.py`) with fixed remote actions, staged and
   hash-verified deploys, server-side tests, automatic API rollback; the API service and the
   Claude bridge were brought into `api/`.
4. **Lint cleanup and a formatting-only commit** for the newer cloud/API files (ASTs identical).
5. **Handoff restructure**: current state separated from dated history; this file added.
6. **Review fixes**: restore drills flag unresolved sends, rollback swaps current/previous,
   failed API keys are throttled per client with bounded state.

## 12 November 2026: AWS trial (Sydney)

Read-only procurement API and MCP endpoint over HTTPS on a single Lightsail instance, serving
immutable SQLite snapshot generations with a table allowlist, SQL authorizer and query limits.
Local stdio bridge for Claude Desktop. First cloud collection run, operations backup and
restore drill, health timer and an AWS-managed instance alarm. Collection timer left disabled.

## 6-10 November 2026: BuyICT monitoring and BDM ledger

Change detection for public BuyICT requirements (silent baseline, revisions, reopenings,
closures), a durable internal alert queue with send-once reconciliation, and a combined
results ledger with owner/stage/follow-up editing and import validation.

## Late October - early November 2026: evidence and data quality

Contract-notice history enrichment from the official AusTender OCDS API, supplier identity
inventory, a data-quality sidecar (placeholder dates, end-before-start, missing originals,
unconfirmed ABN proposals kept as review items), and an evidence library for partner
Reconciliation Action Plans.

## 7 October 2026: engine views

Action tracker with human-field preservation and overdue follow-ups, one-page prime briefs,
and the engine views described in [HANDOFF.md](HANDOFF.md).

## Early October 2026: compliant collection

Switched contract collection to the official AusTender OCDS API and robots.txt-compliant page
routes; scheduled tasks disabled pending a working prototype.
