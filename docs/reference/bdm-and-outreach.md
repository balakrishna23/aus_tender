# Reference: BDM monitor and outreach tooling

Maintain reviewable BDM work, internal change alerts and legacy outreach tools.

Files covered:

- `bdm_monitor.py`
- `bdm_local_runner.py`
- `bdm_workflow.py`
- `bdm_workbench.py`
- `bdm_authorize_gmail.py`
- `bdm_workbench.html`
- `outreach.py`
- `outreach_agent.py`
- `evaluate_outreach.py`

The BDM monitor observes public BuyICT changes and combines fresh RFQs with previously built planning/RAP evidence. Its state is separate from source databases. The first successful poll silently establishes a baseline; later revisions create durable events. Local recording, preview/export and optional internal mail are distinct operations. See [the BDM monitor guide](../bdm_monitor.md) for the complete workflow; its historical deployment statements do not establish current runtime state.

| Module | Responsibility and boundaries |
|---|---|
| `bdm_monitor.py` | `poll --refresh` runs the existing source refresh, observes relevant open RFQs and reconciles planning into `bdm_monitor.db`; `preview`, `export`, `import-edits`, `status`, and `reconcile` manage working evidence and internal alerts. Send requires both configuration opt-in and `--send`. Restricts internal delivery to one recipient on the sender's domain, verifies Gmail identity and reconciles uncertain batches through Sent. Tracks stale/busy source state and emits heartbeat evidence. |
| `bdm_local_runner.py` | One cycle or `--loop` supervises refresh, monitor/export, previews, optional workbench and local journal/status generation. Journal records are keyed by event ID and do not mark mail sent. Uses a local runner lock; loop interval is 15 minutes. Default private configuration path is `inputs/bdm/monitor.yaml`. |
| `bdm_workflow.py` | Stores versioned qualification cards, reconciles stale work/legacy tracked actions, deduplicates desktop and operational notifications, and reminds due follow-ups. Explicit access/capability/readiness/decision fields keep unknowns visible. Legacy decisions update the original action tracker; stale export imports must not overwrite a shadow copy. |
| `bdm_workbench.py` | Loopback HTTP working view, default port 8767. Serves cards/status and validates host/origin/token and edit versions before saving. Human edits and evidence refresh use locks to preserve concurrent changes. CLI selects state/data/output directories and port. |
| `bdm_workbench.html` | Browser interface for source-linked cards, qualification, owner/stage/follow-up/notes/outcome, and local status. It is a local working view, not a public portal. |
| `bdm_authorize_gmail.py` | Manual installed-app OAuth authorization using `--client` and `--token` private paths. Requests Gmail sending/read scopes for identity and Sent reconciliation; saves a private offline token. It is independent of the chat Gmail connector. |
| `outreach.py` | Manual `queue` → human Sheet approval → `send`; `suppress` records opt-outs. Selects people-related open releases, drafts from source/template facts, uses SMTP, cooldown, suppression and per-run limits. `--dry-run` avoids sending. Local history is `outreach.db`; review state is in Sheets. |
| `outreach_agent.py` | Legacy send-capable agent. Reads an off-by-default Sheet switch, processes inbox suppression/bounces/replies, drafts using configured AI or template fallback, checks claims and honors mode, business hours/holidays, cooldown/warm-up caps and locking. Can forward replies and send summaries. State is `outreach_agent.db`. Its autonomous functionality remains in code and must not be enabled as part of BDM or daily-brief maintenance. |
| `evaluate_outreach.py` | Reads logged Sheet messages, applies automatic content checks and optional configured AI grading; compares recent rounds and sizes. Scores are review aids; model grades are not independent validation. Can make remote Sheets/model calls. |

No autonomous buyer or partner outreach is authorized by this project routine. Public contact evidence, an open RFQ, MMR matching or a RAP commitment does not grant permission to approach anyone. The internal daily brief and explicitly configured internal BDM alerts serve a different purpose. The legacy agent's existence and the Windows registration script's outreach task are operational hazards to review before using those old setup paths. Actual scheduled-task states, current mail permissions and delivery are unverified here.
